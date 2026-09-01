"""Authority boundary for aios.mas.arbitration: deterministic core, no execution.

The Arbiter reads specialist proposals and produces structural DecisionBundle
references by digest. It must never import numerical schedule controls, the
resolved-candidate boundary type, the simulator, CHDD, or Guardian -- those
are strictly downstream of Arbiter -> MagnitudeResolver -> simulation.
"""

from __future__ import annotations

import ast
from pathlib import Path

ARBITRATION_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "aios" / "mas" / "arbitration"

# A25-A30
FORBIDDEN_IMPORT_NAMES = frozenset(
    {
        "ProducerControl",
        "InjectorControl",
        "MagnitudeCandidate",
        "Simulator",
        "SimulatorRuntime",
        "SimulationResult",
        "SimulationRequest",
        "ConstraintGuardian",
    }
)

FORBIDDEN_MODULE_PREFIXES = (
    "aios.schedule",
    "aios.evaluation.simulator",
    "aios.evaluation.simulation",
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


def test_arbitration_package_exists() -> None:
    assert ARBITRATION_SRC_DIR.is_dir()


def test_a25_a30_no_forbidden_authority_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(ARBITRATION_SRC_DIR.glob("*.py")):
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
    assert offenders == {}, f"forbidden authority import in: {offenders}"


# A31 / A32 / A33
def test_a31_a32_a33_no_llm_random_or_latency_dependency() -> None:
    forbidden_tokens = (
        "openai",
        "anthropic",
        "import random",
        "from random",
        "time.time",
        "datetime.now",
        "latency",
        "wall_clock",
        "wall-clock",
    )
    for path in sorted(ARBITRATION_SRC_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, f"{path.name} unexpectedly references {token!r}"


# A34 -- ArbiterDecision stays the frozen contract type, not duplicated.
def test_a34_no_duplicate_arbiter_decision_type() -> None:
    from aios.mas.arbiter import ArbiterDecision as FrozenArbiterDecision
    from aios.mas.arbitration.engine import DeterministicArbiter

    assert not hasattr(DeterministicArbiter, "ArbiterDecision")
    import aios.mas.arbitration.engine as engine_module

    for obj in vars(engine_module).values():
        if isinstance(obj, type) and obj.__name__ == "ArbiterDecision":
            assert obj is FrozenArbiterDecision


# A35 -- no new CandidateKind enum.
def test_a35_no_new_candidate_kind_enum() -> None:
    import aios.mas.arbitration.engine as engine_module
    from aios.mas.common import CandidateKind as FrozenCandidateKind

    for obj in vars(engine_module).values():
        if isinstance(obj, type) and obj.__name__ == "CandidateKind":
            assert obj is FrozenCandidateKind


# A36 -- no shared frozen contract file modified by this task.
def test_a36_frozen_contract_files_unmodified() -> None:
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
