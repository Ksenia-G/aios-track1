"""DecisionTrace -- append-only журнал шагов принятия решения.

Минимальная реализация стадии «DecisionTrace / Memory append» из диаграммы
конвейера (docs/team/closed-loop-orchestrator.md): каждая запись -- полный
`DecisionStepResult` в порядке выполнения. Только добавление: у класса нет
ни одного метода изменения или удаления уже записанного, наружу отдаются
только неизменяемые копии.

Намеренно детерминирован: никаких таймстампов и wall-clock значений --
идентичность записи задаётся её содержимым (дайджесты) и порядковым номером
шага. Два одинаковых прогона дают побайтово одинаковый `to_json()`.

Это НЕ MemoryStore: постоянное хранилище с retrieval-семантикой (карты
откликов, поиск по похожести, опыт EXP-001...004) -- отдельная следующая
задача с собственным дизайном. Здесь только журнал текущего прогона в
памяти процесса.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aios.evaluation.canonical import JsonValue

if TYPE_CHECKING:
    from aios.mas.orchestration.orchestrator import DecisionStepResult


@dataclass(frozen=True, slots=True)
class StepFailure:
    """Детерминированный след шага, который не дошёл до коммита.

    Успешный журнал остаётся append-only и содержит только закоммиченные шаги.
    Но шаг, упавший на стадии, раньше не оставлял вообще ничего, кроме
    исключения: разобрать постфактум, где и на каком кандидате он встал, было
    нечем. Здесь ровно столько, сколько нужно для разбора, и ни одного
    wall-clock значения.
    """

    step_index: int
    stage: str
    reason: str
    candidate_digest: str | None = None
    state_card_digest: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "step_index": self.step_index,
            "stage": self.stage,
            "reason": self.reason,
            "candidate_digest": self.candidate_digest,
            "state_card_digest": self.state_card_digest,
        }


class DecisionTrace:
    """Append-only последовательность результатов шагов одного прогона."""

    def __init__(self) -> None:
        self._records: list[DecisionStepResult] = []
        self._failures: list[StepFailure] = []

    def append(self, result: DecisionStepResult) -> None:
        """Дописать результат очередного шага в конец журнала."""

        self._records.append(result)

    def append_failure(self, failure: StepFailure) -> None:
        """Записать несостоявшийся шаг отдельно от журнала коммитов."""

        self._failures.append(failure)

    @property
    def failures(self) -> tuple[StepFailure, ...]:
        """Шаги, не дошедшие до коммита, в порядке возникновения."""

        return tuple(self._failures)

    @property
    def records(self) -> tuple[DecisionStepResult, ...]:
        """Неизменяемый снимок журнала в порядке выполнения шагов."""

        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def to_json(self) -> list[dict[str, JsonValue]]:
        """JSON-представление журнала; `step_index` -- порядковый номер шага."""

        return [
            {"step_index": index, **record.to_json()} for index, record in enumerate(self._records)
        ]

    def failures_to_json(self) -> list[dict[str, JsonValue]]:
        """JSON-представление несостоявшихся шагов."""

        return [failure.to_json() for failure in self._failures]


__all__ = ["DecisionTrace", "StepFailure"]
