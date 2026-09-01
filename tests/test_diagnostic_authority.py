"""Authority boundary for aios.diagnostics: evidence service, not a decision maker.

PressureDiagnostic/SweepDiagnostic describe field/well state. They must never
import or return aios.mas decision-layer types (Intent, Proposal, Arbiter
decision, or numerical schedule controls) -- that would violate NO SETPOINTS
CROSS and give a diagnostic service decision authority it must not have.
"""

from __future__ import annotations

import ast
from pathlib import Path

DIAGNOSTICS_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "aios" / "diagnostics"

FORBIDDEN_IMPORT_NAMES = frozenset(
    {
        "ProductionIntent",
        "InjectionIntent",
        "ProductionProposal",
        "InjectionProposal",
        "ArbiterDecision",
        "MagnitudeCandidate",
        "ProducerControl",
        "InjectorControl",
    }
)


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names.update(alias.name for alias in node.names)
    return names


# D3
def test_legacy_pressure_agent_import_still_works() -> None:
    from aios.agents.pressure_agent import PressureAgent

    assert PressureAgent is not None


# D4
def test_legacy_sweep_agent_import_still_works() -> None:
    from aios.agents.sweep_agent import SweepAgent

    assert SweepAgent is not None


# D9-D14
def test_diagnostics_package_imports_no_decision_layer_types() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(DIAGNOSTICS_SRC_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_names(tree)
        hit = imported & FORBIDDEN_IMPORT_NAMES
        if hit:
            offenders[path.name] = hit
    assert offenders == {}, f"forbidden decision-layer import in: {offenders}"


# D21
def test_diagnostics_source_never_references_aios_mas() -> None:
    # Static AST check, not a runtime sys.modules probe: mutating the shared
    # module cache mid-session would corrupt every other test module that
    # already bound names (enum members, dataclasses) from an aios.mas
    # module imported earlier in the same pytest process.
    for path in sorted(DIAGNOSTICS_SRC_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module.split(".")[:2] != ["aios", "mas"], (
                    f"{path.name} imports from {node.module!r}: "
                    "aios.diagnostics must not depend on aios.mas"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aios.mas"), (
                        f"{path.name} imports {alias.name!r}: "
                        "aios.diagnostics must not depend on aios.mas"
                    )


# D22
def test_diagnostics_package_has_no_llm_or_provider_dependency() -> None:
    forbidden_tokens = ("openai", "anthropic", "llm", "provider", "requests", "httpx")
    for path in sorted(DIAGNOSTICS_SRC_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, f"{path.name} unexpectedly mentions {token!r}"
