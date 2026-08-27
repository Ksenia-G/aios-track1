"""Benchmark contracts: manifests, fairness classification and scoreboard rules.

The point of these tests is that a benchmark table cannot quietly compare two
numbers that mean different things, and cannot crown a candidate that broke the
official constraints or that was never comparable in the first place.
"""

from __future__ import annotations

import dataclasses

import pytest

from aios.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkError,
    BenchmarkOutcome,
    BenchmarkRunManifest,
    CognitiveConfig,
    ComparisonClass,
    CompensationPolicy,
    EvaluationLineage,
    PhysicalFrame,
    RunKind,
    Scoreboard,
    VariantFamily,
    aggregate_repeats,
    classify,
    detect_provider,
    may_claim_win,
)


def _frame(**overrides: object) -> PhysicalFrame:
    fields: dict[str, object] = {
        "case_id": "model_y_validation",
        "case_digest": "case-digest",
        "deck_reference": "${MODEL_Y_ROOT}",
        "deck_digest": None,
        "simulator_identity": "openporousmedia/opmreleases@sha256:abc",
        "runtime_mode": "FULL",
        "initial_state_digest": "state-digest",
        "effective_start": "2014-01-01",
        "effective_end": "2015-12-01",
        "chdd_calculator_version": "NOT_OFFICIAL_PENDING_TNAV_REFERENCE",
        "chdd_month_count": 23,
        "guardian_version": "aios.agents.constraint_guardian/1.0.0",
        "compensation_policy": CompensationPolicy.OFFICIAL_UNKNOWN_PORT,
    }
    fields.update(overrides)
    return PhysicalFrame(**fields)  # type: ignore[arg-type]


def _outcome(
    variant_id: str = "V",
    *,
    npv: float = 100.0,
    family: VariantFamily = VariantFamily.BASELINE,
    run_kind: RunKind = RunKind.FIXED_SCHEDULE_REPLAY,
    guardian_pass: bool = True,
    guardian_failed: int = 0,
    frame: PhysicalFrame | None = None,
    llm_enabled: bool = False,
    model_id: str | None = None,
    run_id: str | None = None,
) -> BenchmarkOutcome:
    manifest = BenchmarkRunManifest(
        benchmark_schema_version=BENCHMARK_SCHEMA_VERSION,
        variant_id=variant_id,
        variant_family=family,
        run_kind=run_kind,
        code_sha="deadbeef",
        frame=frame or _frame(),
        cognitive=CognitiveConfig(llm_enabled=llm_enabled, model_id=model_id, run_id=run_id),
        lineage=(EvaluationLineage(schedule_digest=f"sched-{variant_id}"),),
    )
    return BenchmarkOutcome(
        manifest=manifest,
        npv_mln_rub=npv,
        guardian_pass=guardian_pass,
        guardian_failed_count=guardian_failed,
    )


# --- manifest determinism ----------------------------------------------------


def test_manifest_digest_is_deterministic() -> None:
    assert _outcome().manifest.digest() == _outcome().manifest.digest()


@pytest.mark.parametrize(
    "override",
    [
        {"initial_state_digest": "other-state"},
        {"effective_end": "2016-12-01"},
        {"simulator_identity": "openporousmedia/opmreleases@sha256:def"},
        {"chdd_calculator_version": "other-calculator"},
        {"compensation_policy": CompensationPolicy.RESEARCH_SCREENING_VRR},
    ],
)
def test_different_physical_inputs_change_the_manifest_digest(override: dict) -> None:
    baseline = _outcome().manifest.digest()
    changed = _outcome(frame=_frame(**override)).manifest.digest()
    assert changed != baseline


def test_manifest_never_serialises_a_secret() -> None:
    """Only a model name is recorded; there is nowhere to put a key."""
    manifest = _outcome(llm_enabled=True, model_id="some-model").manifest
    body = manifest.to_json()
    assert set(body["cognitive"]) == {
        "llm_enabled",
        "provider_id",
        "model_id",
        "run_id",
        "seed",
    }
    assert "api_key" not in str(body).lower()
    assert "token" not in str(body).lower()


def test_manifest_carries_no_workspace_or_artifact_paths() -> None:
    body = str(_outcome().to_json())
    for heavy in (".UNSMRY", ".SMSPEC", ".EGRID", ".UNRST", "/private/tmp", "workspace"):
        assert heavy not in body


def test_llm_enabled_without_a_model_is_refused() -> None:
    with pytest.raises(BenchmarkError, match="model_id"):
        CognitiveConfig(llm_enabled=True)


def test_non_finite_npv_is_refused() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(BenchmarkError, match="finite"):
            _outcome(npv=bad)


def test_a_passing_guardian_cannot_carry_failed_checks() -> None:
    with pytest.raises(BenchmarkError, match="failed checks"):
        _outcome(guardian_pass=True, guardian_failed=2)


# --- fairness ----------------------------------------------------------------


def test_identical_frames_and_kinds_are_directly_comparable() -> None:
    comparison = classify(_outcome("A", npv=120.0), _outcome("B", npv=100.0))
    assert comparison.comparison_class is ComparisonClass.DIRECT_COMPARABLE
    assert comparison.delta_npv_mln_rub == pytest.approx(20.0)
    assert comparison.claimable


@pytest.mark.parametrize(
    "override",
    [
        {"case_id": "other_case"},
        {"deck_reference": "/other/deck"},
        {"effective_start": "2015-01-01"},
        {"chdd_calculator_version": "other"},
        {"compensation_policy": CompensationPolicy.RESEARCH_SCREENING_VRR},
    ],
)
def test_apples_to_oranges_fails_closed(override: dict) -> None:
    left = _outcome("A", npv=9999.0, frame=_frame(**override))
    comparison = classify(left, _outcome("B", npv=100.0))
    assert comparison.comparison_class is ComparisonClass.NOT_COMPARABLE
    assert not comparison.claimable
    assert not may_claim_win(comparison, left)


def test_historical_reference_is_never_a_direct_win() -> None:
    historical = _outcome("H", npv=5000.0, run_kind=RunKind.HISTORICAL_REFERENCE)
    comparison = classify(historical, _outcome("B", npv=100.0))
    assert comparison.comparison_class is ComparisonClass.REFERENCE_ONLY
    assert not may_claim_win(comparison, historical)


def test_closed_loop_vs_fixed_schedule_is_normalized_not_direct() -> None:
    """Different mechanisms may be compared, but the table must say so."""
    mas = _outcome(
        "MAS", npv=110.0, family=VariantFamily.MAS_OFF, run_kind=RunKind.CLOSED_LOOP_POLICY
    )
    comparison = classify(mas, _outcome("B0", npv=100.0))
    assert comparison.comparison_class is ComparisonClass.NORMALIZED_COMPARABLE
    assert comparison.claimable
    assert any("run kinds" in reason for reason in comparison.reasons)


def test_guardian_failure_cannot_claim_a_win() -> None:
    loser = _outcome("BAD", npv=99999.0, guardian_pass=False, guardian_failed=3)
    comparison = classify(loser, _outcome("B0", npv=100.0))
    assert comparison.comparison_class is ComparisonClass.DIRECT_COMPARABLE
    assert not may_claim_win(comparison, loser)


# --- scoreboard --------------------------------------------------------------


def test_guardian_failure_is_never_the_headline_candidate() -> None:
    board = Scoreboard(
        outcomes=(
            _outcome("B0", npv=100.0),
            _outcome("BAD", npv=99999.0, guardian_pass=False, guardian_failed=1),
        ),
        reference_variant_id="B0",
    )
    assert board.headline_candidate().variant_id == "B0"


def test_incomparable_candidate_is_never_the_headline_candidate() -> None:
    board = Scoreboard(
        outcomes=(
            _outcome("B0", npv=100.0),
            _outcome("ALIEN", npv=99999.0, frame=_frame(case_id="other_case")),
        ),
        reference_variant_id="B0",
    )
    assert board.headline_candidate().variant_id == "B0"


def test_historical_reference_is_never_the_headline_candidate() -> None:
    board = Scoreboard(
        outcomes=(
            _outcome("B0", npv=100.0),
            _outcome("HIST", npv=99999.0, run_kind=RunKind.HISTORICAL_REFERENCE),
        ),
        reference_variant_id="B0",
    )
    assert board.headline_candidate().variant_id == "B0"


def test_scoreboard_reports_the_class_of_every_comparison() -> None:
    board = Scoreboard(
        outcomes=(
            _outcome("B0", npv=100.0),
            _outcome("ALIEN", npv=200.0, frame=_frame(case_id="other_case")),
        ),
        reference_variant_id="B0",
    )
    payload = board.to_json()
    alien = next(row for row in payload["rows"] if row["variant_id"] == "ALIEN")
    assert alien["comparison_class"] == str(ComparisonClass.NOT_COMPARABLE)
    assert alien["win_claimable"] is False


def test_missing_reference_variant_fails_closed() -> None:
    board = Scoreboard(outcomes=(_outcome("B0"),), reference_variant_id="ABSENT")
    with pytest.raises(BenchmarkError, match="reference variant"):
        board.to_json()


# --- repeats -----------------------------------------------------------------


def test_repeat_aggregation_describes_the_spread() -> None:
    runs = [
        _outcome("B4", npv=value, llm_enabled=True, model_id="m", run_id=f"run-{i}")
        for i, value in enumerate((100.0, 140.0, 120.0), start=1)
    ]
    stats = aggregate_repeats(runs)
    assert stats.count == 3
    assert stats.median_npv == pytest.approx(120.0)
    assert stats.min_npv == pytest.approx(100.0)
    assert stats.max_npv == pytest.approx(140.0)
    assert stats.best_run_id == "run-2"
    assert stats.worst_run_id == "run-1"
    assert stats.evidence_strength == "repeatability_evidence_not_significance"


def test_repeat_aggregation_refuses_mixed_variants() -> None:
    with pytest.raises(BenchmarkError, match="different variants"):
        aggregate_repeats([_outcome("A"), _outcome("B")])


def test_repeat_aggregation_requires_at_least_one_run() -> None:
    with pytest.raises(BenchmarkError, match="at least one"):
        aggregate_repeats([])


def test_deterministic_tie_between_equal_npv_is_stable() -> None:
    board = Scoreboard(
        outcomes=(_outcome("A_TIE", npv=100.0), _outcome("B_TIE", npv=100.0)),
        reference_variant_id="A_TIE",
    )
    assert board.headline_candidate().variant_id == "B_TIE"
    assert board.headline_candidate().variant_id == "B_TIE"


# --- provider ----------------------------------------------------------------


def test_provider_detection_reports_absence_without_secrets(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    availability = detect_provider()
    assert availability.available is False
    assert availability.provider_id is None
    body = str(availability.to_json())
    assert "MAS_LLM" in body


def test_named_provider_without_a_credential_is_unavailable(monkeypatch) -> None:
    """A named model does not conjure an approved credential."""
    monkeypatch.setenv("MAS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MAS_LLM_MODEL", "some-model")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    availability = detect_provider()
    assert availability.available is False
    assert "credential" in availability.reason
    assert availability.model_id == "some-model"


def test_unsupported_provider_name_is_reported_not_guessed(monkeypatch) -> None:
    monkeypatch.setenv("MAS_LLM_PROVIDER", "definitely-not-a-provider")
    monkeypatch.setenv("MAS_LLM_MODEL", "some-model")
    availability = detect_provider()
    assert availability.available is False
    assert "unsupported provider" in availability.reason


def test_frame_comparable_keys_cover_every_frame_field() -> None:
    """A new frame field must be classified, not silently ignored."""
    from aios.benchmark.fairness import _FATAL_KEYS, _NORMALIZABLE_KEYS

    classified = set(_FATAL_KEYS) | set(_NORMALIZABLE_KEYS)
    frame_fields = {field.name for field in dataclasses.fields(PhysicalFrame)}
    unclassified = frame_fields - classified - {"runtime_mode", "chdd_month_count"}
    assert not unclassified, f"unclassified frame fields: {sorted(unclassified)}"
