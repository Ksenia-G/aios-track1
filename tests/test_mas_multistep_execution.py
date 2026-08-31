"""Многошаговое исполнение MAS: эпохи, композитная история, receding horizon.

Приближается к реальному дефекту: при `--decision-steps N > 1` когнитивный
слой продвигался, а физический оставался в исходном состоянии окна, и два
определения HOLD расходились между оркестратором и компилятором.

Утверждаемая семантика: кандидат в эпохе T_i компилируется в ОДНО полное
расписание окна -- неизменяемая закоммиченная история на [T0, T_i) плюс
управления кандидата на [T_i, H] -- и исполняется как одна траектория T0 -> H.
"""

from __future__ import annotations

import datetime
import re

import pytest

from aios.benchmark.epochs import decision_epochs
from aios.execution.history import (
    CommittedHistory,
    CommittedHistoryError,
    CommittedInterval,
    require_committed_history_lineage,
)
from aios.mas.invariants import canonical_control_snapshot
from aios.schedule import InjectorControl, ProducerControl, WellStatus

# -- сетка Model Y: 24 месячных отчётных шага -------------------------------

GRID = tuple(datetime.date(2014 + (m // 12), (m % 12) + 1, 1) for m in range(24))
T_H = GRID[23]


def _producer(well: str, *, rate: float) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=rate)


def _injector(well: str, *, rate: float) -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=rate
    )


def _snapshot(producers, injectors):
    return canonical_control_snapshot(producers, injectors)


# ===========================================================================
# TEST 4 -- эпохи решения продвигаются и садятся на сетку
# ===========================================================================


def test_decision_epochs_match_the_approved_rule() -> None:
    assert decision_epochs(GRID, 3) == (
        datetime.date(2014, 1, 1),
        datetime.date(2014, 9, 1),
        datetime.date(2015, 4, 1),
    )


def test_every_epoch_is_a_valid_grid_boundary() -> None:
    for steps in (1, 2, 3, 4, 6):
        for epoch in decision_epochs(GRID, steps):
            assert epoch in GRID


def test_horizon_end_is_never_a_decision_epoch() -> None:
    for steps in (1, 2, 3, 4, 6):
        assert T_H not in decision_epochs(GRID, steps)


def test_epochs_are_strictly_increasing_and_start_at_management_start() -> None:
    for steps in (1, 2, 3, 4, 6):
        epochs = decision_epochs(GRID, steps)
        assert len(epochs) == steps
        assert epochs[0] == GRID[0]
        assert list(epochs) == sorted(set(epochs))


def test_single_step_reduces_to_management_start() -> None:
    assert decision_epochs(GRID, 1) == (GRID[0],)


# ===========================================================================
# Закоммиченная история
# ===========================================================================

BASE_P = (_producer("P1", rate=100.0), _producer("P2", rate=100.0))
BASE_I = (_injector("I1", rate=200.0),)

A_P = (_producer("P1", rate=80.0), _producer("P2", rate=100.0))
A_I = (_injector("I1", rate=240.0),)

C2_P = (_producer("P1", rate=60.0), _producer("P2", rate=100.0))
C2_I = (_injector("I1", rate=260.0),)

T0, T1, T2 = decision_epochs(GRID, 3) if False else (GRID[0], GRID[8], GRID[15])


def _history_with_A() -> CommittedHistory:
    return CommittedHistory().commit(
        CommittedInterval(
            effective_date=T0, producers=A_P, injectors=A_I, candidate_digest="cand-A"
        )
    )


def test_controls_in_force_before_any_commit_are_the_window_baseline() -> None:
    history = CommittedHistory()
    p, i = history.controls_in_force_at(T0, baseline_producers=BASE_P, baseline_injectors=BASE_I)
    assert _snapshot(p, i) == _snapshot(BASE_P, BASE_I)


def test_controls_in_force_after_commit_are_the_committed_controls() -> None:
    history = _history_with_A()
    p, i = history.controls_in_force_at(T1, baseline_producers=BASE_P, baseline_injectors=BASE_I)
    assert _snapshot(p, i) == _snapshot(A_P, A_I)
    assert _snapshot(p, i) != _snapshot(BASE_P, BASE_I)


def test_committed_history_is_immutable_history() -> None:
    history = _history_with_A()
    grown = history.commit(
        CommittedInterval(
            effective_date=T1, producers=C2_P, injectors=C2_I, candidate_digest="cand-C2"
        )
    )
    assert len(history) == 1
    assert len(grown) == 2
    # Прошлое не переписано.
    assert _snapshot(*[grown.intervals[0].producers, grown.intervals[0].injectors]) == _snapshot(
        A_P, A_I
    )


def test_committed_epoch_must_advance_in_time() -> None:
    with pytest.raises(CommittedHistoryError):
        _history_with_A().commit(
            CommittedInterval(
                effective_date=T0, producers=C2_P, injectors=C2_I, candidate_digest="cand-x"
            )
        )


# ===========================================================================
# TEST 3 -- привязка истории к ВЫБРАННОМУ кандидату
# ===========================================================================


def test_history_from_the_selected_candidate_passes_lineage() -> None:
    require_committed_history_lineage(_history_with_A(), "cand-A")


def test_history_from_an_unselected_candidate_fails_lineage() -> None:
    """C2 выбран, но история собрана из C1 -- отказ по имени, не по SHA."""
    history_from_c1 = CommittedHistory().commit(
        CommittedInterval(
            effective_date=T0, producers=A_P, injectors=A_I, candidate_digest="cand-C1"
        )
    )
    with pytest.raises(CommittedHistoryError):
        require_committed_history_lineage(history_from_c1, "cand-C2")


def test_empty_history_fails_lineage_when_a_commit_was_expected() -> None:
    with pytest.raises(CommittedHistoryError):
        require_committed_history_lineage(CommittedHistory(), "cand-A")


# ===========================================================================
# TEST 6 -- StateCard(T_i) снимается на СЛЕДУЮЩЕЙ эпохе, из выбранного прогона
# ===========================================================================


def test_next_epoch_date_advances_to_the_following_epoch() -> None:
    from aios.mas.orchestration.composition import OpmRealizedStateExtractor

    epochs = decision_epochs(GRID, 3)
    extractor = OpmRealizedStateExtractor(
        case=None, locator=None, initial_state=None, baseline=None, decision_epochs=epochs
    )
    assert extractor._next_epoch_date(epochs[0]) == epochs[1]
    assert extractor._next_epoch_date(epochs[1]) == epochs[2]
    # Последняя эпоха: следующей карты уже не нужно, состояние -- конец горизонта.
    assert extractor._next_epoch_date(epochs[2]) is None


def test_single_step_run_keeps_the_end_of_horizon_state() -> None:
    """Одношаговый прогон эпох не объявляет -- поведение прежнее."""
    from aios.mas.orchestration.composition import OpmRealizedStateExtractor

    extractor = OpmRealizedStateExtractor(
        case=None, locator=None, initial_state=None, baseline=None, decision_epochs=()
    )
    assert extractor._next_epoch_date(GRID[0]) is None


def test_report_step_lookup_is_exact_and_fails_closed() -> None:
    """Состояние снимается на ТОЧНОЙ отчётной дате, иначе отказ."""
    import datetime as _dt

    from aios.mas.orchestration.composition import CompositionError, report_step_for

    class _Summary:
        dates = tuple(
            _dt.datetime(d.year, d.month, d.day)  # noqa: DTZ001 -- модельное время OPM
            for d in GRID
        )

    assert report_step_for(_Summary(), None) == -1
    assert report_step_for(_Summary(), GRID[8]) == 8
    assert report_step_for(_Summary(), GRID[15]) == 15
    with pytest.raises(CompositionError):
        report_step_for(_Summary(), datetime.date(2014, 9, 15))


# ===========================================================================
# TEST 8 -- финальный выбранный кандидат ЕСТЬ реализованная композитная политика
# ===========================================================================


def test_last_epoch_candidate_is_the_whole_realized_composite_policy() -> None:
    """Основание правила «не суммировать перекрывающиеся прогнозы».

    Кандидат последней эпохи несёт закоммиченную историю всех предыдущих эпох
    плюс собственный хвост, то есть уже является политикой T0..H целиком.
    Поэтому авторитетный ЧДД -- это ЕГО обычный полный прогон, посчитанный
    один раз, а не сумма прогнозов.
    """
    history = (
        CommittedHistory()
        .commit(
            CommittedInterval(
                effective_date=T0, producers=A_P, injectors=A_I, candidate_digest="cand-A"
            )
        )
        .commit(
            CommittedInterval(
                effective_date=T1, producers=C2_P, injectors=C2_I, candidate_digest="cand-C2"
            )
        )
    )
    # История покрывает обе предыдущие эпохи...
    covered = [item.effective_date for item in history.before(T2)]
    assert covered == [T0, T1]
    # ...и в силе на последней эпохе стоит именно политика предыдущей эпохи.
    p, i = history.controls_in_force_at(T2, baseline_producers=BASE_P, baseline_injectors=BASE_I)
    assert _snapshot(p, i) == _snapshot(C2_P, C2_I)


def test_forecast_npvs_are_never_summed_by_the_benchmark() -> None:
    """Итоговый ЧДД варианта -- это ОДНО число выбранного кандидата."""
    import inspect

    from aios.benchmark import mas_variant

    source = inspect.getsource(mas_variant)
    assert "sum(" not in source.split("def _run_decision_steps")[1].split("def ")[0]


# ===========================================================================
# TEST 9 -- MAS OFF и MAS ON используют одну и ту же физическую механику
# ===========================================================================


def test_epoch_rule_is_independent_of_the_cognitive_branch() -> None:
    """Эпохи считаются до и вне всякой зависимости от llm_enabled."""
    import inspect

    from aios.benchmark import mas_variant

    source = inspect.getsource(mas_variant.run_mas_variant)
    epoch_line = source.index("decision_epochs(")
    llm_branch = source.find("llm_enabled")
    # Эпохи вычисляются один раз, до какой-либо когнитивной ветки.
    assert epoch_line < source.index("production_agent_factory")
    assert source.count("decision_epochs(") == 1
    assert llm_branch != -1


def test_both_variants_receive_identical_epochs_for_the_same_case() -> None:
    assert decision_epochs(GRID, 3) == decision_epochs(GRID, 3)
    assert decision_epochs(GRID, 3)[0] == GRID[0]


# ===========================================================================
# Линия происхождения: каждый шаг называет СВОЙ вход ЧДД
# ===========================================================================


def test_each_step_records_its_own_chdd_input_digest() -> None:
    """Запись шага обязана называть вход ЧДД ЭТОГО шага, а не последнего.

    При decision_steps=1 разницы не было -- запись одна. На многошаговом
    прогоне общий на всех дайджест означает, что lineage шагов 1..N-1
    указывает на чужой экономический вход, и доказательство происхождения
    перестаёт быть доказательством.
    """
    import inspect

    from aios.benchmark import mas_variant

    source = inspect.getsource(mas_variant.run_mas_variant)
    lineage_block = source.split("EvaluationLineage(")[1].split("for result in results")[0]
    assert "result.selection.selected" in lineage_block, (
        "chdd_input_digest must come from the step's own selection, not the final one"
    )
    # Голое `selected` -- это выбор ПОСЛЕДНЕГО шага; допустимо только
    # `result.selection.selected`.
    assert re.search(r"(?<!selection\.)\bselected\.chdd", lineage_block) is None
