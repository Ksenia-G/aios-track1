"""Deterministic anti-anchoring prompt for the Production specialist."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from aios.mas.envelopes import ProductionEnvelope

from .evidence import EvidenceBuilder

PROMPT_BUILDER_VERSION = "aios.mas.specialists.production.prompt/1.4.0"

#: Порядок качественной силы намерения. ТОЛЬКО порядок: ни одного множителя,
#: процента или ставки здесь нет и быть не может -- перевод качественной силы
#: в число остаётся исключительной властью MagnitudeResolver, и специалист
#: этого перевода не знает.
INTENSITY_ORDER: tuple[str, ...] = ("NONE", "SMALL", "MODERATE", "AGGRESSIVE")

#: Статические строки контракта пучка. Вынесены отдельно от CONTEXT, потому
#: что проверять их обязаны тесты: в CONTEXT числа есть всегда (это измерения),
#: а в инструкции численной величины быть не должно ни одной.
BEAM_CONTRACT_LINES: tuple[str, ...] = (
    "You are the Production specialist in Hybrid MAS v1.1.",
    "Propose only structural production hypotheses for simulation.",
    "Never emit numerical magnitudes, setpoints, rates, percentages, or controls.",
    "Use only allowed actions and cite only evidence_ids present in the context.",
    (
        "hypotheses is a ranked list of 2 to 4 MATERIALLY DIFFERENT structural "
        "strategies, best first; each carries its own intents list. Two hypotheses "
        "differing only in wording are one hypothesis, not two. Materially different "
        "means a different physical strategy -- for example holding the current "
        "regime, lifting offtake where the evidence says it is economically "
        "promising, cutting offtake where it is problematic, or rebalancing between "
        "wells. That list is an illustration of what 'different' means, not a menu "
        "you must pick from and not a set of actions you are being told to propose: "
        "judge the evidence yourself and propose only strategies it supports."
    ),
    # --- A. scope diversity -------------------------------------------------
    (
        "SCOPE. A hypothesis may name as few or as many producers as its own "
        "reasoning justifies: a selective hypothesis naming only the wells whose "
        "own evidence singles them out is legitimate, and so is a field-scale "
        "hypothesis naming every producer whose evidence supports the same "
        "treatment. Neither is the safer choice and neither is the better one. A "
        "narrow scope is not automatically more cautious, and a broad scope is not "
        "automatically stronger; each is simply a claim about how widely the "
        "evidence reaches, and each is answerable from the same context."
    ),
    (
        "Hypotheses may differ in SCOPE as well as in action. If the evidence "
        "supports the same physical strategy both at a selective scope and at a "
        "wider one, those are two materially different hypotheses and both belong "
        "in the beam -- ranked by which you judge better, not by which is narrower. "
        "Do not collapse them into one, and do not manufacture a scope difference "
        "the evidence does not support."
    ),
    # --- B. ordered qualitative intensity ------------------------------------
    (
        "INTENSITY is an ordered qualitative scale of structural strength, from "
        "weakest to strongest: NONE, then SMALL, then MODERATE, then AGGRESSIVE. "
        "It states how strongly you claim the evidence supports the change. It is "
        "not a numerical control, not a rate, not a percentage, and it is not tied "
        "to any number: how a qualitative strength becomes an actual setpoint is "
        "decided downstream and is deliberately not visible to you."
    ),
    (
        "Choose each hypothesis's intensity from its own evidence. If the evidence "
        "supports materially different structural strengths of the same strategy, "
        "the beam should carry more than one of them rather than settling on the "
        "weakest by default. A stronger intensity is not riskier by definition and "
        "a weaker one is not safer by definition; both are claims to be grounded. "
        "Where the evidence supports only one strength, propose only that one."
    ),
    # --- structural coverage: preference and consideration are separate ------
    (
        "COVERAGE. hypotheses carries only what you recommend. considered_alternatives "
        "carries structural strategies you evaluated and do NOT recommend: same fields, "
        "same grounding duty, no ranking. Preferring one direction does not excuse "
        "leaving its opposite unexamined -- until the opposite has been stated as a "
        "concrete structural alternative it has not been ruled out, only left out."
    ),
    (
        "The context lists required_coverage_cells: the structural questions this state "
        "poses, each one an action family paired with a scope class. Return EXACTLY ONE "
        "entry in coverage_declarations for each listed cell -- no cell omitted, none "
        "repeated, none invented. This is a completeness contract, not a recommendation: "
        "it fixes which questions you answer, never which answer you give."
    ),
    (
        "Each declaration takes one disposition. PREFERRED means a ranked hypothesis of "
        "yours already covers that action at that scope. CONSIDERED_NOT_PREFERRED means a "
        "considered_alternative of yours does. DECLINED means neither, and then "
        "decline_reason must begin with exactly one of NOT_GROUNDABLE, INADMISSIBLE_NOW, "
        "NOT_MATERIALIZABLE, SEMANTICS_UNRESOLVED, optionally followed by ': ' and a short "
        "explanation. Declining is a legitimate answer -- do not invent a hypothesis you "
        "cannot ground in order to fill a cell."
    ),
    (
        "A declaration must match reality: claiming PREFERRED or CONSIDERED_NOT_PREFERRED "
        "for a cell you wrote no matching proposal for is rejected, and so is a reason that "
        "contradicts what the context already states about the action."
    ),
    (
        "To state a field-scale alternative, list every producer the context authorises "
        "in target_wells. Do not abbreviate it, do not describe it in words, and do not "
        "give a count -- the list itself is the claim, and it is checked against the "
        "authorised set, so a partial list is a selective alternative rather than a "
        "field-scale one."
    ),
    (
        "Judge every action in allowed_actions on the evidence and record each one "
        "you judge unsupported in considered_actions with a short evidence-based "
        "reason."
    ),
)


@dataclass(frozen=True, slots=True)
class PromptBuilder:
    """Build from the Envelope only, matching SpecialistPromptBuilder protocol."""

    evidence_builder: EvidenceBuilder = field(default_factory=EvidenceBuilder)
    version: str = PROMPT_BUILDER_VERSION

    def build(self, envelope: ProductionEnvelope) -> str:
        evidence = self.evidence_builder.build(envelope)
        context = {
            "prompt_builder_version": self.version,
            "schema_version": envelope.schema_version,
            "variant_id": envelope.variant_id,
            "config_digest": envelope.config_digest,
            "envelope_digest": envelope.digest(),
            "round": str(envelope.round),
            "state_card_schema_version": envelope.state_card.schema_version,
            "state_card_digest": envelope.state_card.digest(),
            "state_metadata": envelope.state_card.metadata.to_json(),
            "horizon_context": envelope.horizon_context.to_json(),
            "memory_slice": envelope.memory_slice.to_json(),
            "allowed_actions": sorted(str(action) for action in envelope.allowed_actions),
            # Какие структурные вопросы задаёт это состояние. Вычислено из
            # текущего фонда и исполнимости резолвера; ни одной величины, ни
            # одного имени скважины и ни одного указания, что предпочесть.
            "required_coverage_cells": [cell.to_json() for cell in self._required_cells(envelope)],
            "evidence": evidence.to_json(),
            "peer_proposals": sorted(
                (reference.to_json() for reference in envelope.peer_proposals),
                key=lambda item: (str(item["role"]), str(item["proposal_digest"])),
            ),
        }
        intent_schema = {
            "action": "<allowed ProductionAction>",
            "direction": "<UP|DOWN|NONE>",
            "intensity": "<NONE|SMALL|MODERATE|AGGRESSIVE>",
            "target_wells": ["<producer well id>"],
            "target_regions": ["<region id>"],
            "evidence_ids": ["<existing evidence id>"],
            "uncertainty": "<LOW|MEDIUM|HIGH|UNKNOWN>",
            "rationale_short": "<at most two short sentences>",
        }
        schema = {
            "hypotheses": [{"intents": [intent_schema]}],
            "considered_alternatives": [{"intents": [intent_schema]}],
            "coverage_declarations": [
                {
                    "action": "<allowed ProductionAction>",
                    "scope_class": "<SELECTIVE|BROAD>",
                    "disposition": "<PREFERRED|CONSIDERED_NOT_PREFERRED|DECLINED>",
                    "decline_reason": "<structural reason token when DECLINED, else empty>",
                    "explanation": "<one short sentence>",
                }
            ],
            "considered_actions": [
                {
                    "action": "<allowed ProductionAction>",
                    "supported": "<boolean>",
                    "reason": "<short evidence-based reason>",
                }
            ],
        }
        action_contracts = {
            "HOLD": "direction NONE; intensity NONE; no targets; empty rationale_short",
            "INCREASE_OFFTAKE": "direction UP; qualitative intensity; producer wells required",
            "REDUCE_OFFTAKE": "direction DOWN; qualitative intensity; producer wells required",
            "SHUT_IN": "direction NONE; intensity NONE; producer wells required",
            "REOPEN": "direction NONE; intensity NONE; producer wells required",
            "REBALANCE_WITHIN_BLOCK": (
                "direction NONE; qualitative intensity; producer wells and their known block required"
            ),
            "ALL_NON_HOLD": "cite at least one existing evidence_id; no numerical output",
        }
        return "\n".join(
            (
                *BEAM_CONTRACT_LINES,
                "Ordered qualitative intensity scale, weakest to strongest:",
                json.dumps(list(INTENSITY_ORDER), ensure_ascii=False, separators=(",", ":")),
                "Return one JSON object only, with exactly this schema:",
                json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "Frozen action contracts:",
                json.dumps(
                    action_contracts,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "CONTEXT:",
                json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )

    @staticmethod
    def _required_cells(envelope: ProductionEnvelope):
        """Требуемые ячейки покрытия для ЭТОГО состояния.

        Единственный источник -- тот же валидатор, что потом проверяет ответ:
        второй список разошёлся бы с ним и приглашал модель в заведомо
        отвергаемый ответ.
        """
        from aios.mas.coverage import required_cells

        from .coverage import authorized_producers, executable_actions

        return required_cells(
            authorized=authorized_producers(envelope), executable=executable_actions(envelope)
        )

    def build_schema_retry(self, envelope: ProductionEnvelope) -> str:
        return (
            self.build(envelope)
            + "\nPrevious response failed strict schema validation. Retry JSON only."
        )
