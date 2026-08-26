"""ORCHESTRATOR-001: детерминированный скелет замкнутого контура (closed-loop pipeline).

Владелец: Лиза Д. (Closed-Loop Orchestrator). Координирует вызовы
специализированных агентов, Arbiter, MagnitudeResolver,
PredictiveSafetyScreen, SimulatorRuntime, Guardian и CHDD в
последовательности, зафиксированной в `docs/team/closed-loop-orchestrator.md`.
Не реализует их внутреннюю логику (см. раздел FORBIDDEN AUTHORITY этого
документа) -- только последовательность вызовов, границы полномочий и
гарантию допустимости коммита:

    commit_eligible ⊆ simulated ∩ guardian_passed

Инварианты, которые оркестратор обязан вызывать (и вызывает) на своих
границах: T3 (пол HOLD/baseline перед симуляцией), T4 (Screen не фильтрует),
T6 (HOLD -- точный перенос предыдущих режимов). Дополнительно: лимит
`simulation_budget` -- закрытый отказ при превышении; запрос Round 2 от
Arbiter (`ArbiterDecision.round2_request`) исполняется максимум один раз на
шаг, рекурсивные переговоры -- ошибка; каждый завершённый шаг дописывается
в append-only `DecisionTrace`, если журнал подключён.

Ни одна зависимость не имеет фальшивого значения по умолчанию. Оркестратор,
собранный без исполнителя, Guardian, CHDD или извлечения реализованного
состояния, отказывается собираться -- вместо того чтобы выдать «успешную»
симуляцию, «легальный» кандидат и экономику из хеша и закоммитить решение,
за которым не стоит ни физики, ни легальности, ни экономики. Тестовые
двойники живут в tests/support и подставляются вызывающей стороной явно.

Прогон связан с кандидатом пересчитанной идентичностью, а не позиционным
доверием и не одним полем провенанса: сам исполнявшийся `SimulationRequest`
обязан называть этого кандидата, провенанс -- описывать то же расписание, а
ключ симуляции -- пересчитываться из фактического запроса. Ярлык, который
проставил вызывающий, идентичности не устанавливает.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from aios.evaluation.canonical import JsonValue, digest
from aios.evaluation.simulation import (
    SimulationRequest,
    SimulatorIdentity,
    compute_request_digest,
    compute_result_digest,
    compute_simulation_key,
)
from aios.execution import RuntimeExecution
from aios.mas.arbiter import ArbiterDecision, Round2Request
from aios.mas.common import MAS_CONTRACT_SCHEMA_VERSION, AgentRole, CandidateKind, DecisionRound
from aios.mas.envelopes import ArbiterEnvelope, InjectionEnvelope, ProductionEnvelope
from aios.mas.evidence import (
    EvidenceArtifact,
    HorizonContext,
    MemorySlice,
    ProposalReference,
    SimulationBudget,
)
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.invariants import (
    require_candidate_preservation,
    require_hold_carry_forward,
    require_simulation_floor,
)
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.orchestration.boundaries import (
    CHDD,
    ArbiterLike,
    CandidateExecutor,
    GuardianVerdict,
    IdentityPredictiveSafetyScreen,
    PredictiveSafetyScreen,
    RealizedConstraintGuardian,
    RealizedStateExtractor,
    StateAdvancer,
    identity_pre_action_validator,
    never_conflict,
)
from aios.mas.orchestration.trace import DecisionTrace, StepFailure
from aios.mas.proposals import InjectionAgentOutput, ProductionAgentOutput
from aios.mas.protocols import Agent, MagnitudeResolver
from aios.mas.selection import (
    CandidateEvaluation,
    ChddOutcome,
    EconomicSelectionError,
    EconomicSelectionResult,
    EconomicSelector,
    GuardianOutcome,
    SimulationOutcome,
    VerifiedChddInput,
    VerifiedExecution,
    VerifiedRealizedState,
)
from aios.state import StateCard

SpecialistFactory = Callable[[bool], object]
ConflictPredicate = Callable[[ProductionAgentOutput, InjectionAgentOutput], bool]
PreActionValidator = Callable[[tuple[MagnitudeCandidate, ...]], tuple[MagnitudeCandidate, ...]]


class OrchestrationError(ValueError):
    """Конвейер не смог получить кандидата, годного для коммита на этом шаге."""


def _require_dependency(name: str, value: object) -> object:
    """Отсутствующая авторитетная зависимость -- отказ сборки, а не заглушка."""
    if value is None:
        raise OrchestrationError(
            f"closed_loop_orchestrator.{name}: an authoritative implementation is required; "
            "there is no default, because a silent test double would commit a candidate "
            "with no physics, no legality and no economics behind it"
        )
    return value


_STAGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("runtime execution identity", "runtime_identity"),
    ("duplicate candidate digest", "candidate_resolution"),
    ("executor returned", "runtime_execution"),
    ("CHDD", "economics"),
    ("Guardian-passed", "guardian"),
    ("simulation_budget", "simulation_budget"),
    ("Round 2", "round2"),
)


def _stage_of(error: OrchestrationError) -> str:
    """Стадия шага по детерминированному тексту отказа."""
    message = str(error)
    for marker, stage in _STAGE_MARKERS:
        if marker in message:
            return stage
    return "decision_step"


def _identity_mismatch(where: str, expected: object, actual: object) -> OrchestrationError:
    return OrchestrationError(
        f"decision_step: runtime execution identity does not belong to the executed "
        f"candidate ({where}: expected {expected!r}, got {actual!r})"
    )


def _require_execution_identity(candidate: MagnitudeCandidate, execution: RuntimeExecution) -> None:
    """Прогон обязан принадлежать именно этому кандидату -- по пересчёту.

    Одного поля `provenance.candidate_digest` недостаточно: это ярлык, и
    исполнитель, вернувший чужой прогон, может проставить в нём нужное
    значение. Тогда физика кандидата B принималась бы как физика A и доходила
    до Guardian, CHDD и коммита.

    Поэтому идентичность проверяется по всей доступной цепочке, и там, где
    типы это позволяют, дайджесты ПЕРЕСЧИТЫВАЮТСЯ из фактических объектов, а
    не сверяются между двумя записанными полями:

    * сам `SimulationRequest` называет кандидата (runtime кладёт его дайджест,
      вид и дату в payload, а bundle -- в scenario_digest), поэтому чужой
      запрос виден независимо от того, что написано в провенансе;
    * `provenance.schedule_sha256` обязан совпасть с `request.schedule_digest`;
    * `provenance.simulation_key` пересчитывается из фактического запроса и
      записанной идентичности симулятора -- подделать его, не имея запроса
      кандидата, нельзя;
    * `result.request_digest`/`result.simulation_key` -- происхождение самого
      результата: физически честный результат другого прогона описывает сам
      себя правильно, поэтому отличить его можно только по тому, какой запрос
      его породил;
    * `result.result_digest` пересчитывается из фактического содержимого
      результата вместе с происхождением, поэтому перепривязать результат к
      другому запросу, сохранив дайджест, нельзя.

    Возвращается корень происхождения прогона: получить его иначе, чем пройдя
    эту проверку, нельзя, и всё, что считается ниже по конвейеру, ссылается
    именно на него, а не на ярлык, написанный вызывающим.
    """
    if not isinstance(execution, RuntimeExecution):
        raise OrchestrationError(
            f"decision_step: executor returned {type(execution).__name__}, expected RuntimeExecution"
        )

    canonical = candidate.canonical()
    expected_digest = canonical.digest()
    request = execution.request
    provenance = execution.provenance
    result = execution.result

    if not isinstance(request, SimulationRequest):
        raise _identity_mismatch("request type", "SimulationRequest", type(request).__name__)

    # 1. Запрос, который реально исполнялся, называет кандидата сам.
    payload = request.payload if isinstance(request.payload, Mapping) else {}
    for field_name, expected_value in (
        ("candidate_digest", expected_digest),
        ("candidate_kind", str(canonical.candidate_kind)),
        ("effective_date", canonical.effective_date.isoformat()),
    ):
        if field_name not in payload:
            raise _identity_mismatch(f"request.payload.{field_name}", expected_value, "<missing>")
        if payload[field_name] != expected_value:
            raise _identity_mismatch(
                f"request.payload.{field_name}", expected_value, payload[field_name]
            )
    if request.scenario_digest != canonical.bundle_digest:
        raise _identity_mismatch(
            "request.scenario_digest", canonical.bundle_digest, request.scenario_digest
        )

    # 2. Провенанс обязан описывать того же кандидата.
    for where, expected_value, actual_value in (
        ("provenance.candidate_digest", expected_digest, provenance.candidate_digest),
        ("provenance.candidate_kind", str(canonical.candidate_kind), provenance.candidate_kind),
        ("provenance.bundle_digest", canonical.bundle_digest, provenance.bundle_digest),
        ("provenance.resolver_version", canonical.resolver_version, provenance.resolver_version),
    ):
        if actual_value != expected_value:
            raise _identity_mismatch(where, expected_value, actual_value)

    # 3. Провенанс и запрос обязаны описывать одно и то же расписание.
    if not provenance.schedule_sha256:
        raise _identity_mismatch("provenance.schedule_sha256", "<a schedule digest>", "<missing>")
    if provenance.schedule_sha256 != request.schedule_digest:
        raise _identity_mismatch(
            "provenance.schedule_sha256", request.schedule_digest, provenance.schedule_sha256
        )

    # 4. Ключ симуляции пересчитывается из фактического запроса.
    identity_json = provenance.simulator_identity_json
    if not isinstance(identity_json, Mapping) or "simulator_id" not in identity_json:
        raise _identity_mismatch(
            "provenance.simulator_identity", "<a simulator identity>", identity_json
        )
    recomputed_key = compute_simulation_key(
        request,
        SimulatorIdentity(
            simulator_id=str(identity_json.get("simulator_id", "")),
            simulator_version=str(identity_json.get("simulator_version", "")),
            configuration=identity_json.get("configuration") or {},
        ),
    )
    if provenance.simulation_key != recomputed_key:
        raise _identity_mismatch(
            "provenance.simulation_key", recomputed_key, provenance.simulation_key
        )

    # 5. Результат обязан называть запрос, который его породил.
    request_digest = compute_request_digest(request)
    if not result.is_request_bound:
        raise _identity_mismatch(
            "result.request_digest", request_digest, "<missing: result carries no request binding>"
        )
    if result.request_digest != request_digest:
        raise _identity_mismatch("result.request_digest", request_digest, result.request_digest)
    if result.simulation_key != recomputed_key:
        raise _identity_mismatch("result.simulation_key", recomputed_key, result.simulation_key)

    # 6. Результат обязан быть самосогласованным вместе со своим происхождением.
    recomputed_result_digest = compute_result_digest(
        status=result.status,
        trajectory_ref=result.trajectory_ref,
        metadata=result.metadata,
        failure=result.failure,
        request_digest=result.request_digest,
        simulation_key=result.simulation_key,
    )
    if result.result_digest != recomputed_result_digest:
        raise _identity_mismatch(
            "result.result_digest", recomputed_result_digest, result.result_digest
        )

    return VerifiedExecution(
        candidate_digest=expected_digest,
        request_digest=request_digest,
        simulation_key=recomputed_key,
        result_digest=result.result_digest,
    )


def _resolve_specialist(
    name: str,
    agent: object | None,
    factory: SpecialistFactory | None,
    llm_enabled: bool,
) -> object:
    """Специалист либо готовый, либо собранный фабрикой под флаг ablation."""
    if agent is not None and factory is not None:
        raise OrchestrationError(
            f"closed_loop_orchestrator.{name}: pass either the agent or its factory, not both"
        )
    if factory is not None:
        built = factory(llm_enabled)
        if built is None:
            raise OrchestrationError(f"closed_loop_orchestrator.{name}: factory returned None")
        return built
    return _require_dependency(name, agent)


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    """Конфигурация прогона.

    Всё, кроме `llm_enabled`, должно совпадать между прогоном Variant B
    (LLM выключен) и Variant D (LLM включён), чтобы сравнение было честным
    -- см. T5 / `require_ablation_equivalence`.
    """

    variant_id: str
    llm_enabled: bool
    simulation_budget: int
    resolver_version: str
    guardian_config_digest: str
    chdd_config_digest: str
    schema_version: str = MAS_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.simulation_budget <= 0:
            raise OrchestrationError("orchestrator_config.simulation_budget: must be > 0")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "variant_id": self.variant_id,
            "llm_enabled": self.llm_enabled,
            "simulation_budget": self.simulation_budget,
            "resolver_version": self.resolver_version,
            "guardian_config_digest": self.guardian_config_digest,
            "chdd_config_digest": self.chdd_config_digest,
        }

    def digest(self) -> str:
        return digest(self.to_json())


# ---------------------------------------------------------------------------
# Вход/выход одного шага.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionStepInput:
    """Всё, что нужно оркестратору, чтобы прогнать ровно один шаг принятия решения.

    `previous_committed` -- кандидат, закоммиченный на предыдущем шаге. Нужен
    для инварианта T6: HOLD-кандидат обязан быть точным переносом предыдущих
    численных режимов, а не «похожим» пересчётом (`require_hold_carry_forward`).
    На самом первом шаге предыдущего коммита не существует -- тогда поле
    остаётся None и проверка T6 пропускается (сравнивать не с чем).
    `run_steps`/`default_next_input` протягивают это поле автоматически.
    """

    state: StateCard
    horizon_context: HorizonContext
    memory_slice: MemorySlice
    production_allowed_actions: tuple[ProductionAction, ...]
    injection_allowed_actions: tuple[InjectionAction, ...]
    previous_committed: MagnitudeCandidate | None = None

    # Общая детерминированная диагностика шага. Специалист, который её не
    # получил, рассуждает по одному StateCard: раньше эти слоты не были
    # предусмотрены входом вовсе и всегда приходили в envelope как None.
    pressure_diagnostic: EvidenceArtifact | None = None
    sweep_diagnostic: EvidenceArtifact | None = None
    geology_card: EvidenceArtifact | None = None
    response_map: EvidenceArtifact | None = None
    compensation_state: EvidenceArtifact | None = None
    # Экономический контекст контрактно виден только Production.
    economic_context: EvidenceArtifact | None = None


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Прослеживаемый результат по одному кандидату: одна строка на каждый resolved MagnitudeCandidate."""

    candidate_digest: str
    candidate_kind: str
    simulation_status: str | None = None
    guardian: GuardianVerdict | None = None
    npv_mln_rub: float | None = None
    ineligibility_reason: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_digest": self.candidate_digest,
            "candidate_kind": self.candidate_kind,
            "simulation_status": self.simulation_status,
            "guardian": self.guardian.to_json() if self.guardian is not None else None,
            "npv_mln_rub": self.npv_mln_rub,
            "ineligibility_reason": self.ineligibility_reason,
        }


@dataclass(frozen=True, slots=True)
class DecisionStepResult:
    """Полный след одного шага принятия решения, плюс закоммиченный кандидат и следующее состояние.

    `committed_candidate` хранится целиком (а не только дайджестом): следующий
    шаг сверяет свои HOLD-кандидаты именно с его численными режимами (T6), а
    запись в `DecisionTrace` остаётся самодостаточной для разбора постфактум.
    """

    round2_triggered: bool
    production_envelope_digest: str
    injection_envelope_digest: str
    arbiter_decision_digest: str
    candidates: tuple[CandidateRecord, ...]
    committed_candidate_digest: str
    committed_candidate: MagnitudeCandidate
    selection: EconomicSelectionResult
    next_state: StateCard

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "round2_triggered": self.round2_triggered,
            "production_envelope_digest": self.production_envelope_digest,
            "injection_envelope_digest": self.injection_envelope_digest,
            "arbiter_decision_digest": self.arbiter_decision_digest,
            "candidates": [record.to_json() for record in self.candidates],
            "committed_candidate_digest": self.committed_candidate_digest,
            "committed_candidate": self.committed_candidate.to_json(),
            "selection": self.selection.to_json(),
            "next_state_digest": self.next_state.digest(),
        }


# ---------------------------------------------------------------------------
# Оркестратор.
# ---------------------------------------------------------------------------


class ClosedLoopOrchestrator:
    """Связывает один шаг принятия решения от начала до конца. Владеет только последовательностью вызовов.

    У `production_agent`, `injection_agent`, `arbiter`, `magnitude_resolver`
    нет значения по умолчанию внутри пакета: настоящих реализаций ещё нет, а
    единственные существующие заглушки (`mas_team_stubs`, из тестового слоя
    поддержки) нельзя импортировать из `src/aios` (TF14/TF15). Вызывающая
    сторона -- тесты, для ORCHESTRATOR-001 -- обязана подставлять их явно.
    """

    def __init__(
        self,
        *,
        config: OrchestratorConfig,
        production_agent: Agent[ProductionEnvelope, ProductionAgentOutput] | None = None,
        injection_agent: Agent[InjectionEnvelope, InjectionAgentOutput] | None = None,
        production_agent_factory: SpecialistFactory | None = None,
        injection_agent_factory: SpecialistFactory | None = None,
        arbiter: ArbiterLike,
        magnitude_resolver: MagnitudeResolver,
        candidate_executor: CandidateExecutor,
        realized_state_extractor: RealizedStateExtractor,
        guardian: RealizedConstraintGuardian,
        chdd: CHDD,
        state_advancer: StateAdvancer,
        conflict_predicate: ConflictPredicate = never_conflict,
        pre_action_validator: PreActionValidator = identity_pre_action_validator,
        safety_screen: PredictiveSafetyScreen | None = None,
        decision_trace: DecisionTrace | None = None,
    ) -> None:
        self._config = config
        # T5: один и тот же граф оркестрации на обоих вариантах ablation.
        # Флаг доходит до специалиста через фабрику, а не остаётся полем
        # конфига, которое никто не читает: топология не ветвится, меняется
        # только LLM-путь внутри специалиста.
        self._production_agent = _resolve_specialist(
            "production_agent", production_agent, production_agent_factory, config.llm_enabled
        )
        self._injection_agent = _resolve_specialist(
            "injection_agent", injection_agent, injection_agent_factory, config.llm_enabled
        )
        self._arbiter = _require_dependency("arbiter", arbiter)
        self._magnitude_resolver = _require_dependency("magnitude_resolver", magnitude_resolver)
        # Ниже -- физика, легальность, экономика и переход состояния. Молчаливой
        # подстановки нет: отсутствующая зависимость означает отказ сборки, а не
        # правдоподобно выглядящий закоммиченный кандидат.
        self._candidate_executor = _require_dependency("candidate_executor", candidate_executor)
        self._realized_state_extractor = _require_dependency(
            "realized_state_extractor", realized_state_extractor
        )
        self._guardian = _require_dependency("guardian", guardian)
        self._chdd = _require_dependency("chdd", chdd)
        self._state_advancer = _require_dependency("state_advancer", state_advancer)
        self._conflict_predicate = conflict_predicate
        self._pre_action_validator = pre_action_validator
        self._safety_screen = (
            safety_screen if safety_screen is not None else IdentityPredictiveSafetyScreen()
        )
        self._decision_trace = decision_trace

    @property
    def config(self) -> OrchestratorConfig:
        return self._config

    @property
    def decision_trace(self) -> DecisionTrace | None:
        return self._decision_trace

    def run_decision_step(self, step_input: DecisionStepInput) -> DecisionStepResult:
        """Один шаг конвейера. Падение шага оставляет разбираемый след."""
        try:
            return self._run_decision_step(step_input)
        except OrchestrationError as error:
            if self._decision_trace is not None:
                self._decision_trace.append_failure(
                    StepFailure(
                        step_index=len(self._decision_trace),
                        stage=_stage_of(error),
                        reason=str(error),
                        state_card_digest=step_input.state.digest(),
                    )
                )
            raise

    def _run_decision_step(self, step_input: DecisionStepInput) -> DecisionStepResult:
        production_output, injection_output, round2_triggered = self._run_specialist_rounds(
            step_input
        )

        arbiter_envelope = self._build_arbiter_envelope(
            step_input, production_output, injection_output
        )
        arbiter_decision = self._arbiter.decide(arbiter_envelope)

        # Arbiter имеет контрактное право сам запросить Round 2
        # (ArbiterDecision.round2_request) -- но общий лимит один Round 2 на
        # шаг, кем бы он ни был запрошен. Рекурсивные переговоры запрещены.
        if arbiter_decision.round2_request is not None:
            if round2_triggered:
                raise OrchestrationError(
                    "decision_step: Arbiter requested Round 2 after Round 2 already ran; "
                    "at most one Round 2 per decision step"
                )
            production_output, injection_output = self._run_arbiter_requested_round2(
                step_input, arbiter_decision.round2_request, production_output, injection_output
            )
            round2_triggered = True
            arbiter_envelope = self._build_arbiter_envelope(
                step_input, production_output, injection_output
            )
            arbiter_decision = self._arbiter.decide(arbiter_envelope)
            if arbiter_decision.round2_request is not None:
                raise OrchestrationError(
                    "decision_step: Arbiter requested a second Round 2; "
                    "recursive negotiation is forbidden"
                )

        candidates = self._magnitude_resolver.resolve(arbiter_decision)
        candidates = self._pre_action_validator(candidates)

        before_ids = tuple(candidate.digest() for candidate in candidates)
        annotated = self._safety_screen.annotate(candidates)
        after_ids = tuple(candidate.digest() for candidate in annotated)
        require_candidate_preservation(before_ids, after_ids)
        candidates = annotated

        # Явная проверка "пола" кандидатов на симуляцию -- ровно там, где её
        # ставит диаграмма конвейера: после Screen, перед SimulatorRuntime.
        require_simulation_floor(candidates)

        # T6: HOLD -- это точный canonical-перенос предыдущих режимов, а не
        # «пересчитать magnitude и получить похожие значения». Проверяется
        # против кандидата, закоммиченного на предыдущем шаге; на первом шаге
        # предыдущего коммита нет и сравнивать не с чем.
        self._require_hold_is_exact_carry_forward(step_input, candidates)

        # Бюджет симуляций -- закрытый отказ, а не молчаливое усечение:
        # выбросить «лишних» кандидатов здесь нельзя (это нарушило бы
        # сохранение кандидатов и пол HOLD/baseline), поэтому превышение --
        # ошибка того, кто собрал candidate set.
        if len(candidates) > self._config.simulation_budget:
            raise OrchestrationError(
                f"decision_step: {len(candidates)} resolved candidates exceed "
                f"simulation_budget={self._config.simulation_budget}"
            )

        result = self._simulate_guard_and_commit(
            candidates=candidates,
            production_envelope_digest=production_output.primary_proposal.envelope_digest,
            injection_envelope_digest=injection_output.primary_proposal.envelope_digest,
            arbiter_decision=arbiter_decision,
            round2_triggered=round2_triggered,
            step_input=step_input,
        )
        if self._decision_trace is not None:
            self._decision_trace.append(result)
        return result

    def run_steps(
        self,
        first_input: DecisionStepInput,
        steps: int,
        *,
        advance_input: Sequence[DecisionStepInput] | None = None,
    ) -> tuple[DecisionStepResult, ...]:
        """Прогоняет `steps` шагов принятия решения подряд, состояние течёт вперёд.

        Если `advance_input` не передан, вход следующего шага собирается
        через `default_next_input` (те же допустимые действия/срез памяти,
        следующее состояние из результата предыдущего шага, на один шаг
        горизонта меньше). Явная передача `advance_input` (по одной записи
        на шаг после первого) позволяет тесту менять допустимые действия на
        каждом шаге.
        """

        if steps <= 0:
            raise OrchestrationError("run_steps.steps: must be > 0")
        if advance_input is not None and len(advance_input) != steps - 1:
            raise OrchestrationError("run_steps.advance_input: must have exactly steps - 1 entries")

        results: list[DecisionStepResult] = []
        current_input = first_input
        for index in range(steps):
            result = self.run_decision_step(current_input)
            results.append(result)
            if index == steps - 1:
                break
            current_input = (
                advance_input[index]
                if advance_input is not None
                else default_next_input(current_input, result)
            )
        return tuple(results)

    # -- внутренние стадии ----------------------------------------------------

    def _build_production_envelope(
        self,
        step_input: DecisionStepInput,
        *,
        round_: DecisionRound,
        peer_proposals: tuple[ProposalReference, ...],
    ) -> ProductionEnvelope:
        return ProductionEnvelope(
            schema_version=self._config.schema_version,
            round=round_,
            variant_id=self._config.variant_id,
            config_digest=self._config.digest(),
            state_card=step_input.state,
            horizon_context=step_input.horizon_context,
            memory_slice=step_input.memory_slice,
            allowed_actions=step_input.production_allowed_actions,
            pressure_diagnostic=step_input.pressure_diagnostic,
            sweep_diagnostic=step_input.sweep_diagnostic,
            geology_card=step_input.geology_card,
            response_map=step_input.response_map,
            compensation_state=step_input.compensation_state,
            economic_context=step_input.economic_context,
            peer_proposals=peer_proposals,
        )

    def _build_injection_envelope(
        self,
        step_input: DecisionStepInput,
        *,
        round_: DecisionRound,
        peer_proposals: tuple[ProposalReference, ...],
    ) -> InjectionEnvelope:
        return InjectionEnvelope(
            schema_version=self._config.schema_version,
            round=round_,
            variant_id=self._config.variant_id,
            config_digest=self._config.digest(),
            state_card=step_input.state,
            horizon_context=step_input.horizon_context,
            memory_slice=step_input.memory_slice,
            allowed_actions=step_input.injection_allowed_actions,
            pressure_diagnostic=step_input.pressure_diagnostic,
            sweep_diagnostic=step_input.sweep_diagnostic,
            geology_card=step_input.geology_card,
            response_map=step_input.response_map,
            compensation_state=step_input.compensation_state,
            peer_proposals=peer_proposals,
        )

    def _run_specialist_rounds(
        self, step_input: DecisionStepInput
    ) -> tuple[ProductionAgentOutput, InjectionAgentOutput, bool]:
        # Round 1: добывающий и нагнетательный специалисты не видят на этой
        # стадии ни envelope, ни вывод друг друга -- каждый вызов независим.
        production_envelope_r1 = self._build_production_envelope(
            step_input, round_=DecisionRound.ROUND_1, peer_proposals=()
        )
        injection_envelope_r1 = self._build_injection_envelope(
            step_input, round_=DecisionRound.ROUND_1, peer_proposals=()
        )
        production_output = self._production_agent.propose(production_envelope_r1)
        injection_output = self._injection_agent.propose(injection_envelope_r1)

        if not self._conflict_predicate(production_output, injection_output):
            return production_output, injection_output, False

        # Опциональный Round 2: максимум один обмен, никогда не цикл переговоров.
        production_envelope_r2 = self._build_production_envelope(
            step_input,
            round_=DecisionRound.ROUND_2,
            peer_proposals=(
                ProposalReference(
                    role=AgentRole.INJECTION,
                    proposal_digest=injection_output.primary_proposal.digest(),
                ),
            ),
        )
        injection_envelope_r2 = self._build_injection_envelope(
            step_input,
            round_=DecisionRound.ROUND_2,
            peer_proposals=(
                ProposalReference(
                    role=AgentRole.PRODUCTION,
                    proposal_digest=production_output.primary_proposal.digest(),
                ),
            ),
        )
        production_output = self._production_agent.propose(production_envelope_r2)
        injection_output = self._injection_agent.propose(injection_envelope_r2)
        return production_output, injection_output, True

    def _run_arbiter_requested_round2(
        self,
        step_input: DecisionStepInput,
        request: Round2Request,
        production_output: ProductionAgentOutput,
        injection_output: InjectionAgentOutput,
    ) -> tuple[ProductionAgentOutput, InjectionAgentOutput]:
        """Один Round 2 по запросу Arbiter: переспросить только запрошенные роли.

        Ссылки на peer-предложения фиксируются ДО повторных вызовов -- обе
        роли видят предложения друг друга первого раунда, а не результат
        последовательного пересчёта (симметрия та же, что и в
        predicate-триггерном Round 2 выше).
        """

        production_peer = ProposalReference(
            role=AgentRole.PRODUCTION,
            proposal_digest=production_output.primary_proposal.digest(),
        )
        injection_peer = ProposalReference(
            role=AgentRole.INJECTION,
            proposal_digest=injection_output.primary_proposal.digest(),
        )
        requested = {AgentRole(role) for role in request.requested_roles}

        if AgentRole.PRODUCTION in requested:
            envelope = self._build_production_envelope(
                step_input, round_=DecisionRound.ROUND_2, peer_proposals=(injection_peer,)
            )
            production_output = self._production_agent.propose(envelope)
        if AgentRole.INJECTION in requested:
            envelope = self._build_injection_envelope(
                step_input, round_=DecisionRound.ROUND_2, peer_proposals=(production_peer,)
            )
            injection_output = self._injection_agent.propose(envelope)
        return production_output, injection_output

    def _require_hold_is_exact_carry_forward(
        self, step_input: DecisionStepInput, candidates: tuple[MagnitudeCandidate, ...]
    ) -> None:
        """T6 на границе перед симуляцией: HOLD == предыдущие режимы, побайтово.

        Правила переноса не изобретаются -- вызывается готовый инвариант
        `aios.mas.require_hold_carry_forward` для каждого HOLD-кандидата.
        """

        previous = step_input.previous_committed
        if previous is None:
            return
        for candidate in candidates:
            if CandidateKind(candidate.candidate_kind) is not CandidateKind.HOLD:
                continue
            require_hold_carry_forward(
                previous_producers=previous.producers,
                previous_injectors=previous.injectors,
                held_producers=candidate.producers,
                held_injectors=candidate.injectors,
            )

    def _build_arbiter_envelope(
        self,
        step_input: DecisionStepInput,
        production_output: ProductionAgentOutput,
        injection_output: InjectionAgentOutput,
    ) -> ArbiterEnvelope:
        return ArbiterEnvelope(
            schema_version=self._config.schema_version,
            variant_id=self._config.variant_id,
            config_digest=self._config.digest(),
            state_card_digest=step_input.state.digest(),
            production_output=production_output,
            injection_output=injection_output,
            horizon_context=step_input.horizon_context,
            memory_slice=step_input.memory_slice,
            simulation_budget=SimulationBudget(max_candidates=self._config.simulation_budget),
        )

    def _simulate_guard_and_commit(
        self,
        *,
        candidates: tuple[MagnitudeCandidate, ...],
        production_envelope_digest: str,
        injection_envelope_digest: str,
        arbiter_decision: ArbiterDecision,
        round2_triggered: bool,
        step_input: DecisionStepInput,
    ) -> DecisionStepResult:
        # Каждый кандидат исполняется ровно один раз, и его прогон
        # принимается только вместе с провенансом, который называет именно
        # этого кандидата.
        evaluations: list[CandidateEvaluation] = []
        executed: dict[str, tuple[MagnitudeCandidate, RuntimeExecution]] = {}
        guardian_verdicts: dict[str, GuardianVerdict] = {}

        for candidate in candidates:
            candidate_digest = candidate.digest()
            if candidate_digest in executed:
                raise OrchestrationError(
                    f"decision_step: duplicate candidate digest {candidate_digest!r} "
                    "in the resolved candidate set"
                )
            execution = self._candidate_executor.execute(candidate, run_label=candidate_digest)
            # Корень происхождения выдаётся только после полной проверки: ниже
            # по конвейеру нет ни одного ярлыка, который вызывающий мог бы
            # написать сам.
            verified = _require_execution_identity(candidate, execution)
            executed[candidate_digest] = (candidate, execution)

            simulation = SimulationOutcome.verified(verified, execution.result)

            guardian_outcome: GuardianOutcome | None = None
            chdd_outcome: ChddOutcome | None = None

            # Только полный успех даёт право на легальность и экономику:
            # оборванная траектория даёт число, которое выглядит сравнимым с
            # полными прогонами, но им не является.
            if execution.result.supports_objective_evaluation:
                realized = self._realized_state_extractor.extract(candidate, execution)
                # Guardian судит именно это состояние, и его вердикт с ним же
                # и связывается: настоящий вердикт о другом кандидате назовёт
                # другую карту и сюда не пройдёт.
                realized_root = VerifiedRealizedState.of(verified, realized)
                report = self._guardian.evaluate(realized)
                guardian_outcome = GuardianOutcome.verified(realized_root, report)
                guardian_verdicts[candidate_digest] = GuardianVerdict.from_report(report)
                if report.summary.failed == 0:
                    evaluation = self._chdd.evaluate(candidate, execution)
                    chdd_outcome = ChddOutcome.verified(
                        VerifiedChddInput(execution=verified, input_digest=evaluation.input_digest),
                        evaluation.result,
                    )
            else:
                guardian_verdicts[candidate_digest] = GuardianVerdict(
                    passed=False,
                    violations=(f"simulation:{execution.result.status}",),
                )

            evaluations.append(
                CandidateEvaluation(
                    candidate=candidate,
                    simulation=simulation,
                    guardian=guardian_outcome,
                    chdd=chdd_outcome,
                )
            )

        # Экономический отбор -- авторитетная зона, а не встроенный max():
        # она же проверяет совпадение идентичностей на каждом звене цепочки
        # (кандидат -> прогон -> Guardian -> CHDD) и объясняет каждый отказ.
        try:
            selection = EconomicSelector().select(evaluations)
        except EconomicSelectionError as error:
            raise OrchestrationError(f"decision_step: {error}") from error

        chosen_digest = selection.selected.candidate_identity
        rejected_reasons = {
            rejection.candidate_identity: str(rejection.reason) for rejection in selection.rejected
        }
        eligible_npv = {
            evaluation.candidate_identity: evaluation.chdd.result.npv_mln_rub
            for evaluation in selection.eligible
            if evaluation.chdd is not None
        }

        records = tuple(
            CandidateRecord(
                candidate_digest=digest_,
                candidate_kind=str(candidate.candidate_kind),
                simulation_status=str(execution.result.status),
                guardian=guardian_verdicts.get(digest_),
                npv_mln_rub=eligible_npv.get(digest_),
                ineligibility_reason=rejected_reasons.get(digest_),
            )
            for digest_, (candidate, execution) in executed.items()
        )

        committed_candidate, committed_execution = executed[chosen_digest]
        next_state = self._state_advancer.advance(
            step_input.state, committed_candidate, committed_execution
        )

        return DecisionStepResult(
            round2_triggered=round2_triggered,
            production_envelope_digest=production_envelope_digest,
            injection_envelope_digest=injection_envelope_digest,
            arbiter_decision_digest=arbiter_decision.digest(),
            candidates=records,
            committed_candidate_digest=chosen_digest,
            committed_candidate=committed_candidate,
            selection=selection,
            next_state=next_state,
        )


def default_next_input(
    previous_input: DecisionStepInput, previous_result: DecisionStepResult
) -> DecisionStepInput:
    """Переносит допустимые действия/память вперёд; продвигает состояние и горизонт на один шаг.

    Закоммиченный кандидат предыдущего шага протягивается в
    `previous_committed`, чтобы проверка T6 (HOLD как точный перенос) работала
    на каждом шаге цикла, начиная со второго, без ручной сборки входа.
    """

    horizon = previous_input.horizon_context
    next_horizon = HorizonContext(
        decision_date=previous_result.next_state.metadata.date,
        horizon_end=horizon.horizon_end,
        remaining_steps=max(horizon.remaining_steps - 1, 0),
    )
    return DecisionStepInput(
        state=previous_result.next_state,
        horizon_context=next_horizon,
        memory_slice=previous_input.memory_slice,
        production_allowed_actions=previous_input.production_allowed_actions,
        injection_allowed_actions=previous_input.injection_allowed_actions,
        previous_committed=previous_result.committed_candidate,
    )


__all__ = [
    "CandidateRecord",
    "ClosedLoopOrchestrator",
    "DecisionStepInput",
    "DecisionStepResult",
    "OrchestrationError",
    "OrchestratorConfig",
    "default_next_input",
]
