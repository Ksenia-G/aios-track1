"""Физические показатели прогона и их разности с контролем.

Экономика отвечает на вопрос «сколько», физика -- на вопрос «откуда».
Здесь снимаются ровно те ряды, по которым потом можно сказать, где нефть
прибавилась, где убавилась и как отозвалось давление, не выходя за пределы
того, что реально измерено в этом прогоне.
"""

from dataclasses import dataclass
from typing import Any

from aios.opm.summary import SummaryData

FIELD_VECTORS: tuple[str, ...] = ("FOPR", "FWPR", "FWIR", "FPR", "FOPT", "FWPT", "FWIT", "FLPT")
FIELD_SERIES_VECTORS: tuple[str, ...] = ("FOPR", "FWPR", "FWIR", "FPR")
WELL_SERIES_VECTORS: tuple[str, ...] = ("WOPR", "WWPR", "WLPR", "WWCT", "WBHP")
WELL_CUMULATIVE_VECTORS: tuple[str, ...] = ("WOPT", "WWPT")


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """Снятые с прогона ряды и итоги."""

    dates: tuple[str, ...]
    window_from: int
    field_series: dict[str, tuple[float, ...]]
    field_final: dict[str, float]
    well_series: dict[str, dict[str, tuple[float, ...]]]
    well_cumulative: dict[str, dict[str, float]]

    def to_json(self) -> dict[str, Any]:
        return {
            "dates": list(self.dates),
            "window_from_index": self.window_from,
            "field_series": {k: list(v) for k, v in self.field_series.items()},
            "field_final": dict(self.field_final),
            "well_series": {
                well: {k: list(v) for k, v in vectors.items()}
                for well, vectors in self.well_series.items()
            },
            "well_cumulative": {
                well: dict(values) for well, values in self.well_cumulative.items()
            },
        }


def collect_metrics(
    summary: SummaryData, *, wells: tuple[str, ...], window_start: str
) -> RunMetrics:
    """Снять ряды поля и выбранных скважин."""

    dates = tuple(moment.date().isoformat() for moment in summary.dates)
    window_from = next(
        (index for index, day in enumerate(dates) if day >= window_start), len(dates)
    )

    field_series = {
        vector: summary.field_series(vector)
        for vector in FIELD_SERIES_VECTORS
        if summary.has_field_vector(vector)
    }
    field_final = {
        vector: summary.field_value(vector, summary.report_steps - 1)
        for vector in FIELD_VECTORS
        if summary.has_field_vector(vector)
    }

    well_series: dict[str, dict[str, tuple[float, ...]]] = {}
    well_cumulative: dict[str, dict[str, float]] = {}
    last = summary.report_steps - 1
    for well in wells:
        series = {
            vector: summary.well_series(vector, well)
            for vector in WELL_SERIES_VECTORS
            if summary.has_well_vector(vector, well)
        }
        if series:
            well_series[well] = series
        cumulative = {
            vector: summary.well_value(vector, well, last)
            for vector in WELL_CUMULATIVE_VECTORS
            if summary.has_well_vector(vector, well)
        }
        if cumulative:
            well_cumulative[well] = cumulative
    return RunMetrics(
        dates=dates,
        window_from=window_from,
        field_series=field_series,
        field_final=field_final,
        well_series=well_series,
        well_cumulative=well_cumulative,
    )


def _series_delta(candidate: tuple[float, ...], baseline: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(c - b for c, b in zip(candidate, baseline, strict=True))


def _stats(values: tuple[float, ...]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "final": 0.0}
    return {
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "final": values[-1],
    }


def compare_metrics(candidate: RunMetrics, baseline: RunMetrics) -> dict[str, Any]:
    """Разности кандидата и контроля по полю и по скважинам.

    Ряды сравниваются только на окне управления: до его начала обе колоды
    идентичны, и разность там обязана быть нулевой -- это же и проверяется.
    """

    if candidate.dates != baseline.dates:
        raise ValueError("candidate and baseline report dates differ")
    start = candidate.window_from

    field_delta: dict[str, Any] = {}
    for vector, series in candidate.field_series.items():
        if vector not in baseline.field_series:
            continue
        delta = _series_delta(series, baseline.field_series[vector])
        field_delta[vector] = {
            "window": _stats(delta[start:]),
            "pre_window_max_abs": max((abs(v) for v in delta[:start]), default=0.0),
            "series": list(delta),
        }

    field_final_delta = {
        vector: value - baseline.field_final[vector]
        for vector, value in candidate.field_final.items()
        if vector in baseline.field_final
    }

    well_delta: dict[str, Any] = {}
    for well, vectors in candidate.well_series.items():
        base_vectors = baseline.well_series.get(well)
        if base_vectors is None:
            continue
        entry: dict[str, Any] = {}
        for vector, series in vectors.items():
            if vector not in base_vectors:
                continue
            delta = _series_delta(series, base_vectors[vector])
            entry[vector] = {"window": _stats(delta[start:]), "series": list(delta)}
        base_cumulative = baseline.well_cumulative.get(well, {})
        for vector, value in candidate.well_cumulative.get(well, {}).items():
            if vector in base_cumulative:
                entry[f"{vector}_delta"] = value - base_cumulative[vector]
        well_delta[well] = entry

    return {
        "field": field_delta,
        "field_final_delta": field_final_delta,
        "wells": well_delta,
    }
