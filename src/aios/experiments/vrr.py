"""Замещение отбора пластовой водой (VRR) как инженерная диагностика.

Прежнее сравнение `FWIT <= FWPT` физически неверно и в EXP-004 не
используется. Закачка возмещает **весь** пластовый объём, освобождённый
добычей, а не одну лишь добытую воду: нефть, извлечённая из пласта, тоже
оставляет после себя объём. Поэтому `FWIR > FWPR` сам по себе ничего не
нарушает и признаком «лишней» воды не является.

Правильный показатель -- отношение пластовых объёмов:

    VRR(t) = Q_inj_res(t) / Q_prod_res(t)

    Q_prod_res(t) = FVPR(t)                     [RM3/сут]
    Q_inj_res(t)  = FWIR(t) * Bw(P(t))          [SM3/сут * RM3/SM3]

`FVPR` -- пластовый объём отбора, который считает сам симулятор; он уже
включает нефть, воду и газ. Брать вместо него сумму поверхностных дебитов
значило бы считать физику заново и хуже.

Объёмный коэффициент воды берётся из `PVTW` колоды, а не константой:

    X     = Cw * (P - P_ref)
    Bw(P) = Bw_ref / (1 + X + X^2 / 2)

Это стандартная формулировка ECLIPSE. Для Model Y `P_ref = 141 bar`,
`Bw_ref = 1.023`, `Cw = 4.35e-5 1/bar`, что даёт Bw ≈ 1.0220 при пластовом
давлении 162.6 bar -- заметно отличается от «примерно 1.02», если давление
уходит далеко от опорного, а в агрессивных сценариях оно уходит.

Давление берётся среднее пластовое (`FPR`): VRR здесь -- показатель по
объекту в целом, и объём, который занимает закачанная вода, оценивается в
пластовых условиях объекта.

**Это диагностика, а не ограничение.** Ни VRR <= 1.0, ни VRR <= 1.1 не
являются подтверждённым правилом организатора; официальная трактовка
водного баланса остаётся неопределённой.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aios.opm.summary import SummaryData

PRODUCTION_VOIDAGE_VECTOR = "FVPR"
PRODUCTION_VOIDAGE_CUMULATIVE = "FVPT"
INJECTION_RATE_VECTOR = "FWIR"
INJECTION_CUMULATIVE = "FWIT"
PRESSURE_VECTOR = "FPR"

# Ниже этого пластового отбора отношение теряет смысл: делитель вырождается,
# и «VRR» превращается в шум деления на почти ноль.
MIN_VOIDAGE_RM3_PER_DAY = 1e-6


class VrrError(RuntimeError):
    """VRR посчитать нельзя. Число не выдумывается."""


@dataclass(frozen=True, slots=True)
class WaterPvt:
    """Параметры `PVTW` для одного региона."""

    reference_pressure_bar: float
    formation_volume_factor: float
    compressibility_per_bar: float
    viscosity_cp: float

    def fvf(self, pressure_bar: float) -> float:
        """Bw при заданном давлении по формулировке ECLIPSE."""

        if not math.isfinite(pressure_bar):
            raise VrrError("pressure must be finite to evaluate Bw")
        x = self.compressibility_per_bar * (pressure_bar - self.reference_pressure_bar)
        denominator = 1.0 + x + x * x / 2.0
        if denominator <= 0.0:
            raise VrrError(f"PVTW expansion is non-physical at {pressure_bar} bar")
        return self.formation_volume_factor / denominator

    def to_json(self) -> dict[str, Any]:
        return {
            "reference_pressure_bar": self.reference_pressure_bar,
            "formation_volume_factor": self.formation_volume_factor,
            "compressibility_per_bar": self.compressibility_per_bar,
            "viscosity_cp": self.viscosity_cp,
        }


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def parse_pvtw(text: str) -> WaterPvt:
    """Прочитать первую запись `PVTW`.

    Регионов в колоде два и они совпадают; берётся первый. Если записи нет
    или в ней меньше четырёх чисел -- отказ, а не подстановка «обычных»
    значений: константа вместо PVT молча испортила бы все пересчёты.
    """

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.split("--")[0].strip() != "PVTW":
            continue
        for follow in lines[index + 1 :]:
            payload = follow.split("--")[0].strip()
            if not payload:
                continue
            numbers = [float(m.group()) for m in _NUMBER.finditer(payload)]
            if len(numbers) < 4:
                raise VrrError(f"PVTW record has {len(numbers)} numbers, need at least 4")
            return WaterPvt(
                reference_pressure_bar=numbers[0],
                formation_volume_factor=numbers[1],
                compressibility_per_bar=numbers[2],
                viscosity_cp=numbers[3],
            )
    raise VrrError("deck has no PVTW record")


def load_water_pvt(pvt_include: Path) -> WaterPvt:
    try:
        return parse_pvtw(pvt_include.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VrrError(f"cannot read PVT include {pvt_include}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class VrrPoint:
    """Мгновенный VRR на одном отчётном шаге."""

    date: str
    fopr: float
    fwpr: float
    flpr: float
    fwir: float
    fvpr: float
    fpr: float
    bw_injection: float
    reservoir_injection_rm3_per_day: float
    vrr: float

    def to_json(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "FOPR": self.fopr,
            "FWPR": self.fwpr,
            "FLPR": self.flpr,
            "FWIR": self.fwir,
            "FVPR": self.fvpr,
            "FPR": self.fpr,
            "Bw_inj": self.bw_injection,
            "reservoir_injection_rate_rm3_per_day": self.reservoir_injection_rm3_per_day,
            "VRR": self.vrr,
        }


def _require_vectors(summary: SummaryData) -> None:
    missing = [
        vector
        for vector in (
            PRODUCTION_VOIDAGE_VECTOR,
            INJECTION_RATE_VECTOR,
            PRESSURE_VECTOR,
            "FOPR",
            "FWPR",
            "FLPR",
        )
        if not summary.has_field_vector(vector)
    ]
    if missing:
        raise VrrError(
            f"summary lacks required field vector(s): {', '.join(missing)}; "
            f"VRR cannot be computed without simulated reservoir voidage"
        )


def vrr_series(
    summary: SummaryData, pvt: WaterPvt, *, window_start: str | None = None
) -> list[VrrPoint]:
    """Мгновенный VRR по отчётным шагам.

    Шаги с вырожденным пластовым отбором пропускаются: отношение к почти
    нулю не является измерением.
    """

    _require_vectors(summary)
    dates = [moment.date().isoformat() for moment in summary.dates]
    points: list[VrrPoint] = []
    for index, day in enumerate(dates):
        if window_start is not None and day < window_start:
            continue
        voidage = summary.field_value(PRODUCTION_VOIDAGE_VECTOR, index)
        if not math.isfinite(voidage) or voidage <= MIN_VOIDAGE_RM3_PER_DAY:
            continue
        pressure = summary.field_value(PRESSURE_VECTOR, index)
        injection = summary.field_value(INJECTION_RATE_VECTOR, index)
        bw = pvt.fvf(pressure)
        reservoir_injection = injection * bw
        points.append(
            VrrPoint(
                date=day,
                fopr=summary.field_value("FOPR", index),
                fwpr=summary.field_value("FWPR", index),
                flpr=summary.field_value("FLPR", index),
                fwir=injection,
                fvpr=voidage,
                fpr=pressure,
                bw_injection=bw,
                reservoir_injection_rm3_per_day=reservoir_injection,
                vrr=reservoir_injection / voidage,
            )
        )
    if not points:
        raise VrrError("no report step has positive reservoir voidage")
    return points


def cumulative_vrr(
    summary: SummaryData, pvt: WaterPvt, *, window_start: str | None = None
) -> dict[str, float]:
    """Отношение накопленных пластовых объёмов за окно.

    Это НЕ то же самое, что среднее мгновенных VRR: отношение сумм и сумма
    отношений совпадают только при постоянном знаменателе. Обе величины
    считаются и печатаются раздельно, чтобы их нельзя было перепутать.

    Накопленная закачка в пластовых условиях считается по шагам с Bw того
    шага, а не одним множителем на весь период: при заметном изменении
    давления единый множитель даёт систематическую ошибку.
    """

    _require_vectors(summary)
    if not summary.has_field_vector(PRODUCTION_VOIDAGE_CUMULATIVE):
        raise VrrError(f"summary lacks {PRODUCTION_VOIDAGE_CUMULATIVE}")
    dates = [moment.date().isoformat() for moment in summary.dates]
    start = 0
    if window_start is not None:
        start = next((i for i, day in enumerate(dates) if day >= window_start), len(dates))
    last = summary.report_steps - 1
    if start >= last:
        raise VrrError("window contains no complete interval")

    injected_reservoir = 0.0
    injected_surface = 0.0
    for index in range(start, last):
        surface = summary.field_value(INJECTION_CUMULATIVE, index + 1) - summary.field_value(
            INJECTION_CUMULATIVE, index
        )
        pressure = 0.5 * (
            summary.field_value(PRESSURE_VECTOR, index)
            + summary.field_value(PRESSURE_VECTOR, index + 1)
        )
        injected_surface += surface
        injected_reservoir += surface * pvt.fvf(pressure)

    produced_reservoir = summary.field_value(
        PRODUCTION_VOIDAGE_CUMULATIVE, last
    ) - summary.field_value(PRODUCTION_VOIDAGE_CUMULATIVE, start)
    if produced_reservoir <= MIN_VOIDAGE_RM3_PER_DAY:
        raise VrrError("cumulative reservoir voidage is not positive")
    return {
        "window_start": dates[start],
        "window_end": dates[last],
        "injected_surface_sm3": injected_surface,
        "injected_reservoir_rm3": injected_reservoir,
        "produced_reservoir_rm3": produced_reservoir,
        "cumulative_vrr": injected_reservoir / produced_reservoir,
    }


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise VrrError("no values to summarise")
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _longest_run(values: Sequence[float], predicate) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if predicate(value) else 0
        best = max(best, current)
    return best


def vrr_envelope(points: Sequence[VrrPoint]) -> dict[str, Any]:
    """Распределение мгновенного VRR. Диагностика, не норматив."""

    values = [point.vrr for point in points]
    if not values:
        raise VrrError("no VRR points to summarise")
    return {
        "count": len(values),
        "min": min(values),
        "p05": _percentile(values, 0.05),
        "p25": _percentile(values, 0.25),
        "median": _percentile(values, 0.50),
        "p75": _percentile(values, 0.75),
        "p95": _percentile(values, 0.95),
        "max": max(values),
        "mean": statistics.fmean(values),
        "longest_run_above_1": _longest_run(values, lambda v: v > 1.0),
        "longest_run_below_1": _longest_run(values, lambda v: v < 1.0),
        "first_date": points[0].date,
        "last_date": points[-1].date,
    }


def write_vrr_csv(points: Sequence[VrrPoint], path: Path) -> Path:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(points[0].to_json())
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for point in points:
            writer.writerow(point.to_json())
    temp.replace(path)
    return path
