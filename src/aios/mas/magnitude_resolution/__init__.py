"""MAGNITUDE-RESOLVER-001: first prescriptive numerical boundary.

`ArbiterDecision`/structural contracts remain frozen and unchanged (see
`src/aios/mas/arbiter.py`, `src/aios/mas/magnitude.py`). Everything in this
package is implementation (central integration write zone), not a new
frozen contract, and is not re-exported from `aios.mas.__init__` -- import
it explicitly as `aios.mas.magnitude_resolution`.

Cognitive/provider (LLM) resolver is a separate future task.
"""

from aios.mas.magnitude_resolution.capability import (
    ActionCapability,
    injection_capabilities,
    production_capabilities,
    supported_injection_actions,
    supported_production_actions,
)
from aios.mas.magnitude_resolution.engine import (
    DeterministicMagnitudeResolver,
    MagnitudeContext,
    MagnitudeResolutionError,
)
from aios.mas.magnitude_resolution.profile import (
    PROFILE_VERSION,
    MagnitudeAnchor,
    ModelYEmpiricalMagnitudeProfile,
)

__all__ = [
    "PROFILE_VERSION",
    "ActionCapability",
    "DeterministicMagnitudeResolver",
    "MagnitudeAnchor",
    "MagnitudeContext",
    "MagnitudeResolutionError",
    "ModelYEmpiricalMagnitudeProfile",
    "injection_capabilities",
    "production_capabilities",
    "supported_injection_actions",
    "supported_production_actions",
]
