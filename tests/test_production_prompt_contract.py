"""Контракт Production-промпта: разнообразие scope и intensity без подсказки.

Промпт обязан объяснить, ЧТО считается материально разной гипотезой, и не
обязан называть победителя. Проверяется отдельно статическая инструкция, а не
собранный промпт целиком: в CONTEXT числа есть всегда -- это измерения, -- а в
инструкции численной величины быть не должно ни одной.
"""

from __future__ import annotations

import json
import re

from aios.mas.intents import ProductionAction
from aios.mas.specialists.authority import contains_numeric_magnitude
from aios.mas.specialists.production.prompt import (
    BEAM_CONTRACT_LINES,
    INTENSITY_ORDER,
    PromptBuilder,
)
from tests.support.mas_team_fixtures import synthetic_production_envelope

INSTRUCTIONS = "\n".join(BEAM_CONTRACT_LINES)


def _envelope():
    return synthetic_production_envelope(
        allowed_actions=(
            ProductionAction.HOLD,
            ProductionAction.INCREASE_OFFTAKE,
            ProductionAction.REDUCE_OFFTAKE,
            ProductionAction.SHUT_IN,
        )
    )


# --- what the contract must say --------------------------------------------


def test_intensity_scale_is_stated_as_an_order() -> None:
    assert INTENSITY_ORDER == ("NONE", "SMALL", "MODERATE", "AGGRESSIVE")
    assert "NONE, then SMALL, then MODERATE, then AGGRESSIVE" in INSTRUCTIONS
    prompt = PromptBuilder().build(_envelope())
    assert json.dumps(list(INTENSITY_ORDER), separators=(",", ":")) in prompt


def test_intensity_is_declared_qualitative_not_numerical() -> None:
    assert "not a numerical control" in INSTRUCTIONS
    assert "not tied" in INSTRUCTIONS


def test_beam_must_not_collapse_to_the_weakest_intensity() -> None:
    assert "rather than settling on the weakest by default" in INSTRUCTIONS


def test_scope_diversity_is_required_when_groundable() -> None:
    assert "differ in SCOPE as well as in action" in INSTRUCTIONS
    assert "both belong" in INSTRUCTIONS


def test_neither_scope_is_declared_preferable() -> None:
    assert "Neither is the safer choice and neither is the better one" in INSTRUCTIONS
    assert "not automatically more cautious" in INSTRUCTIONS
    assert "not automatically stronger" in INSTRUCTIONS


# --- what the contract must NOT say ----------------------------------------


def test_no_action_is_mandatory() -> None:
    lowered = INSTRUCTIONS.lower()
    for phrase in (
        "you must propose increase_offtake",
        "always increase",
        "prefer increase_offtake",
        "must increase offtake",
        "propose increase_offtake",
        "choose moderate",
        "use moderate",
        "must be moderate",
        "prefer aggressive",
    ):
        assert phrase not in lowered
    assert "not a menu you must pick from" in INSTRUCTIONS


def test_scope_and_intensity_lines_carry_no_numerical_magnitude() -> None:
    """Ни множителя, ни процента, ни ставки: NO SETPOINTS CROSS начинается здесь.

    Проверяется тем же предикатом, которым специалист отвергает численную
    величину в rationale модели, а не собственным вторым правилом. Строки,
    добавленные этой задачей, обязаны его проходить целиком.
    """
    contract_lines = [
        line
        for line in BEAM_CONTRACT_LINES
        if line.startswith(
            (
                "SCOPE.",
                "Hypotheses may differ",
                "INTENSITY is",
                "Choose each",
                "The context lists",
                "Each declaration",
                "A declaration must match",
            )
        )
    ]
    assert len(contract_lines) == 7
    for line in contract_lines:
        assert not contains_numeric_magnitude(line), line
        assert re.search(r"\d", line) is None, line


def test_no_magnitude_vocabulary_anywhere_in_the_instructions() -> None:
    """Подстрочный поиск здесь намеренно узкий.

    "rate" встречается внутри слова MODERATE, а "percentage"/"setpoint" -- внутри
    самого запрета ("not a rate, not a percentage"). Поэтому численную величину
    ловит `contains_numeric_magnitude` выше, а этот тест закрывает только те
    токены, которые не могут появиться ни в одном честном контексте.
    """
    lowered = INSTRUCTIONS.lower()
    for token in ("x2", "×2", "double", "multiplier", "factor", "lrat", "bhp"):
        assert token not in lowered


def test_instructions_name_no_well_and_no_oracle_scope() -> None:
    """Ни одного идентификатора скважины и ни одного размера фонда."""
    lowered = INSTRUCTIONS.lower()
    for token in ("33", "all_up", "all up", "oracle", "npv", "чдд", "5435"):
        assert token not in lowered


def test_prompt_does_not_leak_the_oracle_anywhere() -> None:
    prompt = PromptBuilder().build(_envelope()).lower()
    for token in ("all_up_200", "oracle", "5435", "732cb967"):
        assert token not in prompt


def test_no_setpoints_cross_holds_for_the_rendered_prompt() -> None:
    """Схема ответа не предлагает ни одного численного поля управления."""
    from aios.mas.providers.schemas import (
        FORBIDDEN_SCHEMA_FIELDS,
        production_response_schema,
        schema_field_names,
    )

    assert schema_field_names(production_response_schema()) & FORBIDDEN_SCHEMA_FIELDS == set()


def test_prompt_still_carries_the_beam_schema() -> None:
    prompt = PromptBuilder().build(_envelope())
    assert "hypotheses" in prompt
    assert "considered_actions" in prompt


def test_prompt_builder_version_moved() -> None:
    assert PromptBuilder().version == "aios.mas.specialists.production.prompt/1.4.0"
