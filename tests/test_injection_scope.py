"""ScopeAuditor checks donor/recipient/regions against the current StateCard."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.specialists.injection.scope import ScopeAuditor, ScopeError
from aios.state import WellRole, WellStateSnapshot
from tests.support.mas_team_fixtures import synthetic_injection_envelope, synthetic_state_card


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


def _reallocate(*, donor: str, recipient: str, region: str) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        donor_wells=(donor,),
        recipient_wells=(recipient,),
        source_regions=(region,),
        target_regions=(region,),
        evidence_ids=("SYN-pressure-1",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Move support toward the weaker offset producer.",
    )


def _injector(well_id: str) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id=well_id,
        role=WellRole.INJECTOR,
        status="OPEN",
        current_target=150.0,
        control_mode="RATE",
        bhp=250.0,
        wlpr=0.0,
        wopr=0.0,
        wwpr=0.0,
        wwir=150.0,
        wct=0.0,
        wopt=0.0,
        wwpt=0.0,
        wwit=700.0,
        producer_bhp_margin=None,
        injector_bhp_margin=50.0,
        wlpr_margin=None,
    )


def _two_injector_envelope(*, i2_block: str = "SYN-B1"):
    base = synthetic_state_card()
    wells = (*base.wells, _injector("SYN-I2"))
    membership = {**dict(base.geology.block_membership), "SYN-I2": i2_block}
    card = replace(base, wells=wells, geology=replace(base.geology, block_membership=membership))
    return synthetic_injection_envelope(state_card=card)


def test_hold_needs_no_wells_or_regions() -> None:
    ScopeAuditor().audit((_hold(),), synthetic_injection_envelope())


def test_reallocate_accepts_injectors_on_the_card() -> None:
    intents = ScopeAuditor().audit(
        (_reallocate(donor="SYN-I1", recipient="SYN-I2", region="SYN-B1"),),
        _two_injector_envelope(),
    )
    assert intents[0].donor_wells == ("SYN-I1",)
    assert intents[0].recipient_wells == ("SYN-I2",)


def test_rejects_well_missing_from_state_card() -> None:
    with pytest.raises(ScopeError, match="not in the current StateCard"):
        ScopeAuditor().audit(
            (_reallocate(donor="I3", recipient="SYN-I1", region="SYN-B1"),),
            synthetic_injection_envelope(),
        )


def test_rejects_producer_as_injection_donor() -> None:
    with pytest.raises(ScopeError, match="not an injector"):
        ScopeAuditor().audit(
            (_reallocate(donor="SYN-P1", recipient="SYN-I1", region="SYN-B1"),),
            synthetic_injection_envelope(),
        )


def test_rejects_unknown_region() -> None:
    with pytest.raises(ScopeError, match="not in StateCard geology"):
        ScopeAuditor().audit(
            (_reallocate(donor="SYN-I1", recipient="SYN-I2", region="B1"),),
            _two_injector_envelope(),
        )


def test_rejects_well_outside_cited_region() -> None:
    envelope = _two_injector_envelope(i2_block="SYN-B2")
    with pytest.raises(ScopeError, match="belongs to"):
        ScopeAuditor().audit(
            (_reallocate(donor="SYN-I1", recipient="SYN-I2", region="SYN-B1"),),
            envelope,
        )
