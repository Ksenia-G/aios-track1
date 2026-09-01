"""Model Y empirical magnitude profile: evidence-backed anchors only.

Every anchor here must trace to an actually-tested EXP-001..EXP-004 grid
point (experiments/exp004/scenarios.csv, experiments/exp003/compensation.csv)
-- never an aesthetically-chosen round percentage, and never a value outside
the tested grid.
"""

from __future__ import annotations

from aios.mas.common import Intensity
from aios.mas.magnitude_resolution.profile import (
    PROFILE_VERSION,
    ModelYEmpiricalMagnitudeProfile,
)


def _profile() -> ModelYEmpiricalMagnitudeProfile:
    return ModelYEmpiricalMagnitudeProfile()


# ---------------------------------------------------------------------------
# P1 -- every anchor has evidence/provenance declaration.
# ---------------------------------------------------------------------------


def test_p1_every_anchor_declares_provenance() -> None:
    profile = _profile()
    anchors = [
        profile.producer_uplift_factor(Intensity.SMALL),
        profile.producer_uplift_factor(Intensity.MODERATE),
        profile.producer_uplift_factor(Intensity.AGGRESSIVE),
        profile.injection_scale_factor(Intensity.SMALL, direction_up=True),
        profile.injection_scale_factor(Intensity.MODERATE, direction_up=True),
        profile.injection_scale_factor(Intensity.AGGRESSIVE, direction_up=True),
        profile.injection_scale_factor(Intensity.SMALL, direction_up=False),
        profile.injection_scale_factor(Intensity.MODERATE, direction_up=False),
        profile.injection_scale_factor(Intensity.AGGRESSIVE, direction_up=False),
        profile.reallocation_transfer_fraction(Intensity.SMALL),
        profile.reallocation_transfer_fraction(Intensity.MODERATE),
        profile.reallocation_transfer_fraction(Intensity.AGGRESSIVE),
    ]
    for anchor in anchors:
        assert anchor is not None
        assert anchor.source_experiment
        assert anchor.source_case
        assert anchor.note


# ---------------------------------------------------------------------------
# P2 / P3 / P4 -- no arbitrary magnitude, no extrapolation beyond tested grid.
# ---------------------------------------------------------------------------

# Tested grid, transcribed from experiments/exp004/scenarios.csv (B_PROD_*,
# A_INJ_*) and experiments/exp003/compensation.csv (EXP003_D_COMP_ADD_42_100
# transfer fractions). Naming convention P<code>/I<code> = code/100 (e.g.
# B_PROD_110 -> factor 1.10).
TESTED_PRODUCER_UPLIFT_FACTORS = {1.10, 1.25, 1.50, 2.00, 3.00, 5.00}
TESTED_INJECTION_SCALE_FACTORS = {0.60, 0.80, 0.90, 1.10, 1.25, 1.50}
TESTED_REALLOCATION_FRACTIONS = {0.35, 0.825, 0.975}


def test_p2_p3_producer_uplift_anchors_are_within_tested_grid() -> None:
    profile = _profile()
    for intensity in (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE):
        anchor = profile.producer_uplift_factor(intensity)
        assert anchor.value in TESTED_PRODUCER_UPLIFT_FACTORS
    assert profile.producer_uplift_factor(Intensity.AGGRESSIVE).value == max(
        TESTED_PRODUCER_UPLIFT_FACTORS
    )


def test_p2_p3_injection_scale_up_anchors_are_within_tested_grid() -> None:
    profile = _profile()
    for intensity in (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE):
        anchor = profile.injection_scale_factor(intensity, direction_up=True)
        assert anchor.value in TESTED_INJECTION_SCALE_FACTORS
        assert anchor.value > 1.0


def test_p4_injection_scale_down_anchors_are_within_tested_grid() -> None:
    profile = _profile()
    for intensity in (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE):
        anchor = profile.injection_scale_factor(intensity, direction_up=False)
        assert anchor.value in TESTED_INJECTION_SCALE_FACTORS
        assert anchor.value < 1.0


def test_reallocation_fractions_are_within_tested_grid() -> None:
    profile = _profile()
    for intensity in (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE):
        anchor = profile.reallocation_transfer_fraction(intensity)
        assert anchor.value in TESTED_REALLOCATION_FRACTIONS
        assert 0.0 < anchor.value < 1.0


def test_no_intensity_none_anchor_exists() -> None:
    # NONE means "no magnitude" structurally (HOLD-like) -- it must never
    # resolve to a numeric anchor.
    profile = _profile()
    assert profile.producer_uplift_factor(Intensity.NONE) is None
    assert profile.injection_scale_factor(Intensity.NONE, direction_up=True) is None
    assert profile.reallocation_transfer_fraction(Intensity.NONE) is None


# ---------------------------------------------------------------------------
# P5 / P6 -- nominal / aggressive anchors match discovered repository evidence.
# ---------------------------------------------------------------------------


def test_p5_nominal_producer_anchor_matches_discovered_grid_point() -> None:
    profile = _profile()
    small = profile.producer_uplift_factor(Intensity.SMALL)
    assert small.value == 1.10
    assert "EXP004_B_PROD_110" in small.source_case


def test_p6_aggressive_producer_anchor_matches_discovered_grid_point() -> None:
    profile = _profile()
    aggressive = profile.producer_uplift_factor(Intensity.AGGRESSIVE)
    assert aggressive.value == 5.00
    assert "500" in aggressive.source_case or "P500" in aggressive.source_case


def test_reallocation_anchors_trace_to_exp003_best() -> None:
    profile = _profile()
    for intensity in (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE):
        anchor = profile.reallocation_transfer_fraction(intensity)
        assert "EXP003_D_COMP_ADD_42_100" in anchor.source_case
        assert anchor.source_experiment == "EXP-003"


# ---------------------------------------------------------------------------
# P7 -- runtime must not parse markdown; profile is static Python data.
# ---------------------------------------------------------------------------


def test_p7_profile_module_does_not_read_markdown_docs() -> None:
    import inspect

    from aios.mas.magnitude_resolution import profile as profile_module

    source = inspect.getsource(profile_module)
    assert ".md" not in source
    assert "open(" not in source
    assert "Path(" not in source or "read_text" not in source


# ---------------------------------------------------------------------------
# P8 -- profile version deterministic.
# ---------------------------------------------------------------------------


def test_p8_profile_version_is_deterministic_and_stable() -> None:
    assert ModelYEmpiricalMagnitudeProfile().version == PROFILE_VERSION
    assert ModelYEmpiricalMagnitudeProfile().version == ModelYEmpiricalMagnitudeProfile().version


# ---------------------------------------------------------------------------
# P9 / P10 / P11 -- direction monotonicity.
# ---------------------------------------------------------------------------


def test_p9_direction_up_factors_are_monotonically_increasing_with_intensity() -> None:
    profile = _profile()
    small = profile.injection_scale_factor(Intensity.SMALL, direction_up=True).value
    moderate = profile.injection_scale_factor(Intensity.MODERATE, direction_up=True).value
    aggressive = profile.injection_scale_factor(Intensity.AGGRESSIVE, direction_up=True).value
    assert 1.0 < small < moderate < aggressive


def test_p10_direction_down_factors_are_monotonically_decreasing_with_intensity() -> None:
    profile = _profile()
    small = profile.injection_scale_factor(Intensity.SMALL, direction_up=False).value
    moderate = profile.injection_scale_factor(Intensity.MODERATE, direction_up=False).value
    aggressive = profile.injection_scale_factor(Intensity.AGGRESSIVE, direction_up=False).value
    assert aggressive < moderate < small < 1.0


def test_p11_producer_uplift_intensity_ordering_is_monotonic() -> None:
    # NONE never introduces a scalar directional change (there is no anchor
    # at all for NONE, per test_no_intensity_none_anchor_exists); among the
    # three defined intensities the magnitude must increase monotonically.
    profile = _profile()
    small = profile.producer_uplift_factor(Intensity.SMALL).value
    moderate = profile.producer_uplift_factor(Intensity.MODERATE).value
    aggressive = profile.producer_uplift_factor(Intensity.AGGRESSIVE).value
    assert small < moderate < aggressive
