"""Injectable Round2 conflict policy.

ARBITER-001 не решает, что именно является petroleum-domain конфликтом
между Production и Injection primary proposals -- многие внешне
противоположные действия могут быть частью правильной совместной
стратегии, и калиброванного правила для этого пока нет. Поэтому conflict
predicate вынесен в injectable policy, а не зашит в DeterministicArbiter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from aios.mas.arbiter import Round2Request
from aios.mas.envelopes import ArbiterEnvelope


@runtime_checkable
class ConflictPolicy(Protocol):
    """Решает, нужен ли deterministic-triggered Round 2."""

    def evaluate(self, envelope: ArbiterEnvelope) -> Round2Request | None: ...


class NoRound2ConflictPolicy:
    """Default ARBITER-001 policy: детерминированно никогда не запрашивает Round 2.

    Это не суждение о том, что конфликтов не бывает -- это честный
    placeholder: wiring для Round 2 существует и протестирован
    (DeterministicArbiter принимает любую ConflictPolicy), а калиброванный
    domain conflict predicate -- отдельная будущая задача.
    """

    def evaluate(self, envelope: ArbiterEnvelope) -> Round2Request | None:
        return None
