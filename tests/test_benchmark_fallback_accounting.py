"""Fallback accounting in the benchmark must reflect real specialist provenance.

Observed defect: `run_mas_variant` reported `fallback_count=0` unconditionally,
even when one or more Production/Injection specialist decisions actually fell
back to the deterministic baseline (`ProposalProvenance.fallback_used=True`).
This breaks the provenance validity gate silently -- a run full of fallbacks
looked identical to a run with none.

Contract:

* `fallback_count` counts specialist DECISIONS, not epochs: Production and
  Injection at the same epoch are two independent decisions, each with its
  own `fallback_used`.
* It counts the boolean already recorded on the FINAL specialist output that
  reached the Arbiter for that epoch (`DecisionStepResult.production_fallback_used`
  / `.injection_fallback_used`) -- never inferred from source strings, retry
  counts, or candidates that were generated but not part of the actual
  decision provenance.
* MAS OFF invokes the same specialists as MAS ON (same topology, only the
  cognitive branch differs -- see `test_mas_off_and_mas_on_share_every_non_cognitive_setting`
  in test_benchmark_variants.py): with `llm_enabled=False`, every specialist
  call is a deterministic ablation and its provenance is honestly
  `fallback_used=True, fallback_reason="llm_disabled"` (see
  `ProductionController.propose`/`InjectionController.propose`). So MAS OFF's
  `fallback_count` is *expected* to equal `2 * decision_steps`, not 0 -- that
  is not a bug, it is an honest reflection of the ablation design, and this
  file proves that explicitly rather than special-casing "llm_disabled" out
  (which the milestone forbids: no inference from reason strings).
"""

from __future__ import annotations

from aios.benchmark.mas_variant import fallback_count


class _Step:
    """Minimal carrier of exactly what the aggregation reads."""

    def __init__(self, *, production_fallback_used: bool, injection_fallback_used: bool) -> None:
        self.production_fallback_used = production_fallback_used
        self.injection_fallback_used = injection_fallback_used


# --- F1/F2/F3: single epoch -------------------------------------------------


def test_single_production_fallback_counts_as_one() -> None:
    """F1: Production fallback=true, Injection=false -> count == 1."""
    results = [_Step(production_fallback_used=True, injection_fallback_used=False)]
    assert fallback_count(results) == 1


def test_single_injection_fallback_counts_as_one() -> None:
    """F2: Production=false, Injection=true -> count == 1."""
    results = [_Step(production_fallback_used=False, injection_fallback_used=True)]
    assert fallback_count(results) == 1


def test_both_specialists_falling_back_at_one_epoch_counts_as_two() -> None:
    """F3: both fall back at the same epoch -> count == 2, not 1."""
    results = [_Step(production_fallback_used=True, injection_fallback_used=True)]
    assert fallback_count(results) == 2


# --- F4: three-step pattern, independent per epoch --------------------------


def test_three_step_pattern_counts_every_epoch_independently() -> None:
    """F4: T0 P=false I=true, T1 P=true I=false, T2 P=true I=true -> 4.

    Not "epochs with a fallback" (which would be 3): Production and
    Injection at T2 both count, so T2 alone contributes 2.
    """
    results = [
        _Step(production_fallback_used=False, injection_fallback_used=True),  # T0: +1
        _Step(production_fallback_used=True, injection_fallback_used=False),  # T1: +1
        _Step(production_fallback_used=True, injection_fallback_used=True),  # T2: +2
    ]
    assert fallback_count(results) == 4


# --- F5: nothing ever falls back --------------------------------------------


def test_all_live_decisions_count_as_zero() -> None:
    """F5: six live (non-fallback) specialist decisions -> count == 0."""
    results = [
        _Step(production_fallback_used=False, injection_fallback_used=False) for _ in range(3)
    ]
    assert fallback_count(results) == 0


# --- F6: MAS OFF's deterministic ablation is honestly non-zero -------------


def test_mas_off_ablation_reports_every_specialist_call_as_fallback() -> None:
    """F6: MAS OFF invokes the same two specialists every epoch, and their
    provenance is honestly fallback_used=True (reason="llm_disabled") --
    this is the intended semantics, not a defect to be papered over as 0.
    """
    decision_steps = 3
    results = [
        _Step(production_fallback_used=True, injection_fallback_used=True)
        for _ in range(decision_steps)
    ]
    assert fallback_count(results) == 2 * decision_steps


# --- F8: a fallback proposal built but not part of the decision is not counted


def test_only_the_decision_carrying_result_is_counted_not_generated_alternatives() -> None:
    """F8: `_Step` here stands in for `DecisionStepResult`, which already
    carries only the FINAL round's specialist provenance (see
    test_mas_orchestration.py::test_round2_result_reports_the_final_rounds_fallback_state
    for proof that an earlier round's fallback, superseded by Round 2, does
    not leak into this count). At this aggregation layer there is nothing
    else to accidentally double-count: one result carries exactly one
    Production and one Injection boolean.
    """
    results = [_Step(production_fallback_used=False, injection_fallback_used=False)]
    assert fallback_count(results) == 0


# --- Validation gate: sum over an arbitrary synthetic run -------------------


def test_fallback_count_equals_the_sum_over_all_epochs() -> None:
    """No live provider, no OPM: fallback_count == sum(production_i + injection_i)."""
    pattern = [
        (True, False),
        (False, True),
        (True, True),
        (False, False),
        (True, True),
    ]
    results = [_Step(production_fallback_used=p, injection_fallback_used=i) for p, i in pattern]
    expected = sum(int(p) + int(i) for p, i in pattern)
    assert expected == 6
    assert fallback_count(results) == expected
