"""Executable-action capability mask and the injection field contract.

MAS-PERF-001 lost every live Production proposal between the Arbiter and
numerical resolution, because the specialist was offered REDUCE_OFFTAKE and the
resolver has no evidence-backed anchor for it. These tests pin the mask that
stops that, and pin that it is a mask rather than a deletion.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from aios.mas.common import Intensity
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.magnitude_resolution import (
    ModelYEmpiricalMagnitudeProfile,
    injection_capabilities,
    production_capabilities,
    supported_injection_actions,
    supported_production_actions,
)
from aios.mas.magnitude_resolution.profile import MagnitudeAnchor


def _capability(items, action) -> object:
    return next(item for item in items if item.action == str(action))


# --- the mask is derived, not written down -----------------------------------


def test_executable_production_actions_match_the_resolver_branches() -> None:
    supported = supported_production_actions()
    assert ProductionAction.HOLD in supported
    assert ProductionAction.INCREASE_OFFTAKE in supported
    assert ProductionAction.SHUT_IN in supported
    assert ProductionAction.REOPEN in supported


def test_reduce_offtake_is_masked_while_it_has_no_anchor() -> None:
    """The diagnosed failure: an action the resolver cannot materialise.

    EXP-005 has since supplied the anchor, so the pre-EXP-005 condition is
    reproduced with an anchorless profile. The mask logic is what is under
    test, not the momentary contents of the shipped profile.
    """

    class ProfileWithoutReduction(ModelYEmpiricalMagnitudeProfile):
        def producer_reduction_factor(self, intensity: Intensity) -> MagnitudeAnchor | None:
            return None

    profile = ProfileWithoutReduction()
    assert ProductionAction.REDUCE_OFFTAKE not in supported_production_actions(profile)
    capability = _capability(production_capabilities(profile), ProductionAction.REDUCE_OFFTAKE)
    assert capability.executable is False
    assert "no empirical reduction anchor" in capability.reason


def test_rebalance_within_block_is_masked_for_lack_of_a_resolver_branch() -> None:
    capability = _capability(
        production_capabilities(ModelYEmpiricalMagnitudeProfile()),
        ProductionAction.REBALANCE_WITHIN_BLOCK,
    )
    assert capability.executable is False
    assert "no resolver branch" in capability.reason


def test_rebalance_across_blocks_is_masked_for_lack_of_a_resolver_branch() -> None:
    capability = _capability(
        injection_capabilities(ModelYEmpiricalMagnitudeProfile()),
        InjectionAction.REBALANCE_ACROSS_BLOCKS,
    )
    assert capability.executable is False
    assert InjectionAction.REBALANCE_ACROSS_BLOCKS not in supported_injection_actions()


def test_injection_actions_with_anchors_are_executable() -> None:
    supported = supported_injection_actions()
    for action in (
        InjectionAction.HOLD,
        InjectionAction.SCALE_TOTAL_INJECTION,
        InjectionAction.SUPPORT_REGION,
        InjectionAction.REDUCE_SUPPORT_PATH,
        InjectionAction.REALLOCATE_INJECTION,
    ):
        assert action in supported


# --- a mask, not a deletion ---------------------------------------------------


def test_the_frozen_vocabulary_still_contains_the_masked_action() -> None:
    """The domain concept survives; only its executability is withheld."""
    assert ProductionAction.REDUCE_OFFTAKE in set(ProductionAction)
    assert ProductionAction("REDUCE_OFFTAKE") is ProductionAction.REDUCE_OFFTAKE


def test_an_anchor_re_enables_the_action_without_touching_the_vocabulary() -> None:
    """Once EXP005 supplies evidence, the same mask lets the action back."""

    class ProfileWithReduction(ModelYEmpiricalMagnitudeProfile):
        def producer_reduction_factor(self, intensity: Intensity) -> MagnitudeAnchor | None:
            anchors = {
                Intensity.SMALL: MagnitudeAnchor(0.90, "EXP-005", "EXP005_P_RED_090", "test"),
                Intensity.MODERATE: MagnitudeAnchor(0.80, "EXP-005", "EXP005_P_RED_080", "test"),
            }
            return anchors.get(intensity)

    profile = ProfileWithReduction()
    assert ProductionAction.REDUCE_OFFTAKE in supported_production_actions(profile)
    capability = _capability(production_capabilities(profile), ProductionAction.REDUCE_OFFTAKE)
    assert capability.executable is True
    assert "SMALL" in capability.reason and "MODERATE" in capability.reason


def test_a_profile_losing_its_uplift_anchor_masks_increase_offtake() -> None:
    """The mask follows evidence in both directions."""

    class ProfileWithoutUplift(ModelYEmpiricalMagnitudeProfile):
        def producer_uplift_factor(self, intensity: Intensity) -> MagnitudeAnchor | None:
            return None

    assert ProductionAction.INCREASE_OFFTAKE not in supported_production_actions(
        ProfileWithoutUplift()
    )


# --- the benchmark offers only executable actions -----------------------------


def test_benchmark_offers_only_executable_actions() -> None:
    from aios.benchmark.mas_variant import INJECTION_ACTIONS, PRODUCTION_ACTIONS

    assert set(PRODUCTION_ACTIONS) <= set(supported_production_actions())
    assert set(INJECTION_ACTIONS) <= set(supported_injection_actions())
    # Nothing the resolver cannot materialise is ever offered.
    assert ProductionAction.REBALANCE_WITHIN_BLOCK not in PRODUCTION_ACTIONS
    assert InjectionAction.REBALANCE_ACROSS_BLOCKS not in INJECTION_ACTIONS


def test_benchmark_action_lists_are_derived_not_literal() -> None:
    import inspect

    from aios.benchmark import mas_variant

    source = inspect.getsource(mas_variant)
    assert "PRODUCTION_ACTIONS = supported_production_actions()" in source
    assert "INJECTION_ACTIONS = supported_injection_actions()" in source


# --- injection field contract is stated, never relaxed ------------------------


def _injection_prompt(actions: tuple[InjectionAction, ...]) -> dict:
    from aios.mas.specialists.injection.prompt import PromptBuilder
    from tests.support.mas_team_fixtures import synthetic_injection_envelope

    return json.loads(PromptBuilder().build(synthetic_injection_envelope(allowed_actions=actions)))


def test_scale_total_injection_contract_is_stated_to_the_model() -> None:
    """The observed live failure: targeted wells under a global-scaling action."""
    payload = _injection_prompt((InjectionAction.HOLD, InjectionAction.SCALE_TOTAL_INJECTION))
    rule = payload["action_field_contracts"]["SCALE_TOTAL_INJECTION"]
    assert "recipient_wells MUST be empty" in rule
    assert "SUPPORT_REGION" in rule


def test_only_offered_actions_carry_a_contract() -> None:
    payload = _injection_prompt((InjectionAction.HOLD, InjectionAction.SUPPORT_REGION))
    contracts = payload["action_field_contracts"]
    assert set(contracts) == {"HOLD", "SUPPORT_REGION", "ALL_NON_HOLD"}


def test_stated_contracts_match_the_frozen_intent_rules() -> None:
    """A stated rule the parser does not enforce would be a lie to the model."""
    from aios.mas.common import Direction, UncertaintyLevel
    from aios.mas.intents import InjectionIntent, IntentError

    with pytest.raises(IntentError, match="recipient_wells must be empty"):
        InjectionIntent(
            action=InjectionAction.SCALE_TOTAL_INJECTION,
            direction=Direction.UP,
            intensity=Intensity.SMALL,
            donor_wells=(),
            recipient_wells=("SYN-I1",),
            source_regions=(),
            target_regions=(),
            evidence_ids=("SYN-pressure-1",),
            uncertainty=UncertaintyLevel.LOW,
            rationale_short="Support the weaker flank.",
        )


def test_injection_parser_is_unchanged_by_the_prompt_work() -> None:
    """The prompt explains the contract; the parser still enforces it."""
    from aios.mas.specialists.injection.parser import (
        _ALLOWED_INTENT_KEYS,
        _ALLOWED_TOP_LEVEL,
        ParseError,
        StrictOutputParser,
    )

    # PERF-003B: контракт расширен пучком гипотез одного ответа. Прежняя
    # форма с одним набором intents остаётся валидной.
    assert _ALLOWED_TOP_LEVEL == frozenset({"intents", "hypotheses", "considered_actions"})
    assert "recipient_wells" in _ALLOWED_INTENT_KEYS
    bad = json.dumps(
        {
            "intents": [
                {
                    "action": "SCALE_TOTAL_INJECTION",
                    "direction": "UP",
                    "intensity": "SMALL",
                    "donor_wells": [],
                    "recipient_wells": ["SYN-I1"],
                    "source_regions": [],
                    "target_regions": [],
                    "evidence_ids": ["SYN-pressure-1"],
                    "uncertainty": "LOW",
                    "rationale_short": "Support the weaker flank.",
                }
            ]
        }
    )
    with pytest.raises(ParseError):
        StrictOutputParser().parse(bad)


# --- no answer leakage --------------------------------------------------------


def test_capability_information_leaks_no_winning_answer() -> None:
    """Capability may be shared; EXP003/EXP004 answers may not."""
    payload = _injection_prompt((InjectionAction.HOLD, InjectionAction.SCALE_TOTAL_INJECTION))
    body = json.dumps(payload)
    for leak in ("EXP003", "EXP004", "1826.267", "1190.593", "1030.877", "npv", "NPV"):
        assert leak not in body


def test_capability_reasons_carry_no_numerical_anchor_value() -> None:
    """A reason explains why an action is unavailable, not what number to use."""
    for capability in production_capabilities(ModelYEmpiricalMagnitudeProfile()):
        assert "1.10" not in capability.reason
        assert "5.00" not in capability.reason


def test_action_capability_is_serialisable_for_provenance() -> None:
    capability = _capability(
        production_capabilities(ModelYEmpiricalMagnitudeProfile()), ProductionAction.REDUCE_OFFTAKE
    )
    body = capability.to_json()
    assert set(body) == {"action", "executable", "reason"}
    assert dataclasses.is_dataclass(capability)


# ---------------------------------------------------------------------------
# EXP-005: the reduction anchor, and the action it re-enables.
# ---------------------------------------------------------------------------


def test_reduction_anchors_are_the_exp005_tested_points() -> None:
    """Anchors are tested grid points, never interpolated or chosen freehand."""
    profile = ModelYEmpiricalMagnitudeProfile()
    expected = {Intensity.SMALL: 0.90, Intensity.MODERATE: 0.80, Intensity.AGGRESSIVE: 0.60}
    for intensity, value in expected.items():
        anchor = profile.producer_reduction_factor(intensity)
        assert anchor is not None
        assert anchor.value == pytest.approx(value)
        assert anchor.source_experiment == "EXP-005"
        assert "EXP005_" in anchor.source_case


def test_reduction_anchors_mirror_the_injection_decrease_grid() -> None:
    """The magnitudes are the ones this project had already tested."""
    profile = ModelYEmpiricalMagnitudeProfile()
    for intensity in (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE):
        reduction = profile.producer_reduction_factor(intensity)
        injection = profile.injection_scale_factor(intensity, direction_up=False)
        assert reduction.value == pytest.approx(injection.value)


def test_intensity_none_still_has_no_reduction_anchor() -> None:
    assert ModelYEmpiricalMagnitudeProfile().producer_reduction_factor(Intensity.NONE) is None


def test_reduce_offtake_is_now_executable() -> None:
    assert ProductionAction.REDUCE_OFFTAKE in supported_production_actions()
    capability = _capability(
        production_capabilities(ModelYEmpiricalMagnitudeProfile()), ProductionAction.REDUCE_OFFTAKE
    )
    assert capability.executable is True


def test_every_exp005_grid_point_passed_guardian() -> None:
    """The recorded evidence, not a claim about it."""
    import json
    from pathlib import Path

    payload = json.loads(Path("experiments/exp005/results.json").read_text(encoding="utf-8"))
    assert len(payload["results"]) == 6
    assert {r["arm"] for r in payload["results"]} == {"G", "S"}
    assert {r["factor"] for r in payload["results"]} == {0.90, 0.80, 0.60}
    for record in payload["results"]:
        assert record["status"] == "OK"
        assert record["guardian"] == "PASS"
        assert record["guardian_failed"] == 0


def test_recorded_evidence_shows_the_target_dependence() -> None:
    """The anchor fixes the magnitude; the economics remain target-dependent.

    Pinning this keeps the caveat from being quietly forgotten: the same factor
    cost roughly an order of magnitude more NPV field-wide than on the six
    wettest producers.
    """
    import json
    from pathlib import Path

    payload = json.loads(Path("experiments/exp005/results.json").read_text(encoding="utf-8"))
    baseline = 1030.8776958837277
    by_arm = {(r["arm"], r["factor"]): r["npv_mln_rub"] for r in payload["results"]}
    for factor in (0.90, 0.80, 0.60):
        global_delta = abs(by_arm[("G", factor)] - baseline)
        subset_delta = abs(by_arm[("S", factor)] - baseline)
        assert global_delta > subset_delta * 5


def test_reduce_offtake_resolves_to_a_numerical_candidate() -> None:
    """The whole point: a REDUCE_OFFTAKE intent now reaches real controls."""
    from dataclasses import replace as dc_replace

    from aios.mas.common import Direction, UncertaintyLevel
    from aios.mas.intents import ProductionIntent
    from aios.mas.magnitude_resolution.engine import DeterministicMagnitudeResolver
    from aios.schedule import ProducerControl, WellStatus

    intent = ProductionIntent(
        action=ProductionAction.REDUCE_OFFTAKE,
        direction=Direction.DOWN,
        intensity=Intensity.MODERATE,
        target_wells=("P1",),
        target_regions=(),
        evidence_ids=("ev-1",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Water cut evidence supports cutting offtake.",
    )
    control = ProducerControl(well="P1", status=WellStatus.OPEN, mode="LRAT", liquid_rate=200.0)
    controls = {"P1": control}
    resolver = DeterministicMagnitudeResolver.__new__(DeterministicMagnitudeResolver)
    object.__setattr__(resolver, "_profile", ModelYEmpiricalMagnitudeProfile())
    object.__setattr__(resolver, "_context", None)
    resolver._apply_production_intent(intent, controls, set())
    assert controls["P1"].liquid_rate == pytest.approx(160.0)  # 200 * 0.80
    assert dc_replace(control, liquid_rate=160.0) == controls["P1"]
