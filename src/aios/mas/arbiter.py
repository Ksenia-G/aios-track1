"""Arbiter structural contracts: Round2Request, DecisionBundle, ArbiterDecision.

Arbiter работает со structural proposals/bundles, ссылаясь на них по digest,
а не по значению: ни здесь, ни выше по цепочке до MagnitudeResolver не
появляется ни один numerical setpoint. ArbiterDecision гарантирует только
одно инвариантное свойство: HOLD и DETERMINISTIC_BASELINE не могут исчезнуть
из candidate space ни на одном decision step. Это не теорема о том, что
NPV(MAS) >= NPV(BASELINE) -- такой теоремы не существует.
"""

from __future__ import annotations

from dataclasses import dataclass

from aios.evaluation.canonical import JsonValue, digest
from aios.mas.common import AgentRole, CandidateKind


class ArbiterError(ValueError):
    """Arbiter contract нарушен."""


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArbiterError(f"{where}: expected non-empty string")
    return value


_ROUND2_ELIGIBLE_ROLES = (AgentRole.PRODUCTION, AgentRole.INJECTION)


@dataclass(frozen=True, slots=True)
class Round2Request:
    """Опциональный запрос на второй, deterministic-triggered раунд.

    ARBITER не может запросить второй раунд сам у себя -- только PRODUCTION
    и/или INJECTION специалистов. Conflict predicate (что именно вызывает
    Round 2) в этом контракте не реализован.
    """

    requested_roles: tuple[AgentRole, ...]
    conflict_code: str
    proposal_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.conflict_code, "round2_request.conflict_code")
        if not self.requested_roles:
            raise ArbiterError("round2_request.requested_roles: must be non-empty")
        seen: set[AgentRole] = set()
        for role in self.requested_roles:
            role = AgentRole(role)
            if role not in _ROUND2_ELIGIBLE_ROLES:
                raise ArbiterError(
                    f"round2_request.requested_roles: {role!r} is not eligible for Round 2"
                )
            if role in seen:
                raise ArbiterError(f"round2_request.requested_roles: duplicate role {role!r}")
            seen.add(role)
        for proposal_digest in self.proposal_digests:
            _require_text(proposal_digest, "round2_request.proposal_digests")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "requested_roles": [str(role) for role in self.requested_roles],
            "conflict_code": self.conflict_code,
            "proposal_digests": list(self.proposal_digests),
        }


@dataclass(frozen=True, slots=True)
class DecisionBundle:
    """Один кандидат в candidate space: ссылки на proposals/intents по digest.

    Никаких setpoints: числовое воплощение появляется только на границе
    MagnitudeResolver (aios.mas.magnitude), не здесь.
    """

    kind: CandidateKind
    production_proposal_digest: str | None = None
    injection_proposal_digest: str | None = None
    production_intent_digests: tuple[str, ...] = ()
    injection_intent_digests: tuple[str, ...] = ()
    priority_rank: int = 0
    exploration: bool = False

    def __post_init__(self) -> None:
        kind = CandidateKind(self.kind)
        if self.priority_rank < 0:
            raise ArbiterError("decision_bundle.priority_rank: must be >= 0")

        has_proposal_ref = bool(self.production_proposal_digest or self.injection_proposal_digest)
        has_intent_ref = bool(self.production_intent_digests or self.injection_intent_digests)

        if kind is CandidateKind.HOLD:
            if has_proposal_ref:
                raise ArbiterError("decision_bundle.HOLD: must not reference proposal digests")
            if has_intent_ref:
                raise ArbiterError("decision_bundle.HOLD: must not reference intent digests")
        elif kind is CandidateKind.AGENT_BUNDLE:
            if not (has_proposal_ref or has_intent_ref):
                raise ArbiterError(
                    "decision_bundle.AGENT_BUNDLE: requires at least one specialist "
                    "proposal/intent reference"
                )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "kind": str(self.kind),
            "production_proposal_digest": self.production_proposal_digest,
            "injection_proposal_digest": self.injection_proposal_digest,
            "production_intent_digests": list(self.production_intent_digests),
            "injection_intent_digests": list(self.injection_intent_digests),
            "priority_rank": self.priority_rank,
            "exploration": self.exploration,
        }


@dataclass(frozen=True, slots=True)
class ArbiterDecision:
    """Итог работы Arbiter: candidate space для последующего MagnitudeResolver."""

    schema_version: str
    arbiter_envelope_digest: str
    bundles: tuple[DecisionBundle, ...]
    round2_request: Round2Request | None = None

    def __post_init__(self) -> None:
        _require_text(self.schema_version, "arbiter_decision.schema_version")
        _require_text(self.arbiter_envelope_digest, "arbiter_decision.arbiter_envelope_digest")
        kinds = {bundle.kind for bundle in self.bundles}
        if CandidateKind.HOLD not in kinds:
            raise ArbiterError("arbiter_decision.bundles: must include CandidateKind.HOLD")
        if CandidateKind.DETERMINISTIC_BASELINE not in kinds:
            raise ArbiterError(
                "arbiter_decision.bundles: must include CandidateKind.DETERMINISTIC_BASELINE"
            )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "arbiter_envelope_digest": self.arbiter_envelope_digest,
            "bundles": [bundle.to_json() for bundle in self.bundles],
            "round2_request": self.round2_request.to_json() if self.round2_request else None,
        }

    def digest(self) -> str:
        return digest(self.to_json())
