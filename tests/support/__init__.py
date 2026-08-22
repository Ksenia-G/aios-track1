"""Test-only support package for Hybrid MAS v1.1 team foundation.

Everything here is TEST SUPPORT, not production API: no aios.* production
module may import from tests.support (see tests/test_team_foundation.py,
TF14/TF15). Factories build minimal valid instances of already-frozen
contracts; stubs are deterministic placeholders for components that do not
exist yet (specialist agents, Arbiter, MagnitudeResolver).
"""
