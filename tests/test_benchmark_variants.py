"""Benchmark variant discovery, reproduction checks and CLI wiring.

These tests read the repository's own experiment artifacts. They deliberately
do not run OPM: the physical comparison is an explicit benchmark invocation,
not something every unit test pays for.
"""

from __future__ import annotations

import pytest

from aios.benchmark.cli import ALL_VARIANTS, build_parser
from aios.benchmark.variants import (
    recorded_baseline,
    recorded_exp003,
    recorded_exp004,
)

# --- recorded references are read, never typed in -----------------------------


def test_baseline_reference_comes_from_the_repository_artifact() -> None:
    recorded = recorded_baseline()
    assert recorded.scenario_id == "EXP001_BASELINE"
    assert recorded.source == "experiments/exp001/baseline.json"
    assert len(recorded.schedule_sha256) == 64
    assert recorded.npv_mln_rub > 0


def test_exp003_reference_comes_from_the_repository_artifact() -> None:
    recorded = recorded_exp003()
    assert recorded.scenario_id.startswith("EXP003_")
    assert len(recorded.schedule_sha256) == 64
    assert recorded.npv_mln_rub > 0


def test_exp004_reference_comes_from_the_repository_artifact() -> None:
    recorded = recorded_exp004()
    assert recorded.scenario_id == "EXP004_C_JOINT_P500_I600"
    assert len(recorded.schedule_sha256) == 64
    assert recorded.npv_mln_rub > 0


def test_references_are_three_distinct_schedules() -> None:
    digests = {
        recorded_baseline().schedule_sha256,
        recorded_exp003().schedule_sha256,
        recorded_exp004().schedule_sha256,
    }
    assert len(digests) == 3


# --- schedule reproduction ----------------------------------------------------


def _context(model_y_case, tmp_path):
    from pathlib import Path

    from aios.benchmark.adapters import BenchmarkContext

    return BenchmarkContext(
        case=model_y_case,
        case_path=Path("cases/model_y_validation.json"),
        code_sha="test-sha",
        workspace_root=tmp_path,
        deck_reference="${MODEL_Y_ROOT}",
    )


def test_baseline_schedule_reproduces_bit_exactly(model_y_case, tmp_path) -> None:
    """The management baseline must still render to its recorded digest."""
    from aios.benchmark.variants import build_baseline
    from aios.schedule.generator import schedule_sha256

    _schedule, text = build_baseline(_context(model_y_case, tmp_path))
    assert schedule_sha256(text) == recorded_baseline().schedule_sha256


def test_exp004_best_schedule_reproduces_bit_exactly(model_y_case, tmp_path) -> None:
    from aios.benchmark.variants import build_exp004_best
    from aios.schedule.generator import schedule_sha256

    _schedule, text = build_exp004_best(_context(model_y_case, tmp_path))
    assert schedule_sha256(text) == recorded_exp004().schedule_sha256


def test_exp003_best_schedule_no_longer_reproduces(model_y_case, tmp_path) -> None:
    """A known, deliberate divergence -- recorded so it cannot be forgotten.

    The EXP003 winner is a supplemental-injection scenario, and its rendering
    changed when render_full_schedule started preserving history bytes. The
    historical NPV therefore describes a schedule current code does not build,
    which is exactly why that number is carried as a reference and not as a
    fresh result.
    """
    from aios.benchmark.variants import build_exp003_best
    from aios.schedule.generator import schedule_sha256

    _schedule, text = build_exp003_best(_context(model_y_case, tmp_path))
    assert schedule_sha256(text) != recorded_exp003().schedule_sha256


# --- CLI ----------------------------------------------------------------------


def test_cli_exposes_every_variant_through_one_entry_point() -> None:
    parser = build_parser()
    args = parser.parse_args(["--workspace-root", "/tmp/ws"])
    assert args.variants == ",".join(ALL_VARIANTS)
    assert set(ALL_VARIANTS) == {"baseline", "exp003", "exp004", "mas-off", "mas-on"}


def test_cli_requires_an_explicit_workspace_root() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_cli_defaults_do_not_need_source_edits() -> None:
    args = build_parser().parse_args(["--workspace-root", "/tmp/ws"])
    assert args.case == "cases/model_y_validation.json"
    assert args.decision_steps >= 1
    assert args.repeats >= 1


def test_compensation_policy_is_an_explicit_choice() -> None:
    args = build_parser().parse_args(
        ["--workspace-root", "/tmp/ws", "--compensation-policy", "RESEARCH_SCREENING_VRR"]
    )
    assert args.compensation_policy == "RESEARCH_SCREENING_VRR"


def test_mas_off_and_mas_on_share_every_non_cognitive_setting() -> None:
    """The ablation may differ in the cognitive branch and nowhere else."""
    import inspect

    from aios.benchmark import mas_variant

    source = inspect.getsource(mas_variant.run_mas_variant)
    # Both variants are produced by this single function, so the horizon,
    # budget, resolver, Guardian and CHDD cannot diverge between them.
    assert "llm_enabled: bool" in source
    assert "production_agent_factory" in source
    assert "injection_agent_factory" in source
    for forbidden in ("if llm_enabled:", "if not llm_enabled:"):
        assert forbidden not in source, "the cognitive flag must not branch the topology"


# --- MAS OFF vs MAS ON differ only in the cognitive dimension ------------------


def test_mas_on_injects_providers_without_changing_any_other_dimension() -> None:
    """The ablation flag may change the cognitive branch and nothing else."""
    import inspect

    from aios.benchmark import mas_variant

    signature = inspect.signature(mas_variant.run_mas_variant)
    cognitive = {
        "llm_enabled",
        "provider_id",
        "model_id",
        "run_id",
        "production_provider",
        "injection_provider",
    }
    physical = {
        "context",
        "variant_id",
        "initial_state_card",
        "simulation_budget",
        "decision_steps",
        "notes",
    }
    assert set(signature.parameters) == cognitive | physical

    source = inspect.getsource(mas_variant.run_mas_variant)
    # One horizon, one budget, one resolver, one Guardian, one CHDD for both.
    for shared in (
        "build_real_closed_loop",
        "composition.boundaries()",
        "_ContextualMagnitudeResolver",
        "HorizonContext",
    ):
        assert shared in source
    # The flag reaches the specialists only through the factories.
    assert source.count("llm_enabled=enabled") == 2


def test_mas_on_injection_factory_reads_live_provenance_off_the_adapter() -> None:
    """A successful live Injection completion must not default to LLM_LOCAL.

    `InjectionController.llm_source` is a static constructor argument (the
    `LlmProvider` protocol returns bare text and cannot carry its own
    provenance), so the wiring here must read it off the adapter instead of
    relying on the constructor default -- the same asymmetry that ships
    natively for Production via `ProviderResponse.source`.
    """
    import inspect

    from aios.benchmark import mas_variant

    source = inspect.getsource(mas_variant.run_mas_variant)
    assert 'getattr(injection_provider, "source", ProposalSource.LLM_LOCAL)' in source


def test_provider_is_absent_from_the_mas_off_configuration() -> None:
    """MAS OFF must never construct a live provider."""
    import inspect

    from aios.benchmark import cli

    source = inspect.getsource(cli.run_variants)
    off_block = source.split('if "mas-off" in requested:')[1].split('if "mas-on" in requested:')[0]
    assert "build_live_providers" not in off_block
    assert "llm_enabled=False" in off_block


def test_mas_on_manifest_records_provider_identity_but_no_secret() -> None:
    from aios.benchmark import BENCHMARK_SCHEMA_VERSION, CognitiveConfig

    cognitive = CognitiveConfig(
        llm_enabled=True, provider_id="anthropic", model_id="a-model", run_id="run-1"
    )
    body = cognitive.to_json()
    assert body["provider_id"] == "anthropic"
    assert body["model_id"] == "a-model"
    assert set(body) == {"llm_enabled", "provider_id", "model_id", "run_id", "seed"}
    assert BENCHMARK_SCHEMA_VERSION


def test_blocked_provider_message_names_the_missing_variable_not_a_value() -> None:
    from aios.benchmark import detect_provider

    availability = detect_provider()
    if not availability.available:
        assert "MAS_LLM" in availability.reason or "credential" in availability.reason
