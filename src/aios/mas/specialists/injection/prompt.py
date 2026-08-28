"""Промпт Injection Controller. Только envelope, без baseline.

Сигнатура build(envelope) -- часть T1: baseline вторым аргументом передать
нельзя. Численные setpoints в промпт как команды не попадают.
"""

from __future__ import annotations

from aios.evaluation.canonical import canonical_json
from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.envelopes import InjectionEnvelope
from aios.mas.intents import InjectionAction
from aios.mas.invariants import require_no_baseline_field
from aios.mas.specialists.injection.evidence_builder import EvidenceBuilder
from aios.mas.specialists.injection.scope import state_card_regions

PROMPT_BUILDER_VERSION = "aios.mas.specialists.injection.prompt/1.2.0"

#: Полевые правила каждого действия, как их проверяет замороженный
#: `InjectionIntent.__post_init__`.
#:
#: Без них модель видела плоский список полей и не могла знать, что
#: `SCALE_TOTAL_INJECTION` означает глобальное масштабирование и потому
#: запрещает адресные списки скважин. Она честно пыталась выразить адресную
#: поддержку неподходящим действием, и строгий парсер справедливо отклонял
#: результат. Контракт здесь не смягчается -- он просто становится видимым,
#: как это давно сделано у Production.
_ACTION_FIELD_CONTRACTS: dict[str, str] = {
    "HOLD": (
        "direction NONE; intensity NONE; donor_wells, recipient_wells, "
        "source_regions and target_regions all empty; rationale_short empty"
    ),
    "SCALE_TOTAL_INJECTION": (
        "global change of total injection. direction UP or DOWN; qualitative intensity; "
        "donor_wells and recipient_wells MUST be empty. To target specific wells or "
        "regions choose SUPPORT_REGION or REDUCE_SUPPORT_PATH instead"
    ),
    "SUPPORT_REGION": (
        "targeted increase. direction UP; qualitative intensity; "
        "recipient_wells or target_regions required"
    ),
    "REDUCE_SUPPORT_PATH": (
        "targeted decrease. direction DOWN; qualitative intensity; "
        "donor_wells or source_regions required"
    ),
    "REALLOCATE_INJECTION": (
        "move injection within one scope. direction NONE; qualitative intensity; "
        "donor_wells, recipient_wells, source_regions and target_regions all required; "
        "source_regions and target_regions must be the SAME scope"
    ),
    "REBALANCE_ACROSS_BLOCKS": (
        "move injection between scopes. direction NONE; qualitative intensity; "
        "donor_wells, recipient_wells, source_regions and target_regions all required; "
        "source_regions and target_regions must DIFFER"
    ),
    "ALL_NON_HOLD": "cite at least one existing evidence_id; never emit a numerical magnitude",
}

#: Те же два действия на снимке БЕЗ известных блоков. Они принимают скважины
#: ИЛИ регионы, поэтому исчезновение region-действий из allowed_actions их не
#: закрывает: контракт всё ещё предлагал бы region-вариант, и живой провайдер
#: честно им пользовался -- ответ отвергал ScopeAuditor, а специалист падал в
#: DETERMINISTIC_FALLBACK. Регионы здесь не выдумываются: они просто перестают
#: предлагаться, пока StateCard их не знает.
_WELLS_ONLY_ACTION_FIELD_CONTRACTS: dict[str, str] = {
    "SUPPORT_REGION": (
        "targeted increase. direction UP; qualitative intensity; "
        "recipient_wells required. This StateCard exposes no geological blocks, "
        "so source_regions and target_regions MUST stay empty"
    ),
    "REDUCE_SUPPORT_PATH": (
        "targeted decrease. direction DOWN; qualitative intensity; "
        "donor_wells required. This StateCard exposes no geological blocks, "
        "so source_regions and target_regions MUST stay empty"
    ),
}


class PromptBuildError(ValueError):
    """Промпт из envelope собрать нельзя."""


class PromptBuilder:
    """SpecialistPromptBuilder[InjectionEnvelope]: один аргумент, без baseline."""

    version = PROMPT_BUILDER_VERSION

    def build(self, envelope: InjectionEnvelope) -> str:
        if not isinstance(envelope, InjectionEnvelope):
            raise PromptBuildError(f"expected InjectionEnvelope, got {type(envelope).__name__}")
        require_no_baseline_field(envelope)
        # Заземляемость решает StateCard, а не вкус промпта: пока блоков нет,
        # region-вариант не предлагается ни одним каналом.
        contracts = dict(_ACTION_FIELD_CONTRACTS)
        if not state_card_regions(envelope.state_card):
            contracts.update(_WELLS_ONLY_ACTION_FIELD_CONTRACTS)
        payload = {
            "prompt_builder_version": self.version,
            "role": "INJECTION",
            "task": (
                "Return a JSON object with key intents. Use only allowed_actions, and "
                "obey the field contract of the action you choose. "
                "Do not emit numerical setpoints."
            ),
            "schema_version": envelope.schema_version,
            "round": str(envelope.round),
            "variant_id": envelope.variant_id,
            "horizon": envelope.horizon_context.to_json(),
            "allowed_actions": sorted(str(action) for action in envelope.allowed_actions),
            # Только контракты предлагаемых действий: лишние описания склоняют
            # модель к действию, которого ей всё равно не разрешили.
            "action_field_contracts": {
                name: rule
                for name, rule in contracts.items()
                if name == "ALL_NON_HOLD"
                or name in {str(action) for action in envelope.allowed_actions}
            },
            "vocabulary": {
                "action": sorted(member.value for member in InjectionAction),
                "direction": sorted(member.value for member in Direction),
                "intensity": sorted(member.value for member in Intensity),
                "uncertainty": sorted(member.value for member in UncertaintyLevel),
            },
            # Наблюдательное состояние скважин -- это факты, на которых
            # специалист рассуждает. current_target/control_mode -- снимок
            # текущего режима, а не предписание: он описывает, что уже стоит.
            "wells": [
                {
                    "well_id": well.well_id,
                    "role": str(well.role),
                    "status": well.status,
                    "control_mode": well.control_mode,
                    "current_target": well.current_target,
                    "realized": {
                        "BHP": well.bhp,
                        "WWIR": well.wwir,
                        "WLPR": well.wlpr,
                        "WOPR": well.wopr,
                        "WWPR": well.wwpr,
                        "WWCT": well.wct,
                    },
                    "derived": {
                        "producer_bhp_margin": well.producer_bhp_margin,
                        "injector_bhp_margin": well.injector_bhp_margin,
                        "wlpr_margin": well.wlpr_margin,
                    },
                }
                for well in envelope.state_card.wells
            ],
            "field_state": envelope.state_card.field.to_json(),
            "geology": envelope.state_card.geology.to_json(),
            "uncertainty": envelope.state_card.uncertainty.to_json(),
            # Содержание диагностики, а не только её имя. Идентификатор без
            # содержания не является evidence: без payload два материально
            # разных диагноза давали побайтово одинаковый промпт, и специалист
            # не мог отличить стабильное давление от падающего.
            "evidence": [
                {
                    "evidence_id": artifact.evidence_id,
                    "kind": artifact.kind,
                    "source_version": artifact.source_version,
                    "payload": artifact.to_json()["payload"],
                }
                for artifact in EvidenceBuilder().build(envelope).artifacts
            ],
            "peer_proposals": [ref.to_json() for ref in envelope.peer_proposals],
            "output_schema": {
                "intents": [
                    {
                        "action": "InjectionAction",
                        "direction": "Direction",
                        "intensity": "Intensity",
                        "donor_wells": ["well_id"],
                        "recipient_wells": ["well_id"],
                        "source_regions": ["region_id"],
                        "target_regions": ["region_id"],
                        "evidence_ids": ["evidence_id"],
                        "uncertainty": "UncertaintyLevel",
                        "rationale_short": "string",
                    }
                ]
            },
        }
        return canonical_json(payload)
