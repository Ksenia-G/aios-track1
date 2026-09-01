"""Закоммиченная история управлений многошагового прогона.

Композитное расписание кандидата -- это неизменяемая закоммиченная история на
``[T0, T_i)`` плюс собственные управления кандидата на ``[T_i, H]``. История
живёт здесь, а не в бенчмарке: её читает компилятор, и второй, «похожий»
источник той же правды разошёлся бы с ним при первом же изменении.

История неизменяема по построению: ``commit`` возвращает новый объект. Каждый
интервал назван дайджестом кандидата, который его породил, поэтому подмена
невыбранного кандидата отвергается по имени, а не замечается по косвенно
изменившемуся SHA расписания.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aios.schedule.events import InjectorControl, ProducerControl


class CommittedHistoryError(ValueError):
    """История управлений не пригодна для композитного расписания."""


@dataclass(frozen=True, slots=True)
class CommittedInterval:
    """Управления, закоммиченные в эпоху ``effective_date``.

    ``candidate_digest`` -- идентичность ВЫБРАННОГО кандидата, породившего
    интервал: история, собранная из другого кандидата, отличается не только
    числами, но и именем.
    """

    effective_date: date
    producers: tuple[ProducerControl, ...]
    injectors: tuple[InjectorControl, ...]
    candidate_digest: str

    def __post_init__(self) -> None:
        if not self.candidate_digest.strip():
            raise CommittedHistoryError("committed_interval.candidate_digest must not be empty")


@dataclass(frozen=True, slots=True)
class CommittedHistory:
    """Упорядоченная последовательность закоммиченных интервалов."""

    intervals: tuple[CommittedInterval, ...] = ()

    def __post_init__(self) -> None:
        dates = [interval.effective_date for interval in self.intervals]
        if dates != sorted(dates):
            raise CommittedHistoryError("committed_history: intervals must be ordered by date")
        if len(set(dates)) != len(dates):
            raise CommittedHistoryError("committed_history: duplicate committed epoch")

    def commit(self, interval: CommittedInterval) -> CommittedHistory:
        """Дописать интервал. Прошлое при этом не переписывается."""
        if self.intervals and interval.effective_date <= self.intervals[-1].effective_date:
            raise CommittedHistoryError(
                "committed_history.commit: a committed epoch must advance in time"
            )
        return CommittedHistory(self.intervals + (interval,))

    def before(self, moment: date) -> tuple[CommittedInterval, ...]:
        """Интервалы, действующие строго ДО ``moment``."""
        return tuple(item for item in self.intervals if item.effective_date < moment)

    def controls_in_force_at(
        self,
        moment: date,
        *,
        baseline_producers: tuple[ProducerControl, ...],
        baseline_injectors: tuple[InjectorControl, ...],
    ) -> tuple[tuple[ProducerControl, ...], tuple[InjectorControl, ...]]:
        """Управления, реально стоящие в ``moment``.

        До первого коммита это baseline окна; после -- последний
        закоммиченный интервал. Именно против этого сравнивается HOLD.
        """
        prior = self.before(moment)
        if not prior:
            return baseline_producers, baseline_injectors
        last = prior[-1]
        return last.producers, last.injectors

    @property
    def last_candidate_digest(self) -> str | None:
        return self.intervals[-1].candidate_digest if self.intervals else None

    def __len__(self) -> int:
        return len(self.intervals)


def require_committed_history_lineage(
    history: CommittedHistory, expected_last_candidate_digest: str | None
) -> None:
    """Отвергнуть историю, собранную не из ВЫБРАННОГО кандидата.

    Разошедшийся SHA расписания сам по себе доказательством не является: он
    говорит «что-то другое», а не «история происходит не от выбранного
    кандидата». Привязка проверяется по имени кандидата явно.
    """
    actual = history.last_candidate_digest
    if actual != expected_last_candidate_digest:
        raise CommittedHistoryError(
            "committed_history lineage: history was committed by candidate "
            f"{actual!r}, expected the selected candidate {expected_last_candidate_digest!r}"
        )


__all__ = [
    "CommittedHistory",
    "CommittedHistoryError",
    "CommittedInterval",
    "require_committed_history_lineage",
]
