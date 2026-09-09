"""KT3-FINAL-EVIDENCE-PATCH-001: pre-action deliverability, economics, semantics.

Three omissions the diagnostic audit established as P0. Every value here is a
property of the state BEFORE any action: no candidate, no simulation outcome and
no evaluated objective participates in any of it.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from aios.mas.orchestration.economics import (
    ECONOMIC_EVIDENCE_ID,
    REQUIRED_CODES,
    EconomicContextError,
    build_economic_context,
    load_authoritative_norms,
)
from aios.state.extractor import (
    FLOOR_PINNED_SEMANTICS,
    MIN_DRAWDOWN_FOR_RATIO_BAR,
    OPTIONAL_WELL_VECTORS,
    WELL_VECTORS,
    StateExtractor,
    _drawdown,
)
from aios.state.state_card import WellRole

# ---------------------------------------------------------------------------
# A / B / C -- deliverability state
# ---------------------------------------------------------------------------


def test_a_block_pressure_is_part_of_the_extraction_contract() -> None:
    assert WELL_VECTORS["block_pressure"] == "WBP9"
    assert "block_pressure" in OPTIONAL_WELL_VECTORS


def test_b_drawdown_used_is_block_pressure_minus_bhp() -> None:
    used, ratio = _drawdown(
        role=WellRole.PRODUCER, block_pressure=176.8, bhp=159.7, producer_bhp_margin=109.7
    )
    assert used == pytest.approx(176.8 - 159.7)
    assert ratio == pytest.approx(109.7 / (176.8 - 159.7))


def test_c_ratio_is_zero_when_there_is_no_spare_margin() -> None:
    """Ноль -- это наблюдение «запаса нет», а не отсутствие данных."""
    used, ratio = _drawdown(
        role=WellRole.PRODUCER, block_pressure=60.0, bhp=50.0, producer_bhp_margin=0.0
    )
    assert used == pytest.approx(10.0)
    assert ratio == 0.0


def test_c_ratio_is_undefined_when_used_drawdown_is_near_zero() -> None:
    """Деление на почти ноль даёт артефакт, а не наблюдение -- поэтому None."""
    for used_bar in (0.0, -5.0, MIN_DRAWDOWN_FOR_RATIO_BAR / 2, MIN_DRAWDOWN_FOR_RATIO_BAR):
        used, ratio = _drawdown(
            role=WellRole.PRODUCER,
            block_pressure=50.0 + used_bar,
            bhp=50.0,
            producer_bhp_margin=70.0,
        )
        assert ratio is None, used_bar
        assert used == pytest.approx(used_bar)


def test_c_ratio_is_finite_whenever_it_is_defined() -> None:
    _used, ratio = _drawdown(
        role=WellRole.PRODUCER,
        block_pressure=50.0 + MIN_DRAWDOWN_FOR_RATIO_BAR * 1.001,
        bhp=50.0,
        producer_bhp_margin=136.0,
    )
    assert ratio is not None
    assert math.isfinite(ratio)


def test_missing_block_pressure_leaves_the_derived_values_absent() -> None:
    """Отсутствующий вектор -- отсутствие наблюдения, а не подставленное число."""
    assert _drawdown(
        role=WellRole.PRODUCER, block_pressure=None, bhp=100.0, producer_bhp_margin=50.0
    ) == (None, None)


def test_injectors_carry_no_producer_drawdown() -> None:
    assert _drawdown(
        role=WellRole.INJECTOR, block_pressure=250.0, bhp=300.0, producer_bhp_margin=None
    ) == (None, None)


def test_d_derivation_reads_only_the_current_state() -> None:
    """D -- ни будущего состояния, ни кандидата, ни оценки в источниках.

    Проверяется по исходному тексту: извлечение не имеет права импортировать
    ни симулятор, ни экономику, ни отбор победителя.
    """
    import ast

    tree = ast.parse(Path("src/aios/state/extractor.py").read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    for forbidden in ("aios.chdd", "aios.mas.selection", "aios.mas.magnitude"):
        assert not any(m.startswith(forbidden) for m in modules), forbidden
    source = Path("src/aios/state/extractor.py").read_text(encoding="utf-8")
    for token in ("npv", "candidate", "oracle", "ALL_UP"):
        assert token.lower() not in source.lower(), token


# ---------------------------------------------------------------------------
# E / F -- economic context
# ---------------------------------------------------------------------------


def test_e_economic_context_comes_from_the_authoritative_workbook(model_y_case) -> None:
    artifact = build_economic_context(model_y_case)
    payload = artifact.payload
    assert artifact.evidence_id == ECONOMIC_EVIDENCE_ID
    assert set(payload["norms"]) == set(REQUIRED_CODES)
    assert payload["source"]["norms_workbook"] == model_y_case.economics.norms_workbook.name
    # The same loader the evaluator uses, on the same configured path.
    assert payload["norms"] == load_authoritative_norms(model_y_case)
    assert payload["densities"]["oil_t_per_sm3"] == model_y_case.economics.oil_density_t_per_sm3


def test_e_economic_context_states_no_value_and_no_direction(model_y_case) -> None:
    """Проза только ОТРИЦАЕТ вывод; данные не содержат ни одного суждения.

    Токены проверяются раздельно: "no preferred direction" -- это и есть
    требуемое отрицание, и подстрочный поиск по всему артефакту принял бы его
    за нарушение.
    """
    payload = build_economic_context(model_y_case).payload
    assert "No candidate value" in payload["limitations"]
    assert "no preferred direction is computed" in payload["limitations"]

    data = " ".join(str(payload[key]) for key in ("norms", "units", "densities", "source")).lower()
    for forbidden in ("npv", "increase", "reduce", "recommend", "prefer", "should", "better"):
        assert forbidden not in data, forbidden


def test_f_missing_workbook_fails_closed(model_y_case, tmp_path) -> None:
    import dataclasses

    broken = dataclasses.replace(
        model_y_case,
        economics=dataclasses.replace(
            model_y_case.economics, norms_workbook=tmp_path / "absent.xlsx"
        ),
    )
    with pytest.raises(EconomicContextError, match="not found"):
        build_economic_context(broken)


def test_f_conflicting_constants_fail_closed(model_y_case, tmp_path) -> None:
    """Один код с двумя разными значениями -- отказ, а не выбор одного из них."""
    import dataclasses

    import openpyxl

    source = model_y_case.economics.norms_workbook
    book = openpyxl.load_workbook(source)
    sheet = book.worksheets[0]
    sheet.append(["oilPriceRubT", "duplicate", 12345.0])
    target = tmp_path / "conflicting.xlsx"
    book.save(target)
    conflicting = dataclasses.replace(
        model_y_case,
        economics=dataclasses.replace(model_y_case.economics, norms_workbook=target),
    )
    with pytest.raises(EconomicContextError, match="conflicting"):
        build_economic_context(conflicting)


# ---------------------------------------------------------------------------
# G -- floor-pinned semantics
# ---------------------------------------------------------------------------


def test_g_floor_semantics_are_neutral() -> None:
    assert len(FLOOR_PINNED_SEMANTICS) == 1
    text = FLOOR_PINNED_SEMANTICS[0].lower()
    assert "does not push it below the floor" in text
    assert "not by itself a constraint violation" in text
    for forbidden in (
        "safe",
        "desirable",
        "preferred",
        "profitable",
        "expected to work",
        "should",
        "recommend",
        "increase offtake",
        "beneficial",
        "better",
    ):
        assert forbidden not in text, forbidden


def test_g_semantics_reach_the_state_card(model_y_case) -> None:
    from aios.state.state_card import ConstraintState

    state = ConstraintState(official={}, semantics=FLOOR_PINNED_SEMANTICS)
    assert state.to_json()["semantics"] == list(FLOOR_PINNED_SEMANTICS)
    assert ConstraintState.from_json(state.to_json()).semantics == FLOOR_PINNED_SEMANTICS


def test_state_card_round_trips_the_new_fields() -> None:
    from aios.state.state_card import StateCard, WellStateSnapshot
    from tests.support.mas_team_fixtures import synthetic_state_card

    card = synthetic_state_card()
    payload = card.to_json()
    assert "WBP9" in payload["wells"][0]["realized"]
    assert "drawdown_used" in payload["wells"][0]["derived"]
    assert "drawdown_spare_ratio" in payload["wells"][0]["derived"]
    assert StateCard.from_json(payload).digest() == card.digest()
    revived = WellStateSnapshot.from_json(payload["wells"][0], "well")
    assert revived.to_json() == payload["wells"][0]


# ---------------------------------------------------------------------------
# H / I / J -- the authority invariants this patch must not touch
# ---------------------------------------------------------------------------


def test_h_required_coverage_cells_remain_state_derived() -> None:
    from aios.mas.coverage import required_cells
    from aios.mas.intents import ProductionAction

    small = required_cells(authorized=("A",), executable=(ProductionAction.INCREASE_OFFTAKE,))
    wide = required_cells(
        authorized=tuple(f"W{i}" for i in range(9)),
        executable=(ProductionAction.INCREASE_OFFTAKE, ProductionAction.REDUCE_OFFTAKE),
    )
    assert len(small) == 1
    assert len(wide) == 4


def test_i_specialist_still_owns_the_structural_answer() -> None:
    """Патч добавляет наблюдения, а не решения: контракт вывода не изменился."""
    from aios.mas.specialists.production.parser import _DECLARATION_KEYS, _INTENT_KEYS

    assert _INTENT_KEYS == {
        "action",
        "direction",
        "intensity",
        "target_wells",
        "target_regions",
        "evidence_ids",
        "uncertainty",
        "rationale_short",
    }
    assert _DECLARATION_KEYS == {
        "action",
        "scope_class",
        "disposition",
        "decline_reason",
        "explanation",
    }


def test_j_no_code_path_manufactures_a_production_intent() -> None:
    import ast

    for path in (
        "src/aios/state/extractor.py",
        "src/aios/mas/orchestration/economics.py",
    ):
        source = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "ProductionIntent" not in called, path
        assert "ProductionProposal" not in called, path


def test_patch_adds_no_oracle_identifier_to_src() -> None:
    for path in (
        "src/aios/state/extractor.py",
        "src/aios/state/state_card.py",
        "src/aios/mas/orchestration/economics.py",
    ):
        text = Path(path).read_text(encoding="utf-8").lower()
        for token in ("732cb967", "5435", "all_up", "oracle"):
            assert token not in text, (path, token)


def test_optional_vector_is_absent_rather_than_fatal() -> None:
    """Колода без WBP9 обязана давать None, а не отказ извлечения.

    Обязательный вектор поднял бы ошибку; необязательный оставляет производные
    пустыми, и специалист видит отсутствие наблюдения как отсутствие, а не как
    подставленное число.
    """

    class _NoBlockPressure:
        def has_well_vector(self, vector: str, well: str) -> bool:
            return vector != "WBP9"

        def well_value(self, vector: str, well: str, step: int) -> float:
            raise AssertionError("must not be read when the vector is absent")

    extractor = StateExtractor()
    assert extractor._optional_well_value(_NoBlockPressure(), "P1", "block_pressure", 0) is None
    assert extractor._optional_well_value(_NoBlockPressure(), "P1", "not_a_vector", 0) is None
