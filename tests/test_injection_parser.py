"""StrictOutputParser accepts only frozen-vocabulary JSON intents."""

from __future__ import annotations

import json

import pytest

from aios.mas.intents import InjectionAction
from aios.mas.specialists.injection.parser import ParseError, StrictOutputParser


def _hold_payload(**overrides: object) -> dict[str, object]:
    intent: dict[str, object] = {
        "action": "HOLD",
        "direction": "NONE",
        "intensity": "NONE",
        "donor_wells": [],
        "recipient_wells": [],
        "source_regions": [],
        "target_regions": [],
        "evidence_ids": [],
        "uncertainty": "LOW",
        "rationale_short": "",
    }
    intent.update(overrides)
    return {"intents": [intent]}


def _reallocate_payload() -> dict[str, object]:
    return {
        "intents": [
            {
                "action": "REALLOCATE_INJECTION",
                "direction": "NONE",
                "intensity": "SMALL",
                "donor_wells": ["I3"],
                "recipient_wells": ["I1"],
                "source_regions": ["B1"],
                "target_regions": ["B1"],
                "evidence_ids": ["SYN-pressure-1"],
                "uncertainty": "MEDIUM",
                "rationale_short": "Move support toward the weaker offset producer.",
            }
        ]
    }


def test_parses_hold_intent() -> None:
    intents = StrictOutputParser().parse(json.dumps(_hold_payload()))

    assert len(intents) == 1
    assert intents[0].action is InjectionAction.HOLD
    assert intents[0].evidence_ids == ()


def test_parses_reallocate_intent() -> None:
    intents = StrictOutputParser().parse(json.dumps(_reallocate_payload()))

    assert intents[0].action is InjectionAction.REALLOCATE_INJECTION
    assert intents[0].evidence_ids == ("SYN-pressure-1",)


def test_rejects_invalid_json() -> None:
    with pytest.raises(ParseError, match="not valid JSON"):
        StrictOutputParser().parse("{not json")


def test_rejects_markdown_fence() -> None:
    with pytest.raises(ParseError, match="JSON object"):
        StrictOutputParser().parse("```json\n" + json.dumps(_hold_payload()) + "\n```")


def test_rejects_empty_intents() -> None:
    with pytest.raises(ParseError, match="non-empty list"):
        StrictOutputParser().parse(json.dumps({"intents": []}))


def test_rejects_unknown_action() -> None:
    with pytest.raises(ParseError, match="invalid vocabulary"):
        StrictOutputParser().parse(json.dumps(_hold_payload(action="CONVERT_TO_INJECTOR")))


def test_rejects_surface_rate_setpoint_key() -> None:
    payload = _hold_payload()
    payload["intents"][0]["surface_rate"] = 240  # type: ignore[index]
    with pytest.raises(ParseError, match="prescriptive key 'surface_rate'"):
        StrictOutputParser().parse(json.dumps(payload))


def test_rejects_surface_rate_in_rationale() -> None:
    with pytest.raises(ParseError, match="not a valid InjectionIntent"):
        StrictOutputParser().parse(
            json.dumps(_hold_payload(rationale_short="Keep RATE=240 on the donor injector."))
        )


@pytest.mark.parametrize(
    "rationale",
    [
        "Set the donor injector to 240 sm3/d.",
        "Increase injection by 20 percent.",
        "Hold a BHP limit of 300 bar.",
    ],
)
def test_rejects_natural_language_setpoints_in_rationale(rationale: str) -> None:
    with pytest.raises(ParseError, match="not a valid InjectionIntent"):
        StrictOutputParser().parse(json.dumps(_hold_payload(rationale_short=rationale)))


def test_allows_observational_wbhp_in_rationale() -> None:
    intents = StrictOutputParser().parse(
        json.dumps(_hold_payload(rationale_short="Offset producer WBHP margin is still healthy."))
    )
    assert "WBHP" in intents[0].rationale_short


def test_rejects_extra_intent_keys() -> None:
    with pytest.raises(ParseError, match="unexpected keys"):
        StrictOutputParser().parse(json.dumps(_hold_payload(net_total_effect="ZERO")))


def test_rejects_mixed_batch_entirely() -> None:
    payload = {
        "intents": [
            _hold_payload()["intents"][0],
            _hold_payload(action="CONVERT_TO_INJECTOR")["intents"][0],
        ]
    }
    with pytest.raises(ParseError):
        StrictOutputParser().parse(json.dumps(payload))
