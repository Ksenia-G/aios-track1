"""Сверка donor/recipient/regions с текущим StateCard.

Injection Controller читает StateCard, но не ставит setpoints. Wells и
блоки в intents обязаны существовать в текущем снимке; donor/recipient --
только нагнетатели.

Здесь же живёт обратная сторона той же границы: какие действия вообще имеет
смысл ПРЕДЛАГАТЬ специалисту на данном снимке. Правило заземления региона
одно, и держать его в одном модуле обязательно: список допустимых действий,
собранный отдельно от аудитора, разошёлся бы с ним при первом же изменении и
приглашал бы модель в заведомо отвергаемый ответ.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from aios.mas.envelopes import InjectionEnvelope
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.state import StateCard, WellRole, WellStateSnapshot

#: Действия, чей замороженный контракт ТРЕБУЕТ region-поля (см.
#: `InjectionIntent.__post_init__`): без известных блоков они неисполнимы, и
#: предлагать их -- значит приглашать провайдера в заведомо отвергаемый ответ.
REGION_REQUIRED_ACTIONS = frozenset(
    {
        InjectionAction.REALLOCATE_INJECTION,
        InjectionAction.REBALANCE_ACROSS_BLOCKS,
    }
)


class ScopeError(ValueError):
    """Intent ссылается на скважину или регион вне текущего StateCard."""


def state_card_regions(state_card: StateCard) -> frozenset[str]:
    """Блоки, против которых вообще можно заземлить region-цель."""
    return frozenset(dict(state_card.geology.block_membership).values())


def groundable_injection_actions(
    state_card: StateCard, actions: Sequence[InjectionAction]
) -> tuple[InjectionAction, ...]:
    """Подмножество `actions`, заземляемое против текущего StateCard.

    Ничего не выдумывает: при известных блоках список возвращается как есть.
    Пустой `block_membership` -- это не повод сочинить членство, а повод не
    предлагать действия, которые без него не собираются.
    """
    if state_card_regions(state_card):
        return tuple(actions)
    return tuple(action for action in actions if action not in REGION_REQUIRED_ACTIONS)


__all__ = [
    "REGION_REQUIRED_ACTIONS",
    "ScopeAuditor",
    "ScopeError",
    "groundable_injection_actions",
    "state_card_regions",
]


class ScopeAuditor:
    """Проверить, что структурные цели принадлежат текущему StateCard."""

    def audit(
        self,
        intents: Sequence[InjectionIntent],
        envelope: InjectionEnvelope,
    ) -> tuple[InjectionIntent, ...]:
        if not intents:
            raise ScopeError("intents must not be empty")
        wells: dict[str, WellStateSnapshot] = {
            well.well_id: well for well in envelope.state_card.wells
        }
        injectors = {well_id for well_id, well in wells.items() if well.role is WellRole.INJECTOR}
        membership = dict(envelope.state_card.geology.block_membership)
        regions = frozenset(membership.values())
        for intent in intents:
            _audit_wells(intent.donor_wells, wells, injectors, "donor_wells")
            _audit_wells(intent.recipient_wells, wells, injectors, "recipient_wells")
            _audit_regions(intent.source_regions, regions, "source_regions")
            _audit_regions(intent.target_regions, regions, "target_regions")
            _audit_well_region_alignment(
                intent.donor_wells, intent.source_regions, membership, "donor_wells"
            )
            _audit_well_region_alignment(
                intent.recipient_wells, intent.target_regions, membership, "recipient_wells"
            )
        return tuple(intents)


def _audit_wells(
    well_ids: tuple[str, ...],
    wells: Mapping[str, WellStateSnapshot],
    injectors: set[str],
    where: str,
) -> None:
    for well_id in well_ids:
        if well_id not in wells:
            raise ScopeError(f"{where}: well {well_id!r} is not in the current StateCard")
        if well_id not in injectors:
            raise ScopeError(f"{where}: well {well_id!r} is not an injector on the StateCard")


def _audit_regions(region_ids: tuple[str, ...], known: frozenset[str], where: str) -> None:
    for region_id in region_ids:
        if region_id not in known:
            raise ScopeError(
                f"{where}: region {region_id!r} is not in StateCard geology.block_membership"
            )


def _audit_well_region_alignment(
    well_ids: tuple[str, ...],
    region_ids: tuple[str, ...],
    membership: dict[str, str],
    where: str,
) -> None:
    if not well_ids or not region_ids:
        return
    allowed = frozenset(region_ids)
    for well_id in well_ids:
        block = membership.get(well_id)
        if block not in allowed:
            raise ScopeError(
                f"{where}: well {well_id!r} belongs to {block!r}, not {sorted(allowed)}"
            )
