"""Team foundation contract test: proves the parallel-work starting point works.

This does not implement any real specialist/orchestrator/resolver -- it only
proves that a new team member can build valid frozen-contract objects and
exercise the existing system invariants using tests/support fixtures/stubs,
without designing API from scratch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# TF1 -- canonical MAS contract imports available.
# ---------------------------------------------------------------------------


def test_tf1_canonical_mas_contract_imports_available() -> None:
    from aios.mas import (
        ArbiterDecision,
        ArbiterEnvelope,
        CandidateKind,
        DecisionBundle,
        HorizonContext,
        InjectionAgentOutput,
        InjectionEnvelope,
        InjectionProposal,
        MagnitudeCandidate,
        MemorySlice,
        ProductionAgentOutput,
        ProductionEnvelope,
        ProductionProposal,
        require_ablation_equivalence,
        require_hold_carry_forward,
        require_simulation_floor,
    )

    assert all(
        obj is not None
        for obj in (
            ArbiterDecision,
            ArbiterEnvelope,
            CandidateKind,
            DecisionBundle,
            HorizonContext,
            InjectionAgentOutput,
            InjectionEnvelope,
            InjectionProposal,
            MagnitudeCandidate,
            MemorySlice,
            ProductionAgentOutput,
            ProductionEnvelope,
            ProductionProposal,
            require_ablation_equivalence,
            require_hold_carry_forward,
            require_simulation_floor,
        )
    )


# ---------------------------------------------------------------------------
# TF2 -- PressureDiagnostic / SweepDiagnostic available.
# ---------------------------------------------------------------------------


def test_tf2_diagnostic_services_available() -> None:
    from aios.diagnostics import PressureDiagnostic, SweepDiagnostic

    assert PressureDiagnostic is not None
    assert SweepDiagnostic is not None


# ---------------------------------------------------------------------------
# TF3 / TF4 -- envelope fixtures build valid envelopes.
# ---------------------------------------------------------------------------


def test_tf3_injection_fixture_builds_valid_envelope() -> None:
    from aios.mas.envelopes import InjectionEnvelope
    from tests.support.mas_team_fixtures import synthetic_injection_envelope

    envelope = synthetic_injection_envelope()
    assert isinstance(envelope, InjectionEnvelope)


def test_tf4_production_fixture_builds_valid_envelope() -> None:
    from aios.mas.envelopes import ProductionEnvelope
    from tests.support.mas_team_fixtures import synthetic_production_envelope

    envelope = synthetic_production_envelope()
    assert isinstance(envelope, ProductionEnvelope)


# ---------------------------------------------------------------------------
# TF5 / TF6 -- stub agents return valid outputs.
# ---------------------------------------------------------------------------


def test_tf5_stub_injection_agent_returns_valid_output() -> None:
    from aios.mas.proposals import InjectionAgentOutput
    from tests.support.mas_team_fixtures import synthetic_injection_envelope
    from tests.support.mas_team_stubs import StubInjectionAgent

    output = StubInjectionAgent().propose(synthetic_injection_envelope())
    assert isinstance(output, InjectionAgentOutput)


def test_tf6_stub_production_agent_returns_valid_output() -> None:
    from aios.mas.proposals import ProductionAgentOutput
    from tests.support.mas_team_fixtures import synthetic_production_envelope
    from tests.support.mas_team_stubs import StubProductionAgent

    output = StubProductionAgent().propose(synthetic_production_envelope())
    assert isinstance(output, ProductionAgentOutput)


# ---------------------------------------------------------------------------
# TF7 -- stub outputs use deterministic baseline proposal source.
# ---------------------------------------------------------------------------


def test_tf7_stub_outputs_use_deterministic_baseline_source() -> None:
    from aios.mas.common import ProposalSource
    from tests.support.mas_team_fixtures import (
        synthetic_injection_envelope,
        synthetic_production_envelope,
    )
    from tests.support.mas_team_stubs import StubInjectionAgent, StubProductionAgent

    production_output = StubProductionAgent().propose(synthetic_production_envelope())
    injection_output = StubInjectionAgent().propose(synthetic_injection_envelope())

    assert production_output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert injection_output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE


# ---------------------------------------------------------------------------
# TF8 -- baseline absent from specialist envelope.
# ---------------------------------------------------------------------------


def test_tf8_baseline_absent_from_specialist_envelope() -> None:
    import dataclasses

    from aios.mas.envelopes import InjectionEnvelope, ProductionEnvelope

    for cls in (ProductionEnvelope, InjectionEnvelope):
        field_names = {f.name for f in dataclasses.fields(cls)}
        assert "baseline" not in field_names
        assert "baseline_proposal" not in field_names


# ---------------------------------------------------------------------------
# TF9 -- arbiter fixture/decision contains HOLD + DETERMINISTIC_BASELINE.
# ---------------------------------------------------------------------------


def test_tf9_arbiter_decision_fixture_contains_hold_and_baseline() -> None:
    from aios.mas.common import CandidateKind
    from tests.support.mas_team_fixtures import synthetic_arbiter_decision

    decision = synthetic_arbiter_decision()
    kinds = {bundle.kind for bundle in decision.bundles}
    assert CandidateKind.HOLD in kinds
    assert CandidateKind.DETERMINISTIC_BASELINE in kinds


# ---------------------------------------------------------------------------
# TF10 -- MagnitudeCandidate fixture uses existing ProducerControl/InjectorControl.
# ---------------------------------------------------------------------------


def test_tf10_magnitude_candidate_fixture_uses_existing_controls() -> None:
    from aios.schedule import InjectorControl, ProducerControl
    from tests.support.mas_team_fixtures import synthetic_magnitude_candidate

    candidate = synthetic_magnitude_candidate()
    assert all(isinstance(control, ProducerControl) for control in candidate.producers)
    assert all(isinstance(control, InjectorControl) for control in candidate.injectors)


# ---------------------------------------------------------------------------
# TF11 -- require_simulation_floor passes on prepared candidate set.
# ---------------------------------------------------------------------------


def test_tf11_simulation_floor_guard_passes_on_prepared_candidates() -> None:
    from aios.mas.invariants import require_simulation_floor
    from tests.support.mas_team_fixtures import synthetic_candidate_set_with_floor

    require_simulation_floor(synthetic_candidate_set_with_floor())


# ---------------------------------------------------------------------------
# TF12 -- HOLD carry-forward fixture passes T6 guard.
# ---------------------------------------------------------------------------


def test_tf12_hold_carry_forward_fixture_passes_guard() -> None:
    from aios.mas.invariants import require_hold_carry_forward
    from tests.support.mas_team_fixtures import synthetic_hold_carry_forward_pair

    previous, held = synthetic_hold_carry_forward_pair()
    require_hold_carry_forward(
        previous_producers=previous[0],
        previous_injectors=previous[1],
        held_producers=held[0],
        held_injectors=held[1],
    )


# ---------------------------------------------------------------------------
# TF13 -- ablation configs differing only by llm_enabled pass the guard.
# ---------------------------------------------------------------------------


def test_tf13_ablation_fixture_passes_guard() -> None:
    from aios.mas.invariants import require_ablation_equivalence
    from tests.support.mas_team_fixtures import synthetic_ablation_config_pair

    config_a, config_b = synthetic_ablation_config_pair()
    require_ablation_equivalence(config_a, config_b)


# ---------------------------------------------------------------------------
# TF14 / TF15 -- test support isolation.
# ---------------------------------------------------------------------------


def _imported_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_tf14_support_modules_are_not_imported_by_production_code() -> None:
    src_dir = REPO_ROOT / "src"
    offenders: list[str] = []
    for path in sorted(src_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_module_names(tree)
        if any(name == "tests.support" or name.startswith("tests.support.") for name in imported):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"production code imports tests.support: {offenders}"


def test_tf15_no_production_module_imports_tests_support() -> None:
    # Same guarantee stated the other way round, matching the task wording
    # literally: covers both "aios.*" (src layout) and any future top-level
    # production package that is not under tests/.
    src_dir = REPO_ROOT / "src"
    for path in sorted(src_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "tests.support" not in text, f"{path}: references tests.support"


# ---------------------------------------------------------------------------
# TF16 -- team fixtures create no new simulation/result/control duplicate types.
# ---------------------------------------------------------------------------


def test_tf16_fixtures_module_defines_no_duplicate_authority_types() -> None:
    import tests.support.mas_team_fixtures as fixtures_module

    forbidden_class_names = {
        "SimulationRequest",
        "SimulationResult",
        "ProducerControl",
        "InjectorControl",
        "StateCard",
    }
    defined_here = {
        name
        for name, obj in vars(fixtures_module).items()
        if isinstance(obj, type) and obj.__module__ == fixtures_module.__name__
    }
    assert defined_here & forbidden_class_names == set()


# ---------------------------------------------------------------------------
# TF17 / TF18 / TF19 -- team docs exist.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs/team/README.md",
        "docs/team/injection-controller.md",
        "docs/team/production-controller.md",
        "docs/team/closed-loop-orchestrator.md",
        "docs/team/runtime-execution.md",
        "docs/team/authority-matrix.md",
        "docs/team/organizer-constraints.md",
        "docs/team/branching-and-handoff.md",
    ],
)
def test_tf17_tf18_tf19_role_and_authority_docs_exist(relative_path: str) -> None:
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"missing team document: {relative_path}"
    assert path.stat().st_size > 0


# ---------------------------------------------------------------------------
# TF20 -- README links the team starting point.
# ---------------------------------------------------------------------------


def test_tf20_readme_links_team_starting_point() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/team/README.md" in text
