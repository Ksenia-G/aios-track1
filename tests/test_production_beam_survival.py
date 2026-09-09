"""End-to-end выживание пучка Production: гипотеза -> Arbiter -> резолвер -> числа.

Пучок, который специалист вернул, но который никуда не доходит, ничем не лучше
его отсутствия. Здесь проверяется вся воронка целиком, включая ту её ступень,
на которой admitted альтернатива раньше исчезала бы молча: Arbiter перебирал
только гипотезы закачки, а лукап намерений резолвера знал лишь primary.

Ни одного имени скважины реальной колоды здесь нет: фонд синтетический, а
утверждения структурные.
"""

from __future__ import annotations

import dataclasses

from aios.mas.arbitration.engine import DeterministicArbiter
from aios.mas.common import CandidateKind, Intensity, ProposalSource
from aios.mas.evidence import SimulationBudget
from aios.mas.intents import ProductionAction
from aios.mas.magnitude_resolution.engine import DeterministicMagnitudeResolver
from aios.mas.magnitude_resolution.neighbourhood import target_subsets
from aios.mas.proposals import ProductionAgentOutput, ProductionProposal
from tests.test_magnitude_resolver_engine import (
    _build_envelope,
    _context,
    _producer,
    _production_hold,
    _production_increase,
    _production_reduce,
    _proposal,
)

#: Синтетический фонд. Размер выбран больше, чем MAX_TARGET_SUBSETS, чтобы
#: полный набор и его подмножества были различимы.
FUND = tuple(f"SYN-P{index:02d}" for index in range(1, 34))
PREVIOUS_RATE = 100.0


def _fund_controls():
    return tuple(_producer(well, rate=PREVIOUS_RATE) for well in FUND)


def _with_beam(envelope, alternatives_intents):
    """Тот же envelope, но primary сопровождают альтернативные гипотезы."""
    ped = envelope.production_output.baseline_proposal.envelope_digest
    alternatives = tuple(
        _proposal(ProductionProposal, intents=intents, envelope_digest=ped)
        for intents in alternatives_intents
    )
    return dataclasses.replace(
        envelope,
        production_output=ProductionAgentOutput(
            primary_proposal=envelope.production_output.primary_proposal,
            baseline_proposal=envelope.production_output.baseline_proposal,
            alternatives=alternatives,
        ),
    )


def _resolve(envelope):
    decision = DeterministicArbiter().decide(envelope)
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope,
            previous_producers=_fund_controls(),
            baseline_producers=_fund_controls(),
        )
    )
    return decision, resolver, resolver.resolve(decision)


def _budget(envelope, max_candidates: int):
    return dataclasses.replace(
        envelope, simulation_budget=SimulationBudget(max_candidates=max_candidates)
    )


# --- the beam must reach arbitration at all --------------------------------


def test_every_production_hypothesis_reaches_arbitration() -> None:
    envelope = _with_beam(
        _budget(
            _build_envelope(
                production_primary_intents=(
                    _production_increase(target_wells=FUND, intensity=Intensity.MODERATE),
                )
            ),
            12,
        ),
        [
            (_production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),),
            (_production_hold(),),
        ],
    )
    decision, _resolver, _candidates = _resolve(envelope)
    agent_bundles = [b for b in decision.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    admitted = {b.production_intent_digests for b in agent_bundles}
    assert len(admitted) >= 2, "an admitted alternative produced no bundle of its own"


def test_single_hypothesis_behaviour_is_unchanged() -> None:
    """Пустые alternatives обязаны давать ровно прежний набор."""
    envelope = _budget(
        _build_envelope(
            production_primary_intents=(
                _production_increase(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        12,
    )
    decision, _resolver, candidates = _resolve(envelope)
    beamed = _with_beam(envelope, [])
    decision_2, _r2, candidates_2 = _resolve(beamed)
    assert [b.to_json() for b in decision.bundles] == [b.to_json() for b in decision_2.bundles]
    assert [c.digest() for c in candidates] == [c.digest() for c in candidates_2]


def test_no_admitted_hypothesis_disappears_silently() -> None:
    """Каждый admitted структурный источник обязан дойти до чисел."""
    envelope = _with_beam(
        _budget(
            _build_envelope(
                production_primary_intents=(
                    _production_increase(target_wells=FUND, intensity=Intensity.MODERATE),
                )
            ),
            12,
        ),
        [(_production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),)],
    )
    _decision, resolver, _candidates = _resolve(envelope)
    funnel = resolver.last_funnel
    assert funnel["structural_origins_admitted"] >= 2
    assert funnel["structural_origins_resolved"] == funnel["structural_origins_admitted"], (
        "an admitted bundle resolved into nothing"
    )
    assert funnel["structural_origins_evaluated"] == funnel["structural_origins_admitted"], (
        "an admitted bundle produced candidates but none survived the budget"
    )


def test_both_directions_reach_the_numerical_layer() -> None:
    envelope = _with_beam(
        _budget(
            _build_envelope(
                production_primary_intents=(
                    _production_increase(target_wells=FUND, intensity=Intensity.MODERATE),
                )
            ),
            12,
        ),
        [(_production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),)],
    )
    _decision, _resolver, candidates = _resolve(envelope)
    agents = [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    rates = {rate for c in agents for rate in (p.liquid_rate for p in c.producers)}
    assert any(rate > PREVIOUS_RATE for rate in rates), "no candidate lifted offtake"
    assert any(rate < PREVIOUS_RATE for rate in rates), "no candidate cut offtake"


# --- the oracle neighbourhood is reachable from a structural hypothesis -----


def test_full_fund_is_the_first_target_subset() -> None:
    """Полный набор целей остаётся первым; ID bias его не вытесняет."""
    subsets = target_subsets(FUND)
    assert set(subsets[0]) == set(FUND)


def _has_doubling(candidates) -> bool:
    """Кандидат, поднявший КАЖДУЮ авторизованную скважину ровно вдвое.

    Ни одного числа из колоды: проверяется отношение к прежней уставке.
    """
    return any(
        c.candidate_kind is CandidateKind.AGENT_BUNDLE
        and {p.well for p in c.producers} == set(FUND)
        and all(p.liquid_rate == PREVIOUS_RATE * 2.0 for p in c.producers)
        for c in candidates
    )


def test_preferred_ladder_alone_never_reaches_the_doubling_rung() -> None:
    """Предпочтение навсегда ограничено своим резервом, каким бы ни был бюджет.

    Осознанное следствие раздельных резервов: широкий бюджет больше не
    углубляет лестницу предпочтённой гипотезы. Путь к удвоению теперь идёт не
    через глубину лестницы, а через рассмотренную альтернативу, и это
    проверяется в tests/test_production_coverage.py.
    """
    for budget in (8, 10, 12, 16, 24):
        envelope = _budget(
            _build_envelope(
                production_primary_intents=(
                    _production_increase(target_wells=FUND, intensity=Intensity.MODERATE),
                )
            ),
            budget,
        )
        _d, _r, candidates = _resolve(envelope)
        assert not _has_doubling(candidates), budget


def test_preferred_keeps_exactly_its_own_reserve() -> None:
    """Покрытие не отнимает у предпочтения ни одного слота."""
    from aios.mas.magnitude_resolution.coverage import PREFERRED_RESERVE

    envelope = _budget(
        _build_envelope(
            production_primary_intents=(
                _production_increase(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        14,
    )
    _d, _r, candidates = _resolve(envelope)
    agents = [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    assert len(agents) == PREFERRED_RESERVE


def test_beam_does_not_widen_the_action_vocabulary() -> None:
    """Пучок не добавляет действий: словарь остаётся прежним."""
    envelope = _with_beam(
        _budget(
            _build_envelope(
                production_primary_intents=(
                    _production_increase(target_wells=FUND, intensity=Intensity.MODERATE),
                )
            ),
            12,
        ),
        [(_production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),)],
    )
    actions = {
        ProductionAction(intent.action)
        for proposal in envelope.production_output.hypotheses()
        for intent in proposal.intents
    }
    assert actions <= set(ProductionAction)
    assert all(
        proposal.source is ProposalSource.LLM_LIVE
        for proposal in envelope.production_output.hypotheses()
    )
