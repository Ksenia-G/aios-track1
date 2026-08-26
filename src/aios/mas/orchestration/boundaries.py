"""Границы closed-loop оркестратора: типы и протоколы соседних зон.

Оркестратор владеет ТОЛЬКО последовательностью вызовов. Физика, легальность и
экономика принадлежат соседям, и этот модуль описывает их ровно теми
контрактами, которые в Wave1 уже существуют, а не параллельными:

* исполнение кандидата -- `aios.execution.SimulatorRuntime`, возвращающий
  `RuntimeExecution` (запрос + физический результат + провенанс). Голый
  `SimulationResult` границей не является: он не несёт идентичности кандидата,
  и на нём невозможно доказать, что результат относится именно к этому
  кандидату;
* реализованная легальность -- `aios.agents.ConstraintGuardian.evaluate(
  state_card) -> ConstraintReport`. Guardian судит по реализованному
  состоянию, а не по паре (кандидат, результат);
* экономика -- CHDD, у которого в репозитории пока нет реализации; протокол
  описан, реализация обязана быть передана явно.

Заглушек по умолчанию здесь нет намеренно. Fake-исполнитель, fake-Guardian и
fake-CHDD, подставленные молча, дают «успешную симуляцию», «легальный
кандидат» и экономическую оценку из хеша -- то есть закоммиченное решение без
физики, легальности и экономики. Тестовые двойники живут в tests/support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from aios.agents.constraint_guardian import FAIL, ConstraintReport
from aios.chdd.runner import ChddResult
from aios.evaluation.canonical import JsonValue
from aios.execution import ExecutionMode, RuntimeExecution, SimulatorRuntime
from aios.mas.arbiter import ArbiterDecision
from aios.mas.envelopes import ArbiterEnvelope
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.proposals import InjectionAgentOutput, ProductionAgentOutput
from aios.state import StateCard


class BoundaryError(ValueError):
    """Контракт соседней зоны нарушен."""


@dataclass(frozen=True, slots=True)
class GuardianVerdict:
    """Вердикт реализованной легальности по одному кандидату.

    Строится из `ConstraintReport` настоящего Guardian, а не выносится
    оркестратором: оркестратор не знает ни одного официального ограничения.
    """

    passed: bool
    violations: tuple[str, ...] = ()
    report: JsonValue | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "passed": self.passed,
            "violations": list(self.violations),
            "report": self.report,
        }

    @classmethod
    def from_report(cls, report: ConstraintReport) -> GuardianVerdict:
        violations = tuple(
            f"{check.well_name}:{check.constraint}"
            for check in report.official_checks
            if check.status == FAIL
        )
        return cls(passed=not violations, violations=violations, report=report.to_json())


@runtime_checkable
class ArbiterLike(Protocol):
    """Структурная арбитрация. Численных setpoints не производит."""

    def decide(self, envelope: ArbiterEnvelope) -> ArbiterDecision: ...


@runtime_checkable
class CandidateExecutor(Protocol):
    """Исполнение кандидата с сохранением провенанса.

    Возвращается `RuntimeExecution`, а не голый результат: только провенанс
    связывает результат с кандидатом, который его породил.
    """

    def execute(self, candidate: MagnitudeCandidate, *, run_label: str) -> RuntimeExecution: ...


@runtime_checkable
class RealizedStateExtractor(Protocol):
    """Реализованное состояние из физического результата прогона."""

    def extract(self, candidate: MagnitudeCandidate, execution: RuntimeExecution) -> StateCard: ...


@runtime_checkable
class RealizedConstraintGuardian(Protocol):
    """Ровно контракт существующего `aios.agents.ConstraintGuardian`."""

    def evaluate(self, state_card: StateCard) -> ConstraintReport: ...


@runtime_checkable
class CHDD(Protocol):
    """Авторитетная экономика по кандидату и его физическому прогону.

    Возвращается существующий `aios.chdd.ChddResult`, а не параллельный тип:
    экономический отбор ниже по конвейеру ранжирует именно его NPV.
    """

    def evaluate(
        self, candidate: MagnitudeCandidate, execution: RuntimeExecution
    ) -> ChddResult: ...


@runtime_checkable
class PredictiveSafetyScreen(Protocol):
    """Аннотация кандидатов. Не фильтр: удалять и добавлять identity запрещено."""

    def annotate(
        self, candidates: tuple[MagnitudeCandidate, ...]
    ) -> tuple[MagnitudeCandidate, ...]: ...


@runtime_checkable
class StateAdvancer(Protocol):
    """Переход к состоянию следующего шага после коммита."""

    def advance(
        self,
        state: StateCard,
        candidate: MagnitudeCandidate,
        execution: RuntimeExecution,
    ) -> StateCard: ...


ConflictPredicate = "ConflictPredicate"


def identity_pre_action_validator(
    candidates: tuple[MagnitudeCandidate, ...],
) -> tuple[MagnitudeCandidate, ...]:
    """PreActionValidator по умолчанию: правил у оркестратора нет."""
    return candidates


def never_conflict(
    production_output: ProductionAgentOutput,
    injection_output: InjectionAgentOutput,
) -> bool:
    """Конфликт по умолчанию не объявляется: предикат принадлежит вызывающему."""
    return False


class IdentityPredictiveSafetyScreen:
    """Аннотация-заглушка: набор кандидатов возвращается без изменений.

    Это допустимая производственная реализация, а не физическая заглушка: она
    ничего не утверждает о безопасности и ничего не скрывает -- в отличие от
    fake-симулятора, который выдаёт несуществующий SUCCESS.
    """

    def annotate(
        self, candidates: tuple[MagnitudeCandidate, ...]
    ) -> tuple[MagnitudeCandidate, ...]:
        return candidates


@dataclass(frozen=True, slots=True)
class SimulatorRuntimeExecutor:
    """Адаптер к настоящему `aios.execution.SimulatorRuntime`.

    Единственная задача -- связать `execute(candidate, *, run_label)` с полной
    сигнатурой runtime-зоны, не переписывая её. Режим исполнения остаётся
    явным полем: restart-оценку нельзя молча выдать за авторитетный результат.
    """

    runtime: SimulatorRuntime
    workspace_root: Path
    mode: ExecutionMode = ExecutionMode.FULL
    policy_id: str | None = None
    _restart_date: object = field(default=None, repr=False)

    def execute(self, candidate: MagnitudeCandidate, *, run_label: str) -> RuntimeExecution:
        return self.runtime.execute(
            candidate,
            workspace_root=Path(self.workspace_root),
            run_label=run_label,
            mode=self.mode,
            policy_id=self.policy_id,
        )


__all__ = [
    "CHDD",
    "ArbiterLike",
    "BoundaryError",
    "CandidateExecutor",
    "GuardianVerdict",
    "IdentityPredictiveSafetyScreen",
    "PredictiveSafetyScreen",
    "RealizedConstraintGuardian",
    "RealizedStateExtractor",
    "SimulatorRuntimeExecutor",
    "StateAdvancer",
    "identity_pre_action_validator",
    "never_conflict",
]
