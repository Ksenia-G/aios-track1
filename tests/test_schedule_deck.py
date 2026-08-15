"""Разбор колоды, граница истории и восстановление состояния."""

from datetime import date

import pytest

from aios.cases import Case
from aios.schedule import (
    DeckParseError,
    WellRole,
    WellStatus,
    parse_schedule_file,
    parse_start_date,
    reconstruct_state,
)
from aios.schedule.deck import format_deck_date, tokenize


def _deck(case: Case):
    start = parse_start_date(case.history.data_path.read_text(encoding="utf-8"))
    return parse_schedule_file(case.history.schedule_path, start_date=start)


def test_start_date_comes_from_the_data_file(mini_case: Case) -> None:
    assert parse_start_date(mini_case.history.data_path.read_text(encoding="utf-8")) == date(
        2020, 1, 1
    )


def test_blocks_carry_their_effective_date(mini_case: Case) -> None:
    deck = _deck(mini_case)

    by_keyword = {}
    for block, day in zip(deck.blocks, deck.block_dates, strict=True):
        by_keyword.setdefault(block.keyword, []).append(day)

    assert by_keyword["WELSPECS"] == [date(2020, 1, 1)]
    assert by_keyword["WCONPROD"] == [date(2020, 1, 1), date(2020, 2, 1)]
    assert by_keyword["DATES"] == [date(2020, 2, 1), date(2020, 3, 1), date(2020, 4, 1)]


def test_well_universe_covers_welspecs_and_compdat(mini_case: Case) -> None:
    assert _deck(mini_case).wells == ("I1", "P1", "P2")


def test_history_is_cut_exactly_at_the_management_start(mini_case: Case) -> None:
    deck = _deck(mini_case)

    history = deck.history_text(mini_case.management_start_date)

    assert "WELSPECS" in history
    assert "'P1' 'OPEN' 'LRAT' 1* 1* 1* 100" in history
    # Первый управляемый шаг и всё, что за ним, в историю не попадает.
    assert "01 'FEB' 2020" not in history
    assert "120" not in history


def test_state_is_reconstructed_from_the_replayed_history(mini_case: Case) -> None:
    state = reconstruct_state(_deck(mini_case), mini_case.management_start_date)

    by_well = state.by_well
    assert set(by_well) == {"P1", "P2", "I1"}
    assert by_well["P1"].role is WellRole.PRODUCER
    assert by_well["P1"].target == pytest.approx(100.0)
    assert by_well["P1"].bhp_limit == pytest.approx(50.0)
    assert by_well["P1"].status is WellStatus.OPEN
    assert by_well["I1"].role is WellRole.INJECTOR
    assert by_well["I1"].target == pytest.approx(200.0)
    assert by_well["I1"].phase == "WATER"
    # Управление 01.02.2020 уже за границей среза и состояние не меняет.
    assert by_well["P1"].target != pytest.approx(120.0)


def test_control_schedule_covers_only_the_window(mini_case: Case) -> None:
    schedule = _deck(mini_case).control_schedule(
        mini_case.management_start_date, mini_case.management_end_date
    )

    assert schedule.dates == (date(2020, 2, 1), date(2020, 3, 1), date(2020, 4, 1))
    assert [c.well for c in schedule.steps[0].producers] == ["P1"]
    assert schedule.steps[0].producers[0].liquid_rate == pytest.approx(120.0)
    assert schedule.steps[1].is_empty


def test_repeat_counts_expand_into_positions() -> None:
    blocks = list(tokenize("WCONPROD\n 'W' 'OPEN' 'LRAT' 3* 100 1* 50 1* 1* /\n/\n"))

    record = blocks[0].records[0]
    assert record.text(2) == "LRAT"
    assert record.number(6) == pytest.approx(100.0)
    assert record.number(8) == pytest.approx(50.0)


def test_unterminated_keyword_is_rejected() -> None:
    with pytest.raises(DeckParseError, match="not terminated"):
        list(tokenize("WCONPROD\n 'W' 'OPEN' 'LRAT'\n"))


def test_unknown_month_in_start_is_rejected() -> None:
    with pytest.raises(DeckParseError, match="unknown month"):
        parse_start_date("START\n 01 'FOO' 2020 /\n")


def test_deck_without_start_is_rejected() -> None:
    with pytest.raises(DeckParseError, match="no usable START"):
        parse_start_date("RUNSPEC\nMETRIC\n")


def test_missing_management_start_boundary_is_reported(mini_case: Case) -> None:
    with pytest.raises(DeckParseError, match="no single-date DATES block"):
        _deck(mini_case).history_text(date(2021, 1, 1))


def test_deck_date_formatting_round_trips() -> None:
    assert format_deck_date(date(2014, 1, 1)) == "01 'JAN' 2014"
    assert format_deck_date(date(2015, 12, 1)) == "01 'DEC' 2015"
