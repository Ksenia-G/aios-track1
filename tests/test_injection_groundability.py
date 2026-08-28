"""Каждое действие, предложенное Injection-специалисту, обязано быть заземляемым.

Реальный StateCard Model Y приходит с пустым `geology.block_membership`:
в `src/` нет ни одного источника блоков, они заполняются только в фикстурах.
На таком снимке любая ссылка на регион отвергается ScopeAuditor, поэтому
предлагать действия, которые без регионов неисполнимы, -- значит приглашать
живого провайдера в заведомо отвергаемый ответ. Один такой отказ уже наблюдался
на реальном прогоне: target_regions 'producers:1,9,...' -> DETERMINISTIC_FALLBACK
при полностью исправном Production (LLM_LIVE, SHUT_IN).

Инвариант: КАЖДАЯ пара действие/цель, предложенная специалисту, обязана быть
заземляемой против текущего StateCard.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.specialists.injection.prompt import PromptBuilder
from aios.mas.specialists.injection.scope import (
    ScopeAuditor,
    groundable_injection_actions,
    state_card_regions,
)
from aios.state import GeologyContext
from tests.support.mas_team_fixtures import (
    synthetic_injection_envelope,
    synthetic_state_card,
)

#: Действия, замороженный контракт которых ТРЕБУЕТ region-поля (см.
#: InjectionIntent.__post_init__): без регионов они неисполнимы в принципе.
REGION_ONLY_ACTIONS = (
    InjectionAction.REALLOCATE_INJECTION,
    InjectionAction.REBALANCE_ACROSS_BLOCKS,
)

#: Действия, принимающие скважины ИЛИ регионы: остаются доступными, но их
#: контракт не имеет права предлагать region-вариант при пустых блоках.
WELLS_OR_REGIONS_ACTIONS = (
    InjectionAction.SUPPORT_REGION,
    InjectionAction.REDUCE_SUPPORT_PATH,
)

ALL_ACTIONS = tuple(InjectionAction)


def _card_without_regions():
    return replace(synthetic_state_card(), geology=GeologyContext())


def _card_with_regions():
    return synthetic_state_card()


def test_real_state_card_condition_has_no_regions() -> None:
    """Условие реального прогона: блоков нет вовсе."""
    assert state_card_regions(_card_without_regions()) == frozenset()
    assert state_card_regions(_card_with_regions()) == frozenset({"SYN-B1"})


@pytest.mark.parametrize("action", REGION_ONLY_ACTIONS)
def test_region_requiring_actions_are_not_offered_without_regions(action) -> None:
    offered = groundable_injection_actions(_card_without_regions(), ALL_ACTIONS)
    assert action not in offered


def test_hold_and_well_scoped_actions_survive_without_regions() -> None:
    offered = groundable_injection_actions(_card_without_regions(), ALL_ACTIONS)
    assert InjectionAction.HOLD in offered
    assert InjectionAction.SCALE_TOTAL_INJECTION in offered
    for action in WELLS_OR_REGIONS_ACTIONS:
        assert action in offered


def test_every_action_survives_when_regions_exist() -> None:
    offered = groundable_injection_actions(_card_with_regions(), ALL_ACTIONS)
    assert set(offered) == set(ALL_ACTIONS)


def test_filter_preserves_order_and_never_invents_actions() -> None:
    offered = groundable_injection_actions(_card_without_regions(), ALL_ACTIONS)
    assert list(offered) == [a for a in ALL_ACTIONS if a in set(offered)]


@pytest.mark.parametrize("action", WELLS_OR_REGIONS_ACTIONS)
def test_prompt_never_invites_region_targets_when_no_regions_exist(action) -> None:
    """Утечка через «wells ИЛИ regions»: контракт обязан требовать скважины."""
    card = _card_without_regions()
    envelope = synthetic_injection_envelope(
        state_card=card,
        allowed_actions=groundable_injection_actions(card, ALL_ACTIONS),
    )
    payload = json.loads(PromptBuilder().build(envelope))
    contract = payload["action_field_contracts"][str(action)]
    # Регион не предлагается как ВАРИАНТ выполнения...
    assert "or target_regions" not in contract
    assert "or source_regions" not in contract
    # ...и прямо запрещён, иначе «промолчать» о нём -- всё ещё приглашение.
    assert "MUST stay empty" in contract
    required = (
        "recipient_wells required"
        if action is InjectionAction.SUPPORT_REGION
        else "donor_wells required"
    )
    assert required in contract


def test_prompt_does_not_advertise_region_only_actions_without_regions() -> None:
    card = _card_without_regions()
    envelope = synthetic_injection_envelope(
        state_card=card,
        allowed_actions=groundable_injection_actions(card, ALL_ACTIONS),
    )
    payload = json.loads(PromptBuilder().build(envelope))
    advertised = set(payload["allowed_actions"])
    for action in REGION_ONLY_ACTIONS:
        assert str(action) not in advertised
        assert str(action) not in payload["action_field_contracts"]


def test_prompt_keeps_region_contract_when_regions_exist() -> None:
    """Фикс не имеет права обеднять промпт там, где блоки реально есть."""
    card = _card_with_regions()
    envelope = synthetic_injection_envelope(
        state_card=card,
        allowed_actions=groundable_injection_actions(card, ALL_ACTIONS),
    )
    payload = json.loads(PromptBuilder().build(envelope))
    assert "target_regions" in payload["action_field_contracts"]["SUPPORT_REGION"]
    assert "source_regions" in payload["action_field_contracts"]["REDUCE_SUPPORT_PATH"]


def _well_scoped_intent(action: InjectionAction) -> InjectionIntent:
    """Минимальный intent, использующий ТОЛЬКО скважины."""
    common = {
        "evidence_ids": ("SYN-pressure-1",),
        "uncertainty": UncertaintyLevel.MEDIUM,
        "rationale_short": "well scoped support decision",
        "source_regions": (),
        "target_regions": (),
    }
    if action is InjectionAction.HOLD:
        return InjectionIntent(
            action=action,
            direction=Direction.NONE,
            intensity=Intensity.NONE,
            donor_wells=(),
            recipient_wells=(),
            evidence_ids=(),
            uncertainty=UncertaintyLevel.LOW,
            rationale_short="",
            source_regions=(),
            target_regions=(),
        )
    if action is InjectionAction.SCALE_TOTAL_INJECTION:
        return InjectionIntent(
            action=action,
            direction=Direction.UP,
            intensity=Intensity.SMALL,
            donor_wells=(),
            recipient_wells=(),
            **common,
        )
    if action is InjectionAction.SUPPORT_REGION:
        return InjectionIntent(
            action=action,
            direction=Direction.UP,
            intensity=Intensity.SMALL,
            donor_wells=(),
            recipient_wells=("SYN-I1",),
            **common,
        )
    if action is InjectionAction.REDUCE_SUPPORT_PATH:
        return InjectionIntent(
            action=action,
            direction=Direction.DOWN,
            intensity=Intensity.SMALL,
            donor_wells=("SYN-I1",),
            recipient_wells=(),
            **common,
        )
    raise AssertionError(f"unexpected offered action {action}")


def test_every_offered_action_is_groundable_against_the_state_card() -> None:
    """Сам инвариант: всё предложенное обязано проходить ScopeAuditor."""
    card = _card_without_regions()
    offered = groundable_injection_actions(card, ALL_ACTIONS)
    envelope = synthetic_injection_envelope(state_card=card, allowed_actions=offered)
    auditor = ScopeAuditor()
    for action in offered:
        auditor.audit((_well_scoped_intent(action),), envelope)


def test_benchmark_funnel_applies_groundability_to_the_real_state_card() -> None:
    """Ворота бенчмарка обязаны фильтровать, а не только уметь фильтровать."""
    from aios.benchmark.mas_variant import INJECTION_ACTIONS, injection_actions_for_state

    offered = injection_actions_for_state(_card_without_regions())
    for action in REGION_ONLY_ACTIONS:
        if action in INJECTION_ACTIONS:
            assert action not in offered
    assert InjectionAction.HOLD in offered
    assert set(offered) <= set(INJECTION_ACTIONS)


def test_benchmark_funnel_is_unchanged_when_regions_exist() -> None:
    from aios.benchmark.mas_variant import INJECTION_ACTIONS, injection_actions_for_state

    assert injection_actions_for_state(_card_with_regions()) == INJECTION_ACTIONS
