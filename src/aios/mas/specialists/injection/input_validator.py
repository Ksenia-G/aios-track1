"""Первая стадия Injection Controller: проверка официального входа.

Валидатор не предлагает действий и не создаёт setpoints. Он только решает,
можно ли вообще кормить `InjectionEnvelope` остальному пайплайну.
"""

from __future__ import annotations

from aios.mas.common import MAS_CONTRACT_SCHEMA_VERSION, AgentRole, DecisionRound
from aios.mas.envelopes import InjectionEnvelope
from aios.mas.intents import InjectionAction


class InputValidationError(ValueError):
    """InjectionEnvelope не годится как вход специалиста."""


class InputValidator:
    """Детерминированные ворота на входе Injection Controller."""

    def validate(self, envelope: object) -> InjectionEnvelope:
        if not isinstance(envelope, InjectionEnvelope):
            raise InputValidationError(f"expected InjectionEnvelope, got {type(envelope).__name__}")
        if envelope.schema_version != MAS_CONTRACT_SCHEMA_VERSION:
            raise InputValidationError(
                f"schema_version {envelope.schema_version!r} != {MAS_CONTRACT_SCHEMA_VERSION!r}"
            )
        if not envelope.allowed_actions:
            raise InputValidationError("allowed_actions must not be empty")
        if InjectionAction.HOLD not in envelope.allowed_actions:
            raise InputValidationError("allowed_actions must include HOLD")
        if envelope.round is DecisionRound.ROUND_1 and envelope.peer_proposals:
            raise InputValidationError("ROUND_1 must not carry peer_proposals")
        if envelope.round is DecisionRound.ROUND_2:
            _require_production_peers(envelope)
        return envelope


def _require_production_peers(envelope: InjectionEnvelope) -> None:
    if not envelope.peer_proposals:
        raise InputValidationError("ROUND_2 requires non-empty peer_proposals")
    for index, reference in enumerate(envelope.peer_proposals):
        if reference.role is not AgentRole.PRODUCTION:
            raise InputValidationError(
                f"ROUND_2 peer_proposals[{index}] must be PRODUCTION, got {reference.role}"
            )
