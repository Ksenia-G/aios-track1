"""Эволюция контракта: Injection-специалист возвращает пучок гипотез.

Одна структурная гипотеза за эпоху означала, что стохастический выбор действия
мог на целую эпоху выключить исправно работающую downstream-способность:
живой прогон PERF-003 предложил SUPPORT_REGION/HOLD/HOLD и ни разу не выбрал
REALLOCATE_INJECTION, хотя действие было заземляемо и численно достижимо.

Расширение аддитивное и структурное. `alternatives` -- дополнительные
структурные гипотезы того же ОДНОГО вызова провайдера; ранг задаётся порядком
(primary -- ранг 1), а происхождение переносится уже существующими типами:
источник, provenance с fallback и response_digest (общий на весь пучок),
grounding_report с evidence_ids, digest структуры и
`ProposalAudit.declined_alternatives` для «рассмотрено, но не поддержано».

Численные величины в этот контракт не входят и входить не могут.
"""

from __future__ import annotations

import dataclasses

import pytest

from aios.mas.common import ProposalSource
from aios.mas.proposals import (
    InjectionAgentOutput,
    InjectionProposal,
    ProposalError,
)
from tests.support.mas_team_fixtures import (
    synthetic_injection_hold_intent,
    synthetic_injection_proposal,
)


def _intent(action, wells=(), intensity=None):
    """Структурно различная гипотеза: разное действие/цели -- разный digest."""
    from aios.mas.common import Direction, Intensity, UncertaintyLevel
    from aios.mas.intents import InjectionAction, InjectionIntent

    if action is InjectionAction.HOLD:
        return synthetic_injection_hold_intent()
    return InjectionIntent(
        action=action,
        direction=Direction.UP,
        intensity=intensity or Intensity.MODERATE,
        donor_wells=(),
        recipient_wells=tuple(wells),
        source_regions=(),
        target_regions=(),
        evidence_ids=("SYN-pressure-1",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="beam contract fixture",
    )


def _proposal(source=ProposalSource.LLM_LIVE, **overrides) -> InjectionProposal:
    base = synthetic_injection_proposal(source=source)
    return dataclasses.replace(base, **overrides) if overrides else base


def _distinct(n: int, source=ProposalSource.LLM_LIVE, **overrides) -> InjectionProposal:
    """n-я структурно различная гипотеза пучка."""
    from aios.mas.intents import InjectionAction
    from aios.mas.proposals import GroundingReport

    intent = _intent(InjectionAction.SUPPORT_REGION, wells=(f"SYN-I{n}",))
    return dataclasses.replace(
        synthetic_injection_proposal(source=source),
        intents=(intent,),
        grounding_report=GroundingReport(grounded_evidence_ids=("SYN-pressure-1",)),
        **overrides,
    )


def _baseline() -> InjectionProposal:
    return _proposal(source=ProposalSource.DETERMINISTIC_BASELINE)


# ===========================================================================
# C1 -- обратная совместимость
# ===========================================================================


def test_old_two_argument_construction_still_works() -> None:
    output = InjectionAgentOutput(primary_proposal=_proposal(), baseline_proposal=_baseline())
    assert output.alternatives == ()


def test_alternatives_default_is_empty() -> None:
    output = InjectionAgentOutput(primary_proposal=_proposal(), baseline_proposal=_baseline())
    assert isinstance(output.alternatives, tuple)
    assert len(output.alternatives) == 0


# ===========================================================================
# C4 -- пучок доходит целиком
# ===========================================================================


def test_primary_plus_two_alternatives_are_all_reachable() -> None:
    primary = _proposal()
    alt1 = _distinct(1)
    alt2 = _distinct(2)
    output = InjectionAgentOutput(
        primary_proposal=primary,
        baseline_proposal=_baseline(),
        alternatives=(alt1, alt2),
    )
    assert len(output.hypotheses()) == 3
    assert output.hypotheses()[0] is primary


def test_hypothesis_rank_follows_beam_order() -> None:
    """Ранг задаётся порядком: primary -- 1, alternatives -- 2..N."""
    primary = _proposal()
    alt1 = _distinct(1)
    alt2 = _distinct(2)
    output = InjectionAgentOutput(
        primary_proposal=primary, baseline_proposal=_baseline(), alternatives=(alt1, alt2)
    )
    ranked = output.ranked_hypotheses()
    assert [rank for rank, _ in ranked] == [1, 2, 3]
    assert ranked[1][1] is alt1
    assert ranked[2][1] is alt2


# ===========================================================================
# C5 -- никакого дублирования primary/baseline в alternatives
# ===========================================================================


def test_primary_may_not_be_duplicated_into_alternatives() -> None:
    primary = _proposal()
    with pytest.raises(ProposalError):
        InjectionAgentOutput(
            primary_proposal=primary,
            baseline_proposal=_baseline(),
            alternatives=(primary,),
        )


def test_baseline_may_not_be_duplicated_into_alternatives() -> None:
    baseline = _baseline()
    with pytest.raises(ProposalError):
        InjectionAgentOutput(
            primary_proposal=_proposal(),
            baseline_proposal=baseline,
            alternatives=(baseline,),
        )


def test_structurally_identical_alternatives_are_rejected() -> None:
    alt = _proposal()
    with pytest.raises(ProposalError):
        InjectionAgentOutput(
            primary_proposal=_proposal(
                intents=(synthetic_injection_hold_intent(),),
            ),
            baseline_proposal=_baseline(),
            alternatives=(alt, alt),
        )


# ===========================================================================
# C6 -- никаких численных полей
# ===========================================================================


def test_contract_carries_no_numerical_setpoint_field() -> None:
    fields = {f.name for f in dataclasses.fields(InjectionAgentOutput)}
    assert fields == {"primary_proposal", "baseline_proposal", "alternatives"}
    forbidden = {
        "rate",
        "rates",
        "surface_rate",
        "injection_rate",
        "bhp",
        "bhp_limit",
        "setpoint",
        "setpoints",
        "transfer_fraction",
        "magnitude",
    }
    proposal_fields = {f.name for f in dataclasses.fields(InjectionProposal)}
    assert not (fields & forbidden)
    assert not (proposal_fields & forbidden)


# ===========================================================================
# C7 -- происхождение сохраняется по каждой гипотезе
# ===========================================================================


def test_every_hypothesis_keeps_its_typed_provenance() -> None:
    output = InjectionAgentOutput(
        primary_proposal=_proposal(),
        baseline_proposal=_baseline(),
        alternatives=(_distinct(1), _distinct(2)),
    )
    for _rank, hypothesis in output.ranked_hypotheses():
        assert hypothesis.source is ProposalSource.LLM_LIVE
        assert hypothesis.provenance is not None
        assert hypothesis.digest()
        assert hypothesis.grounding_report is not None


def test_beam_lineage_is_the_shared_completion_digest() -> None:
    """Один вызов провайдера -- один response_digest на весь пучок."""
    from aios.mas.proposals import ProposalProvenance

    prov = ProposalProvenance(model_id="m", response_digest="r" * 64)
    output = InjectionAgentOutput(
        primary_proposal=_proposal(provenance=prov),
        baseline_proposal=_baseline(),
        alternatives=(_distinct(1, provenance=prov),),
    )
    digests = {h.provenance.response_digest for _r, h in output.ranked_hypotheses()}
    assert digests == {"r" * 64}


def test_baseline_is_not_part_of_the_hypothesis_beam() -> None:
    baseline = _baseline()
    output = InjectionAgentOutput(
        primary_proposal=_proposal(), baseline_proposal=baseline, alternatives=()
    )
    assert baseline not in output.hypotheses()


# ===========================================================================
# C2 -- каноническое представление переносит пучок
# ===========================================================================


def test_alternatives_survive_canonical_representation() -> None:
    output = InjectionAgentOutput(
        primary_proposal=_proposal(),
        baseline_proposal=_baseline(),
        alternatives=(_distinct(1),),
    )
    payload = output.to_json()
    assert len(payload["alternatives"]) == 1
    assert payload["primary_proposal"]
    assert payload["baseline_proposal"]


# ===========================================================================
# существующие инварианты не ослаблены
# ===========================================================================


def test_alternatives_must_share_the_envelope_digest() -> None:
    with pytest.raises(ProposalError):
        InjectionAgentOutput(
            primary_proposal=_proposal(),
            baseline_proposal=_baseline(),
            alternatives=(_distinct(1, envelope_digest="f" * 64),),
        )


def test_alternative_may_not_be_a_deterministic_baseline_source() -> None:
    """Альтернатива -- структурная гипотеза, а не второй пол."""
    with pytest.raises(ProposalError):
        InjectionAgentOutput(
            primary_proposal=_proposal(),
            baseline_proposal=_baseline(),
            alternatives=(_distinct(1, source=ProposalSource.DETERMINISTIC_BASELINE),),
        )
