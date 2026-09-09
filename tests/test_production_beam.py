"""Пучок структурных гипотез Production: один вызов, несколько гипотез.

Симметрично уже существующему пучку Injection. До этого Production возвращал
ровно одну гипотезу за эпоху, поэтому стохастический выбор действия мог на
целую эпоху выключить исправно работающую downstream-способность: на живом T0
единственной гипотезой оказался SHUT_IN, и численный слой не получал ни одного
кандидата на подъём отбора, хотя вся его механика для этого была на месте.

Пучок не заставляет модель предлагать подъём и не добавляет ни одного нового
действия: он лишь перестаёт выбрасывать альтернативы, которые модель и так
рассмотрела.
"""

from __future__ import annotations

import json

import pytest

from aios.mas.common import ProposalSource
from aios.mas.intents import ProductionAction
from aios.mas.specialists.production.controller import ProductionController
from aios.mas.specialists.production.errors import StrictOutputError
from aios.mas.specialists.production.parser import StrictOutputParser
from aios.mas.specialists.production.provider import ProviderResponse
from tests.support.mas_team_fixtures import synthetic_production_envelope


def _intent(action: str, wells=(), direction="UP", intensity="MODERATE"):
    return {
        "action": action,
        "direction": "NONE" if action in ("HOLD", "SHUT_IN", "REOPEN") else direction,
        "intensity": "NONE" if action in ("HOLD", "SHUT_IN", "REOPEN") else intensity,
        "target_wells": list(wells),
        "target_regions": [],
        "evidence_ids": [] if action == "HOLD" else ["SYN-pressure-1"],
        "uncertainty": "LOW",
        "rationale_short": "" if action == "HOLD" else "beam fixture.",
    }


def _declines(*actions):
    """Структурная причина отказа: только она закрывает ячейку покрытия."""
    return [{"action": a, "supported": False, "reason": "NOT_GROUNDABLE"} for a in actions]


def _declaration(action, disposition, scope="BROAD", reason="", explanation="fixture"):
    return {
        "action": action,
        "scope_class": scope,
        "disposition": disposition,
        "decline_reason": reason,
        "explanation": explanation,
    }


#: Фонд синтетического envelope -- одна скважина, поэтому требуемых ячеек две,
#: обе BROAD. Число скважин нигде не записано: оно приходит из StateCard.
def _cover(
    increase="DECLINED", reduce="DECLINED", inc_reason="NOT_GROUNDABLE", red_reason="NOT_GROUNDABLE"
):
    return [
        _declaration(
            "INCREASE_OFFTAKE", increase, reason=inc_reason if increase == "DECLINED" else ""
        ),
        _declaration("REDUCE_OFFTAKE", reduce, reason=red_reason if reduce == "DECLINED" else ""),
    ]


def _beam(hypotheses, considered=None, declarations=None):
    payload = {
        "hypotheses": [{"intents": intents} for intents in hypotheses],
        "considered_alternatives": [],
        "coverage_declarations": declarations if declarations is not None else _cover(),
        "considered_actions": considered if considered is not None else [],
    }
    return json.dumps(payload)


class _Provider:
    """Провайдер, считающий обращения: пучок обязан стоить один вызов."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            text=self.text, model_id="test-model", source=ProposalSource.LLM_LIVE
        )


def _envelope():
    return synthetic_production_envelope(
        allowed_actions=(
            ProductionAction.HOLD,
            ProductionAction.INCREASE_OFFTAKE,
            ProductionAction.REDUCE_OFFTAKE,
        )
    )


def _controller(provider):
    return ProductionController(llm_enabled=True, provider=provider)


# --- RED before the beam: a single completion carried a single hypothesis ---


def test_one_completion_yields_multiple_structural_hypotheses() -> None:
    provider = _Provider(
        _beam(
            [
                [_intent("INCREASE_OFFTAKE", wells=("SYN-P1",))],
                [_intent("REDUCE_OFFTAKE", wells=("SYN-P1",), direction="DOWN")],
                [_intent("HOLD")],
            ],
            declarations=_cover(increase="PREFERRED", reduce="PREFERRED"),
        )
    )
    output = _controller(provider).propose(_envelope())

    assert len(output.hypotheses()) == 3
    actions = [h.intents[0].action for h in output.hypotheses()]
    assert ProductionAction.INCREASE_OFFTAKE in actions
    assert ProductionAction.REDUCE_OFFTAKE in actions
    assert ProductionAction.HOLD in actions


def test_beam_costs_exactly_one_provider_call() -> None:
    provider = _Provider(
        _beam(
            [[_intent("INCREASE_OFFTAKE", wells=("SYN-P1",))], [_intent("HOLD")]],
            declarations=_cover(increase="PREFERRED"),
        )
    )
    _controller(provider).propose(_envelope())
    assert provider.calls == 1


def test_primary_is_the_first_hypothesis_returned() -> None:
    provider = _Provider(
        _beam(
            [[_intent("REDUCE_OFFTAKE", wells=("SYN-P1",), direction="DOWN")], [_intent("HOLD")]],
            considered=_declines("INCREASE_OFFTAKE"),
            declarations=_cover(reduce="PREFERRED"),
        )
    )
    output = _controller(provider).propose(_envelope())
    assert output.primary_proposal.intents[0].action is ProductionAction.REDUCE_OFFTAKE
    assert len(output.alternatives) == 1
    assert output.ranked_hypotheses()[0][0] == 1


def test_structurally_identical_hypotheses_are_deduplicated() -> None:
    duplicate = [_intent("INCREASE_OFFTAKE", wells=("SYN-P1",))]
    provider = _Provider(
        _beam(
            [duplicate, duplicate, [_intent("HOLD")]],
            considered=_declines("REDUCE_OFFTAKE"),
            declarations=_cover(increase="PREFERRED"),
        )
    )
    output = _controller(provider).propose(_envelope())
    assert len(output.hypotheses()) == 2


def test_invalid_hypothesis_is_rejected_without_discarding_siblings() -> None:
    """Негодная альтернатива не имеет права уносить с собой годные."""
    provider = _Provider(
        _beam(
            [
                [_intent("INCREASE_OFFTAKE", wells=("SYN-P1",))],
                # SHUT_IN отсутствует в allowed_actions этого envelope.
                [_intent("SHUT_IN", wells=("SYN-P1",))],
                [_intent("HOLD")],
            ],
            considered=_declines("REDUCE_OFFTAKE"),
            declarations=_cover(increase="PREFERRED"),
        )
    )
    output = _controller(provider).propose(_envelope())
    actions = [h.intents[0].action for h in output.hypotheses()]
    assert ProductionAction.INCREASE_OFFTAKE in actions
    assert ProductionAction.HOLD in actions
    assert ProductionAction.SHUT_IN not in actions
    assert output.primary_proposal.source is ProposalSource.LLM_LIVE


def test_beam_with_no_valid_hypothesis_falls_back_closed() -> None:
    provider = _Provider(_beam([[_intent("SHUT_IN", wells=("SYN-P1",))]]))
    output = _controller(provider).propose(_envelope())
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.alternatives == ()
    assert output.primary_proposal.provenance.fallback_used is True


def test_considered_but_unsupported_actions_are_recorded() -> None:
    provider = _Provider(
        _beam(
            [[_intent("INCREASE_OFFTAKE", wells=("SYN-P1",))]],
            considered=[
                {"action": "REDUCE_OFFTAKE", "supported": False, "reason": "NOT_GROUNDABLE"},
                {"action": "INCREASE_OFFTAKE", "supported": True, "reason": "sweep headroom"},
            ],
            declarations=_cover(increase="PREFERRED"),
        )
    )
    output = _controller(provider).propose(_envelope())
    declined = output.primary_proposal.audit.declined_alternatives
    assert any("REDUCE_OFFTAKE" in item for item in declined)
    assert not any("INCREASE_OFFTAKE" in item for item in declined)


def test_numerical_magnitude_in_a_hypothesis_is_rejected() -> None:
    bad = _intent("INCREASE_OFFTAKE", wells=("SYN-P1",))
    bad["rationale_short"] = "raise LRAT to 200."
    with pytest.raises(StrictOutputError):
        StrictOutputParser().parse_beam(_beam([bad and [bad]]))


def test_legacy_single_proposal_response_still_parses() -> None:
    """Прежняя форма по-прежнему РАЗБИРАЕТСЯ, но полноту доказать не может.

    Разборщик её принимает без изменений. Контроллер -- нет: в прежней форме
    нет места для заявлений о покрытии, поэтому множество отвеченных вопросов
    доказуемо не совпадает с множеством заданных, и ответ закрывается. Это
    ограничение самой формы, а не регрессия разбора.
    """
    legacy = json.dumps({"intents": [_intent("INCREASE_OFFTAKE", wells=("SYN-P1",))]})
    beam = StrictOutputParser().parse_beam(legacy)
    assert len(beam.hypotheses) == 1
    assert beam.considered == ()

    narrow = synthetic_production_envelope(
        allowed_actions=(ProductionAction.HOLD, ProductionAction.INCREASE_OFFTAKE)
    )
    output = _controller(_Provider(legacy)).propose(narrow)
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert "coverage_declarations_invalid" in (
        output.primary_proposal.provenance.fallback_reason or ""
    )


def test_prompt_names_no_mandatory_action() -> None:
    """Промпт не имеет права предписывать конкретное действие."""
    from aios.mas.specialists.production.prompt import PromptBuilder

    envelope = _envelope()
    prompt = PromptBuilder().build(envelope)
    assert "hypotheses" in prompt
    assert "considered_actions" in prompt
    for phrase in (
        "you must propose INCREASE_OFFTAKE",
        "always increase",
        "prefer INCREASE_OFFTAKE",
    ):
        assert phrase.lower() not in prompt.lower()
