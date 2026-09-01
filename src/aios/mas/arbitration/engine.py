"""DeterministicArbiter -- production-ready deterministic arbitration core.

Второй эшелон Hybrid MAS v1.1: принимает уже готовые
`ProductionAgentOutput`/`InjectionAgentOutput`, составляет structural
`DecisionBundle`s и возвращает frozen `ArbiterDecision`. Ничего здесь не
создаёт numerical setpoints, не запускает симулятор и не считает
CHDD/compensation -- см. `docs/architecture/mas-v1.1-arbiter.md`.

КЛЮЧЕВОЕ РАЗЛИЧЕНИЕ: frozen `ProductionIntent.digest()`/`InjectionIntent.digest()`
включают `evidence_ids`/`uncertainty`/`rationale_short` (аудируемые поля, не
prescriptive структура), а `Proposal.digest()` дополнительно включает
`source`/`provenance`. Ни то, ни другое не годится как критерий novelty или
структурного дедупликации candidate bundles: LLM и deterministic fallback с
абсолютно одинаковыми Intent не должны тратить два слота simulation budget
только из-за разного `model_id`/`prompt_digest`. Поэтому этот модуль строит
собственный internal structural signature -- ТОЛЬКО из prescriptive полей
(`action`, `direction`, `intensity`, targets/regions) -- используя тот же
`aios.evaluation.canonical.digest`, а не второй canonical framework.
"""

from __future__ import annotations

from collections.abc import Sequence

from aios.evaluation.canonical import JsonValue, digest
from aios.mas.arbiter import ArbiterDecision, DecisionBundle, Round2Request
from aios.mas.arbitration.policy import ConflictPolicy, NoRound2ConflictPolicy
from aios.mas.common import CandidateKind
from aios.mas.envelopes import ArbiterEnvelope
from aios.mas.intents import InjectionIntent, ProductionIntent
from aios.mas.proposals import InjectionProposal, ProductionProposal


class ArbitrationError(ValueError):
    """Arbitration contract нарушен: не может быть выполнен детерминированно."""


# ---------------------------------------------------------------------------
# Structural signatures -- prescriptive fields only, not the frozen Intent
# digest, not audit/rationale/evidence/uncertainty.
# ---------------------------------------------------------------------------


def _production_structural_payload(intent: ProductionIntent) -> dict[str, JsonValue]:
    return {
        "action": str(intent.action),
        "direction": str(intent.direction),
        "intensity": str(intent.intensity),
        "target_wells": sorted(intent.target_wells),
        "target_regions": sorted(intent.target_regions),
    }


def _injection_structural_payload(intent: InjectionIntent) -> dict[str, JsonValue]:
    return {
        "action": str(intent.action),
        "direction": str(intent.direction),
        "intensity": str(intent.intensity),
        "donor_wells": sorted(intent.donor_wells),
        "recipient_wells": sorted(intent.recipient_wells),
        "source_regions": sorted(intent.source_regions),
        "target_regions": sorted(intent.target_regions),
    }


def _production_intent_structural_digest(intent: ProductionIntent) -> str:
    return digest(_production_structural_payload(intent))


def _injection_intent_structural_digest(intent: InjectionIntent) -> str:
    return digest(_injection_structural_payload(intent))


def _production_signature(intents: Sequence[ProductionIntent]) -> tuple[str, ...]:
    return tuple(sorted(_production_intent_structural_digest(intent) for intent in intents))


def _injection_signature(intents: Sequence[InjectionIntent]) -> tuple[str, ...]:
    return tuple(sorted(_injection_intent_structural_digest(intent) for intent in intents))


def _production_is_novel(primary: ProductionProposal, baseline: ProductionProposal) -> bool:
    return _production_signature(primary.intents) != _production_signature(baseline.intents)


def _injection_is_novel(primary: InjectionProposal, baseline: InjectionProposal) -> bool:
    return _injection_signature(primary.intents) != _injection_signature(baseline.intents)


# ---------------------------------------------------------------------------
# Bundle construction helpers.
# ---------------------------------------------------------------------------


def _hold_bundle() -> DecisionBundle:
    return DecisionBundle(kind=CandidateKind.HOLD, priority_rank=0, exploration=False)


def _baseline_bundle(
    production_baseline: ProductionProposal, injection_baseline: InjectionProposal
) -> DecisionBundle:
    return DecisionBundle(
        kind=CandidateKind.DETERMINISTIC_BASELINE,
        production_proposal_digest=production_baseline.digest(),
        injection_proposal_digest=injection_baseline.digest(),
        production_intent_digests=tuple(
            sorted(intent.digest() for intent in production_baseline.intents)
        ),
        injection_intent_digests=tuple(
            sorted(intent.digest() for intent in injection_baseline.intents)
        ),
        priority_rank=1,
        exploration=False,
    )


class _AgentBundleSpec:
    """Internal candidate spec before priority_rank assignment.

    `structural_key` drives both deduplication and the neutral ordering
    tie-break for competing mixed alternatives (never role-based).
    """

    __slots__ = ("bundle", "exploration", "structural_key")

    def __init__(
        self,
        *,
        production_proposal: ProductionProposal,
        injection_proposal: InjectionProposal,
        exploration: bool,
    ) -> None:
        production_signature = _production_signature(production_proposal.intents)
        injection_signature = _injection_signature(injection_proposal.intents)
        # A single combined digest, not a (production, injection) tuple: tuple
        # comparison would compare the production side first and only fall
        # back to injection on a tie, which silently privileges whichever
        # candidate has the smaller production signature -- a hidden
        # "production first" bias forbidden by spec. One opaque combined
        # digest orders candidates without favoring either role's position.
        self.structural_key = digest(
            {
                "production_intents": list(production_signature),
                "injection_intents": list(injection_signature),
            }
        )
        self.exploration = exploration
        self.bundle = DecisionBundle(
            kind=CandidateKind.AGENT_BUNDLE,
            production_proposal_digest=production_proposal.digest(),
            injection_proposal_digest=injection_proposal.digest(),
            production_intent_digests=tuple(
                sorted(intent.digest() for intent in production_proposal.intents)
            ),
            injection_intent_digests=tuple(
                sorted(intent.digest() for intent in injection_proposal.intents)
            ),
            exploration=exploration,
        )


def _generate_agent_bundle_pool(
    *,
    production_primary: ProductionProposal,
    production_baseline: ProductionProposal,
    injection_primary: InjectionProposal,
    injection_baseline: InjectionProposal,
) -> list[_AgentBundleSpec]:
    production_novel = _production_is_novel(production_primary, production_baseline)
    injection_novel = _injection_is_novel(injection_primary, injection_baseline)

    if not production_novel and not injection_novel:
        return []

    pool: list[_AgentBundleSpec] = []
    seen_keys: set[str] = set()

    def _add(
        production_proposal: ProductionProposal,
        injection_proposal: InjectionProposal,
        *,
        exploration: bool,
    ) -> None:
        spec = _AgentBundleSpec(
            production_proposal=production_proposal,
            injection_proposal=injection_proposal,
            exploration=exploration,
        )
        if spec.structural_key in seen_keys:
            return
        seen_keys.add(spec.structural_key)
        pool.append(spec)

    # Combined primary: novel side keeps its primary, non-novel side
    # canonicalizes to baseline so a provenance-only difference never
    # fabricates a second candidate. Always exploration=False, inserted
    # first so it wins any structural-identity collision with a mixed
    # alternative below.
    _add(
        production_primary if production_novel else production_baseline,
        injection_primary if injection_novel else injection_baseline,
        exploration=False,
    )

    if production_novel:
        _add(production_primary, injection_baseline, exploration=True)
    if injection_novel:
        _add(production_baseline, injection_primary, exploration=True)

    return pool


def _canonical_pool_order(pool: list[_AgentBundleSpec]) -> list[_AgentBundleSpec]:
    """Combined primary (exploration=False) first, then neutral canonical order.

    Neutral means: sorted by the bundle's own structural key, never by which
    role (Production/Injection) contributed the novel side.
    """
    combined = [spec for spec in pool if not spec.exploration]
    mixed = sorted(
        (spec for spec in pool if spec.exploration),
        key=lambda spec: spec.structural_key,
    )
    return combined + mixed


class DeterministicArbiter:
    """Deterministic core/fallback Arbiter. Cognitive/provider ветка -- ARBITER-002."""

    def __init__(self, *, conflict_policy: ConflictPolicy | None = None) -> None:
        self._conflict_policy: ConflictPolicy = conflict_policy or NoRound2ConflictPolicy()

    def decide(self, envelope: ArbiterEnvelope) -> ArbiterDecision:
        if envelope.simulation_budget.max_candidates < 2:
            raise ArbitrationError(
                "arbitration: simulation_budget.max_candidates must be >= 2 "
                "to preserve HOLD + DETERMINISTIC_BASELINE"
            )

        production_primary = envelope.production_output.primary_proposal
        production_baseline = envelope.production_output.baseline_proposal
        injection_baseline = envelope.injection_output.baseline_proposal

        # Пучок структурных гипотез: каждая получает собственное семейство
        # кандидатов. Пустые alternatives дают ровно прежний набор.
        pool = []
        seen_keys: set[str] = set()
        for hypothesis in envelope.injection_output.hypotheses():
            for spec in _generate_agent_bundle_pool(
                production_primary=production_primary,
                production_baseline=production_baseline,
                injection_primary=hypothesis,
                injection_baseline=injection_baseline,
            ):
                if spec.structural_key in seen_keys:
                    continue
                seen_keys.add(spec.structural_key)
                pool.append(spec)
        ordered_pool = _canonical_pool_order(pool)

        remaining_budget = envelope.simulation_budget.max_candidates - 2
        selected_agent_specs = ordered_pool[: max(remaining_budget, 0)]

        bundles: list[DecisionBundle] = [
            _hold_bundle(),
            _baseline_bundle(production_baseline, injection_baseline),
        ]
        for rank, spec in enumerate(selected_agent_specs, start=2):
            bundles.append(
                DecisionBundle(
                    kind=spec.bundle.kind,
                    production_proposal_digest=spec.bundle.production_proposal_digest,
                    injection_proposal_digest=spec.bundle.injection_proposal_digest,
                    production_intent_digests=spec.bundle.production_intent_digests,
                    injection_intent_digests=spec.bundle.injection_intent_digests,
                    priority_rank=rank,
                    exploration=spec.bundle.exploration,
                )
            )

        round2_request: Round2Request | None = self._conflict_policy.evaluate(envelope)

        return ArbiterDecision(
            schema_version=envelope.schema_version,
            arbiter_envelope_digest=envelope.digest(),
            bundles=tuple(bundles),
            round2_request=round2_request,
        )
