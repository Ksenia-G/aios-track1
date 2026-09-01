"""Диагностический контекст одной эпохи решения.

T0 собирал диагностику в бенчмарке, а `default_next_input` не собирал её
вовсе: на T1/T2 все слоты evidence приходили как None, и специалист отклонял
активные гипотезы не потому, что состояние их не поддерживает, а потому что
свидетельств физически не было. Здесь живёт ОДИН строитель, которым
пользуются обе точки, чтобы у T0 и следующих эпох была одна семантика
диагностики и отличалось только реализованное состояние.

Разделение, которое этот модуль обязан удерживать:

* ДИНАМИКА (давление/охват, региональный дисбаланс поддержки) считается
  заново от StateCard эпохи. Протянуть сюда T0-диагностику было бы такой же
  ошибкой, как оставить слот пустым, только незаметной: вход выглядел бы
  обоснованным, а описывал бы прошлое;
* СТАТИКА (связность отсеков по массиву регионов колоды) переиспользуется.
  Она не зависит от режимов и не обязана менять идентичность от эпохи к
  эпохе -- поэтому её `evidence_id` стабилен, а у динамических фактов он
  проштампован датой своей эпохи: сослаться на «давление» без указания,
  какой эпохи, значит разрешить цитировать устаревший факт.

Новой физики здесь нет: считают существующие `aios.diagnostics`, а модуль
только раскладывает их результат по типизированным слотам входа. Численных
управлений в evidence не появляется -- диагностики их не производят.
"""

from __future__ import annotations

from aios.diagnostics import PressureDiagnostic, SweepDiagnostic
from aios.diagnostics.support import (
    CONNECTIVITY_VERSION,
    IMBALANCE_VERSION,
    derive_support_connectivity,
    derive_support_imbalance,
)
from aios.mas.evidence import EvidenceArtifact
from aios.state import StateCard

#: Префиксы id evidence. Динамические дополняются датой своей эпохи.
PRESSURE_EVIDENCE_PREFIX = "diagnostic.pressure"
SWEEP_EVIDENCE_PREFIX = "diagnostic.sweep"
SUPPORT_IMBALANCE_EVIDENCE_PREFIX = "diagnostic.support_imbalance"
#: Связность выводится из колоды, а не из режимов, и id у неё один на прогон.
SUPPORT_CONNECTIVITY_EVIDENCE_ID = "diagnostic.support_connectivity"

#: Слоты `DecisionStepInput`/envelope, которые заполняет этот строитель.
CONTEXT_SLOTS: tuple[str, ...] = (
    "pressure_diagnostic",
    "sweep_diagnostic",
    "geology_card",
    "response_map",
)


def epoch_evidence_id(prefix: str, state_card: StateCard) -> str:
    """id динамического факта, принадлежащий эпохе своего StateCard."""
    return f"{prefix}.{state_card.metadata.date.isoformat()}"


def build_decision_context(state_card: StateCard) -> dict[str, EvidenceArtifact]:
    """Диагностические слоты входа для эпохи, представленной ``state_card``.

    Возвращаются только те слоты, под которые есть содержание: пустая
    геология даёт отсутствующую связность, а не выдуманные связи.
    """
    context: dict[str, EvidenceArtifact] = {
        "pressure_diagnostic": EvidenceArtifact(
            evidence_id=epoch_evidence_id(PRESSURE_EVIDENCE_PREFIX, state_card),
            kind="pressure_diagnostic",
            payload=PressureDiagnostic().propose(state_card).to_json(),
        ),
        "sweep_diagnostic": EvidenceArtifact(
            evidence_id=epoch_evidence_id(SWEEP_EVIDENCE_PREFIX, state_card),
            kind="sweep_diagnostic",
            payload=SweepDiagnostic().propose(state_card).to_json(),
        ),
    }
    connectivity = derive_support_connectivity(wells=state_card.wells, geology=state_card.geology)
    if connectivity.links:
        context["geology_card"] = EvidenceArtifact(
            evidence_id=SUPPORT_CONNECTIVITY_EVIDENCE_ID,
            kind="support_connectivity",
            payload=connectivity.to_json(),
            source_version=CONNECTIVITY_VERSION,
        )
    imbalance = derive_support_imbalance(wells=state_card.wells, geology=state_card.geology)
    if imbalance.scopes:
        context["response_map"] = EvidenceArtifact(
            evidence_id=epoch_evidence_id(SUPPORT_IMBALANCE_EVIDENCE_PREFIX, state_card),
            kind="regional_support_imbalance",
            payload=imbalance.to_json(),
            source_version=IMBALANCE_VERSION,
        )
    return context


__all__ = [
    "CONTEXT_SLOTS",
    "PRESSURE_EVIDENCE_PREFIX",
    "SUPPORT_CONNECTIVITY_EVIDENCE_ID",
    "SUPPORT_IMBALANCE_EVIDENCE_PREFIX",
    "SWEEP_EVIDENCE_PREFIX",
    "build_decision_context",
    "epoch_evidence_id",
]
