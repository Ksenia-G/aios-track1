"""Запуск официального калькулятора ЧДД.

Формулы не переписываются и не дублируются. Пакет `CHDD_PYTHON` подключается
как есть по пути из кейса, и всё, что делает этот модуль, -- кормит его
корректным входом и забирает результат.

Отсюда следует и режим отказа: `ValueError` калькулятора (например, `WLPR`
выше 500) не перехватывается в число, а поднимается как отказ пайплайна.

Результат калькулятора разбирается строго. Умолчания для обязательных полей
здесь запрещены: пропущенный `totalChddM`, подставленный как 0.0, -- это не
осторожность, а тихая подмена официального результата числом, которое никто
не считал. Отсутствие ключа, неверный тип, NaN и бесконечность одинаково
дают отказ.
"""

import importlib
import sys
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.chdd.trajectory import ChddInput


class ChddError(RuntimeError):
    """Экономический расчёт не выполнен."""


class ChddResultSchemaError(ChddError):
    """Результат калькулятора не соответствует ожидаемой форме."""


def _require_mapping(payload: Any, key: str, where: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ChddResultSchemaError(f"CHDD result: {where} is not an object")
    if key not in payload:
        raise ChddResultSchemaError(f"CHDD result: missing required section {where}.{key}")
    value = payload[key]
    if not isinstance(value, Mapping):
        raise ChddResultSchemaError(f"CHDD result: {where}.{key} must be an object")
    return value


def _require_sequence(payload: Mapping[str, Any], key: str, where: str) -> Sequence[Any]:
    if key not in payload:
        raise ChddResultSchemaError(f"CHDD result: missing required section {where}.{key}")
    value = payload[key]
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ChddResultSchemaError(f"CHDD result: {where}.{key} must be a list")
    return value


def _require_number(payload: Mapping[str, Any], key: str, where: str) -> float:
    if key not in payload:
        raise ChddResultSchemaError(f"CHDD result: missing required field {where}.{key}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ChddResultSchemaError(
            f"CHDD result: {where}.{key} must be a number, got {type(value).__name__}"
        )
    number = float(value)
    if not isfinite(number):
        raise ChddResultSchemaError(f"CHDD result: {where}.{key} must be finite, got {value!r}")
    return number


def _require_count(payload: Mapping[str, Any], key: str, where: str) -> int:
    """Целочисленный счётчик. Калькулятор отдаёт часть из них как float."""

    number = _require_number(payload, key, where)
    if not number.is_integer():
        raise ChddResultSchemaError(
            f"CHDD result: {where}.{key} must be a whole number, got {number}"
        )
    return int(number)


def _require_text(payload: Mapping[str, Any], key: str, where: str) -> str:
    if key not in payload:
        raise ChddResultSchemaError(f"CHDD result: missing required field {where}.{key}")
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise ChddResultSchemaError(f"CHDD result: {where}.{key} must be a non-empty string")
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    """Необязательное поле: калькулятор вправе не знать значения.

    Единственный такой случай -- `sourceMaxDate`: он диагностический и на
    заявленный результат не влияет.
    """

    value = payload.get(key)
    return None if value is None else str(value)


@contextmanager
def _calculator_on_path(root: Path):
    if not (root / "chdd_model.py").is_file():
        raise ChddError(f"official CHDD package not found at {root}")
    marker = str(root)
    inserted = marker not in sys.path
    if inserted:
        sys.path.insert(0, marker)
    try:
        yield
    finally:
        if inserted and marker in sys.path:
            sys.path.remove(marker)


ANNUAL_NPV_KEYS = ("chddM", "yearlyChddM", "yearlyNpvM", "npvM")
ANNUAL_CUMULATIVE_KEYS = ("cumulativeChddM", "cumulativeNpvM", "cumulativeM")
ANNUAL_CORE_KEYS = frozenset(
    {
        "year",
        "months",
        "activeWellMonths",
        "stopCount",
        "startCount",
        *ANNUAL_NPV_KEYS,
        *ANNUAL_CUMULATIVE_KEYS,
    }
)


@dataclass(frozen=True, slots=True)
class AnnualChddRow:
    """One annual row emitted by the official calculator."""

    year: int
    npv_mln_rub: float
    cumulative_mln_rub: float | None
    months: int
    active_well_months: int
    stop_events: int
    start_events: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "npv_mln_rub": self.npv_mln_rub,
            "cumulative_mln_rub": self.cumulative_mln_rub,
            "months": self.months,
            "active_well_months": self.active_well_months,
            "stop_events": self.stop_events,
            "start_events": self.start_events,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ChddResult:
    """Итог экономического расчёта вместе с происхождением."""

    calculator_version: str
    provenance: str
    npv_mln_rub: float
    profitability_index: float
    economic_month_count: int
    months_by_year: dict[str, int]
    yearly_profile: tuple[AnnualChddRow, ...]
    start_date: str
    last_economic_month: str | None
    source_max_date: str | None
    oil_kt_total: float
    liquid_kt_total: float
    injection_k_total: float
    active_well_months: int
    pump_events: int
    stop_events: int
    start_events: int
    conversion_events: int
    excluded_negative_rows: int
    duplicate_well_months: int
    invalid_dates: int
    ignored_blank_wells: int
    warnings: tuple[str, ...]
    raw: Mapping[str, Any]
    input_digest: str | None = None
    """Идентичность входа, по которому посчитан этот результат.

    Формулы и суммы от этого поля не зависят: оно отвечает только на вопрос
    "из какой траектории получено это число". Экономически авторитетным
    результат без него не считается.
    """

    def to_json(self) -> dict[str, Any]:
        return {
            "input_digest": self.input_digest,
            "calculator_version": self.calculator_version,
            "provenance": self.provenance,
            "npv_mln_rub": self.npv_mln_rub,
            "profitability_index": self.profitability_index,
            "economic_month_count": self.economic_month_count,
            "months_by_year": dict(self.months_by_year),
            "yearly_profile": [row.to_json() for row in self.yearly_profile],
            "economic_start_date": self.start_date,
            "last_economic_month": self.last_economic_month,
            "source_max_date": self.source_max_date,
            "oil_kt_total": self.oil_kt_total,
            "liquid_kt_total": self.liquid_kt_total,
            "injection_k_total": self.injection_k_total,
            "active_well_months": self.active_well_months,
            "events": {
                "pump": self.pump_events,
                "stop": self.stop_events,
                "start": self.start_events,
                "conversion": self.conversion_events,
            },
            "excluded_negative_rows": self.excluded_negative_rows,
            "duplicate_well_months": self.duplicate_well_months,
            "invalid_dates": self.invalid_dates,
            "ignored_blank_wells": self.ignored_blank_wells,
            "warnings": list(self.warnings),
        }


def _annual_money(entry: Mapping[str, Any], keys: tuple[str, ...], where: str) -> float | None:
    for key in keys:
        if key in entry:
            return _require_number(entry, key, where)
    return None


def parse_chdd_result(
    result: Any, *, provenance: str, input_digest: str | None = None
) -> ChddResult:
    """Строго разобрать результат официального калькулятора.

    Каждое поле, от которого зависят заявленный NPV, экономическая
    диагностика или вердикт, обязано присутствовать, быть числом нужного
    вида и конечным. Необязательным остаётся только `sourceMaxDate`.
    """

    if not isinstance(result, Mapping):
        raise ChddResultSchemaError("CHDD result: top level is not an object")

    summary = _require_mapping(result, "summary", "result")
    diagnostics = _require_mapping(result, "diagnostics", "result")
    annual = _require_sequence(result, "annual", "result")
    monthly = _require_sequence(result, "fieldMonthly", "result")

    months_by_year: dict[str, int] = {}
    yearly_profile: list[AnnualChddRow] = []
    active_well_months = 0
    stop_events = 0
    start_events = 0
    for index, entry in enumerate(annual):
        where = f"result.annual[{index}]"
        if not isinstance(entry, Mapping):
            raise ChddResultSchemaError(f"CHDD result: {where} must be an object")
        year = _require_count(entry, "year", where)
        months = _require_count(entry, "months", where)
        active = _require_count(entry, "activeWellMonths", where)
        stops = _require_count(entry, "stopCount", where)
        starts = _require_count(entry, "startCount", where)
        npv = _annual_money(entry, ANNUAL_NPV_KEYS, where)
        if npv is None:
            raise ChddResultSchemaError(f"CHDD result: {where}.yearly NPV is missing")
        cumulative = _annual_money(entry, ANNUAL_CUMULATIVE_KEYS, where)
        metadata = {str(key): value for key, value in entry.items() if key not in ANNUAL_CORE_KEYS}
        months_by_year[str(year)] = months
        active_well_months += active
        stop_events += stops
        start_events += starts
        yearly_profile.append(
            AnnualChddRow(
                year=year,
                npv_mln_rub=npv,
                cumulative_mln_rub=cumulative,
                months=months,
                active_well_months=active,
                stop_events=stops,
                start_events=starts,
                metadata=metadata,
            )
        )

    last_economic_month: str | None = None
    for index, entry in enumerate(monthly):
        if not isinstance(entry, Mapping):
            raise ChddResultSchemaError(
                f"CHDD result: result.fieldMonthly[{index}] must be an object"
            )
    if monthly:
        last_economic_month = _require_text(
            monthly[-1], "month", f"result.fieldMonthly[{len(monthly) - 1}]"
        )

    warnings = _require_sequence(diagnostics, "warnings", "result.diagnostics")

    return ChddResult(
        input_digest=input_digest,
        calculator_version=_require_text(result, "version", "result"),
        provenance=provenance,
        npv_mln_rub=_require_number(summary, "totalChddM", "result.summary"),
        profitability_index=_require_number(summary, "profitabilityIndex", "result.summary"),
        economic_month_count=len(monthly),
        months_by_year=months_by_year,
        yearly_profile=tuple(yearly_profile),
        start_date=_require_text(result, "startDate", "result"),
        last_economic_month=last_economic_month,
        source_max_date=_optional_text(diagnostics, "sourceMaxDate"),
        oil_kt_total=_require_number(summary, "totalOilKt", "result.summary"),
        liquid_kt_total=_require_number(summary, "totalLiquidKt", "result.summary"),
        injection_k_total=_require_number(summary, "totalInjectionK", "result.summary"),
        active_well_months=active_well_months,
        pump_events=_require_count(summary, "pumpChanges", "result.summary"),
        stop_events=stop_events,
        start_events=start_events,
        conversion_events=_require_count(summary, "conversionCount", "result.summary"),
        excluded_negative_rows=_require_count(
            diagnostics, "excludedNegativeRows", "result.diagnostics"
        ),
        duplicate_well_months=_require_count(
            diagnostics, "duplicateWellMonths", "result.diagnostics"
        ),
        invalid_dates=_require_count(diagnostics, "invalidDates", "result.diagnostics"),
        ignored_blank_wells=_require_count(diagnostics, "ignoredBlankWells", "result.diagnostics"),
        warnings=tuple(str(item) for item in warnings),
        raw=result,
    )


def run_chdd(case: Case, chdd_input: ChddInput, *, source_file: Path) -> ChddResult:
    """Посчитать ЧДД официальным пакетом по уже построенному входу.

    Любой сбой пакета -- импорта, чтения нормативов, самого расчёта -- выходит
    отсюда как `ChddError`. Наружу не должно уехать исключение, которое
    пайплайн не ждёт и потому не обработает по правилам закрытого отказа.
    """

    with _calculator_on_path(case.economics.calculator_root):
        try:
            chdd_model = importlib.import_module("chdd_model")
            excel_io = importlib.import_module("excel_io")
        except Exception as exc:
            raise ChddError(f"cannot import the official CHDD package: {exc}") from exc

        norms = case.economics.norms_workbook
        if not norms.is_file():
            raise ChddError(f"CHDD norms workbook not found: {norms}")
        try:
            assumptions, pumps = excel_io.load_norms(norms)
        except Exception as exc:
            raise ChddError(f"cannot read the CHDD norms workbook {norms}: {exc}") from exc

        records = [dict(zip(chdd_input.headers, row, strict=True)) for row in chdd_input.rows]
        try:
            result = chdd_model.compute_calculation(
                records,
                headers=list(chdd_input.headers),
                assumptions=assumptions,
                pumps=pumps,
                start_date=case.economics.economic_start_date.isoformat(),
                source_file=str(source_file),
            )
        except ValueError as exc:
            raise ChddError(f"official CHDD calculator rejected the input: {exc}") from exc
        except Exception as exc:
            raise ChddError(f"official CHDD calculator failed: {exc}") from exc

    # Происхождение проставляет авторитетный расчёт: результат называет вход,
    # по которому он получен, и подменить траекторию после расчёта нельзя.
    return parse_chdd_result(
        result,
        provenance=case.economics.provenance,
        input_digest=chdd_input.digest(),
    )
