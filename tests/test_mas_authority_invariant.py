"""T2 (I5) -- AST/package-level import authority audit.

Structural dataclass-field checks (I4/I6) live in test_mas_invariants.py.
This file only verifies that ProducerControl/InjectorControl imports have
not spread into the specialist decision layer (intents/proposals/envelopes/
arbiter/common/evidence/protocols). magnitude.py and invariants.py are the
two recognized boundary modules that legitimately handle already-resolved
numerical controls -- magnitude.py produces them, invariants.py audits/
compares them downstream (HOLD carry-forward, canonical identity).
"""

from __future__ import annotations

from aios.mas.invariants import (
    NUMERICAL_CONTROL_BOUNDARY_MODULES,
    audit_numerical_control_imports,
    require_numerical_control_import_boundary,
)


def test_i5_no_forbidden_numerical_control_imports_outside_boundary() -> None:
    offenders = audit_numerical_control_imports()
    assert offenders == (), f"unexpected ProducerControl/InjectorControl import in: {offenders}"


def test_i5_require_numerical_control_import_boundary_passes_for_real_package() -> None:
    require_numerical_control_import_boundary()


def test_boundary_modules_are_exactly_magnitude_and_invariants() -> None:
    assert NUMERICAL_CONTROL_BOUNDARY_MODULES == frozenset({"magnitude.py", "invariants.py"})


def test_magnitude_and_invariants_do_import_numerical_controls() -> None:
    import ast
    from pathlib import Path

    mas_dir = Path(__file__).resolve().parents[1] / "src" / "aios" / "mas"
    for name in NUMERICAL_CONTROL_BOUNDARY_MODULES:
        tree = ast.parse((mas_dir / name).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom | ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "ProducerControl" in imported, name
        assert "InjectorControl" in imported, name
