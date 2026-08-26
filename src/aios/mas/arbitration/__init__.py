"""Deterministic arbitration core (ARBITER-001) over frozen aios.mas contracts.

Cognitive/provider Arbiter branch is a separate future task (ARBITER-002).
This package is not re-exported from aios.mas.__init__: import it explicitly
as `aios.mas.arbitration` to keep the frozen aios.mas top-level surface
unmodified.
"""

from aios.mas.arbitration.engine import ArbitrationError, DeterministicArbiter
from aios.mas.arbitration.policy import ConflictPolicy, NoRound2ConflictPolicy

__all__ = [
    "ArbitrationError",
    "ConflictPolicy",
    "DeterministicArbiter",
    "NoRound2ConflictPolicy",
]
