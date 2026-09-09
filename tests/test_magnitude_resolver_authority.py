"""Authority boundary for aios.mas.magnitude_resolution.

MagnitudeResolver is the FIRST prescriptive numerical boundary: it may
create ProducerControl/InjectorControl/MagnitudeCandidate, but must never
call the simulator, read a future SimulationResult, call Guardian/CHDD, use
an official compensation/water rule, or depend on random/wall-clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

MR_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "aios" / "mas" / "magnitude_resolution"

# A4-A9
FORBIDDEN_IMPORT_NAMES = frozenset(
    {
        "Simulator",
        "SimulatorRuntime",
        "SimulationResult",
        "ConstraintGuardian",
        "NpvEvaluator",
        "NpvEstimate",
    }
)

FORBIDDEN_MODULE_PREFIXES = (
    "aios.evaluation.simulator",
    "aios.evaluation.simulation",
    "aios.evaluation.npv",
    "aios.chdd",
    "aios.agents.constraint_guardian",
)


def _imported_names_and_modules(tree: ast.Module) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                names.add(alias.name)
    return names, modules


def test_package_exists() -> None:
    assert MR_SRC_DIR.is_dir()


# A1 / A2 / A3 -- resolver may import/create the frozen numerical types.
def test_a1_a2_a3_resolver_imports_producer_and_injector_control() -> None:
    tree = ast.parse((MR_SRC_DIR / "engine.py").read_text(encoding="utf-8"))
    names, _modules = _imported_names_and_modules(tree)
    assert "ProducerControl" in names
    assert "InjectorControl" in names
    assert "MagnitudeCandidate" in names


# A4-A9
def test_a4_a9_no_forbidden_downstream_authority_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(MR_SRC_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names, modules = _imported_names_and_modules(tree)
        hit_names = names & FORBIDDEN_IMPORT_NAMES
        hit_modules = {
            module
            for module in modules
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in FORBIDDEN_MODULE_PREFIXES
            )
        }
        hit = hit_names | hit_modules
        if hit:
            offenders[path.name] = hit
    assert offenders == {}, f"forbidden downstream-authority import in: {offenders}"


# A10 / A11 -- no official compensation/water rule vocabulary.
def test_a10_a11_no_official_compensation_or_water_rule() -> None:
    forbidden_tokens = (
        "official_compensation",
        "is_officially_compliant",
        "official_water_rule",
        "water_rule_gate",
        "officially_compliant",
    )
    for path in sorted(MR_SRC_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, f"{path.name} unexpectedly references {token!r}"


# A9 (LLM/provider) / A12 / A13 -- no LLM, no random, no wall-clock ranking.
def test_a9_a12_a13_no_llm_random_or_wallclock_dependency() -> None:
    forbidden_tokens = (
        "openai",
        "anthropic",
        "import random",
        "from random",
        "time.time",
        "datetime.now",
        "latency",
    )
    for path in sorted(MR_SRC_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, f"{path.name} unexpectedly references {token!r}"


# A14 / A15 -- no new control dataclass, no new CandidateKind.
def test_a14_no_new_control_dataclass() -> None:
    # engine.py legitimately IMPORTS (reuses) ProducerControl/InjectorControl
    # -- that's not a redefinition. What must never happen is a *second*
    # class also named ProducerControl/InjectorControl bound in this module.
    import aios.mas.magnitude_resolution.engine as engine_module
    import aios.mas.magnitude_resolution.profile as profile_module
    from aios.schedule import InjectorControl as FrozenInjectorControl
    from aios.schedule import ProducerControl as FrozenProducerControl

    for module in (engine_module, profile_module):
        producer_candidate = getattr(module, "ProducerControl", None)
        if producer_candidate is not None:
            assert producer_candidate is FrozenProducerControl, module.__name__
        injector_candidate = getattr(module, "InjectorControl", None)
        if injector_candidate is not None:
            assert injector_candidate is FrozenInjectorControl, module.__name__


def test_a15_no_new_candidate_kind_enum() -> None:
    import aios.mas.magnitude_resolution.engine as engine_module
    from aios.mas.common import CandidateKind as FrozenCandidateKind

    for obj in vars(engine_module).values():
        if isinstance(obj, type) and obj.__name__ == "CandidateKind":
            assert obj is FrozenCandidateKind


# A16 -- frozen magnitude.py (and all other frozen contracts) unchanged.
def test_a16_frozen_contract_files_unmodified() -> None:
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    frozen_files = [
        "src/aios/mas/__init__.py",
        "src/aios/mas/common.py",
        "src/aios/mas/intents.py",
        "src/aios/mas/envelopes.py",
        "src/aios/mas/proposals.py",
        "src/aios/mas/arbiter.py",
        "src/aios/mas/magnitude.py",
        "src/aios/mas/invariants.py",
        "src/aios/mas/protocols.py",
    ]
    result = subprocess.run(
        ["git", "status", "--porcelain", *frozen_files],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", f"frozen contract files touched: {result.stdout}"


# Specialist-zone isolation.
def test_does_not_touch_specialist_zones() -> None:
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    team_zones = [
        "src/aios/mas/specialists/injection",
        "src/aios/mas/specialists/production",
    ]
    result = subprocess.run(
        ["git", "status", "--porcelain", *team_zones],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == ""


# Resolver conforms to the frozen Protocol.
def test_resolver_conforms_to_frozen_magnitude_resolver_protocol() -> None:
    import datetime

    from aios.mas.magnitude_resolution.engine import (
        DeterministicMagnitudeResolver,
        MagnitudeContext,
    )
    from aios.mas.protocols import MagnitudeResolver as FrozenMagnitudeResolverProtocol
    from tests.support.mas_team_fixtures import synthetic_arbiter_envelope

    envelope = synthetic_arbiter_envelope()
    context = MagnitudeContext(
        effective_date=datetime.date(2020, 4, 1),
        arbiter_envelope=envelope,
        previous_producers=(),
        previous_injectors=(),
        baseline_producers=(),
        baseline_injectors=(),
    )
    resolver = DeterministicMagnitudeResolver(context=context)
    assert isinstance(resolver, FrozenMagnitudeResolverProtocol)
