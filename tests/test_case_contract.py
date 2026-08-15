"""Кейс: разбор, относительные пути и инварианты Track 1."""

import json
from datetime import date
from pathlib import Path

import pytest

from aios.cases import CaseError, load_case, parse_case


def test_case_parses_all_sections(mini_case_dir: Path) -> None:
    case = load_case(mini_case_dir / "case.json")

    assert case.case_id == "mini"
    assert case.track == 1
    assert case.management_start_date == date(2020, 2, 1)
    assert case.management_end_date == date(2020, 4, 1)
    assert case.constraints.allowed_producer_controls == ("LRAT",)
    assert case.constraints.allowed_injector_controls == ("RATE",)
    assert case.economics.provenance == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
    assert case.simulator.docker_image.startswith("example/flow@sha256:")


def test_relative_paths_resolve_against_the_case_file(mini_case_dir: Path) -> None:
    case = load_case(mini_case_dir / "case.json")

    assert case.history.deck_root == (mini_case_dir / "deck").resolve()
    assert case.history.data_path.is_file()
    assert case.history.schedule_path.is_file()


def test_acceptance_section_is_optional(mini_case_dir: Path, case_payload: dict) -> None:
    assert load_case(mini_case_dir / "case.json").acceptance is None

    case_payload["acceptance"] = {
        "baseline_npv_mln_rub": 1030.877696,
        "npv_tolerance_mln_rub": 1e-6,
        "reference": "lab reference",
    }
    case = parse_case(case_payload, base_dir=mini_case_dir)

    assert case.acceptance is not None
    assert case.acceptance.npv_tolerance_mln_rub == pytest.approx(1e-6)


def test_missing_section_is_rejected(mini_case_dir: Path, case_payload: dict) -> None:
    del case_payload["constraints"]

    with pytest.raises(CaseError, match="constraints"):
        parse_case(case_payload, base_dir=mini_case_dir)


def test_other_tracks_are_rejected(mini_case_dir: Path, case_payload: dict) -> None:
    case_payload["track"] = 2

    with pytest.raises(CaseError, match="track"):
        parse_case(case_payload, base_dir=mini_case_dir)


def test_wlpr_limit_above_the_hard_cap_is_rejected(mini_case_dir: Path, case_payload: dict) -> None:
    case_payload["constraints"]["producer_wlpr_max"] = 500.0001

    with pytest.raises(CaseError, match="hard CHDD limit"):
        parse_case(case_payload, base_dir=mini_case_dir)


def test_conversion_cannot_be_enabled_on_track_1(mini_case_dir: Path, case_payload: dict) -> None:
    case_payload["constraints"]["allow_producer_to_injector"] = True

    with pytest.raises(CaseError, match="conversion is forbidden"):
        parse_case(case_payload, base_dir=mini_case_dir)


def test_backwards_window_is_rejected(mini_case_dir: Path, case_payload: dict) -> None:
    case_payload["management_end_date"] = "2019-01-01"

    with pytest.raises(CaseError, match="management_end_date"):
        parse_case(case_payload, base_dir=mini_case_dir)


def test_broken_json_is_reported_as_a_case_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(CaseError, match="not valid JSON"):
        load_case(broken)


def test_case_round_trips_through_json(mini_case_dir: Path) -> None:
    case = load_case(mini_case_dir / "case.json")

    payload = json.loads(json.dumps(case.to_json(), ensure_ascii=False))

    assert payload["case_id"] == case.case_id
    assert payload["management_start_date"] == "2020-02-01"
    assert payload["constraints"]["allow_producer_to_injector"] is False
