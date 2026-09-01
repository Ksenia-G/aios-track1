"""Production foundation contracts for the Track-1 closed loop."""

from __future__ import annotations

import datetime
import json
import struct
from pathlib import Path

import pytest

from aios.cases import Case, CaseError, load_case
from aios.chdd.runner import ChddResultSchemaError, parse_chdd_result
from aios.opm.workspace import prepare_workspace
from aios.pipeline import (
    CLAIMED_NPV_FILENAME,
    CONSTRAINT_REPORT_FILENAME,
    RUN_PROVENANCE_FILENAME,
    SCHEDULE_FILENAME,
    YEARLY_NPV_PROFILE_FILENAME,
    PipelineReport,
    build_schedule_for_case,
    claimed_npv_payload,
)
from aios.schedule import (
    DeckParseError,
    WellRole,
    parse_schedule_file,
    parse_start_date,
    reconstruct_state,
)
from aios.schedule.generator import preserved_history_sha256
from aios.schedule.policy import DEFAULT_POLICY_ID


def _source_history(case: Case) -> str:
    start = parse_start_date(case.history.data_path.read_text(encoding="utf-8"))
    deck = parse_schedule_file(case.history.schedule_path, start_date=start)
    return deck.history_text(case.management_start_date)


def test_full_schedule_artifact_preserves_source_history_and_appends_management(
    mini_case: Case,
) -> None:
    schedule, state, schedule_text, history_text = build_schedule_for_case(
        mini_case, policy_id=DEFAULT_POLICY_ID
    )

    assert schedule.dates[0] == mini_case.management_start_date
    assert state.as_of == mini_case.management_start_date
    assert history_text == _source_history(mini_case)
    assert schedule_text.startswith(history_text)
    assert preserved_history_sha256(schedule_text, history_text) == preserved_history_sha256(
        history_text, history_text
    )
    assert "WELSPECS" in schedule_text
    assert "2020-01-01" in schedule_text
    assert "01 'FEB' 2020" in schedule_text
    assert "01 'APR' 2020" in schedule_text
    assert schedule_text.count("'P1' 'OPEN' 'LRAT' 1* 1* 1* 100") == 1
    assert schedule_text.count("01 'FEB' 2020") == 1


def test_workspace_uses_complete_schedule_without_duplicating_history(
    mini_case: Case, tmp_path: Path
) -> None:
    _schedule, _state, schedule_text, history_text = build_schedule_for_case(
        mini_case, policy_id=DEFAULT_POLICY_ID
    )

    workspace = prepare_workspace(
        mini_case,
        root=tmp_path / "workspace",
        history_text="",
        schedule_text=schedule_text,
    )

    assert workspace.history_file.read_text(encoding="utf-8") == ""
    published = workspace.schedule_file.read_text(encoding="utf-8")
    assert published.startswith(history_text)
    assert published.count("WELSPECS") == 1
    assert published.count("01 'FEB' 2020") == 1


def test_handover_date_can_move_to_a_different_existing_step(
    mini_case_dir: Path, case_payload: dict
) -> None:
    case_payload["management_start_date"] = "2020-03-01"
    (mini_case_dir / "case.json").write_text(json.dumps(case_payload), encoding="utf-8")
    case = load_case(mini_case_dir / "case.json")

    schedule, state, schedule_text, history_text = build_schedule_for_case(
        case, policy_id=DEFAULT_POLICY_ID
    )

    assert state.as_of == datetime.date(2020, 3, 1)
    assert state.by_well["P1"].target == pytest.approx(120.0)
    assert schedule.dates == (datetime.date(2020, 3, 1), datetime.date(2020, 4, 1))
    assert schedule_text.startswith(history_text)
    assert "01 'MAR' 2020" in schedule_text


def test_dates_layout_with_comments_and_unquoted_month_is_supported(tmp_path: Path) -> None:
    schedule = """WELSPECS
 'P1' 'G' 1 1 1* 'OIL' /
/
WCONPROD
 'P1' 'OPEN' 'LRAT' 3* 100 1* 50 1* 1* /
/
DATES
   01  FEB   2020 / -- handover line differs from Model Y formatting
/
WCONPROD
 'P1' 'OPEN' 'LRAT' 3* 110 1* 50 1* 1* /
/
DATES
 01 'MAR' 2020 /
/
"""
    path = tmp_path / "SCH.INC"
    path.write_text(schedule, encoding="utf-8")

    deck = parse_schedule_file(path, start_date=datetime.date(2020, 1, 1))
    window = deck.control_schedule(datetime.date(2020, 2, 1), datetime.date(2020, 3, 1))

    assert window.dates == (datetime.date(2020, 2, 1), datetime.date(2020, 3, 1))
    assert window.steps[0].producers[0].liquid_rate == pytest.approx(110.0)


def test_supported_weltarg_rate_change_reconstructs_state(tmp_path: Path) -> None:
    schedule = """WELSPECS
 'P1' 'G' 1 1 1* 'OIL' /
/
WCONPROD
 'P1' 'OPEN' 'LRAT' 3* 100 1* 50 1* 1* /
/
WELTARG
 'P1' 'LRAT' 135 /
/
DATES
 01 'FEB' 2020 /
/
"""
    path = tmp_path / "SCH.INC"
    path.write_text(schedule, encoding="utf-8")

    deck = parse_schedule_file(path, start_date=datetime.date(2020, 1, 1))
    state = reconstruct_state(deck, datetime.date(2020, 2, 1))

    assert state.by_well["P1"].target == pytest.approx(135.0)
    assert state.by_well["P1"].mode == "LRAT"


def test_unsupported_weltarg_change_fails_loudly(tmp_path: Path) -> None:
    schedule = """WELSPECS
 'P1' 'G' 1 1 1* 'OIL' /
/
WCONPROD
 'P1' 'OPEN' 'LRAT' 3* 100 1* 50 1* 1* /
/
WELTARG
 'P1' 'THP' 135 /
/
DATES
 01 'FEB' 2020 /
/
"""
    path = tmp_path / "SCH.INC"
    path.write_text(schedule, encoding="utf-8")
    deck = parse_schedule_file(path, start_date=datetime.date(2020, 1, 1))

    with pytest.raises(DeckParseError, match="unsupported WELTARG target 'THP'"):
        reconstruct_state(deck, datetime.date(2020, 2, 1))


def test_well_role_comes_from_actual_history_not_static_lists(tmp_path: Path) -> None:
    schedule = """WELSPECS
 '7' 'G' 1 1 1* 'WATER' /
/
WCONINJE
 '7' 'WATER' 'OPEN' 'RATE' 80 1* 300 1* 1* /
/
DATES
 01 'FEB' 2020 /
/
WCONPROD
 '7' 'OPEN' 'LRAT' 3* 90 1* 50 1* 1* /
/
DATES
 01 'MAR' 2020 /
/
"""
    path = tmp_path / "SCH.INC"
    path.write_text(schedule, encoding="utf-8")
    deck = parse_schedule_file(path, start_date=datetime.date(2020, 1, 1))

    before = reconstruct_state(deck, datetime.date(2020, 2, 1))
    after = reconstruct_state(deck, datetime.date(2020, 3, 1))

    assert before.by_well["7"].role is WellRole.INJECTOR
    assert after.by_well["7"].role is WellRole.PRODUCER


def test_case_separates_official_realized_constraints_from_search_bounds(
    mini_case: Case,
) -> None:
    assert mini_case.constraints.official_realized.producer_bhp_min == pytest.approx(50.0)
    assert mini_case.constraints.official_realized.injector_bhp_max == pytest.approx(300.0)
    assert mini_case.constraints.official_realized.producer_wlpr_max == pytest.approx(500.0)
    assert mini_case.constraints.search_bounds.producer_lrat_max == pytest.approx(500.0)
    assert mini_case.constraints.compensation.status == "NOT_APPLICABLE"


def test_unresolved_compensation_definition_fails_closed(
    mini_case_dir: Path, case_payload: dict
) -> None:
    case_payload["constraints"]["compensation_gate"] = "REQUIRED"
    (mini_case_dir / "case.json").write_text(json.dumps(case_payload), encoding="utf-8")

    with pytest.raises(CaseError, match="COMPENSATION_DEFINITION_UNRESOLVED"):
        load_case(mini_case_dir / "case.json")


def _chdd_payload(*, annual: list[dict]) -> dict:
    return {
        "version": "7.0.2-negative-row-filter",
        "startDate": "2014-01-01",
        "summary": {
            "totalChddM": 1030.0,
            "profitabilityIndex": 1.2,
            "totalOilKt": 1.0,
            "totalLiquidKt": 2.0,
            "totalInjectionK": 3.0,
            "pumpChanges": 0,
            "conversionCount": 0,
        },
        "diagnostics": {
            "excludedNegativeRows": 0,
            "duplicateWellMonths": 0,
            "invalidDates": 0,
            "ignoredBlankWells": 0,
            "warnings": [],
        },
        "annual": annual,
        "fieldMonthly": [{"month": "2014-12"}],
    }


def test_yearly_chdd_profile_preserves_monetary_rows() -> None:
    result = parse_chdd_result(
        _chdd_payload(
            annual=[
                {
                    "year": 2014,
                    "months": 12,
                    "activeWellMonths": 10,
                    "stopCount": 0,
                    "startCount": 1,
                    "chddM": 400.25,
                    "cumulativeChddM": 400.25,
                    "oilKt": 11.0,
                }
            ]
        ),
        provenance="official-package/test",
    )

    assert result.yearly_profile[0].year == 2014
    assert result.yearly_profile[0].npv_mln_rub == pytest.approx(400.25)
    assert result.yearly_profile[0].cumulative_mln_rub == pytest.approx(400.25)
    assert result.yearly_profile[0].metadata["oilKt"] == 11.0
    assert result.to_json()["yearly_profile"][0]["npv_mln_rub"] == pytest.approx(400.25)


def test_missing_yearly_monetary_value_is_rejected() -> None:
    with pytest.raises(ChddResultSchemaError, match="annual\\[0\\].yearly NPV"):
        parse_chdd_result(
            _chdd_payload(
                annual=[
                    {
                        "year": 2014,
                        "months": 12,
                        "activeWellMonths": 10,
                        "stopCount": 0,
                        "startCount": 1,
                    }
                ]
            ),
            provenance="official-package/test",
        )


def _block(keyword: str, dtype: str, values: list[int]) -> bytes:
    head = struct.pack(">i", 16)
    head += keyword.ljust(8).encode("ascii")
    head += struct.pack(">i", len(values))
    head += dtype.ljust(4).encode("ascii")
    head += struct.pack(">i", 16)
    if not values:
        return head
    payload = b"".join(struct.pack(">i", int(v)) for v in values)
    return head + struct.pack(">i", len(payload)) + payload + struct.pack(">i", len(payload))


def _restart_record(seqnum: int, day: int, month: int, year: int) -> bytes:
    values = [0] * 411
    values[64], values[65], values[66] = day, month, year
    return (
        _block("SEQNUM", "INTE", [seqnum])
        + _block("INTEHEAD", "INTE", values)
        + _block("IWEL", "INTE", [1])
    )


def test_restart_primitive_selects_exact_state_and_records_policy(tmp_path: Path) -> None:
    from aios.opm.restart import RestartRiskPolicy, find_restart_record, read_restart_index

    path = tmp_path / "SOURCE.UNRST"
    path.write_bytes(_restart_record(80, 1, 1, 2014) + _restart_record(83, 1, 4, 2014))

    record = find_restart_record(read_restart_index(path), datetime.date(2014, 4, 1))
    policy = RestartRiskPolicy.model_y_spike_001abc()

    assert record.seqnum == 83
    assert policy.final_claim_requires_full_run is True
    assert policy.to_json()["source"] == "MAS-SPIKE-001A/B/C"


def test_claimed_npv_never_accepts_restart_execution(mini_case: Case) -> None:
    report = PipelineReport(
        case_path=Path("case.json"),
        policy_id=DEFAULT_POLICY_ID,
        case=mini_case,
        case_loaded=True,
        history_loaded=True,
        constraints_loaded=True,
        schedule_valid=True,
        simulation_pass=True,
        chdd_pass=True,
        baseline_reconciled=True,
        schedule_sha256="a" * 64,
        chdd_result=parse_chdd_result(
            _chdd_payload(
                annual=[
                    {
                        "year": 2014,
                        "months": 1,
                        "activeWellMonths": 1,
                        "stopCount": 0,
                        "startCount": 0,
                        "chddM": 1.0,
                    }
                ]
            ),
            provenance="official-package/test",
        ),
        execution_mode="restart",
    )

    with pytest.raises(ValueError, match="FINAL_CLAIM_REQUIRES_FULL_RUN"):
        claimed_npv_payload(report)


def test_result_package_has_separate_deterministic_responsibilities() -> None:
    assert SCHEDULE_FILENAME == "wells_schedule.inc"
    assert CLAIMED_NPV_FILENAME == "claimed_npv.json"
    assert YEARLY_NPV_PROFILE_FILENAME == "yearly_npv_profile.json"
    assert CONSTRAINT_REPORT_FILENAME == "constraint_report.json"
    assert RUN_PROVENANCE_FILENAME == "run_provenance.json"
