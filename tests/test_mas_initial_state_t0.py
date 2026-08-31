"""T0-1..T0-14: the initial StateCard must reflect the decision date, not H.

`_initial_state_card` builds the causal starting point for every MAS
decision loop (MAS OFF, MAS ON, single-step and multi-step). It runs the
full history+baseline OPM schedule and used to hand the resulting summary
to `StateExtractor.extract()` with no explicit `step=`, which defaults to
the LAST report step -- the end of the horizon (H), not the requested
decision date (T0). A decision made "at T0" that actually reads H-dated
dynamic state is future information leakage, not a cosmetic date bug.

`report_step_for` is the SAME date-driven resolver `OpmRealizedStateExtractor`
already uses (correctly) to find the report step for T1/T2/H mid-loop; the
fix reuses it for T0 rather than inventing a second date mapping.
"""

from __future__ import annotations

import datetime

import pytest

from aios.benchmark import cli as benchmark_cli
from aios.benchmark.adapters import BenchmarkContext, ScheduleEvaluation
from aios.cases import Case
from aios.mas.orchestration import CompositionError, report_step_for
from aios.opm.summary import SummaryData
from aios.state import GeologyContext, StateExtractor, WellControlSnapshot, WellRole

# --- a summary spanning pre-history, T0, T1, T2, H and one post-H step -------

_HISTORY = datetime.datetime(2013, 6, 1)  # noqa: DTZ001
_T0 = datetime.datetime(2014, 1, 1)  # noqa: DTZ001
_T1 = datetime.datetime(2014, 9, 1)  # noqa: DTZ001
_T2 = datetime.datetime(2015, 4, 1)  # noqa: DTZ001
_H = datetime.datetime(2015, 12, 1)  # noqa: DTZ001
_POST_H = datetime.datetime(2016, 3, 1)  # noqa: DTZ001

_DATES = (_HISTORY, _T0, _T1, _T2, _H, _POST_H)

_FIELD_INDEX = {"FPR": 0, "FOPR": 1, "FWPR": 2, "FLPR": 3, "FWIR": 4, "FVPR": 5, "FVPT": 6}
_WELL_INDEX = {
    ("WBHP", "P1"): 7,
    ("WLPR", "P1"): 8,
    ("WOPR", "P1"): 9,
    ("WWPR", "P1"): 10,
    ("WWIR", "P1"): 11,
    ("WWCT", "P1"): 12,
    ("WOPT", "P1"): 13,
    ("WWPT", "P1"): 14,
    ("WWIT", "P1"): 15,
}


def _row(step_index: int) -> tuple[float, ...]:
    """One clearly distinct row per step index: values are ``step_index``-tagged."""
    base = 1000.0 * (step_index + 1)
    return (
        200.0 + base,  # FPR
        90.0 + base,  # FOPR
        20.0 + base,  # FWPR
        110.0 + base,  # FLPR
        100.0 + base,  # FWIR
        120.0 + base,  # FVPR
        900.0 + base,  # FVPT
        60.0 + base,  # WBHP
        110.0 + base,  # WLPR
        90.0 + base,  # WOPR
        20.0 + base,  # WWPR
        100.0 + base,  # WWIR
        0.10 + step_index * 0.01,  # WWCT
        900.0 + base,  # WOPT
        300.0 + base,  # WWPT
        50.0 + base,  # WWIT
    )


def _summary(*, dates: tuple[datetime.datetime, ...] = _DATES, mutate_after: int | None = None):
    """Distinct rows per step; ``mutate_after`` scrambles every row past that index.

    Used for T0-10: two summaries identical through T0 but different after it
    must still produce byte-identical T0 extraction.
    """
    rows = [_row(index) for index in range(len(dates))]
    if mutate_after is not None:
        for index in range(mutate_after + 1, len(dates)):
            scrambled = list(_row(index))
            scrambled[_FIELD_INDEX["FPR"]] += 999_999.0
            rows[index] = tuple(scrambled)
    return SummaryData(
        start=dates[0] - datetime.timedelta(days=1),
        dates=dates,
        wells=("P1",),
        units={},
        _field_index=_FIELD_INDEX,
        _well_index=_WELL_INDEX,
        _params=tuple(rows),
    )


def _extract_at(summary: SummaryData, moment: datetime.datetime, mini_case: Case):
    step = report_step_for(summary, moment.date())
    return StateExtractor().extract(
        summary=summary,
        case=mini_case,
        run_id="t0-test",
        simulator_digest="sim-sha256:" + "a" * 64,
        schedule_hash="b" * 64,
        well_controls={
            "P1": WellControlSnapshot(
                role=WellRole.PRODUCER,
                status="OPEN",
                current_target=100.0,
                control_mode="LRAT",
            )
        },
        step=step,
    )


# --- T0-1 / T0-2: date-driven report-step resolution -------------------------


def test_report_step_for_finds_the_exact_calendar_date() -> None:
    """T0-1: the resolved step's date equals the requested moment, not H."""
    summary = _summary()
    index = report_step_for(summary, _T0.date())
    assert summary.dates[index].date() == _T0.date()
    assert summary.dates[index].date() != _H.date()


@pytest.mark.parametrize("extra_dates", [(), (datetime.datetime(2012, 1, 1),)])  # noqa: DTZ001
def test_report_step_for_is_index_independent(extra_dates) -> None:
    """T0-2: inserting unrelated report steps must not change which date is picked."""
    dates = (*extra_dates, *_DATES, datetime.datetime(2017, 1, 1))  # noqa: DTZ001
    summary = _summary(dates=dates)
    index = report_step_for(summary, _T0.date())
    assert summary.dates[index].date() == _T0.date()


def test_report_step_for_rejects_a_moment_absent_from_the_summary() -> None:
    summary = _summary()
    with pytest.raises(CompositionError, match="2014-06-15"):
        report_step_for(summary, datetime.date(2014, 6, 15))


def test_report_step_for_none_still_means_end_of_horizon() -> None:
    """Backward compatibility: mid-loop callers pass moment=None at the final epoch."""
    summary = _summary()
    assert report_step_for(summary, None) == -1


# --- T0-3 / T0-4: no future dynamic values leak into the T0 StateCard --------


def test_t0_state_uses_t0_well_values_not_h(mini_case: Case) -> None:
    """T0-3: WWIR/BHP/WOPR/WLPR/WWCT at T0 must not be H's values."""
    summary = _summary()
    t0_card = _extract_at(summary, _T0, mini_case)
    h_card = _extract_at(summary, _H, mini_case)

    t0_well = t0_card.wells[0]
    h_well = h_card.wells[0]
    assert t0_well.bhp != h_well.bhp
    assert t0_well.wlpr != h_well.wlpr
    assert t0_well.wwir != h_well.wwir
    assert t0_well.wct != h_well.wct

    expected_t0_row = _row(_DATES.index(_T0))
    assert t0_well.bhp == pytest.approx(expected_t0_row[7])
    assert t0_well.wwir == pytest.approx(expected_t0_row[11])


def test_t0_field_values_are_not_h_field_values(mini_case: Case) -> None:
    """T0-4: field-level pressure/totals also come from T0, not H."""
    summary = _summary()
    t0_card = _extract_at(summary, _T0, mini_case)
    h_card = _extract_at(summary, _H, mini_case)

    assert t0_card.field.average_pressure != h_card.field.average_pressure
    expected_t0_row = _row(_DATES.index(_T0))
    assert t0_card.field.average_pressure == pytest.approx(expected_t0_row[0])
    assert t0_card.metadata.date == _T0.date()
    assert h_card.metadata.date == _H.date()


# --- T0-8: interior epochs (T1/T2) resolve causally, not to the horizon end --


def test_interior_epoch_dates_do_not_resolve_past_themselves(mini_case: Case) -> None:
    """T0-8: a T1 (resp. T2) request never returns state dated after T1 (resp. T2)."""
    summary = _summary()
    t1_card = _extract_at(summary, _T1, mini_case)
    t2_card = _extract_at(summary, _T2, mini_case)

    assert t1_card.metadata.date == _T1.date()
    assert t1_card.metadata.date <= _T1.date()
    assert t2_card.metadata.date == _T2.date()
    assert t2_card.metadata.date <= _T2.date()


# --- T0-10: future mutation invariance ---------------------------------------


def _causal_payload(card):
    """Everything a specialist actually reasons over -- excludes provenance
    identity (`source_summary_identity` legitimately hashes the WHOLE run,
    so it correctly differs between two distinct physical runs even when
    their shared T0 prefix is identical; that is a run-identity fact, not
    lookahead)."""
    return (card.field.to_json(), tuple(w.to_json() for w in card.wells), card.constraints)


def test_t0_extraction_is_invariant_to_what_happens_after_t0(mini_case: Case) -> None:
    """T0-10: two summaries identical through T0 but different after must
    produce byte-identical T0 physical state -- the strongest lookahead
    regression. If any post-T0 row leaked in, this would differ."""
    t0_index = _DATES.index(_T0)
    baseline_summary = _summary()
    mutated_summary = _summary(mutate_after=t0_index)

    # Sanity: the mutation actually changed a later row (H), or the test proves nothing.
    assert baseline_summary.field_value("FPR", _DATES.index(_H)) != mutated_summary.field_value(
        "FPR", _DATES.index(_H)
    )

    card_a = _extract_at(baseline_summary, _T0, mini_case)
    card_b = _extract_at(mutated_summary, _T0, mini_case)
    assert card_a.metadata.date == card_b.metadata.date == _T0.date()
    assert _causal_payload(card_a) == _causal_payload(card_b)


# --- T0-9 / entry point: _initial_state_card must select T0, not H ----------


def _summary_around(decision_date: datetime.date, *, mutate_after_decision: bool = False):
    """A history+H-shaped summary anchored on an arbitrary decision date.

    `_initial_state_card` resolves against `case.management_start_date`,
    which the `mini_case` fixture pins to 2020-02-01 -- independent of the
    fixed T0/T1/T2/H calendar used by the resolver-level tests above.
    """
    pre_history = datetime.datetime.combine(decision_date, datetime.time()) - datetime.timedelta(
        days=200
    )
    decision = datetime.datetime.combine(decision_date, datetime.time())
    horizon_end = decision + datetime.timedelta(days=400)
    dates = (pre_history, decision, horizon_end)
    return _summary(
        dates=dates,
        mutate_after=1 if mutate_after_decision else None,
    )


class _StubBaseline:
    """Sentinel schedule/state objects: everything downstream of them is mocked."""


def _install_initial_state_stubs(monkeypatch, *, summary: SummaryData, decision_date):
    """Mock every collaborator of `_initial_state_card` except StateExtractor
    and `report_step_for`, so the real production code path under test still
    runs the real date resolution -- only the OPM/deck machinery is stubbed."""
    from aios.benchmark import adapters as adapters_module
    from aios.state import extractor as extractor_module

    monkeypatch.setattr(benchmark_cli, "build_baseline", lambda context: (_StubBaseline(), "TEXT"))
    monkeypatch.setattr(
        BenchmarkContext,
        "baseline_inputs",
        lambda self: (_StubBaseline(), _StubBaseline(), "history-text", decision_date),
    )
    monkeypatch.setattr(BenchmarkContext, "geology", lambda self: GeologyContext())
    monkeypatch.setattr(
        extractor_module,
        "well_controls_from_schedule",
        lambda state, schedule, *, at_date: {
            "P1": WellControlSnapshot(
                role=WellRole.PRODUCER,
                status="OPEN",
                current_target=100.0,
                control_mode="LRAT",
            )
        },
    )

    def _fake_evaluate_schedule_text(context, *, schedule_text, schedule, run_label):
        return ScheduleEvaluation(
            schedule_sha="c" * 64,
            summary=summary,
            realized_state_digest="d" * 64,
            guardian_pass=True,
            guardian_checks=0,
            guardian_failed=0,
            npv_mln_rub=0.0,
            chdd_input_digest="e" * 64,
            chdd_result_digest="f" * 64,
            yearly_profile_digest="g" * 64,
            report_steps=summary.report_steps,
            well_count=len(summary.wells),
        )

    monkeypatch.setattr(adapters_module, "evaluate_schedule_text", _fake_evaluate_schedule_text)


def _benchmark_context(mini_case: Case, tmp_path, case_path) -> BenchmarkContext:
    return BenchmarkContext(
        case=mini_case,
        case_path=case_path,
        code_sha="0" * 40,
        workspace_root=tmp_path,
        deck_reference="${MODEL_Y_ROOT}",
    )


def test_initial_state_card_selects_t0_not_the_horizon_end(
    monkeypatch, mini_case: Case, mini_case_dir, tmp_path
) -> None:
    """T0-1/T0-5/T0-6/T0-7/T0-9 at the real entry point.

    `mini_case.management_start_date` is the decision origin every MAS
    variant (OFF/ON, single- and multi-step) is supposed to start from.
    """
    summary = _summary_around(mini_case.management_start_date)
    decision = datetime.datetime.combine(mini_case.management_start_date, datetime.time())
    horizon_end = decision + datetime.timedelta(days=400)
    _install_initial_state_stubs(
        monkeypatch, summary=summary, decision_date=mini_case.management_start_date
    )
    context = _benchmark_context(mini_case, tmp_path, mini_case_dir / "case.json")

    card = benchmark_cli._initial_state_card(context)

    assert card.metadata.date == mini_case.management_start_date
    assert card.metadata.date != horizon_end.date()


def test_initial_state_card_is_invariant_to_post_t0_mutation(
    monkeypatch, mini_case: Case, mini_case_dir, tmp_path
) -> None:
    """T0-10 at the entry point: post-T0 rows must not affect the initial card."""
    context = _benchmark_context(mini_case, tmp_path, mini_case_dir / "case.json")

    _install_initial_state_stubs(
        monkeypatch,
        summary=_summary_around(mini_case.management_start_date),
        decision_date=mini_case.management_start_date,
    )
    card_a = benchmark_cli._initial_state_card(context)

    _install_initial_state_stubs(
        monkeypatch,
        summary=_summary_around(mini_case.management_start_date, mutate_after_decision=True),
        decision_date=mini_case.management_start_date,
    )
    card_b = benchmark_cli._initial_state_card(context)

    assert card_a.metadata.date == card_b.metadata.date == mini_case.management_start_date
    assert _causal_payload(card_a) == _causal_payload(card_b)


# --- T0-11: static geology is unaffected by the decision date ---------------


def test_geology_is_independent_of_the_resolved_step(
    monkeypatch, mini_case: Case, mini_case_dir, tmp_path
) -> None:
    """T0-11: `context.geology()` takes no date/step argument -- deck-static."""
    import inspect

    source = inspect.getsource(benchmark_cli._initial_state_card)
    assert "context.geology()" in source

    context = _benchmark_context(mini_case, tmp_path, mini_case_dir / "case.json")
    _install_initial_state_stubs(
        monkeypatch,
        summary=_summary_around(mini_case.management_start_date),
        decision_date=mini_case.management_start_date,
    )
    card = benchmark_cli._initial_state_card(context)
    assert card.geology == GeologyContext()
