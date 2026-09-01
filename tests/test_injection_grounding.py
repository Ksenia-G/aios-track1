"""GroundingAuditor accepts only evidence_ids that exist in EvidenceContext."""

from __future__ import annotations

import pytest

from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.specialists.injection.evidence_builder import EvidenceBuilder
from aios.mas.specialists.injection.grounding import GroundingAuditor, GroundingError
from tests.support.mas_team_fixtures import synthetic_injection_envelope


def _hold(*, evidence_ids: tuple[str, ...] = ()) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=evidence_ids,
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )


def _reallocate(*evidence_ids: str) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        donor_wells=("I3",),
        recipient_wells=("I1",),
        source_regions=("B1",),
        target_regions=("B1",),
        evidence_ids=evidence_ids,
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Move support toward the weaker offset producer.",
    )


def _evidence():
    return EvidenceBuilder().build(synthetic_injection_envelope())


def test_hold_without_evidence_passes() -> None:
    report = GroundingAuditor().audit((_hold(),), _evidence())

    assert report.passed is True
    assert report.grounded_evidence_ids == ()
    assert report.unknown_evidence_ids == ()


def test_known_evidence_ids_are_grounded() -> None:
    report = GroundingAuditor().audit((_reallocate("SYN-pressure-1"),), _evidence())

    assert report.passed is True
    assert report.grounded_evidence_ids == ("SYN-pressure-1",)
    assert report.unknown_evidence_ids == ()


def test_duplicates_are_recorded_once_in_first_seen_order() -> None:
    report = GroundingAuditor().audit(
        (
            _reallocate("SYN-sweep-1", "SYN-pressure-1"),
            _hold(evidence_ids=("SYN-pressure-1", "SYN-memory-1")),
        ),
        _evidence(),
    )

    assert report.passed is True
    assert report.grounded_evidence_ids == (
        "SYN-sweep-1",
        "SYN-pressure-1",
        "SYN-memory-1",
    )


def test_unknown_evidence_id_rejects_the_whole_set() -> None:
    with pytest.raises(GroundingError, match="unknown evidence_ids") as caught:
        GroundingAuditor().audit((_reallocate("SYN-missing"),), _evidence())

    assert caught.value.report is not None
    assert caught.value.report.passed is False
    assert caught.value.report.unknown_evidence_ids == ("SYN-missing",)


def test_mixed_known_and_unknown_is_rejected_entirely() -> None:
    with pytest.raises(GroundingError) as caught:
        GroundingAuditor().audit(
            (_hold(), _reallocate("SYN-pressure-1", "SYN-ghost")),
            _evidence(),
        )

    assert caught.value.report is not None
    assert caught.value.report.grounded_evidence_ids == ("SYN-pressure-1",)
    assert caught.value.report.unknown_evidence_ids == ("SYN-ghost",)


def test_rejects_empty_intents() -> None:
    with pytest.raises(GroundingError, match="must not be empty"):
        GroundingAuditor().audit((), _evidence())


def test_same_intents_are_reproducible() -> None:
    auditor = GroundingAuditor()
    intents = (_reallocate("SYN-pressure-1", "SYN-sweep-1"),)
    evidence = _evidence()

    first = auditor.audit(intents, evidence)
    second = auditor.audit(intents, evidence)

    assert first == second
    assert first.passed is True
