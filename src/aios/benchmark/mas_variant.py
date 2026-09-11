"""Вариант MAS: настоящий замкнутый контур на той же физике и экономике.

MAS OFF и MAS ON отличаются ровно одним -- состоянием когнитивной ветки
специалистов. Горизонт, кейс, стартовое состояние, бюджет симуляций, профиль
магнитуд, Guardian, ЧДД и топология решения общие: иначе сравнение измеряло бы
подмену условий, а не работу LLM.

Здесь нет ни одного рукописного кандидата: управления рождаются там, где им
положено -- на границе MagnitudeResolver, после структурного решения Arbiter.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from aios.benchmark.adapters import BenchmarkContext
from aios.benchmark.contracts import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkError,
    BenchmarkOutcome,
    BenchmarkRunManifest,
    CognitiveConfig,
    EvaluationLineage,
    RunKind,
    VariantFamily,
)
from aios.benchmark.epochs import decision_epochs
from aios.evaluation.canonical import JsonValue, digest
from aios.execution.history import (
    CommittedHistory,
    CommittedInterval,
    require_committed_history_lineage,
)
from aios.mas.arbitration import DeterministicArbiter
from aios.mas.common import ProposalSource
from aios.mas.envelopes import ArbiterEnvelope
from aios.mas.evidence import EvidenceArtifact, HorizonContext, MemorySlice
from aios.mas.magnitude_resolution import (
    DeterministicMagnitudeResolver,
    MagnitudeContext,
    supported_injection_actions,
    supported_production_actions,
)
from aios.mas.orchestration import (
    ClosedLoopOrchestrator,
    DecisionStepInput,
    DecisionTrace,
    OrchestratorConfig,
    build_decision_context,
    build_real_closed_loop,
    default_next_input,
)
from aios.mas.orchestration.economics import build_economic_context
from aios.mas.specialists.injection import InjectionController
from aios.mas.specialists.injection.scope import groundable_injection_actions
from aios.mas.specialists.production import ProductionController
from aios.ui.events import emit_event, events_enabled

#: Действия, доступные специалистам в бенчмарке. Одинаковы для OFF и ON.
#:
#: Список не задан вручную: он выводится из того, что детерминированный
#: резолвер при активном профиле действительно умеет превратить в числа.
#: Предлагать модели действие без якоря -- значит гарантированно потерять её
#: решение между Arbiter и численным разрешением, что и произошло со всеми
#: пятью живыми Production-предложениями в MAS-PERF-001.
PRODUCTION_ACTIONS = supported_production_actions()
INJECTION_ACTIONS = supported_injection_actions()


def injection_actions_for_state(state_card) -> tuple:
    """Исполнимые Injection-действия, ЗАЗЕМЛЯЕМЫЕ против конкретного снимка.

    `supported_injection_actions()` отбирает по одному признаку -- умеет ли
    резолвер превратить действие в числа. Этого мало: реальный StateCard
    приходит с пустым `geology.block_membership`, и region-действие, которое
    резолвер собрать умеет, ScopeAuditor всё равно отвергает. Второй фильтр
    берётся у самого аудитора, а не собирается здесь заново.
    """
    return groundable_injection_actions(state_card, INJECTION_ACTIONS)


@dataclass
class _EnvelopeCapturingArbiter:
    """Арбитр, запоминающий свой последний envelope.

    Резолверу магнитуд нужен именно тот ArbiterEnvelope, по которому принято
    решение, а протокол `resolve(decision)` его не передаёт. Перехват на этом
    шве -- интеграционная деталь, а не изменение логики арбитража: решение
    принимает настоящий DeterministicArbiter.
    """

    inner: DeterministicArbiter = field(default_factory=DeterministicArbiter)
    last_envelope: ArbiterEnvelope | None = None

    def decide(self, envelope: ArbiterEnvelope):
        self.last_envelope = envelope
        emit_event("arbiter", "active")
        decision = self.inner.decide(envelope)
        emit_event("arbiter", "done", text=f"структурных бандлов: {len(decision.bundles)}")
        kinds: dict[str, int] = {}
        for bundle in decision.bundles:
            kinds[_value(bundle.kind)] = kinds.get(_value(bundle.kind), 0) + 1
        emit_event(
            "arbiter",
            text=(
                "почему: пол HOLD + DETERMINISTIC_BASELINE всегда в наборе, остальное это "
                "гипотезы специалистов в пределах бюджета · "
                + ", ".join(f"{kind} ×{count}" for kind, count in kinds.items())
            ),
            why=True,
        )
        return decision


@dataclass
class _ContextualMagnitudeResolver:
    """Резолвер, собирающий контекст магнитуд для текущего шага."""

    arbiter: _EnvelopeCapturingArbiter
    effective_date: object
    baseline_producers: tuple
    baseline_injectors: tuple
    previous_producers: tuple = ()
    previous_injectors: tuple = ()

    funnel: list = field(default_factory=list)

    def resolve(self, decision):
        envelope = self.arbiter.last_envelope
        if envelope is None:
            raise BenchmarkError("magnitude resolver ran before the arbiter produced an envelope")
        context = MagnitudeContext(
            effective_date=self.effective_date,
            arbiter_envelope=envelope,
            previous_producers=self.previous_producers or self.baseline_producers,
            previous_injectors=self.previous_injectors or self.baseline_injectors,
            baseline_producers=self.baseline_producers,
            baseline_injectors=self.baseline_injectors,
        )
        emit_event("resolver", "active")
        engine = DeterministicMagnitudeResolver(context=context)
        candidates = engine.resolve(decision)
        self.funnel.append({"effective_date": str(self.effective_date), **engine.last_funnel})
        funnel = engine.last_funnel
        emit_event(
            "resolver",
            "done",
            text=(
                f"кандидатов на физику: {len(candidates)} "
                f"(сгенерировано {funnel.get('virtual_candidates_generated', '—')})"
            ),
        )
        return candidates


def _carry_forward(resolver: _ContextualMagnitudeResolver, committed) -> None:
    """Перенести ЗАКОММИЧЕННЫЕ управления в контекст следующего шага.

    HOLD на шаге N обязан быть точным переносом того, что реально стоит после
    шага N-1 (инвариант T6), а не возвратом к исходному baseline. Резолвер
    держит для этого отдельное поле `previous_*`; пока его никто не обновлял,
    `previous_* or baseline_*` на каждом шаге отдавал исходный baseline. Пока
    MAS коммитил baseline-эквивалент, это совпадало случайно -- и разошлось на
    первом же реальном AGENT_BUNDLE.

    Новых величин здесь не появляется: переносятся ровно те объекты
    управления, которые уже создал MagnitudeResolver.
    """
    resolver.previous_producers = tuple(committed.producers)
    resolver.previous_injectors = tuple(committed.injectors)


@dataclass(frozen=True, slots=True)
class GuardianTelemetry:
    """Что Guardian реально сделал за прогон варианта.

    Контрактные поля (`committed_*`) описывают ПРИНЯТУЮ политику и потому
    сопоставимы с опорными вариантами, где кандидат ровно один. Поиск-широкие
    поля описывают весь перебор: Guardian выполняется по каждому кандидату, и
    отказ отклонённого кандидата -- это выполненная работа, а не её отсутствие.
    """

    checks_total: int = 0
    failed_total: int = 0
    candidates_evaluated: int = 0
    candidates_rejected: int = 0
    committed_checks: int = 0
    committed_failed: int = 0


def _verdict_summary(verdict) -> dict | None:
    """Сводка ConstraintReport из вердикта, если Guardian по нему отработал.

    `GuardianVerdict.report` -- это уже сериализованный отчёт (dict); проверять
    на dict сам вердикт бессмысленно, и именно эта ошибка обнуляла счётчики.
    """
    if verdict is None:
        return None
    report = getattr(verdict, "report", None)
    if not isinstance(report, Mapping):
        return None
    summary = report.get("summary")
    return summary if isinstance(summary, Mapping) else None


def guardian_telemetry(results) -> GuardianTelemetry:
    """Собрать телеметрию Guardian по шагам решения."""
    checks = failed = evaluated = rejected = 0
    committed_checks = committed_failed = 0
    for result in results:
        for record in result.candidates:
            summary = _verdict_summary(record.guardian)
            if summary is None:
                continue
            total = int(summary.get("total_checks", 0))
            step_failed = int(summary.get("failed", 0))
            checks += total
            failed += step_failed
            evaluated += 1
            if step_failed:
                rejected += 1
            if record.candidate_digest == result.committed_candidate_digest:
                committed_checks += total
                committed_failed += step_failed
    return GuardianTelemetry(
        checks_total=checks,
        failed_total=failed,
        candidates_evaluated=evaluated,
        candidates_rejected=rejected,
        committed_checks=committed_checks,
        committed_failed=committed_failed,
    )


def fallback_count(results) -> int:
    """Число specialist-РЕШЕНИЙ (не эпох), чей провенанс несёт fallback_used=True.

    Production и Injection одной эпохи считаются НЕЗАВИСИМО: эпоха, где оба
    упали на fallback, даёт +2, а не +1. `DecisionStepResult.*_fallback_used`
    -- это уже провенанс ИМЕННО того раунда, что дошёл до Arbiter (Round 2
    переопределяет Round 1, если он состоялся), поэтому здесь не нужно (и
    нельзя) заново решать, какой раунд считать: это уже решено там, откуда
    приходит `results`, и пересчитывать по source/тексту ошибки значило бы
    завести второй источник истины рядом с `ProposalProvenance.fallback_used`.
    """
    return sum(
        int(result.production_fallback_used) + int(result.injection_fallback_used)
        for result in results
    )


def epoch_audits(epochs, results) -> list[JsonValue]:
    """Одна строка на эпоху: дата решения рядом с датой прочитанного состояния.

    Оркестратор знает состояние, бенчмарк знает сетку эпох; сводит их только
    здесь. Обе даты лежат в одной строке намеренно -- причинный гейт «состояние
    не позже своей эпохи» обязан читаться из артефакта глазами, а не
    восстанавливаться из поведения прогона, как это было в PERF-004.

    Ничего не вычисляется заново: `DecisionEpochAudit` собран на самом шаге.
    """
    records: list[JsonValue] = []
    for index, (epoch, result) in enumerate(zip(epochs, results, strict=False)):
        audit = getattr(result, "audit", None)
        payload: dict[str, JsonValue] = {
            "epoch_index": index,
            "decision_date": epoch.isoformat(),
        }
        if audit is None:
            payload["state_card_date"] = None
            records.append(payload)
            continue
        payload.update(audit.to_json())
        records.append(payload)
    return records


def _committed_action_controls(runtime, candidate):
    """Полные управления, встающие в эпоху кандидата.

    У AGENT_BUNDLE в самом кандидате лежат только изменённые скважины; в силу
    вступает результат наложения на действующие режимы, и его считает
    компилятор. Пересчитывать это здесь второй раз значило бы завести второй
    источник той же правды -- расписание переспрашивается у компилятора.
    """
    compiled = runtime.compile(candidate)
    effective_date = candidate.canonical().effective_date
    for step in compiled.schedule.canonical().steps:
        if step.date == effective_date:
            return step.producers, step.injectors
    raise BenchmarkError(
        f"committed controls: compiled schedule has no step at {effective_date.isoformat()}"
    )


# ---------------------------------------------------------------------------
# Граф страницы запуска
#
# Страница запуска показывает, какой узел контура работает сейчас и что он
# выдал. Для этого вызовы специалистов и границ размечаются строками
# `::aios {...}` (см. `aios.ui.events`). Разметка печатается, только если
# процесс запустила страница; без неё `_announced*` возвращают те же самые
# объекты, и контур собирается ровно так же, как раньше. Решения обёртки не
# трогают: каждый вызов уходит в настоящий объект, а обёртка только сообщает
# о его начале, конце и результате.
#
# Методы объявлены явно, а не через `__getattr__`: протоколы границ помечены
# `@runtime_checkable`, а в Python 3.12 такая проверка ищет методы статически.
# ---------------------------------------------------------------------------


def _value(item: object) -> str:
    return str(getattr(item, "value", item))


def _proposal_text(output: object) -> str | None:
    """Коротко: что предложил специалист и откуда это решение."""
    proposal = getattr(output, "primary_proposal", None)
    if proposal is None:
        return None
    parts = []
    for intent in getattr(proposal, "intents", ()):
        if _value(intent.action) == "HOLD":
            parts.append("HOLD")
            continue
        wells = [str(well) for well in (getattr(intent, "target_wells", ()) or ())]
        scope = ""
        if wells:
            scope = " · скв. " + ", ".join(wells[:4]) + (" …" if len(wells) > 4 else "")
        parts.append(
            f"{_value(intent.action)} {_value(intent.direction)} {_value(intent.intensity)}{scope}"
        )
    provenance = getattr(proposal, "provenance", None)
    if getattr(provenance, "fallback_used", False):
        origin = "детерминированный пол"
    else:
        origin = _value(getattr(proposal, "source", ""))
    alternatives = len(getattr(output, "alternatives", ()) or ())
    extra = f" · альтернатив: {alternatives}" if alternatives else ""
    return f"{'; '.join(parts) or 'HOLD'} ({origin}){extra}"


def _violations_text(report: object) -> str:
    violations = getattr(report, "violations", None)
    if violations is None:
        return "проверено"
    return "без нарушений" if not violations else f"нарушений: {len(violations)}"


def _proposal_reasons(output: object) -> list[str]:
    """Почему специалист предложил именно это -- его собственными словами.

    Ничего не выводится заново: `rationale_short` и `evidence_ids` обязательны
    для не-HOLD намерения и уже прошли проверку grounding, отклонённые
    альтернативы записал сам специалист.
    """
    proposal = getattr(output, "primary_proposal", None)
    if proposal is None:
        return []
    reasons: list[str] = []
    provenance = getattr(proposal, "provenance", None)
    if getattr(provenance, "fallback_used", False):
        cause = getattr(provenance, "fallback_reason", None) or "ответ модели не прошёл проверку"
        reasons.append(f"почему: сработал детерминированный пол, {cause}")
    for intent in getattr(proposal, "intents", ()):
        rationale = (getattr(intent, "rationale_short", "") or "").strip()
        if rationale:
            reasons.append(f"почему ({_value(intent.action)}): {rationale}")
        evidence = [str(item) for item in (getattr(intent, "evidence_ids", ()) or ())]
        if evidence:
            reasons.append("опирается на: " + ", ".join(evidence))
    audit = getattr(proposal, "audit", None)
    declined = [str(item) for item in (getattr(audit, "declined_alternatives", ()) or ())]
    if declined:
        tail = " …" if len(declined) > 4 else ""
        reasons.append("рассмотрено и отклонено: " + "; ".join(declined[:4]) + tail)
    return reasons


def _identity(candidate: object) -> object:
    identity = getattr(candidate, "digest", None)
    return identity() if callable(identity) else id(candidate)


class _Forwarding:
    """Всё, что не размечается, берётся у настоящего объекта."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _AnnouncedAgent(_Forwarding):
    def __init__(self, inner: object, node: str) -> None:
        super().__init__(inner)
        self._node = node

    def propose(self, envelope):
        emit_event(self._node, "active")
        try:
            output = self._inner.propose(envelope)
        except Exception:
            emit_event(self._node, "fail")
            raise
        emit_event(self._node, "done", text=_proposal_text(output))
        for reason in _proposal_reasons(output):
            emit_event(self._node, text=reason, why=True)
        return output


class _AnnouncedExecutor(_Forwarding):
    def execute(self, candidate, *, run_label):
        emit_event("opm", "active", text=f"кандидат {_value(candidate.candidate_kind)}")
        try:
            execution = self._inner.execute(candidate, run_label=run_label)
        except Exception:
            emit_event("opm", "fail")
            raise
        emit_event("opm", "done", text=f"прогон: {_value(execution.result.status)}")
        return execution


class _AnnouncedGuardian(_Forwarding):
    def evaluate(self, state_card):
        emit_event("guardian", "active")
        report = self._inner.evaluate(state_card)
        emit_event("guardian", "done", text=_violations_text(report))
        return report


class _AnnouncedTrajectoryGuardian(_Forwarding):
    def evaluate(self, candidate, execution, *, final_state):
        emit_event("guardian", "active")
        report = self._inner.evaluate(candidate, execution, final_state=final_state)
        emit_event("guardian", "done", text="траектория: " + _violations_text(report))
        return report


class _AnnouncedChdd(_Forwarding):
    def __init__(self, inner: object, ledger: dict) -> None:
        super().__init__(inner)
        self._ledger = ledger

    def evaluate(self, candidate, execution):
        emit_event("chdd", "active")
        evaluation = self._inner.evaluate(candidate, execution)
        npv = evaluation.result.npv_mln_rub
        self._ledger[_identity(candidate)] = npv
        emit_event("chdd", "done", text=f"ЧДД {npv:.2f} млн ₽")
        return evaluation


class _AnnouncedAdvancer(_Forwarding):
    def __init__(self, inner: object, ledger: dict) -> None:
        super().__init__(inner)
        self._ledger = ledger

    def advance(self, state, candidate, execution):
        emit_event("selector", "active")
        next_state = self._inner.advance(state, candidate, execution)
        emit_event("selector", "done", text=f"закоммичен: {_value(candidate.candidate_kind)}")
        chosen = self._ledger.get(_identity(candidate))
        if chosen is not None:
            emit_event(
                "selector",
                text=(
                    f"почему: максимальный ЧДД {chosen:.2f} млн ₽ среди {len(self._ledger)} "
                    "посчитанных кандидатов эпохи; к отбору допускаются только прошедшие Guardian"
                ),
                why=True,
            )
        self._ledger.clear()
        return next_state


def _announced(agent: object, node: str) -> object:
    """Специалист, видимый на графе, -- или он же, если страницы нет."""
    return _AnnouncedAgent(agent, node) if events_enabled() else agent


def _announced_boundaries(boundaries: Mapping[str, object]) -> Mapping[str, object]:
    """Границы, видимые на графе, -- или те же самые, если страницы нет."""
    if not events_enabled():
        return boundaries
    # Общий журнал ЧДД эпохи: по нему отбор объясняет, почему победил именно
    # закоммиченный кандидат. Числа берутся из того, что уже посчитал CHDD.
    ledger: dict = {}
    wrapped = dict(boundaries)
    wrapped["candidate_executor"] = _AnnouncedExecutor(boundaries["candidate_executor"])
    wrapped["guardian"] = _AnnouncedGuardian(boundaries["guardian"])
    if boundaries.get("trajectory_guardian") is not None:
        wrapped["trajectory_guardian"] = _AnnouncedTrajectoryGuardian(
            boundaries["trajectory_guardian"]
        )
    wrapped["chdd"] = _AnnouncedChdd(boundaries["chdd"], ledger)
    wrapped["state_advancer"] = _AnnouncedAdvancer(boundaries["state_advancer"], ledger)
    return wrapped


def _announce_epoch(label: str, index: int, epoch) -> None:
    """Начало эпохи: состояние снято, диагностика собрана до специалистов."""
    prefix = f"{label} · " if label else ""
    emit_event(epoch=f"{prefix}T{index} · {epoch.isoformat()}")
    emit_event("state", "done", text=f"состояние на {epoch.isoformat()}")
    emit_event("diag", "done", text="давление, охват, связность, экономика")


def _run_decision_steps(orchestrator, resolver, runtime, step_input, epochs, *, label=""):
    """Receding-horizon цикл эпох: когнитивный и физический слой идут вместе.

    На каждой эпохе кандидат компилируется как ОДНО полное расписание окна --
    неизменяемая закоммиченная история плюс собственный хвост, -- поэтому
    физическое состояние в эпохе порождено ровно теми управлениями, которые
    действительно были закоммичены. Историю дописывает только ВЫБРАННЫЙ
    кандидат, и её происхождение проверяется по его имени.
    """
    results = []
    current = step_input
    history = CommittedHistory()
    for index, epoch in enumerate(epochs):
        _announce_epoch(label, index, epoch)
        resolver.effective_date = epoch
        runtime.advance_to_epoch(history, require_history_before=epoch if index else None)
        result = orchestrator.run_decision_step(current)
        results.append(result)
        if index == len(epochs) - 1:
            break

        selected = result.committed_candidate
        producers, injectors = _committed_action_controls(runtime, selected)
        history = history.commit(
            CommittedInterval(
                effective_date=epoch,
                producers=producers,
                injectors=injectors,
                candidate_digest=result.committed_candidate_digest,
            )
        )
        # Привязка происхождения: история обязана быть дописана ИМЕННО
        # выбранным кандидатом, а не просто отличаться содержимым.
        require_committed_history_lineage(history, result.committed_candidate_digest)
        _carry_forward(resolver, selected)
        current = default_next_input(current, result)
    return tuple(results)


def _baseline_controls(state):
    """Опорные управления так, как их выводит компилятор расписания.

    Именно этот срез состояния компилятор сравнивает с HOLD-кандидатом при
    проверке T6, поэтому резолвер обязан отталкиваться от него же. Свой,
    похожий, вывод из расписания дал бы формально другой набор -- и точный
    перенос управлений перестал бы быть точным.
    """
    return (
        tuple(producer.as_producer() for producer in state.producers),
        tuple(injector.as_injector() for injector in state.injectors),
    )


def _diagnostics(state_card) -> dict[str, EvidenceArtifact]:
    """Диагностика T0 -- тем же строителем, что и у следующих эпох.

    Раньше эта функция была единственным местом, где evidence эпохи вообще
    собиралось, и `default_next_input` его не воспроизводил: T1/T2 получали
    пустые слоты. Строитель теперь один на все эпохи
    (`aios.mas.orchestration.build_decision_context`), различается только
    реализованный StateCard -- второй, параллельной, реализации диагностики
    здесь заводить нельзя.
    """
    return build_decision_context(state_card)


def run_mas_variant(
    context: BenchmarkContext,
    *,
    variant_id: str,
    llm_enabled: bool,
    initial_state_card,
    provider_id: str | None = None,
    model_id: str | None = None,
    run_id: str | None = None,
    production_provider: object | None = None,
    injection_provider: object | None = None,
    simulation_budget: int = 14,
    decision_steps: int = 1,
    notes: tuple[str, ...] = (),
) -> BenchmarkOutcome:
    """Прогнать настоящий замкнутый контур и собрать запись бенчмарка."""
    started = time.monotonic()
    state, baseline, _history, _start = context.baseline_inputs()
    epochs = decision_epochs(baseline.canonical().dates, decision_steps)
    effective_date = epochs[0]

    composition = build_real_closed_loop(
        case=context.case,
        history_text=_history,
        history_start_date=_start,
        initial_state=state,
        baseline=baseline,
        workspace_root=Path(context.workspace_root) / variant_id,
        decision_epochs=epochs,
        geology=context.geology(),
    )

    arbiter = _EnvelopeCapturingArbiter()
    baseline_producers, baseline_injectors = _baseline_controls(state)
    resolver = _ContextualMagnitudeResolver(
        arbiter=arbiter,
        effective_date=effective_date,
        baseline_producers=baseline_producers,
        baseline_injectors=baseline_injectors,
    )

    config = OrchestratorConfig(
        variant_id=variant_id,
        llm_enabled=llm_enabled,
        simulation_budget=simulation_budget,
        resolver_version="aios.mas.magnitude_resolution/1.0.0",
        guardian_config_digest=digest({"guardian": "aios.agents.constraint_guardian/1.0.0"}),
        chdd_config_digest=digest({"chdd": context.case.economics.provenance}),
    )
    trace = DecisionTrace()
    orchestrator = ClosedLoopOrchestrator(
        config=config,
        # Флаг доходит до специалистов через фабрику: топология одна и та же,
        # различается только когнитивная ветка внутри специалиста.
        production_agent_factory=lambda enabled: _announced(
            ProductionController(llm_enabled=enabled, provider=production_provider),
            "production",
        ),
        # InjectionController's llm_source is a static constructor knob (the
        # LlmProvider protocol returns bare text, so it cannot carry its own
        # provenance); the live adapter declares its provenance via `.source`
        # (see InjectionLiveProvider), so read it here rather than defaulting
        # a real live completion to the LLM_LOCAL fallback value.
        injection_agent_factory=lambda enabled: _announced(
            InjectionController(
                llm_enabled=enabled,
                llm_provider=injection_provider,
                llm_source=getattr(injection_provider, "source", ProposalSource.LLM_LOCAL),
            ),
            "injection",
        ),
        arbiter=arbiter,
        magnitude_resolver=resolver,
        decision_trace=trace,
        **_announced_boundaries(composition.boundaries()),
    )

    diagnostics = _diagnostics(initial_state_card)
    horizon = HorizonContext(
        decision_date=effective_date,
        horizon_end=context.case.management_end_date,
        remaining_steps=decision_steps,
    )
    step_input = DecisionStepInput(
        state=initial_state_card,
        horizon_context=horizon,
        memory_slice=MemorySlice(),
        production_allowed_actions=PRODUCTION_ACTIONS,
        injection_allowed_actions=injection_actions_for_state(initial_state_card),
        # Статический экономический контекст прогона. Пустой слот означал, что
        # специалист видел обводнённость и не видел ни цены, ни затрат, то есть
        # размен нефть/жидкость нельзя было даже поставить как вопрос.
        economic_context=build_economic_context(context.case),
        **diagnostics,
    )

    results = _run_decision_steps(
        orchestrator, resolver, composition.runtime, step_input, epochs, label=variant_id
    )
    final = results[-1]
    selected = final.selection.selected

    simulation_count = sum(len(result.candidates) for result in results)
    lineage = tuple(
        EvaluationLineage(
            schedule_digest=result.committed_candidate.digest(),
            realized_state_digest=result.next_state.digest(),
            # Вход ЧДД ЭТОГО шага, а не последнего: общий на всех дайджест
            # означал бы, что lineage шагов 1..N-1 указывает на чужой
            # экономический вход.
            chdd_input_digest=(
                result.selection.selected.chdd.input_digest
                if result.selection.selected.chdd is not None
                else None
            ),
        )
        for result in results
    )
    committed = final.committed_candidate.canonical()
    guardian = guardian_telemetry(results)

    manifest = BenchmarkRunManifest(
        benchmark_schema_version=BENCHMARK_SCHEMA_VERSION,
        variant_id=variant_id,
        variant_family=VariantFamily.MAS_ON if llm_enabled else VariantFamily.MAS_OFF,
        run_kind=RunKind.CLOSED_LOOP_POLICY,
        code_sha=context.code_sha,
        frame=context.frame(
            initial_state_digest=initial_state_card.digest(),
            chdd_month_count=None,
        ),
        cognitive=CognitiveConfig(
            llm_enabled=llm_enabled,
            provider_id=provider_id,
            model_id=model_id,
            run_id=run_id,
        ),
        lineage=lineage,
        source_reference="aios.mas.orchestration.build_real_closed_loop",
        notes=notes,
    )
    if selected.chdd is None:
        raise BenchmarkError("mas variant: committed candidate carries no CHDD outcome")

    return BenchmarkOutcome(
        manifest=manifest,
        npv_mln_rub=selected.chdd.result.npv_mln_rub,
        guardian_pass=guardian.committed_failed == 0,
        guardian_check_count=guardian.committed_checks,
        guardian_failed_count=guardian.committed_failed,
        guardian_checks_total=guardian.checks_total,
        guardian_failed_total=guardian.failed_total,
        guardian_candidates_evaluated=guardian.candidates_evaluated,
        guardian_candidates_rejected=guardian.candidates_rejected,
        simulation_count=simulation_count,
        control_change_count=len(committed.producers) + len(committed.injectors),
        decision_steps=len(results),
        round2_count=sum(1 for result in results if result.round2_triggered),
        fallback_count=fallback_count(results),
        chdd_result_digest=digest(selected.chdd.result.to_json()),
        wall_duration_seconds=round(time.monotonic() - started, 3),
        extra={
            "committed_candidate_digest": final.committed_candidate_digest,
            "selection_rule": final.selection.selection_rule_version,
            "rejected": [str(item.reason) for item in final.selection.rejected],
            # Воронка поиска по эпохам: виртуальных вариантов может быть много,
            # физических -- никогда больше бюджета.
            "search_funnel": list(resolver.funnel),
            # Причинный аудит: по одной записи на эпоху, наблюдательно.
            "epoch_audits": epoch_audits(epochs, results),
        },
    )


__all__ = [
    "INJECTION_ACTIONS",
    "PRODUCTION_ACTIONS",
    "GuardianTelemetry",
    "guardian_telemetry",
    "injection_actions_for_state",
    "run_mas_variant",
]
