"""Экономические константы кейса как evidence для специалиста.

Здесь НЕ считается ни ценность кандидата, ни ожидаемый прирост ЧДД, ни
предпочтительное направление. Передаются только те нормативы, без которых
размен «добытая нефть против добытой жидкости» невозможно даже поставить как
вопрос: специалист видел обводнённость и не видел ни цены, ни затрат.

Источник ровно один -- тот же файл нормативов и тот же загрузчик официального
пакета, которым считает оценка. Второй источник тех же чисел разошёлся бы с
первым, и специалист рассуждал бы об экономике, которой система не считает.

Отказ закрытый: отсутствующий файл, отсутствующий норматив, нечисловое или
неконечное значение и противоречивые записи одного кода одинаково дают отказ,
а не подставленное значение.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from math import isfinite
from pathlib import Path

from aios.cases.contract import Case
from aios.evaluation.canonical import JsonValue
from aios.mas.evidence import EvidenceArtifact

ECONOMIC_CONTEXT_VERSION = "aios.mas.orchestration.economics/1.0.0"
ECONOMIC_EVIDENCE_ID = "economic.norms"

#: Нормативы, необходимые и достаточные, чтобы поставить размен нефть/жидкость.
#: Больше не передаётся: налоги, амортизация и стоимость насосов к этому
#: размену не относятся и только расширили бы поверхность утечки.
REQUIRED_CODES: tuple[str, ...] = (
    "oilPriceRubT",
    "deductionsRubT",
    "oilOpexRubT",
    "liquidOpexRubT",
)

UNITS: Mapping[str, str] = {
    "oilPriceRubT": "RUB per tonne of oil",
    "deductionsRubT": "RUB per tonne of oil",
    "oilOpexRubT": "RUB per tonne of oil",
    "liquidOpexRubT": "RUB per tonne of produced liquid",
}


class EconomicContextError(ValueError):
    """Авторитетные экономические константы недоступны или противоречивы."""


@contextmanager
def _calculator_on_path(root: Path):
    marker = str(root)
    inserted = marker not in sys.path
    if inserted:
        sys.path.insert(0, marker)
    try:
        yield
    finally:
        if inserted and marker in sys.path:
            sys.path.remove(marker)


def _raw_code_values(workbook: Path) -> dict[str, list[float]]:
    """Все записи каждого кода в листе нормативов -- для поиска противоречий."""
    import openpyxl

    try:
        book = openpyxl.load_workbook(workbook, data_only=True)
    except Exception as exc:
        raise EconomicContextError(f"cannot read the norms workbook {workbook}: {exc}") from exc
    found: dict[str, list[float]] = {}
    for sheet in book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            if not row or not isinstance(row[0], str):
                continue
            code = row[0].strip()
            if code not in REQUIRED_CODES:
                continue
            for cell in row[1:]:
                if isinstance(cell, bool) or not isinstance(cell, int | float):
                    continue
                found.setdefault(code, []).append(float(cell))
                break
    return found


def load_authoritative_norms(case: Case) -> dict[str, float]:
    """Нормативы из того же файла и тем же загрузчиком, что и оценка."""
    workbook = case.economics.norms_workbook
    if not workbook.is_file():
        raise EconomicContextError(f"CHDD norms workbook not found: {workbook}")
    with _calculator_on_path(case.economics.calculator_root):
        try:
            excel_io = importlib.import_module("excel_io")
        except Exception as exc:
            raise EconomicContextError(f"cannot import the official CHDD package: {exc}") from exc
        try:
            assumptions, _pumps = excel_io.load_norms(workbook)
        except Exception as exc:
            raise EconomicContextError(
                f"cannot read the CHDD norms workbook {workbook}: {exc}"
            ) from exc

    raw = _raw_code_values(workbook)
    values: dict[str, float] = {}
    for code in REQUIRED_CODES:
        if code not in assumptions:
            raise EconomicContextError(f"norms workbook {workbook} has no {code!r}")
        value = assumptions[code]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise EconomicContextError(f"norm {code!r} is not a number: {value!r}")
        number = float(value)
        if not isfinite(number):
            raise EconomicContextError(f"norm {code!r} is not finite: {value!r}")
        recorded = raw.get(code, [])
        if not recorded:
            raise EconomicContextError(f"norm {code!r} has no readable row in {workbook}")
        conflicting = {round(item, 9) for item in recorded} | {round(number, 9)}
        if len(conflicting) > 1:
            raise EconomicContextError(
                f"norm {code!r} is declared with conflicting values {sorted(conflicting)} "
                f"in {workbook}; refusing to choose one"
            )
        values[code] = number
    return values


def build_economic_context(case: Case) -> EvidenceArtifact:
    """Evidence-артефакт с нормативами. Ни вывода, ни рекомендации внутри."""
    values = load_authoritative_norms(case)
    workbook = case.economics.norms_workbook
    payload: dict[str, JsonValue] = {
        "basis": (
            "constants read from the same norms workbook, with the same loader, that the "
            "official calculator uses for evaluation"
        ),
        "limitations": (
            "these are unit norms only. No candidate value, no expected change in the "
            "objective and no preferred direction is computed here or implied by their "
            "presence; converting rates to value is the reader's own reasoning."
        ),
        "norms": dict(values),
        "units": dict(UNITS),
        "densities": {
            "oil_t_per_sm3": case.economics.oil_density_t_per_sm3,
            "water_t_per_sm3": case.economics.water_density_t_per_sm3,
        },
        "source": {
            "norms_workbook": workbook.name,
            "norms_workbook_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
            "calculator_provenance": case.economics.provenance,
        },
    }
    return EvidenceArtifact(
        evidence_id=ECONOMIC_EVIDENCE_ID,
        kind="economic_norms",
        payload=payload,
        source_version=ECONOMIC_CONTEXT_VERSION,
    )


__all__ = [
    "ECONOMIC_CONTEXT_VERSION",
    "ECONOMIC_EVIDENCE_ID",
    "REQUIRED_CODES",
    "UNITS",
    "EconomicContextError",
    "build_economic_context",
    "load_authoritative_norms",
]
