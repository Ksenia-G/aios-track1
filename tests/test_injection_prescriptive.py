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


def test_control_authority_keys_are_prescriptive() -> None:
    """A control name, or a directive word carrying a magnitude, is refused."""
    assert first_prescriptive_payload_key({"surface_rate": 240}) == "payload.surface_rate"
    assert first_prescriptive_payload_key({"producer_control": 420}) == "payload.producer_control"
    assert first_prescriptive_payload_key({"setpoint": 300}) == "payload.setpoint"
    # `target` carrying a magnitude is a setpoint ...
    assert first_prescriptive_payload_key({"target": 240}) == "payload.target"


def test_unknown_diagnostic_key_is_not_prescriptive_by_itself() -> None:
    """Open world: an unfamiliar diagnostic key is not a command.

    A closed allowlist made every schema change a specialist outage, and it
    rejected the only real diagnostics this repository produces.
    """
    assert first_prescriptive_payload_key({"pressure_condition": "PRESSURE_SUPPORT_NEEDED"}) is None
    assert first_prescriptive_payload_key({"response_strength": 0.42}) is None
    assert first_prescriptive_payload_key({"connectivity_confidence": {"SYN-I1": 0.8}}) is None
    # ... but `target` referencing wells is a fact, which is the real shape.
    assert first_prescriptive_payload_key({"target": ["SYN-I1", "SYN-I2"]}) is None


def test_real_repository_diagnostics_are_not_prescriptive() -> None:
    from aios.diagnostics import PressureDiagnostic, SweepDiagnostic
    from tests.support.mas_team_fixtures import synthetic_state_card

    card = synthetic_state_card()
    assert first_prescriptive_payload_key(PressureDiagnostic().propose(card).to_json()) is None
    assert first_prescriptive_payload_key(SweepDiagnostic().propose(card).to_json()) is None


def test_observational_payload_is_allowed() -> None:
    assert (
        first_prescriptive_payload_key({"wbhp": 180.0, "wwir": 150.0, "status": "UNRESOLVED"})
        is None
    )


def test_setpoint_smuggled_in_allowed_note_is_prescriptive() -> None:
    assert first_prescriptive_payload_key({"note": "inject 240 sm3/d"}) == "payload.note"


# ---------------------------------------------------------------------------
# COMPLETION I1 -- NO SETPOINTS CROSS, not NO DIGITS CROSS.
# Every real identifier in this system contains a digit, so a blanket digit
# ban made any grounded rationale impossible.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Move support toward SYN-I1.",
        "Pressure support in B2 is weakening.",
        "Evidence for SYN-I1 supports reallocation.",
        "Injector I10 shows weaker response.",
        "Fault F3 separates the regions.",
        "Region BLOCK-12 has poorer sweep.",
        "Pressure support is weakening.",
        "Donor SYN-I1 and recipient SYN-I2 sit in block SYN-B1.",
    ],
)
def test_identifiers_containing_digits_survive(text: str) -> None:
    reject_prescriptive_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Inject 240 m3/day into SYN-I1.",
        "Increase injection by 20 percent.",
        "Set BHP to 300 bar.",
        "Use 150 for the donor.",
        "RATE=240",
        "WCONINJE OPEN 240",
        "Set injection to 240 m3/day",
        "Move 20 percent to SYN-I2",
        "Set rate: 240",
        "surface rate to 240",
        "Target 240 m3/day",
        "Increase injection to 240",
    ],
)
def test_true_numerical_setpoints_are_rejected(text: str) -> None:
    with pytest.raises(PrescriptiveTextError):
        reject_prescriptive_text(text)
