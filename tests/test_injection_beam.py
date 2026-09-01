"""Пучок структурных гипотез Injection: один вызов, несколько гипотез.

Одна гипотеза за эпоху означала, что стохастический выбор действия мог на целую
эпоху выключить исправно работающую downstream-способность. Пучок снимает
именно это, не трогая ни численную власть, ни право модели не предлагать
действие: он лишь делает выбор наблюдаемым.
"""

from __future__ import annotations

import json

import pytest

from aios.mas.common import ProposalSource
from aios.mas.intents import InjectionAction
from aios.mas.specialists.injection.controller import InjectionController
from aios.mas.specialists.injection.parser import ParseError, StrictOutputParser
from tests.support.mas_team_fixtures import synthetic_injection_envelope


def _hypothesis(action: str, wells=(), regions=(), direction="UP", intensity="MODERATE"):
    return {
        "intents": [
            {
                "action": action,
                "direction": "NONE" if action == "HOLD" else direction,
                "intensity": "NONE" if action == "HOLD" else intensity,
                "donor_wells": [],
                "recipient_wells": list(wells),
                "source_regions": [],
                "target_regions": list(regions),
                "evidence_ids": [] if action == "HOLD" else ["SYN-pressure-1"],
                "uncertainty": "LOW" if action == "HOLD" else "MEDIUM",
                "rationale_short": "" if action == "HOLD" else "beam fixture",
            }
        ]
    }


def _beam_payload(hypotheses, considered=None):
    payload = {"hypotheses": hypotheses}
    if considered is not None:
        payload["considered_actions"] = considered
    return json.dumps(payload)


class _Provider:
    """Провайдер, считающий обращения: пучок обязан стоить один вызов."""

    def __init__(self, text: str):
        self.text = text
        self.calls = 0
        self.source = ProposalSource.LLM_LIVE
        self.model_id = "test-model"

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return self.text


def _controller(provider):
    return InjectionController(
        llm_enabled=True, llm_provider=provider, llm_source=ProposalSource.LLM_LIVE
    )


def _envelope():
    return synthetic_injection_envelope(
        allowed_actions=(
            InjectionAction.HOLD,
            InjectionAction.SUPPORT_REGION,
            InjectionAction.SCALE_TOTAL_INJECTION,
        )
    )


# ===========================================================================
# B1 -- один ответ -> несколько структурных гипотез
# ===========================================================================


def test_one_completion_yields_multiple_structural_hypotheses() -> None:
    provider = _Provider(
        _beam_payload(
            [
                _hypothesis("SUPPORT_REGION", wells=("SYN-I1",)),
                _hypothesis("SCALE_TOTAL_INJECTION"),
                _hypothesis("HOLD"),
            ]
        )
    )
    output = _controller(provider).propose(_envelope())
    assert len(output.hypotheses()) == 3
    actions = [h.intents[0].action for h in output.hypotheses()]
    assert InjectionAction.SUPPORT_REGION in actions
    assert InjectionAction.SCALE_TOTAL_INJECTION in actions


# ===========================================================================
# B3/B20 -- ровно один вызов провайдера
# ===========================================================================


def test_beam_costs_exactly_one_provider_call() -> None:
    provider = _Provider(
        _beam_payload(
            [
                _hypothesis("SUPPORT_REGION", wells=("SYN-I1",)),
                _hypothesis("SCALE_TOTAL_INJECTION"),
                _hypothesis("HOLD"),
            ]
        )
    )
    _controller(provider).propose(_envelope())
    assert provider.calls == 1, "a beam must never cost more than one completion"


# ===========================================================================
# B4 -- ранг сохраняет порядок провайдера
# ===========================================================================


def test_primary_is_the_first_hypothesis_returned() -> None:
    provider = _Provider(
        _beam_payload(
            [
                _hypothesis("SCALE_TOTAL_INJECTION"),
                _hypothesis("SUPPORT_REGION", wells=("SYN-I1",)),
            ]
        )
    )
    output = _controller(provider).propose(_envelope())
    assert output.primary_proposal.intents[0].action is InjectionAction.SCALE_TOTAL_INJECTION
    ranks = [rank for rank, _ in output.ranked_hypotheses()]
    assert ranks == [1, 2]


# ===========================================================================
# B5 -- структурные дубликаты не занимают слот
# ===========================================================================


def test_structurally_identical_hypotheses_are_deduplicated() -> None:
    provider = _Provider(
        _beam_payload(
            [
                _hypothesis("SUPPORT_REGION", wells=("SYN-I1",)),
                _hypothesis("SUPPORT_REGION", wells=("SYN-I1",)),
                _hypothesis("HOLD"),
            ]
        )
    )
    output = _controller(provider).propose(_envelope())
    digests = [h.digest() for h in output.hypotheses()]
    assert len(digests) == len(set(digests))
    assert len(output.hypotheses()) == 2


# ===========================================================================
# B6 -- негодная гипотеза не убивает годных соседей
# ===========================================================================


def test_invalid_hypothesis_is_rejected_without_discarding_siblings() -> None:
    provider = _Provider(
        _beam_payload(
            [
                _hypothesis("SUPPORT_REGION", wells=("SYN-I1",)),
                # REALLOCATE не входит в allowed_actions этого конверта.
                _hypothesis("REALLOCATE_INJECTION", wells=("SYN-I1",)),
                _hypothesis("HOLD"),
            ]
        )
    )
    output = _controller(provider).propose(_envelope())
    actions = [h.intents[0].action for h in output.hypotheses()]
    assert InjectionAction.SUPPORT_REGION in actions
    assert InjectionAction.HOLD in actions
    assert InjectionAction.REALLOCATE_INJECTION not in actions
    assert output.primary_proposal.source is ProposalSource.LLM_LIVE


def test_beam_with_no_valid_hypothesis_falls_back() -> None:
    provider = _Provider(_beam_payload([_hypothesis("REALLOCATE_INJECTION", wells=("SYN-I1",))]))
    output = _controller(provider).propose(_envelope())
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.primary_proposal.provenance.fallback_used is True


# ===========================================================================
# B7 -- «рассмотрено, но не поддержано» наблюдаемо
# ===========================================================================


def test_considered_but_unsupported_actions_are_recorded() -> None:
    provider = _Provider(
        _beam_payload(
            [_hypothesis("SUPPORT_REGION", wells=("SYN-I1",))],
            considered=[
                {"action": "SCALE_TOTAL_INJECTION", "supported": False, "reason": "no evidence"}
            ],
        )
    )
    output = _controller(provider).propose(_envelope())
    declined = output.primary_proposal.audit.declined_alternatives
    assert any("SCALE_TOTAL_INJECTION" in item for item in declined)


# ===========================================================================
# B8 -- отказ провайдера остаётся fail-closed
# ===========================================================================


def test_provider_failure_still_falls_back_closed() -> None:
    class _Broken(_Provider):
        def complete(self, prompt: str) -> str:
            self.calls += 1
            raise RuntimeError("provider down")

    provider = _Broken("")
    output = _controller(provider).propose(_envelope())
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.primary_proposal.provenance.fallback_used is True
    assert output.alternatives == ()


# ===========================================================================
# B2 -- никаких численных полей в ответе
# ===========================================================================


def test_numerical_fields_in_a_hypothesis_are_rejected() -> None:
    bad = _hypothesis("SUPPORT_REGION", wells=("SYN-I1",))
    bad["intents"][0]["surface_rate"] = 250.0
    with pytest.raises(ParseError):
        StrictOutputParser().parse(_beam_payload([bad]))


# ===========================================================================
# обратная совместимость: одиночный ответ прежнего вида
# ===========================================================================


def test_legacy_single_proposal_response_still_parses() -> None:
    legacy = json.dumps(_hypothesis("SUPPORT_REGION", wells=("SYN-I1",)))
    provider = _Provider(legacy)
    output = _controller(provider).propose(_envelope())
    assert output.primary_proposal.source is ProposalSource.LLM_LIVE
    assert output.alternatives == ()
