"""Структурное покрытие Production: авторство, потолок, симметрия, резервы.

Покрытие существует, чтобы допустимое направление не осталось физически
непроверенным только потому, что специалист его не предпочёл. Оно НИКОГДА не
изобретает действие: закрыть пробел может лишь сам специалист.

Ни одного идентификатора Model Y и ни одного размера фонда здесь нет: фонд
синтетический, утверждения структурные, величины выражены отношением к
прежней уставке.
"""

from __future__ import annotations

import dataclasses
import json

from aios.mas.arbitration.engine import DeterministicArbiter
from aios.mas.common import CandidateKind, Intensity, ProposalSource
from aios.mas.coverage import (
    CoverageStatus,
    ScopeClass,
    coverage_gaps,
    is_all_authorized,
    required_cells,
    scope_class,
)
from aios.mas.evidence import SimulationBudget
from aios.mas.intents import ProductionAction
from aios.mas.magnitude_resolution.coverage import (
    COVERAGE_RESERVE,
    PREFERRED_RESERVE,
    CoverageOrigin,
    allocate_coverage,
    ceiling_representatives,
)
from aios.mas.magnitude_resolution.engine import DeterministicMagnitudeResolver
from aios.mas.proposals import ProductionAgentOutput, ProductionProposal
from aios.mas.specialists.production.controller import ProductionController
from aios.mas.specialists.production.provider import ProviderResponse
from tests.support.mas_team_fixtures import synthetic_production_envelope
from tests.test_magnitude_resolver_engine import (
    _build_envelope,
    _context,
    _producer,
    _production_increase,
    _production_reduce,
    _proposal,
)

FUND = tuple(f"SYN-P{index:02d}" for index in range(1, 34))
PREVIOUS_RATE = 100.0
INC = ProductionAction.INCREASE_OFFTAKE
RED = ProductionAction.REDUCE_OFFTAKE


def _fund_controls():
    return tuple(_producer(well, rate=PREVIOUS_RATE) for well in FUND)


# ===========================================================================
# Contract layer: scope is derived from the state, never from a constant
# ===========================================================================


def test_scope_class_is_relative_to_the_authorized_fund() -> None:
    assert scope_class(FUND, FUND) is ScopeClass.BROAD
    assert scope_class(FUND[:2], FUND) is ScopeClass.SELECTIVE
    assert scope_class((), FUND) is None
    # The same target list is BROAD against a small fund and SELECTIVE against
    # a large one: the class is a claim about reach, not a well count.
    assert scope_class(FUND[:3], FUND[:3]) is ScopeClass.BROAD


def test_full_authorized_scope_is_checked_by_set_equality() -> None:
    assert is_all_authorized(FUND, FUND)
    assert is_all_authorized(tuple(reversed(FUND)), FUND)
    assert not is_all_authorized(FUND[:-1], FUND)
    assert not is_all_authorized((*FUND, "SYN-P99"), FUND)


def test_required_cells_cover_both_directions_at_both_scopes() -> None:
    cells = required_cells(authorized=FUND, executable=(INC, RED))
    assert {cell.key() for cell in cells} == {
        f"{INC}|BROAD",
        f"{INC}|SELECTIVE",
        f"{RED}|BROAD",
        f"{RED}|SELECTIVE",
    }


def test_a_fund_too_small_to_distinguish_scopes_requires_only_one_column() -> None:
    cells = required_cells(authorized=("SYN-P01",), executable=(INC, RED))
    assert {cell.scope for cell in cells} == {ScopeClass.BROAD}


def test_non_executable_family_is_never_required() -> None:
    cells = required_cells(authorized=FUND, executable=(INC,))
    assert {cell.action for cell in cells} == {INC}


# ===========================================================================
# RED 12/13 -- determinism may not invent; an unclosed cell fails closed
# ===========================================================================


def _intent_json(action, wells=(), direction="UP", intensity="MODERATE"):
    return {
        "action": action,
        "direction": "NONE" if action in ("HOLD", "SHUT_IN", "REOPEN") else direction,
        "intensity": "NONE" if action in ("HOLD", "SHUT_IN", "REOPEN") else intensity,
        "target_wells": list(wells),
        "target_regions": [],
        "evidence_ids": [] if action == "HOLD" else ["SYN-pressure-1"],
        "uncertainty": "LOW",
        "rationale_short": "" if action == "HOLD" else "coverage fixture.",
    }


def _decl(action, disposition, scope="BROAD", reason="", explanation="fixture"):
    return {
        "action": action,
        "scope_class": scope,
        "disposition": disposition,
        "decline_reason": reason,
        "explanation": explanation,
    }


def _cover(
    increase="DECLINED", reduce="DECLINED", inc_reason="NOT_GROUNDABLE", red_reason="NOT_GROUNDABLE"
):
    """Заявления на обе требуемые ячейки синтетического фонда.

    Число скважин здесь не записано: класс охвата выводится из StateCard.
    """
    return [
        _decl("INCREASE_OFFTAKE", increase, reason=inc_reason if increase == "DECLINED" else ""),
        _decl("REDUCE_OFFTAKE", reduce, reason=red_reason if reduce == "DECLINED" else ""),
    ]


def _response(hypotheses, considered_alternatives=(), considered_actions=(), declarations=None):
    return json.dumps(
        {
            "hypotheses": [{"intents": intents} for intents in hypotheses],
            "considered_alternatives": [{"intents": i} for i in considered_alternatives],
            "coverage_declarations": declarations if declarations is not None else _cover(),
            "considered_actions": list(considered_actions),
        }
    )


class _Provider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            text=self.text, model_id="test-model", source=ProposalSource.LLM_LIVE
        )


def _prod_envelope():
    return synthetic_production_envelope(allowed_actions=(ProductionAction.HOLD, INC, RED))


def test_missing_required_cell_without_a_reason_fails_closed() -> None:
    """RED 13: неполный структурный ответ не считается валидным."""
    provider = _Provider(
        _response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            declarations=[_decl("REDUCE_OFFTAKE", "PREFERRED")],
        )
    )
    output = ProductionController(llm_enabled=True, provider=provider).propose(_prod_envelope())
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert "missing:INCREASE_OFFTAKE|BROAD" in (
        output.primary_proposal.provenance.fallback_reason or ""
    )


def test_determinism_never_synthesizes_the_missing_family() -> None:
    """RED 12: пробел закрывает только специалист, никогда не система."""
    provider = _Provider(
        _response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            declarations=[_decl("REDUCE_OFFTAKE", "PREFERRED")],
        )
    )
    output = ProductionController(llm_enabled=True, provider=provider).propose(_prod_envelope())
    authored = {
        ProductionAction(intent.action)
        for proposal in output.structural_alternatives()
        for intent in proposal.intents
    }
    assert INC not in authored, "an INCREASE the model never wrote appeared downstream"


def test_structured_reason_closes_the_cell_without_a_hypothesis() -> None:
    provider = _Provider(
        _response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            considered_actions=[
                {"action": "INCREASE_OFFTAKE", "supported": False, "reason": "NOT_GROUNDABLE"}
            ],
            declarations=_cover(reduce="PREFERRED"),
        )
    )
    output = ProductionController(llm_enabled=True, provider=provider).propose(_prod_envelope())
    assert output.primary_proposal.source is ProposalSource.LLM_LIVE
    assert output.considered_alternatives == ()


def test_free_text_reason_does_not_close_the_cell() -> None:
    provider = _Provider(
        _response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            considered_actions=[
                {"action": "INCREASE_OFFTAKE", "supported": False, "reason": "felt wrong"}
            ],
            declarations=[
                _decl("REDUCE_OFFTAKE", "PREFERRED"),
                _decl("INCREASE_OFFTAKE", "DECLINED", reason="felt wrong"),
            ],
        )
    )
    output = ProductionController(llm_enabled=True, provider=provider).propose(_prod_envelope())
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK


def test_considered_alternative_closes_the_cell_and_survives(  # RED 14
) -> None:
    provider = _Provider(
        _response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            considered_alternatives=[[_intent_json("INCREASE_OFFTAKE", ("SYN-P1",))]],
            declarations=_cover(reduce="PREFERRED", increase="CONSIDERED_NOT_PREFERRED"),
        )
    )
    output = ProductionController(llm_enabled=True, provider=provider).propose(_prod_envelope())
    assert output.primary_proposal.source is ProposalSource.LLM_LIVE
    assert len(output.considered_alternatives) == 1
    considered = output.considered_alternatives[0]
    assert ProductionAction(considered.intents[0].action) is INC
    # Preference is untouched: the considered alternative is not ranked.
    assert considered not in output.hypotheses()
    assert output.hypotheses()[0].intents[0].action is RED


# ===========================================================================
# RED 17 -- the authored intensity is a ceiling
# ===========================================================================


def test_representatives_never_exceed_the_authored_intensity_ceiling() -> None:
    small = ceiling_representatives(INC, Intensity.SMALL)
    moderate = ceiling_representatives(INC, Intensity.MODERATE)
    aggressive = ceiling_representatives(INC, Intensity.AGGRESSIVE)
    assert set(small) < set(moderate) < set(aggressive)
    assert max(small) < max(moderate) < max(aggressive)
    # A weaker authored strength can never borrow a stronger tier's rung.
    assert max(moderate) not in small
    assert max(aggressive) not in moderate


def test_reduction_ceiling_is_the_mirror_of_the_uplift_ceiling() -> None:
    for authored in (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE):
        up = ceiling_representatives(INC, authored)
        down = ceiling_representatives(RED, authored)
        assert len(up) == len(down)
        assert all(factor > 1.0 for factor in up)
        assert all(factor < 1.0 for factor in down)


def test_no_representatives_without_an_authored_scalable_intensity() -> None:
    assert ceiling_representatives(INC, Intensity.NONE) == ()


# ===========================================================================
# RED 18/19/20/21 -- symmetry, depth, forfeiture, preferred protection
# ===========================================================================


def _origin(tag, action, scope, intensity=Intensity.MODERATE):
    return CoverageOrigin(
        bundle_digest=tag, action=action, scope=ScopeClass(scope), authored_intensity=intensity
    )


def test_swapping_the_directions_changes_no_allocation() -> None:
    """RED 18: симметрия falsification обеспечена конструкцией."""
    forward = [
        _origin("a", INC, "BROAD"),
        _origin("b", INC, "SELECTIVE"),
        _origin("c", RED, "BROAD"),
        _origin("d", RED, "SELECTIVE"),
    ]
    swapped = [
        _origin("a", RED, "BROAD"),
        _origin("b", RED, "SELECTIVE"),
        _origin("c", INC, "BROAD"),
        _origin("d", INC, "SELECTIVE"),
    ]
    assert allocate_coverage(forward) == allocate_coverage(swapped)


def test_both_broad_cells_get_at_least_two_representatives() -> None:
    """RED 19: даже когда требуются все четыре ячейки сразу."""
    counts = allocate_coverage(
        [
            _origin("ib", INC, "BROAD"),
            _origin("is", INC, "SELECTIVE"),
            _origin("rb", RED, "BROAD"),
            _origin("rs", RED, "SELECTIVE"),
        ]
    )
    assert counts["ib"] >= 2
    assert counts["rb"] >= 2
    assert counts["ib"] == counts["rb"]
    assert counts["is"] == counts["rs"]


def test_unused_coverage_slots_are_forfeited() -> None:
    """RED 20: неполный ответ не вознаграждается силой поиска."""
    counts = allocate_coverage([_origin("only", INC, "BROAD", Intensity.SMALL)])
    assert sum(counts.values()) <= len(ceiling_representatives(INC, Intensity.SMALL))
    assert sum(counts.values()) < COVERAGE_RESERVE


def test_allocation_never_exceeds_the_reserve() -> None:
    counts = allocate_coverage(
        [
            _origin("ib", INC, "BROAD", Intensity.AGGRESSIVE),
            _origin("rb", RED, "BROAD", Intensity.AGGRESSIVE),
        ]
    )
    assert sum(counts.values()) <= COVERAGE_RESERVE


def test_an_incomplete_round_is_never_started() -> None:
    """Остаток слотов сгорает, а не достаётся первому по сортировке."""
    counts = allocate_coverage([_origin(str(i), INC, "SELECTIVE") for i in range(5)], slots=7)
    assert set(counts.values()) == {1}


def test_reserves_are_disjoint_constants() -> None:
    """RED 21: у предпочтения остаётся ровно его прежний резерв."""
    assert PREFERRED_RESERVE == 6
    assert COVERAGE_RESERVE == 6


# ===========================================================================
# RED 15/16 -- scope is authored, never widened by the deterministic layer
# ===========================================================================


def _with_considered(envelope, considered_intents, budget):
    ped = envelope.production_output.baseline_proposal.envelope_digest
    considered = tuple(
        _proposal(ProductionProposal, intents=intents, envelope_digest=ped)
        for intents in considered_intents
    )
    return dataclasses.replace(
        envelope,
        production_output=ProductionAgentOutput(
            primary_proposal=envelope.production_output.primary_proposal,
            baseline_proposal=envelope.production_output.baseline_proposal,
            alternatives=envelope.production_output.alternatives,
            considered_alternatives=considered,
        ),
        simulation_budget=SimulationBudget(max_candidates=budget),
    )


def _resolve(envelope):
    decision = DeterministicArbiter().decide(envelope)
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope, previous_producers=_fund_controls(), baseline_producers=_fund_controls()
        )
    )
    return decision, resolver, resolver.resolve(decision)


def test_coverage_never_widens_a_selective_alternative() -> None:
    """RED 15: узкая рассмотренная альтернатива остаётся узкой."""
    narrow = FUND[:3]
    envelope = _with_considered(
        _build_envelope(
            production_primary_intents=(
                _production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        [(_production_increase(target_wells=narrow, intensity=Intensity.MODERATE),)],
        14,
    )
    _d, _r, candidates = _resolve(envelope)
    for candidate in candidates:
        moved = {p.well for p in candidate.producers if p.liquid_rate > PREVIOUS_RATE}
        assert moved <= set(narrow), moved


def test_broad_coverage_scope_comes_from_the_specialist() -> None:
    """RED 16: широкий охват существует, только если специалист его написал."""
    envelope = _with_considered(
        _build_envelope(
            production_primary_intents=(
                _production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        [(_production_increase(target_wells=FUND, intensity=Intensity.MODERATE),)],
        14,
    )
    _d, _r, candidates = _resolve(envelope)
    broad = [
        c
        for c in candidates
        if c.candidate_kind is CandidateKind.AGENT_BUNDLE
        and {p.well for p in c.producers if p.liquid_rate > PREVIOUS_RATE} == set(FUND)
    ]
    assert broad, "an authored field-scale alternative produced no broad candidate"


def test_a_considered_alternative_reaches_the_numerical_layer() -> None:
    envelope = _with_considered(
        _build_envelope(
            production_primary_intents=(
                _production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        [(_production_increase(target_wells=FUND, intensity=Intensity.MODERATE),)],
        14,
    )
    _d, resolver, candidates = _resolve(envelope)
    lifted = [
        c
        for c in candidates
        if c.candidate_kind is CandidateKind.AGENT_BUNDLE
        and any(p.liquid_rate > PREVIOUS_RATE for p in c.producers)
    ]
    assert lifted, "the non-preferred direction never reached a physical candidate"
    assert resolver.last_funnel["coverage_origins"] == 1
    assert resolver.last_funnel["coverage_origins_served"] == 1


def test_coverage_does_not_reduce_the_preferred_reserve() -> None:
    """RED 21 end-to-end: предпочтение сохраняет свои шесть слотов."""
    envelope = _with_considered(
        _build_envelope(
            production_primary_intents=(
                _production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        [(_production_increase(target_wells=FUND, intensity=Intensity.MODERATE),)],
        14,
    )
    _d, resolver, candidates = _resolve(envelope)
    coverage_origins = set(resolver._coverage_origins)
    agents = [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    preferred = [c for c in agents if c.bundle_digest not in coverage_origins]
    assert len(preferred) == PREFERRED_RESERVE


# ===========================================================================
# Known-T0 acceptance: the oracle neighbourhood becomes DISCOVERABLE
# ===========================================================================


def test_authored_broad_increase_at_a_moderate_ceiling_yields_the_doubling_candidate() -> None:
    """Точный физический вариант удвоения появляется через рассмотренную альтернативу.

    Ни один множитель здесь не назван: проверяется отношение к прежней уставке.
    Победа не утверждается -- утверждается только обнаружимость.
    """
    envelope = _with_considered(
        _build_envelope(
            production_primary_intents=(
                _production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        [(_production_increase(target_wells=FUND, intensity=Intensity.MODERATE),)],
        14,
    )
    _d, _r, candidates = _resolve(envelope)
    doubled = [
        c
        for c in candidates
        if c.candidate_kind is CandidateKind.AGENT_BUNDLE
        and {p.well for p in c.producers} == set(FUND)
        and all(p.liquid_rate == PREVIOUS_RATE * 2.0 for p in c.producers)
    ]
    assert doubled, "the authored broad counterfactual did not reach the doubling rung"


def test_a_small_ceiling_does_not_reach_the_doubling_rung() -> None:
    """Обнаружимость НЕ безусловна: слабый заявленный потолок её не даёт.

    Это прямое следствие того, что потолок принадлежит специалисту: система
    не имеет права усилить его гипотезу за него.
    """
    envelope = _with_considered(
        _build_envelope(
            production_primary_intents=(
                _production_reduce(target_wells=FUND, intensity=Intensity.MODERATE),
            )
        ),
        [(_production_increase(target_wells=FUND, intensity=Intensity.SMALL),)],
        14,
    )
    _d, _r, candidates = _resolve(envelope)
    assert not [
        c
        for c in candidates
        if c.candidate_kind is CandidateKind.AGENT_BUNDLE
        and all(p.liquid_rate == PREVIOUS_RATE * 2.0 for p in c.producers)
    ]


def test_coverage_gaps_are_empty_once_the_specialist_covers_the_matrix() -> None:
    occupied = {
        cell: CoverageStatus.PREFERRED
        for cell in required_cells(authorized=FUND, executable=(INC, RED))
    }
    assert (
        coverage_gaps(authorized=FUND, executable=(INC, RED), occupied=occupied, declined={}) == ()
    )


# ===========================================================================
# Declaration contract: completeness is answered, not inferred (A..M)
# ===========================================================================


def _declared_response(hypotheses, declarations, considered_alternatives=(), actions=()):
    return _response(
        hypotheses,
        considered_alternatives=considered_alternatives,
        considered_actions=actions,
        declarations=declarations,
    )


def _propose(text):
    return ProductionController(llm_enabled=True, provider=_Provider(text)).propose(
        _prod_envelope()
    )


def _reason_of(output):
    return output.primary_proposal.provenance.fallback_reason or ""


def test_a_missing_required_cell_is_rejected() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            [_decl("REDUCE_OFFTAKE", "PREFERRED")],
        )
    )
    assert out.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert "missing:INCREASE_OFFTAKE|BROAD" in _reason_of(out)


def test_b_all_required_cells_declared_is_accepted() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            _cover(reduce="PREFERRED"),
        )
    )
    assert out.primary_proposal.source is ProposalSource.LLM_LIVE


def test_c_duplicate_cell_is_rejected() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            [
                _decl("REDUCE_OFFTAKE", "PREFERRED"),
                _decl("REDUCE_OFFTAKE", "PREFERRED"),
                _decl("INCREASE_OFFTAKE", "DECLINED", reason="NOT_GROUNDABLE"),
            ],
        )
    )
    assert "duplicate:REDUCE_OFFTAKE|BROAD" in _reason_of(out)


def test_unexpected_cell_is_rejected() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            [
                *_cover(reduce="PREFERRED"),
                _decl("REDUCE_OFFTAKE", "DECLINED", scope="SELECTIVE", reason="NOT_GROUNDABLE"),
            ],
        )
    )
    assert "unexpected:REDUCE_OFFTAKE|SELECTIVE" in _reason_of(out)


def test_d_preferred_without_a_matching_hypothesis_is_rejected() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            _cover(reduce="PREFERRED", increase="PREFERRED"),
        )
    )
    assert "PREFERRED without a matching ranked hypothesis" in _reason_of(out)


def test_e_considered_without_a_matching_alternative_is_rejected() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            _cover(reduce="PREFERRED", increase="CONSIDERED_NOT_PREFERRED"),
        )
    )
    assert "CONSIDERED_NOT_PREFERRED without a matching alternative" in _reason_of(out)


def test_f_declined_without_a_reason_is_rejected() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            [_decl("REDUCE_OFFTAKE", "PREFERRED"), _decl("INCREASE_OFFTAKE", "DECLINED")],
        )
    )
    assert "DECLINED without a valid structural reason" in _reason_of(out)


def test_g_declined_with_an_unknown_token_is_rejected() -> None:
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            _cover(reduce="PREFERRED", inc_reason="BECAUSE_I_SAID_SO"),
        )
    )
    assert "DECLINED without a valid structural reason" in _reason_of(out)


def test_h_natural_reason_with_a_trailing_explanation_is_accepted() -> None:
    from aios.mas.coverage import decline_explanation, parse_decline_reason

    text = "NOT_MATERIALIZABLE: all authorised producers are already open"
    assert parse_decline_reason(text) == "NOT_MATERIALIZABLE"
    assert decline_explanation(text) == "all authorised producers are already open"
    assert parse_decline_reason("not_groundable: lower case is fine") == "NOT_GROUNDABLE"


def test_i_a_token_buried_in_prose_does_not_count() -> None:
    from aios.mas.coverage import parse_decline_reason

    assert parse_decline_reason("This appears NOT_MATERIALIZABLE") is None
    assert parse_decline_reason("NOT_MATERIALIZABLE_BUT_MAYBE") is None
    assert parse_decline_reason("") is None


def test_j_a_reason_contradicting_known_capability_cannot_close_a_cell() -> None:
    """Правильный токен -- ещё не основание."""
    for bad in ("NOT_MATERIALIZABLE", "SEMANTICS_UNRESOLVED", "INADMISSIBLE_NOW"):
        out = _propose(
            _declared_response(
                [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
                _cover(reduce="PREFERRED", inc_reason=bad),
            )
        )
        assert out.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK, bad
        assert "coverage_declarations_invalid" in _reason_of(out), bad


def test_not_groundable_remains_a_legitimate_decline() -> None:
    """Единственная причина, которую детерминизм не вправе опровергать.

    Заземляемо ли направление на этих данных, судит специалист: подменить это
    правилом значило бы вернуть себе отданную ему власть.
    """
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            _cover(reduce="PREFERRED", inc_reason="NOT_GROUNDABLE: no headroom in evidence"),
        )
    )
    assert out.primary_proposal.source is ProposalSource.LLM_LIVE


def test_k_required_cells_are_derived_from_the_state_not_from_a_constant() -> None:
    from aios.mas.specialists.production.coverage import authorized_producers, executable_actions

    envelope = _prod_envelope()
    fund = authorized_producers(envelope)
    cells = required_cells(authorized=fund, executable=executable_actions(envelope))
    assert cells, "no required cell derived from a state that holds producers"
    assert all(cell.action in (INC, RED) for cell in cells)
    # A larger fund changes the matrix without any code knowing a fund size.
    assert len(required_cells(authorized=FUND, executable=(INC, RED))) == 4


def test_l_a_valid_decline_creates_no_candidate() -> None:
    """Отказ закрывает ячейку и НЕ порождает гипотезу за модель."""
    out = _propose(
        _declared_response(
            [[_intent_json("REDUCE_OFFTAKE", ("SYN-P1",), "DOWN")]],
            _cover(reduce="PREFERRED"),
        )
    )
    authored = {
        ProductionAction(intent.action)
        for proposal in out.structural_alternatives()
        for intent in proposal.intents
    }
    assert INC not in authored


def test_m_prompt_lists_the_cells_without_naming_a_winner() -> None:
    from aios.mas.specialists.production.prompt import BEAM_CONTRACT_LINES, PromptBuilder

    envelope = _prod_envelope()
    prompt = PromptBuilder().build(envelope)
    assert "required_coverage_cells" in prompt
    instructions = "\n".join(BEAM_CONTRACT_LINES).lower()
    for phrase in (
        "choose increase",
        "prefer broad",
        "test doubling",
        "maximize production",
        "oracle",
        "x2",
        "double",
        "multiplier",
    ):
        assert phrase not in instructions
