"""ProductionIntent / InjectionIntent -- the NO SETPOINTS CROSS boundary.

Специалисты предлагают структурные намерения: действие, направление,
качественная интенсивность, целевые скважины/регионы, evidence. Никаких
числовых setpoint-полей здесь быть не может -- это дизайн-инвариант, а не
случайность, и он проверяется структурно через dataclasses.fields.
"""

from __future__ import annotations

import dataclasses

import pytest

from aios.mas.common import Direction, Intensity, NetTotalEffect, UncertaintyLevel
from aios.mas.intents import (
    InjectionAction,
    InjectionIntent,
    IntentError,
    ProductionAction,
    ProductionIntent,
    net_total_effect_for,
)

FORBIDDEN_NUMERIC_FIELD_NAMES = {
    "liquid_rate",
    "lrat",
    "bhp",
    "bhp_limit",
    "target_rate",
    "surface_rate",
    "injection_rate",
    "rate",
    "setpoint",
}


def _production_hold() -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        target_wells=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )


def _production_increase(**overrides: object) -> ProductionIntent:
    fields = {
        "action": ProductionAction.INCREASE_OFFTAKE,
        "direction": Direction.UP,
        "intensity": Intensity.MODERATE,
        "target_wells": ("P1", "P2"),
        "target_regions": (),
        "evidence_ids": ("ev-1",),
        "uncertainty": UncertaintyLevel.MEDIUM,
        "rationale_short": "WBHP margin is healthy across the block.",
    }
    fields.update(overrides)
    return ProductionIntent(**fields)  # type: ignore[arg-type]


def _injection_hold() -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )


def _injection_reallocate(**overrides: object) -> InjectionIntent:
    fields = {
        "action": InjectionAction.REALLOCATE_INJECTION,
        "direction": Direction.NONE,
        "intensity": Intensity.SMALL,
        "donor_wells": ("I3",),
        "recipient_wells": ("I1",),
        "source_regions": ("B1",),
        "target_regions": ("B1",),
        "evidence_ids": ("ev-2",),
        "uncertainty": UncertaintyLevel.MEDIUM,
        "rationale_short": "Move support toward the weaker offset producer.",
    }
    fields.update(overrides)
    return InjectionIntent(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# C15: no numerical setpoint fields anywhere on the intent contracts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [ProductionIntent, InjectionIntent])
def test_intent_has_no_numerical_setpoint_fields(cls: type) -> None:
    field_names = {f.name.lower() for f in dataclasses.fields(cls)}
    overlap = field_names & FORBIDDEN_NUMERIC_FIELD_NAMES
    assert overlap == set(), f"{cls.__name__} exposes forbidden setpoint fields: {overlap}"
    for f in dataclasses.fields(cls):
        assert f.type not in ("float", "float | None"), (
            f"{cls.__name__}.{f.name} is a float field: setpoints are forbidden here"
        )


# ---------------------------------------------------------------------------
# C6: ProductionIntent HOLD cannot carry targets/intensity/direction.
# ---------------------------------------------------------------------------


def test_production_hold_is_valid_when_empty() -> None:
    _production_hold()


@pytest.mark.parametrize(
    "override",
    [
        {"direction": Direction.UP},
        {"intensity": Intensity.SMALL},
        {"target_wells": ("P1",)},
        {"target_regions": ("B1",)},
    ],
)
def test_production_hold_rejects_non_empty_fields(override: dict[str, object]) -> None:
    fields = {
        "action": ProductionAction.HOLD,
        "direction": Direction.NONE,
        "intensity": Intensity.NONE,
        "target_wells": (),
        "target_regions": (),
        "evidence_ids": (),
        "uncertainty": UncertaintyLevel.LOW,
        "rationale_short": "",
    }
    fields.update(override)
    with pytest.raises(IntentError):
        ProductionIntent(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# C7: InjectionIntent HOLD cannot carry donors/recipients/regions/intensity/direction.
# ---------------------------------------------------------------------------


def test_injection_hold_is_valid_when_empty() -> None:
    _injection_hold()


@pytest.mark.parametrize(
    "override",
    [
        {"direction": Direction.UP},
        {"intensity": Intensity.SMALL},
        {"donor_wells": ("I1",)},
        {"recipient_wells": ("I2",)},
        {"source_regions": ("B1",)},
        {"target_regions": ("B1",)},
    ],
)
def test_injection_hold_rejects_non_empty_fields(override: dict[str, object]) -> None:
    fields = {
        "action": InjectionAction.HOLD,
        "direction": Direction.NONE,
        "intensity": Intensity.NONE,
        "donor_wells": (),
        "recipient_wells": (),
        "source_regions": (),
        "target_regions": (),
        "evidence_ids": (),
        "uncertainty": UncertaintyLevel.LOW,
        "rationale_short": "",
    }
    fields.update(override)
    with pytest.raises(IntentError):
        InjectionIntent(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# C8 / C9: non-HOLD requires evidence and rationale.
# ---------------------------------------------------------------------------


def test_production_non_hold_requires_evidence() -> None:
    with pytest.raises(IntentError):
        _production_increase(evidence_ids=())


def test_production_non_hold_requires_rationale() -> None:
    with pytest.raises(IntentError):
        _production_increase(rationale_short="")


def test_injection_non_hold_requires_evidence() -> None:
    with pytest.raises(IntentError):
        _injection_reallocate(evidence_ids=())


def test_injection_non_hold_requires_rationale() -> None:
    with pytest.raises(IntentError):
        _injection_reallocate(rationale_short="")


def test_rationale_short_must_stay_short() -> None:
    with pytest.raises(IntentError):
        _production_increase(rationale_short="Sentence one. Sentence two. Sentence three.")
    with pytest.raises(IntentError):
        _production_increase(rationale_short="x" * 500)


# ---------------------------------------------------------------------------
# C10 / C11: action/direction mismatch rejected.
# ---------------------------------------------------------------------------


def test_production_action_direction_mismatch_rejected() -> None:
    with pytest.raises(IntentError):
        _production_increase(direction=Direction.DOWN)
    with pytest.raises(IntentError):
        ProductionIntent(
            action=ProductionAction.REDUCE_OFFTAKE,
            direction=Direction.UP,
            intensity=Intensity.SMALL,
            target_wells=("P1",),
            target_regions=(),
            evidence_ids=("ev-1",),
            uncertainty=UncertaintyLevel.LOW,
            rationale_short="wrong direction",
        )


def test_injection_action_direction_mismatch_rejected() -> None:
    with pytest.raises(IntentError):
        InjectionIntent(
            action=InjectionAction.SUPPORT_REGION,
            direction=Direction.DOWN,
            intensity=Intensity.SMALL,
            donor_wells=(),
            recipient_wells=("I1",),
            source_regions=(),
            target_regions=(),
            evidence_ids=("ev-1",),
            uncertainty=UncertaintyLevel.LOW,
            rationale_short="wrong direction",
        )


# ---------------------------------------------------------------------------
# C12 / C13: region scope rules for REALLOCATE_INJECTION / REBALANCE_ACROSS_BLOCKS.
# ---------------------------------------------------------------------------


def test_reallocate_injection_requires_same_region_scope() -> None:
    _injection_reallocate()
    with pytest.raises(IntentError):
        _injection_reallocate(source_regions=("B1",), target_regions=("B2",))


def test_rebalance_across_blocks_requires_distinct_region_scope() -> None:
    intent = InjectionIntent(
        action=InjectionAction.REBALANCE_ACROSS_BLOCKS,
        direction=Direction.NONE,
        intensity=Intensity.MODERATE,
        donor_wells=("I3",),
        recipient_wells=("I1",),
        source_regions=("B2",),
        target_regions=("B1",),
        evidence_ids=("ev-3",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Support the weaker block from surplus capacity.",
    )
    assert intent.action is InjectionAction.REBALANCE_ACROSS_BLOCKS
    with pytest.raises(IntentError):
        InjectionIntent(
            action=InjectionAction.REBALANCE_ACROSS_BLOCKS,
            direction=Direction.NONE,
            intensity=Intensity.MODERATE,
            donor_wells=("I3",),
            recipient_wells=("I1",),
            source_regions=("B1",),
            target_regions=("B1",),
            evidence_ids=("ev-3",),
            uncertainty=UncertaintyLevel.MEDIUM,
            rationale_short="Same scope should not be accepted here.",
        )


def test_reallocate_and_rebalance_reject_missing_well_or_region_refs() -> None:
    with pytest.raises(IntentError):
        _injection_reallocate(donor_wells=())
    with pytest.raises(IntentError):
        _injection_reallocate(recipient_wells=())
    with pytest.raises(IntentError):
        _injection_reallocate(source_regions=())
    with pytest.raises(IntentError):
        _injection_reallocate(target_regions=())


# ---------------------------------------------------------------------------
# C14: NetTotalEffect derived metadata is deterministic and correct.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (InjectionAction.HOLD, NetTotalEffect.ZERO),
        (InjectionAction.REALLOCATE_INJECTION, NetTotalEffect.ZERO),
        (InjectionAction.SCALE_TOTAL_INJECTION, NetTotalEffect.DIRECTIONAL),
        (InjectionAction.REDUCE_SUPPORT_PATH, NetTotalEffect.DECREASE),
        (InjectionAction.SUPPORT_REGION, NetTotalEffect.INCREASE),
        (InjectionAction.REBALANCE_ACROSS_BLOCKS, NetTotalEffect.ZERO),
    ],
)
def test_net_total_effect_for_action(action: InjectionAction, expected: NetTotalEffect) -> None:
    assert net_total_effect_for(action) is expected


def test_injection_intent_exposes_net_total_effect_property() -> None:
    assert _injection_hold().net_total_effect is NetTotalEffect.ZERO
    assert _injection_reallocate().net_total_effect is NetTotalEffect.ZERO


# ---------------------------------------------------------------------------
# C16 / C17: digest is invariant to semantically irrelevant tuple ordering.
# ---------------------------------------------------------------------------


def test_production_intent_digest_ignores_tuple_ordering() -> None:
    a = _production_increase(target_wells=("P1", "P2"), evidence_ids=("ev-1", "ev-2"))
    b = _production_increase(target_wells=("P2", "P1"), evidence_ids=("ev-2", "ev-1"))
    assert a.digest() == b.digest()


def test_injection_intent_digest_ignores_tuple_ordering() -> None:
    a = _injection_reallocate(donor_wells=("I3", "I4"), recipient_wells=("I1", "I2"))
    b = InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        donor_wells=("I4", "I3"),
        recipient_wells=("I2", "I1"),
        source_regions=("B1",),
        target_regions=("B1",),
        evidence_ids=("ev-2",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Move support toward the weaker offset producer.",
    )
    assert a.digest() == b.digest()


def test_production_intent_digest_changes_with_action() -> None:
    hold = _production_hold()
    increase = _production_increase()
    assert hold.digest() != increase.digest()
