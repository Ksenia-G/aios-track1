"""ProductionIntent / InjectionIntent -- граница NO SETPOINTS CROSS.

LLM предлагает гипотезы через структурные intents: действие, направление,
качественная интенсивность, целевые скважины/регионы, evidence. Numeric
prescriptive control (LRAT=..., RATE=..., BHP=..., surface_rate=...) на этом
уровне запрещён architecturally -- он появляется только после Arbiter, на
границе MagnitudeResolver (см. aios.mas.magnitude). Producer -> injector
конверсия здесь не существует и намеренно не добавлена: HOLD -- единственный
noop, ROLE фиксирован типом контракта.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from aios.evaluation.canonical import JsonValue, digest
from aios.mas.common import Direction, Intensity, NetTotalEffect, UncertaintyLevel

MAX_RATIONALE_CHARS = 240
MAX_RATIONALE_SENTENCES = 2


class IntentError(ValueError):
    """Intent contract нарушен: недопустимая комбинация полей или setpoint-like данные."""


class ProductionAction(StrEnum):
    """Структурное действие Production-специалиста."""

    HOLD = "HOLD"
    INCREASE_OFFTAKE = "INCREASE_OFFTAKE"
    REDUCE_OFFTAKE = "REDUCE_OFFTAKE"
    SHUT_IN = "SHUT_IN"
    REOPEN = "REOPEN"
    REBALANCE_WITHIN_BLOCK = "REBALANCE_WITHIN_BLOCK"


class InjectionAction(StrEnum):
    """Структурное действие Injection-специалиста."""

    HOLD = "HOLD"
    REALLOCATE_INJECTION = "REALLOCATE_INJECTION"
    SCALE_TOTAL_INJECTION = "SCALE_TOTAL_INJECTION"
    REDUCE_SUPPORT_PATH = "REDUCE_SUPPORT_PATH"
    SUPPORT_REGION = "SUPPORT_REGION"
    REBALANCE_ACROSS_BLOCKS = "REBALANCE_ACROSS_BLOCKS"


_INJECTION_NET_EFFECT: dict[InjectionAction, NetTotalEffect] = {
    InjectionAction.HOLD: NetTotalEffect.ZERO,
    InjectionAction.REALLOCATE_INJECTION: NetTotalEffect.ZERO,
    InjectionAction.SCALE_TOTAL_INJECTION: NetTotalEffect.DIRECTIONAL,
    InjectionAction.REDUCE_SUPPORT_PATH: NetTotalEffect.DECREASE,
    InjectionAction.SUPPORT_REGION: NetTotalEffect.INCREASE,
    InjectionAction.REBALANCE_ACROSS_BLOCKS: NetTotalEffect.ZERO,
}


def net_total_effect_for(action: InjectionAction) -> NetTotalEffect:
    """Метаданные суммарного эффекта action на total закачку. Не число."""
    return _INJECTION_NET_EFFECT[InjectionAction(action)]


def _canonical(values: Iterable[str]) -> tuple[str, ...]:
    """Порядок в этих полях не несёт семантики: канонизируем сортировкой."""
    return tuple(sorted(values))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntentError(message)


def _validate_rationale_short(value: str, where: str) -> None:
    """Короткое auditable объяснение, не chain-of-thought.

    Простой детерминированный лимит без NLP-зависимости: не более
    MAX_RATIONALE_SENTENCES предложений и MAX_RATIONALE_CHARS символов.
    """
    text = value.strip()
    _require(bool(text), f"{where}: rationale_short must be non-empty for a non-HOLD intent")
    _require(len(text) <= MAX_RATIONALE_CHARS, f"{where}: rationale_short exceeds {MAX_RATIONALE_CHARS} chars")
    sentence_count = len([part for part in re.split(r"[.!?]+", text) if part.strip()])
    _require(
        sentence_count <= MAX_RATIONALE_SENTENCES,
        f"{where}: rationale_short must be at most {MAX_RATIONALE_SENTENCES} sentences",
    )


@dataclass(frozen=True, slots=True)
class ProductionIntent:
    """Структурное намерение Production-специалиста. Никаких числовых полей."""

    action: ProductionAction
    direction: Direction
    intensity: Intensity
    target_wells: tuple[str, ...]
    target_regions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    uncertainty: UncertaintyLevel
    rationale_short: str

    def __post_init__(self) -> None:
        where = "production_intent"
        action = ProductionAction(self.action)
        Direction(self.direction)
        Intensity(self.intensity)
        UncertaintyLevel(self.uncertainty)

        if action is ProductionAction.HOLD:
            _require(self.direction is Direction.NONE, f"{where}.HOLD: direction must be NONE")
            _require(self.intensity is Intensity.NONE, f"{where}.HOLD: intensity must be NONE")
            _require(not self.target_wells, f"{where}.HOLD: target_wells must be empty")
            _require(not self.target_regions, f"{where}.HOLD: target_regions must be empty")
        elif action is ProductionAction.INCREASE_OFFTAKE:
            _require(self.direction is Direction.UP, f"{where}.INCREASE_OFFTAKE: direction must be UP")
            _require(self.intensity is not Intensity.NONE, f"{where}.INCREASE_OFFTAKE: intensity required")
            _require(bool(self.target_wells), f"{where}.INCREASE_OFFTAKE: target_wells required")
        elif action is ProductionAction.REDUCE_OFFTAKE:
            _require(self.direction is Direction.DOWN, f"{where}.REDUCE_OFFTAKE: direction must be DOWN")
            _require(self.intensity is not Intensity.NONE, f"{where}.REDUCE_OFFTAKE: intensity required")
            _require(bool(self.target_wells), f"{where}.REDUCE_OFFTAKE: target_wells required")
        elif action is ProductionAction.SHUT_IN:
            _require(self.direction is Direction.NONE, f"{where}.SHUT_IN: direction must be NONE")
            _require(self.intensity is Intensity.NONE, f"{where}.SHUT_IN: intensity must be NONE")
            _require(bool(self.target_wells), f"{where}.SHUT_IN: target_wells required")
        elif action is ProductionAction.REOPEN:
            _require(self.direction is Direction.NONE, f"{where}.REOPEN: direction must be NONE")
            _require(self.intensity is Intensity.NONE, f"{where}.REOPEN: intensity must be NONE")
            _require(bool(self.target_wells), f"{where}.REOPEN: target_wells required")
        elif action is ProductionAction.REBALANCE_WITHIN_BLOCK:
            _require(
                self.direction is Direction.NONE, f"{where}.REBALANCE_WITHIN_BLOCK: direction must be NONE"
            )
            _require(
                self.intensity is not Intensity.NONE, f"{where}.REBALANCE_WITHIN_BLOCK: intensity required"
            )
            _require(bool(self.target_wells), f"{where}.REBALANCE_WITHIN_BLOCK: target_wells required")
            _require(bool(self.target_regions), f"{where}.REBALANCE_WITHIN_BLOCK: target_regions required")

        if action is not ProductionAction.HOLD:
            _require(bool(self.evidence_ids), f"{where}: non-HOLD intent requires evidence_ids")
            _validate_rationale_short(self.rationale_short, f"{where}.rationale_short")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "action": str(self.action),
            "direction": str(self.direction),
            "intensity": str(self.intensity),
            "target_wells": list(_canonical(self.target_wells)),
            "target_regions": list(_canonical(self.target_regions)),
            "evidence_ids": list(_canonical(self.evidence_ids)),
            "uncertainty": str(self.uncertainty),
            "rationale_short": self.rationale_short,
        }

    def digest(self) -> str:
        return digest(self.to_json())


@dataclass(frozen=True, slots=True)
class InjectionIntent:
    """Структурное намерение Injection-специалиста. Никаких числовых полей."""

    action: InjectionAction
    direction: Direction
    intensity: Intensity
    donor_wells: tuple[str, ...]
    recipient_wells: tuple[str, ...]
    source_regions: tuple[str, ...]
    target_regions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    uncertainty: UncertaintyLevel
    rationale_short: str

    def __post_init__(self) -> None:
        where = "injection_intent"
        action = InjectionAction(self.action)
        Direction(self.direction)
        Intensity(self.intensity)
        UncertaintyLevel(self.uncertainty)

        if action is InjectionAction.HOLD:
            _require(self.direction is Direction.NONE, f"{where}.HOLD: direction must be NONE")
            _require(self.intensity is Intensity.NONE, f"{where}.HOLD: intensity must be NONE")
            _require(not self.donor_wells, f"{where}.HOLD: donor_wells must be empty")
            _require(not self.recipient_wells, f"{where}.HOLD: recipient_wells must be empty")
            _require(not self.source_regions, f"{where}.HOLD: source_regions must be empty")
            _require(not self.target_regions, f"{where}.HOLD: target_regions must be empty")
        elif action is InjectionAction.SCALE_TOTAL_INJECTION:
            _require(
                self.direction in (Direction.UP, Direction.DOWN),
                f"{where}.SCALE_TOTAL_INJECTION: direction must be UP or DOWN",
            )
            _require(self.intensity is not Intensity.NONE, f"{where}.SCALE_TOTAL_INJECTION: intensity required")
            _require(not self.donor_wells, f"{where}.SCALE_TOTAL_INJECTION: donor_wells must be empty")
            _require(not self.recipient_wells, f"{where}.SCALE_TOTAL_INJECTION: recipient_wells must be empty")
        elif action is InjectionAction.SUPPORT_REGION:
            _require(self.direction is Direction.UP, f"{where}.SUPPORT_REGION: direction must be UP")
            _require(self.intensity is not Intensity.NONE, f"{where}.SUPPORT_REGION: intensity required")
            _require(
                bool(self.recipient_wells) or bool(self.target_regions),
                f"{where}.SUPPORT_REGION: recipient_wells or target_regions required",
            )
        elif action is InjectionAction.REDUCE_SUPPORT_PATH:
            _require(self.direction is Direction.DOWN, f"{where}.REDUCE_SUPPORT_PATH: direction must be DOWN")
            _require(self.intensity is not Intensity.NONE, f"{where}.REDUCE_SUPPORT_PATH: intensity required")
            _require(
                bool(self.donor_wells) or bool(self.source_regions),
                f"{where}.REDUCE_SUPPORT_PATH: donor_wells or source_regions required",
            )
        elif action is InjectionAction.REALLOCATE_INJECTION:
            _require(self.direction is Direction.NONE, f"{where}.REALLOCATE_INJECTION: direction must be NONE")
            _require(self.intensity is not Intensity.NONE, f"{where}.REALLOCATE_INJECTION: intensity required")
            _require(bool(self.donor_wells), f"{where}.REALLOCATE_INJECTION: donor_wells required")
            _require(bool(self.recipient_wells), f"{where}.REALLOCATE_INJECTION: recipient_wells required")
            _require(bool(self.source_regions), f"{where}.REALLOCATE_INJECTION: source_regions required")
            _require(bool(self.target_regions), f"{where}.REALLOCATE_INJECTION: target_regions required")
            _require(
                _canonical(self.source_regions) == _canonical(self.target_regions),
                f"{where}.REALLOCATE_INJECTION: source_regions and target_regions must be the same scope",
            )
        elif action is InjectionAction.REBALANCE_ACROSS_BLOCKS:
            _require(
                self.direction is Direction.NONE, f"{where}.REBALANCE_ACROSS_BLOCKS: direction must be NONE"
            )
            _require(
                self.intensity is not Intensity.NONE, f"{where}.REBALANCE_ACROSS_BLOCKS: intensity required"
            )
            _require(bool(self.donor_wells), f"{where}.REBALANCE_ACROSS_BLOCKS: donor_wells required")
            _require(bool(self.recipient_wells), f"{where}.REBALANCE_ACROSS_BLOCKS: recipient_wells required")
            _require(bool(self.source_regions), f"{where}.REBALANCE_ACROSS_BLOCKS: source_regions required")
            _require(bool(self.target_regions), f"{where}.REBALANCE_ACROSS_BLOCKS: target_regions required")
            _require(
                _canonical(self.source_regions) != _canonical(self.target_regions),
                f"{where}.REBALANCE_ACROSS_BLOCKS: source_regions and target_regions must differ",
            )

        if action is not InjectionAction.HOLD:
            _require(bool(self.evidence_ids), f"{where}: non-HOLD intent requires evidence_ids")
            _validate_rationale_short(self.rationale_short, f"{where}.rationale_short")

    @property
    def net_total_effect(self) -> NetTotalEffect:
        return net_total_effect_for(InjectionAction(self.action))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "action": str(self.action),
            "direction": str(self.direction),
            "intensity": str(self.intensity),
            "donor_wells": list(_canonical(self.donor_wells)),
            "recipient_wells": list(_canonical(self.recipient_wells)),
            "source_regions": list(_canonical(self.source_regions)),
            "target_regions": list(_canonical(self.target_regions)),
            "evidence_ids": list(_canonical(self.evidence_ids)),
            "uncertainty": str(self.uncertainty),
            "rationale_short": self.rationale_short,
            "net_total_effect": str(self.net_total_effect),
        }

    def digest(self) -> str:
        return digest(self.to_json())
