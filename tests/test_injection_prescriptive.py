"""Natural-language and payload setpoint classes are rejected, not one regex."""

from __future__ import annotations

import pytest

from aios.mas.specialists.injection.prescriptive import (
    PrescriptiveTextError,
    first_prescriptive_payload_key,
    reject_prescriptive_text,
)


@pytest.mark.parametrize(
    "text",
    [
        "Keep RATE=240 on the donor injector.",
        "Set the donor injector to 240 sm3/d.",
        "Increase injection by 20 percent.",
        "Hold a BHP limit of 300 bar.",
        "Give I1 about 150 cubic metres per day.",
        "Open WCONINJE on the weak flank.",
    ],
)
def test_natural_language_setpoints_are_rejected(text: str) -> None:
    with pytest.raises(PrescriptiveTextError):
        reject_prescriptive_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Move support toward the weaker offset producer.",
        "Offset producer WBHP margin is still healthy.",
        "Field pressure support is weakening.",
        "",
    ],
)
def test_qualitative_rationale_is_allowed(text: str) -> None:
    reject_prescriptive_text(text)


def test_unknown_payload_key_is_prescriptive() -> None:
    assert first_prescriptive_payload_key({"target": 240}) == "target"
    assert first_prescriptive_payload_key({"surface_rate": 240}) == "surface_rate"


def test_observational_payload_is_allowed() -> None:
    assert (
        first_prescriptive_payload_key({"wbhp": 180.0, "wwir": 150.0, "status": "UNRESOLVED"})
        is None
    )


def test_setpoint_smuggled_in_allowed_note_is_prescriptive() -> None:
    assert first_prescriptive_payload_key({"note": "inject 240 sm3/d"}) == "note"
