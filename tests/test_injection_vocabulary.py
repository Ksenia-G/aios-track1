"""VocabularyValidator accepts only frozen InjectionAction/Direction/Intensity."""

from __future__ import annotations

import pytest

from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.specialists.injection.vocabulary import VocabularyError, VocabularyValidator


def _hold() -> InjectionIntent:
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


def _reallocate() -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        donor_wells=("I3",),
        recipient_wells=("I1",),
        source_regions=("B1",),
        target_regions=("B1",),
        evidence_ids=("ev-2",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Move support toward the weaker offset producer.",
    )


def _scale() -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.SCALE_TOTAL_INJECTION,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=("ev-2",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="Field pressure support is weakening.",
    )


def test_validate_labels_accepts_frozen_members() -> None:
    action, direction, intensity = VocabularyValidator().validate_labels(
        "REALLOCATE_INJECTION", "NONE", "SMALL"
    )
    assert action is InjectionAction.REALLOCATE_INJECTION
    assert direction is Direction.NONE
    assert intensity is Intensity.SMALL


def test_rejects_unknown_action_label() -> None:
    with pytest.raises(VocabularyError, match="action"):
        VocabularyValidator().validate_labels("CONVERT_TO_INJECTOR", "NONE", "NONE")


def test_rejects_unknown_direction_label() -> None:
    with pytest.raises(VocabularyError, match="direction"):
        VocabularyValidator().validate_labels("HOLD", "LEFT", "NONE")


def test_rejects_unknown_intensity_label() -> None:
    with pytest.raises(VocabularyError, match="intensity"):
        VocabularyValidator().validate_labels("HOLD", "NONE", "EXTREME")


def test_accepts_intents_in_allowed_actions() -> None:
    intents = VocabularyValidator().validate_intents(
        (_hold(), _reallocate()),
        allowed_actions=(InjectionAction.HOLD, InjectionAction.REALLOCATE_INJECTION),
    )
    assert intents[0].action is InjectionAction.HOLD
    assert intents[1].action is InjectionAction.REALLOCATE_INJECTION


def test_rejects_action_outside_allowed_actions() -> None:
    with pytest.raises(VocabularyError, match="not in allowed_actions"):
        VocabularyValidator().validate_intents(
            (_scale(),),
            allowed_actions=(InjectionAction.HOLD, InjectionAction.REALLOCATE_INJECTION),
        )


def test_rejects_empty_intents_all_or_nothing() -> None:
    with pytest.raises(VocabularyError, match="must not be empty"):
        VocabularyValidator().validate_intents(
            (),
            allowed_actions=(InjectionAction.HOLD,),
        )


def test_mixed_batch_is_rejected_entirely() -> None:
    with pytest.raises(VocabularyError):
        VocabularyValidator().validate_intents(
            (_hold(), _scale()),
            allowed_actions=(InjectionAction.HOLD, InjectionAction.REALLOCATE_INJECTION),
        )


def test_rejects_rate_assignment_in_rationale() -> None:
    intent = InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        donor_wells=("I3",),
        recipient_wells=("I1",),
        source_regions=("B1",),
        target_regions=("B1",),
        evidence_ids=("ev-2",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Move RATE=240 from the donor to the recipient.",
    )
    with pytest.raises(VocabularyError, match="numerical setpoint"):
        VocabularyValidator().validate_intents(
            (intent,),
            allowed_actions=(InjectionAction.HOLD, InjectionAction.REALLOCATE_INJECTION),
        )
