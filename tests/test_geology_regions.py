"""Вывод геологической принадлежности скважин из самой колоды.

Реальный StateCard приходил с пустым `geology.block_membership`: в `src/` не
было ни одного источника блоков, и region-действия (REALLOCATE_INJECTION)
корректно снимались с предложения как незаземляемые.

Источник существует и лежит в колоде: массив регионов по ячейкам (`FIPFAULT`),
размерность сетки (`DIMENS`), устье скважины (`WELSPECS`) и интервал
перфораций (`COMPDAT`). Принадлежность выводится детерминированно и работает
на любой совместимой колоде -- никакого списка скважин и никакой привязки к
конкретному кейсу здесь нет.

Скважина, перфорированная через несколько регионов, относится к региону
БОЛЬШИНСТВА своих ячеек; при равенстве побеждает меньший номер региона,
поэтому результат не зависит от порядка обхода.
"""

from __future__ import annotations

import pytest

from aios.geology.regions import (
    RegionDerivationError,
    WellRegionMap,
    derive_well_regions,
    parse_cell_array,
    parse_grid_dimensions,
)

# Крошечная синтетическая сетка 2x2x2 с двумя известными регионами.
_MINI_DATA = """RUNSPEC
DIMENS
 2 2 2 /
"""

# k=1 -> все ячейки регион 1; k=2 -> все ячейки регион 2.
_MINI_REGS = """FIPFAULT
 4*1 4*2 /
"""

_MINI_SCH = """WELSPECS
 'P1' 'G' 1 1 1* 'OIL' /
 'P2' 'G' 2 2 1* 'OIL' /
/
COMPDAT
 'P1' 1* 1* 1 1 'OPEN' /
 'P2' 1* 1* 2 2 'OPEN' /
/
"""


def _mini_map() -> WellRegionMap:
    return derive_well_regions(
        data_text=_MINI_DATA, region_text=_MINI_REGS, schedule_text=_MINI_SCH
    )


# --- G1: пустая карта, когда источника нет --------------------------------


def test_no_region_array_yields_no_membership() -> None:
    """G5: отсутствие свидетельства не порождает выдуманных блоков."""
    result = derive_well_regions(data_text=_MINI_DATA, region_text="", schedule_text=_MINI_SCH)
    assert result.block_membership == {}
    assert result.regions == ()


def test_state_card_geology_is_empty_without_derivation() -> None:
    """G1: до подключения вывода реальный путь давал пустую принадлежность."""
    from aios.state import GeologyContext

    assert GeologyContext().block_membership == {}


# --- G2: вывод из сетки и перфораций --------------------------------------


def test_wells_are_assigned_to_their_completion_region() -> None:
    result = _mini_map()
    assert result.block_membership == {"P1": "1", "P2": "2"}
    assert result.regions == ("1", "2")


def test_grid_dimensions_are_read_from_the_deck() -> None:
    assert parse_grid_dimensions(_MINI_DATA) == (2, 2, 2)


def test_cell_array_expands_run_length_encoding() -> None:
    assert parse_cell_array(_MINI_REGS, "FIPFAULT") == [1, 1, 1, 1, 2, 2, 2, 2]


def test_cell_array_length_must_match_the_grid() -> None:
    with pytest.raises(RegionDerivationError):
        derive_well_regions(
            data_text=_MINI_DATA, region_text="FIPFAULT\n 3*1 /\n", schedule_text=_MINI_SCH
        )


# --- G3: скважина через несколько регионов ---------------------------------


def test_multi_region_well_takes_its_dominant_completion_region() -> None:
    sch = """WELSPECS
 'P1' 'G' 1 1 1* 'OIL' /
/
COMPDAT
 'P1' 1* 1* 1 2 'OPEN' /
/
"""
    # k=1 -> регион 1 (одна ячейка), k=2 -> регион 2 (одна ячейка): ничья.
    # Детерминированное правило -- меньший номер региона.
    result = derive_well_regions(data_text=_MINI_DATA, region_text=_MINI_REGS, schedule_text=sch)
    assert result.block_membership == {"P1": "1"}


def test_dominant_region_wins_over_a_minority_completion() -> None:
    data = "DIMENS\n 1 1 4 /\n"
    regs = "FIPFAULT\n 1 2 2 2 /\n"
    sch = """WELSPECS
 'P1' 'G' 1 1 1* 'OIL' /
/
COMPDAT
 'P1' 1* 1* 1 4 'OPEN' /
/
"""
    result = derive_well_regions(data_text=data, region_text=regs, schedule_text=sch)
    assert result.block_membership == {"P1": "2"}


def test_inactive_cells_do_not_assign_a_region() -> None:
    """Регион 0 -- отсутствие принадлежности, а не шестой блок."""
    data = "DIMENS\n 1 1 2 /\n"
    regs = "FIPFAULT\n 0 0 /\n"
    sch = """WELSPECS
 'P1' 'G' 1 1 1* 'OIL' /
/
COMPDAT
 'P1' 1* 1* 1 2 'OPEN' /
/
"""
    result = derive_well_regions(data_text=data, region_text=regs, schedule_text=sch)
    assert result.block_membership == {}


# --- G4: детерминизм -------------------------------------------------------


def test_derivation_is_deterministic_in_content_and_order() -> None:
    first, second = _mini_map(), _mini_map()
    assert first.block_membership == second.block_membership
    assert list(first.block_membership) == list(second.block_membership)
    assert first.regions == second.regions


def test_provenance_is_explicit() -> None:
    """G(I19): происхождение карты обязано быть named, а не подразумеваемым."""
    result = _mini_map()
    assert result.provenance
    assert "FIPFAULT" in result.provenance


# --- анти-оверфит ----------------------------------------------------------


def test_no_case_specific_identifiers_in_the_derivation() -> None:
    import re
    from pathlib import Path

    source = Path("src/aios/geology/regions.py").read_text(encoding="utf-8")
    for banned in ("model_y", "MODEL_Y", "EXP003", "EXP004", "state_digest"):
        assert banned not in source
    assert not re.search(r"[\"'](?:\d{1,2})[\"']\s*,\s*[\"'](?:\d{1,2})[\"']", source)


# --- G2/G(I19): загрузка по кейсу ------------------------------------------


def test_case_geology_scans_the_deck_for_a_region_array(tmp_path) -> None:
    """Имя файла с регионами не задаётся: оно ищется в самой колоде."""
    from aios.geology.regions import geology_for_deck

    root = tmp_path / "deck"
    (root / "INCLUDE").mkdir(parents=True)
    (root / "MINI.DATA").write_text(_MINI_DATA, encoding="utf-8")
    (root / "INCLUDE" / "regs.inc").write_text(_MINI_REGS, encoding="utf-8")
    (root / "INCLUDE" / "sch.inc").write_text(_MINI_SCH, encoding="utf-8")

    context = geology_for_deck(
        deck_root=root, data_file="MINI.DATA", schedule_include="INCLUDE/sch.inc"
    )
    assert context.block_membership == {"P1": "1", "P2": "2"}


def test_case_geology_is_empty_when_the_deck_has_no_region_array(tmp_path) -> None:
    from aios.geology.regions import geology_for_deck

    root = tmp_path / "deck"
    (root / "INCLUDE").mkdir(parents=True)
    (root / "MINI.DATA").write_text(_MINI_DATA, encoding="utf-8")
    (root / "INCLUDE" / "sch.inc").write_text(_MINI_SCH, encoding="utf-8")

    context = geology_for_deck(
        deck_root=root, data_file="MINI.DATA", schedule_include="INCLUDE/sch.inc"
    )
    assert context.block_membership == {}
