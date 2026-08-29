"""Связность поддержки и региональный дисбаланс -- детерминированные диагностики.

Живой прогон PERF-003B отклонил перераспределение с явной причиной: в StateCard
не было ничего, что связывало бы нагнетательную скважину с поддерживаемыми ею
добывающими и показывало бы, где поддержки избыток, а где недостаток.

Связность здесь не выдумывается. Единственное основание -- общий отсек по
массиву регионов колоды: скважины разных отсеков не связываются, а внутри
отсека отношения ранжируются расстоянием между перфорациями. Это evidence, а
не утверждение о линиях тока, и ограничение записано в самой диагностике.

Численных управлений диагностики не содержат: они говорят «эти скважины
связаны» и «здесь поддержки больше, чем там», но никогда «закачивать столько».
"""

from __future__ import annotations

import pytest

from aios.diagnostics.support import (
    derive_support_connectivity,
    derive_support_imbalance,
)
from aios.state import GeologyContext, WellRole


class _Well:
    """Минимальный носитель наблюдаемого состояния скважины."""

    def __init__(
        self,
        well_id,
        role,
        *,
        wwir=None,
        wlpr=None,
        wopr=None,
        wct=None,
        injector_bhp_margin=None,
        current_target=None,
    ):
        self.well_id = well_id
        self.role = role
        self.wwir = wwir
        self.wlpr = wlpr
        self.wopr = wopr
        self.wct = wct
        self.injector_bhp_margin = injector_bhp_margin
        self.current_target = current_target


def _geology(mapping):
    return GeologyContext(block_membership=dict(mapping))


# ===========================================================================
# D1/D2 -- связность выводится из отсеков, межотсековая запрещена
# ===========================================================================


def test_wells_in_one_compartment_are_linked() -> None:
    wells = [
        _Well("I1", WellRole.INJECTOR, wwir=100.0),
        _Well("P1", WellRole.PRODUCER, wlpr=100.0),
        _Well("P2", WellRole.PRODUCER, wlpr=100.0),
    ]
    result = derive_support_connectivity(
        wells=wells, geology=_geology({"I1": "A", "P1": "A", "P2": "A"})
    )
    link = next(item for item in result.links if item.injector == "I1")
    assert set(link.supported_producers) == {"P1", "P2"}
    assert link.scope == "A"


def test_wells_in_different_compartments_are_never_linked() -> None:
    wells = [
        _Well("I1", WellRole.INJECTOR, wwir=100.0),
        _Well("P9", WellRole.PRODUCER, wlpr=100.0),
    ]
    result = derive_support_connectivity(wells=wells, geology=_geology({"I1": "A", "P9": "B"}))
    link = next(item for item in result.links if item.injector == "I1")
    assert link.supported_producers == ()


def test_connectivity_basis_is_named_explicitly() -> None:
    wells = [_Well("I1", WellRole.INJECTOR), _Well("P1", WellRole.PRODUCER)]
    result = derive_support_connectivity(wells=wells, geology=_geology({"I1": "A", "P1": "A"}))
    assert result.basis
    assert "compartment" in result.basis.lower()
    assert result.limitations


# ===========================================================================
# D3 -- детерминированность
# ===========================================================================


def test_connectivity_is_deterministic() -> None:
    wells = [
        _Well("I2", WellRole.INJECTOR),
        _Well("I1", WellRole.INJECTOR),
        _Well("P2", WellRole.PRODUCER),
        _Well("P1", WellRole.PRODUCER),
    ]
    geo = _geology({"I1": "A", "I2": "A", "P1": "A", "P2": "A"})
    first = derive_support_connectivity(wells=wells, geology=geo)
    second = derive_support_connectivity(wells=wells, geology=geo)
    assert first.to_json() == second.to_json()


# ===========================================================================
# D4 -- нет свидетельства -> нет выдуманной связности
# ===========================================================================


def test_missing_geology_yields_no_connectivity() -> None:
    wells = [_Well("I1", WellRole.INJECTOR), _Well("P1", WellRole.PRODUCER)]
    result = derive_support_connectivity(wells=wells, geology=GeologyContext())
    assert result.links == ()
    assert result.basis == "UNKNOWN"


def test_unassigned_well_is_not_linked() -> None:
    wells = [_Well("I1", WellRole.INJECTOR), _Well("PX", WellRole.PRODUCER)]
    result = derive_support_connectivity(wells=wells, geology=_geology({"I1": "A"}))
    link = next(item for item in result.links if item.injector == "I1")
    assert link.supported_producers == ()


# ===========================================================================
# D5 -- дисбаланс из реализованного состояния
# ===========================================================================


def _region(inj, prod):
    return derive_support_imbalance(
        wells=[*inj, *prod],
        geology=_geology({w.well_id: "A" for w in [*inj, *prod]}),
    )


def test_saturated_injector_is_not_recipient_like() -> None:
    """Нулевой запас по давлению -- физический факт: принять больше нельзя."""
    inj = [
        _Well("I1", WellRole.INJECTOR, wwir=200.0, injector_bhp_margin=0.0),
        _Well("I2", WellRole.INJECTOR, wwir=50.0, injector_bhp_margin=120.0),
    ]
    prod = [_Well("P1", WellRole.PRODUCER, wlpr=100.0, wopr=10.0, wct=0.9)]
    scope = _region(inj, prod).scopes[0]
    assert "I1" not in scope.recipient_like
    assert "I2" in scope.recipient_like


def test_high_injection_into_watered_out_group_is_donor_like() -> None:
    inj = [
        _Well("I1", WellRole.INJECTOR, wwir=300.0, injector_bhp_margin=50.0),
        _Well("I2", WellRole.INJECTOR, wwir=50.0, injector_bhp_margin=50.0),
    ]
    prod = [_Well("P1", WellRole.PRODUCER, wlpr=100.0, wopr=1.0, wct=0.99)]
    scope = _region(inj, prod).scopes[0]
    assert "I1" in scope.donor_like


def test_balanced_region_produces_no_forced_labels() -> None:
    """D13: симметричное состояние не выдумывает донора и получателя."""
    inj = [
        _Well("I1", WellRole.INJECTOR, wwir=100.0, injector_bhp_margin=50.0),
        _Well("I2", WellRole.INJECTOR, wwir=100.0, injector_bhp_margin=50.0),
    ]
    prod = [_Well("P1", WellRole.PRODUCER, wlpr=200.0, wopr=40.0, wct=0.5)]
    scope = _region(inj, prod).scopes[0]
    assert scope.donor_like == () or scope.recipient_like == ()


def test_single_injector_scope_offers_no_pair() -> None:
    inj = [_Well("I1", WellRole.INJECTOR, wwir=100.0, injector_bhp_margin=50.0)]
    prod = [_Well("P1", WellRole.PRODUCER, wlpr=100.0, wopr=10.0, wct=0.9)]
    scope = _region(inj, prod).scopes[0]
    assert not (scope.donor_like and scope.recipient_like)


# ===========================================================================
# D6 -- контракт единиц
# ===========================================================================


def test_support_ratio_is_named_as_a_surface_proxy() -> None:
    inj = [_Well("I1", WellRole.INJECTOR, wwir=100.0, injector_bhp_margin=10.0)]
    prod = [_Well("P1", WellRole.PRODUCER, wlpr=200.0, wopr=20.0, wct=0.9)]
    scope = _region(inj, prod).scopes[0]
    assert scope.surface_support_ratio == pytest.approx(0.5)
    payload = _region(inj, prod).to_json()
    assert "surface_support_ratio" in str(payload)
    assert "VRR" not in str(payload), "a surface ratio must not be called VRR"


def test_units_and_limitations_are_stated() -> None:
    result = _region(
        [_Well("I1", WellRole.INJECTOR, wwir=1.0, injector_bhp_margin=1.0)],
        [_Well("P1", WellRole.PRODUCER, wlpr=1.0, wopr=1.0, wct=0.1)],
    )
    assert result.limitations
    assert "surface" in result.limitations.lower()


def test_zero_withdrawal_does_not_divide_by_zero() -> None:
    scope = _region(
        [_Well("I1", WellRole.INJECTOR, wwir=100.0, injector_bhp_margin=10.0)],
        [_Well("P1", WellRole.PRODUCER, wlpr=0.0, wopr=0.0, wct=0.0)],
    ).scopes[0]
    assert scope.surface_support_ratio is None


# ===========================================================================
# D7 -- никаких численных управлений
# ===========================================================================


def test_diagnostics_expose_no_control_setpoints() -> None:
    result = _region(
        [_Well("I1", WellRole.INJECTOR, wwir=300.0, injector_bhp_margin=0.0)],
        [_Well("P1", WellRole.PRODUCER, wlpr=100.0, wopr=1.0, wct=0.99)],
    )
    from aios.mas.specialists.authority import find_prescriptive_control

    assert find_prescriptive_control(result.to_json(), "support_imbalance") is None


def test_connectivity_exposes_no_control_setpoints() -> None:
    from aios.mas.specialists.authority import find_prescriptive_control

    result = derive_support_connectivity(
        wells=[_Well("I1", WellRole.INJECTOR), _Well("P1", WellRole.PRODUCER)],
        geology=_geology({"I1": "A", "P1": "A"}),
    )
    assert find_prescriptive_control(result.to_json(), "support_connectivity") is None


# ===========================================================================
# D8 -- прослеживаемость
# ===========================================================================


def test_every_scope_names_the_wells_behind_its_conclusion() -> None:
    inj = [
        _Well("I1", WellRole.INJECTOR, wwir=300.0, injector_bhp_margin=50.0),
        _Well("I2", WellRole.INJECTOR, wwir=50.0, injector_bhp_margin=150.0),
    ]
    prod = [_Well("P1", WellRole.PRODUCER, wlpr=100.0, wopr=1.0, wct=0.99)]
    scope = _region(inj, prod).scopes[0]
    assert set(scope.injectors) == {"I1", "I2"}
    assert scope.producers == ("P1",)
    assert scope.reason


# ===========================================================================
# D14 -- анти-оверфит
# ===========================================================================


def test_no_case_specific_identifiers_in_diagnostics() -> None:
    import re
    from pathlib import Path

    source = Path("src/aios/diagnostics/support.py").read_text(encoding="utf-8")
    for banned in ("model_y", "MODEL_Y", "EXP003", "EXP004", "state_digest"):
        assert banned not in source
    assert not re.search(r"[\"'](?:\d{1,2})[\"']\s*,\s*[\"'](?:\d{1,2})[\"']", source)
