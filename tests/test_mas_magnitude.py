"""MagnitudeCandidate: the first contract layer where setpoints are legal."""

from __future__ import annotations

import ast
import datetime
from pathlib import Path

import pytest

from aios.mas.common import CandidateKind
from aios.mas.magnitude import MagnitudeCandidate, MagnitudeCandidateError
from aios.schedule import InjectorControl, ProducerControl, WellStatus

MAS_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "aios" / "mas"


def _producer(well: str = "P1", liquid_rate: float = 150.0) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=liquid_rate)


def _injector(well: str = "I1", surface_rate: float = 200.0) -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=surface_rate
    )


def _candidate(**overrides: object) -> MagnitudeCandidate:
    fields = {
        "candidate_kind": CandidateKind.AGENT_BUNDLE,
        "bundle_digest": "b" * 64,
        "effective_date": datetime.date(2020, 4, 1),
        "producers": (_producer("P1"), _producer("P2")),
        "injectors": (_injector("I1"),),
        "source_intent_digests": ("d" * 64,),
        "resolver_version": "aios.mas.magnitude/1.0.0",
    }
    fields.update(overrides)
    return MagnitudeCandidate(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# C28 / C29: accepts existing ProducerControl / InjectorControl.
# ---------------------------------------------------------------------------


def test_magnitude_candidate_accepts_producer_control() -> None:
    candidate = _candidate(injectors=())
    assert candidate.producers[0].well == "P1"


def test_magnitude_candidate_accepts_injector_control() -> None:
    candidate = _candidate(producers=())
    assert candidate.injectors[0].well == "I1"


# ---------------------------------------------------------------------------
# C30: rejects same well as producer and injector in the same candidate.
# ---------------------------------------------------------------------------


def test_magnitude_candidate_rejects_well_as_both_producer_and_injector() -> None:
    with pytest.raises(MagnitudeCandidateError):
        _candidate(
            producers=(_producer("W1"),),
            injectors=(_injector("W1"),),
        )


# ---------------------------------------------------------------------------
# C31: digest invariant to control ordering.
# ---------------------------------------------------------------------------


def test_magnitude_candidate_digest_ignores_control_ordering() -> None:
    a = _candidate(producers=(_producer("P1"), _producer("P2")))
    b = _candidate(producers=(_producer("P2"), _producer("P1")))
    assert a.digest() == b.digest()


def test_magnitude_candidate_digest_changes_with_rate() -> None:
    a = _candidate(producers=(_producer("P1", liquid_rate=150.0),))
    b = _candidate(producers=(_producer("P1", liquid_rate=151.0),))
    assert a.digest() != b.digest()


def test_magnitude_candidate_canonical_deduplicates_exact_repeats() -> None:
    a = _candidate(producers=(_producer("P1"), _producer("P1")))
    b = _candidate(producers=(_producer("P1"),))
    assert a.digest() == b.digest()


def test_magnitude_candidate_requires_bundle_digest_and_resolver_version() -> None:
    with pytest.raises(MagnitudeCandidateError):
        _candidate(bundle_digest="")
    with pytest.raises(MagnitudeCandidateError):
        _candidate(resolver_version="")


# ---------------------------------------------------------------------------
# C32: MagnitudeCandidate is the first contract layer importing numerical
# schedule controls. No other aios.mas module may import ProducerControl or
# InjectorControl.
# ---------------------------------------------------------------------------


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_only_magnitude_module_imports_numerical_schedule_controls() -> None:
    # invariants.py (MAS-INVARIANTS-001) is the second and only other
    # recognized boundary module: it audits/compares already-resolved
    # controls (HOLD carry-forward, canonical identity) but does not decide
    # which controls to produce -- that remains magnitude.py's job. The
    # shared constant is the single source of truth for this boundary set.
    from aios.mas.invariants import NUMERICAL_CONTROL_BOUNDARY_MODULES

    offenders: list[str] = []
    for path in sorted(MAS_SRC_DIR.glob("*.py")):
        if path.name in NUMERICAL_CONTROL_BOUNDARY_MODULES or path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_names(tree)
        if "ProducerControl" in imported or "InjectorControl" in imported:
            offenders.append(path.name)
    assert offenders == [], f"unexpected numerical control import in: {offenders}"

    magnitude_tree = ast.parse((MAS_SRC_DIR / "magnitude.py").read_text(encoding="utf-8"))
    magnitude_imports = _imported_names(magnitude_tree)
    assert "ProducerControl" in magnitude_imports
    assert "InjectorControl" in magnitude_imports
