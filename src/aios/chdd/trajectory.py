"""14-колоночный вход официального калькулятора ЧДД.

Контракт колонок принадлежит калькулятору (`REQUIRED_COLUMNS` в `chdd_model`),
здесь он только воспроизводится. Три вещи в этом модуле принципиальны:

1. Массовый мост. OPM отдаёт объёмы, калькулятор ждёт тонны по нефти и по
   жидкости. Жидкость собирается как масса нефти плюс масса воды, а не как
   объём, выданный за тонны. Плотности берутся из кейса (в колоде Model Y --
   832 и 1012.8 кг/м3) и остаются кандидатскими до сверки с tNavigator.
2. Приросты считаются внутри одной скважины. Глобальный `diff` по склеенному
   столбцу даёт отрицательные строки на стыках скважин; фильтр отрицательных
   строк калькулятора в конкретном датасете их убирает, но это совпадение,
   а не гарантия.
3. Подаётся вся история, а не только оцениваемое окно: состояние ЭЦН
   восстанавливается машиной состояний калькулятора по всем строкам, а деньги
   считаются только от `start_date`.

Дата пишется голым `YYYY-MM-DD`: строку с временем калькулятор молча
выбрасывает, и месяц добычи исчез бы без единой ошибки.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.opm.summary import SummaryData

CHDD_COLUMNS = (
    "DATA",
    "well",
    "WLPT",
    "WLPR",
    "WOMT",
    "WOMR",
    "WWIR",
    "WWIT",
    "THP",
    "BHP",
    "WEFF",
    "WLPT_Diff",
    "WOMT_Diff",
    "WWIT_Diff",
)

REQUIRED_VECTORS = ("WOPT", "WOPR", "WWPT", "WLPR", "WWIR", "WWIT", "WBHP", "WBP9")

WELL_EFFICIENCY = "1"


class TrajectoryError(RuntimeError):
    """Траекторию нельзя привести к контракту калькулятора."""


def _number(value: float) -> str:
    return f"{value:.10g}"


@dataclass(frozen=True, slots=True)
class TrajectoryStats:
    """Проверяемые свойства построенного входа."""

    row_count: int
    well_count: int
    interval_count: int
    date_min: str
    date_max: str
    economic_rows: int
    pre_start_rows: int
    max_wlpr: float
    rows_over_wlpr_limit: int
    negative_diff_counts: dict[str, int]

    def to_json(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "well_count": self.well_count,
            "interval_count": self.interval_count,
            "date_min": self.date_min,
            "date_max": self.date_max,
            "economic_rows": self.economic_rows,
            "pre_start_rows": self.pre_start_rows,
            "max_wlpr": self.max_wlpr,
            "rows_over_wlpr_limit": self.rows_over_wlpr_limit,
            "negative_diff_counts": dict(self.negative_diff_counts),
        }


@dataclass(frozen=True, slots=True)
class ChddInput:
    """Готовый вход калькулятора."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    stats: TrajectoryStats


def build_chdd_input(summary: SummaryData, case: Case) -> ChddInput:
    """Собрать 14-колоночный вход из сводки OPM."""

    missing = summary.missing_well_vectors(REQUIRED_VECTORS)
    if missing:
        raise TrajectoryError(f"summary is missing required vectors: {', '.join(missing[:10])}")
    if summary.report_steps < 2:
        raise TrajectoryError("need at least two report states to form one interval")

    rho_oil = case.economics.oil_density_t_per_sm3
    rho_water = case.economics.water_density_t_per_sm3
    econ_start = case.economics.economic_start_date.isoformat()
    wlpr_limit = case.constraints.producer_wlpr_max

    rows: list[tuple[str, ...]] = []
    negatives = {"WLPT_Diff": 0, "WOMT_Diff": 0, "WWIT_Diff": 0}
    max_wlpr = 0.0
    over_limit = 0

    for well in summary.wells:
        for index in range(summary.report_steps - 1):
            start, end = index, index + 1
            oil_mass_start = summary.well_value("WOPT", well, start) * rho_oil
            oil_mass_end = summary.well_value("WOPT", well, end) * rho_oil
            water_mass_start = summary.well_value("WWPT", well, start) * rho_water
            water_mass_end = summary.well_value("WWPT", well, end) * rho_water
            liquid_mass_start = oil_mass_start + water_mass_start
            liquid_mass_end = oil_mass_end + water_mass_end
            injection_start = summary.well_value("WWIT", well, start)
            injection_end = summary.well_value("WWIT", well, end)

            diff_liquid = liquid_mass_end - liquid_mass_start
            diff_oil = oil_mass_end - oil_mass_start
            diff_injection = injection_end - injection_start
            for key, value in (
                ("WLPT_Diff", diff_liquid),
                ("WOMT_Diff", diff_oil),
                ("WWIT_Diff", diff_injection),
            ):
                if value < 0:
                    negatives[key] += 1

            liquid_rate = summary.well_value("WLPR", well, end)
            max_wlpr = max(max_wlpr, liquid_rate)
            if liquid_rate > wlpr_limit:
                over_limit += 1

            # Метка строки -- начало интервала, а значения состояния -- на его
            # конце. Это та же раскладка, что даёт легаси-выгрузка, поэтому
            # сравнение с ней изолирует ровно дефект приростов.
            rows.append(
                (
                    summary.dates[start].date().isoformat(),
                    well,
                    _number(liquid_mass_end),
                    _number(liquid_rate),
                    _number(oil_mass_end),
                    _number(summary.well_value("WOPR", well, end) * rho_oil),
                    _number(summary.well_value("WWIR", well, end)),
                    _number(injection_end),
                    _number(summary.well_value("WBP9", well, end)),
                    _number(summary.well_value("WBHP", well, end)),
                    WELL_EFFICIENCY,
                    _number(diff_liquid),
                    _number(diff_oil),
                    _number(diff_injection),
                )
            )

    dates = [row[0] for row in rows]
    stats = TrajectoryStats(
        row_count=len(rows),
        well_count=len(summary.wells),
        interval_count=summary.report_steps - 1,
        date_min=min(dates),
        date_max=max(dates),
        economic_rows=sum(1 for day in dates if day >= econ_start),
        pre_start_rows=sum(1 for day in dates if day < econ_start),
        max_wlpr=max_wlpr,
        rows_over_wlpr_limit=over_limit,
        negative_diff_counts=negatives,
    )
    return ChddInput(headers=CHDD_COLUMNS, rows=tuple(rows), stats=stats)


def write_chdd_csv(chdd_input: ChddInput, path: Path) -> Path:
    """Записать вход калькулятора. Перевод строки фиксирован, чтобы файл был воспроизводим."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(chdd_input.headers)
        writer.writerows(chdd_input.rows)
    return path
