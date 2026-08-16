"""Замещение отбора: формула, единицы и режим отказа.

VRR -- инженерная диагностика, и её единственная ценность в том, что она
считается по фактическим пластовым объёмам, а не по поверхностным дебитам
и не по заданным уставкам. Поэтому проверяется в первую очередь то, что
подменить эти величины нельзя.
"""

from __future__ import annotations

import datetime
import math

import pytest

from aios.experiments.vrr import (
    MIN_VOIDAGE_RM3_PER_DAY,
    VrrError,
    WaterPvt,
    cumulative_vrr,
    load_water_pvt,
    parse_pvtw,
    vrr_envelope,
    vrr_series,
)

MODEL_Y_PVT = WaterPvt(
    reference_pressure_bar=141.0,
    formation_volume_factor=1.023,
    compressibility_per_bar=4.35e-5,
    viscosity_cp=0.41,
)


class FakeSummary:
    """Минимальная сводка с управляемыми полевыми векторами."""

    def __init__(self, vectors: dict[str, tuple[float, ...]], start: str = "2014-01-01") -> None:
        self._vectors = vectors
        self.report_steps = len(next(iter(vectors.values())))
        base = datetime.date.fromisoformat(start)
        self.dates = tuple(
            datetime.datetime(base.year, base.month, base.day)  # noqa: DTZ001
            + datetime.timedelta(days=30 * i)
            for i in range(self.report_steps)
        )

    def has_field_vector(self, vector: str) -> bool:
        return vector in self._vectors

    def field_value(self, vector: str, step: int) -> float:
        return self._vectors[vector][step]


def _summary(**overrides) -> FakeSummary:
    vectors = {
        "FOPR": (416.4973, 400.0),
        "FWPR": (2452.6787, 2500.0),
        "FLPR": (2869.1760, 2900.0),
        "FWIR": (3027.5952, 3000.0),
        "FVPR": (2959.4368, 2950.0),
        "FPR": (162.5712, 160.0),
        "FVPT": (100000.0, 188000.0),
        "FWIT": (90000.0, 180000.0),
    }
    vectors.update(overrides)
    return FakeSummary(vectors)


# --- PVT -------------------------------------------------------------------


def test_pvtw_is_parsed_from_the_deck_record() -> None:
    pvt = parse_pvtw(
        "PVTW\n-- pref fvf compressibility viscosity viscosibility\n"
        " 141   1.023   4.35E-005   0.41   /\n"
    )

    assert pvt.reference_pressure_bar == pytest.approx(141.0)
    assert pvt.formation_volume_factor == pytest.approx(1.023)
    assert pvt.compressibility_per_bar == pytest.approx(4.35e-5)
    assert pvt.viscosity_cp == pytest.approx(0.41)


def test_missing_pvtw_fails_closed() -> None:
    with pytest.raises(VrrError, match="no PVTW"):
        parse_pvtw("PVDG\n 1.0 1.08 0.013 /\n")


def test_truncated_pvtw_record_fails_closed() -> None:
    with pytest.raises(VrrError, match="at least 4"):
        parse_pvtw("PVTW\n 141 1.023 /\n")


def test_water_fvf_is_pressure_dependent() -> None:
    at_reference = MODEL_Y_PVT.fvf(141.0)
    at_field = MODEL_Y_PVT.fvf(162.5712)

    assert at_reference == pytest.approx(1.023, abs=1e-12)
    # Выше опорного давления вода сжата, Bw меньше.
    assert at_field < at_reference
    assert at_field == pytest.approx(1.022041, abs=1e-6)


def test_water_fvf_below_reference_expands() -> None:
    assert MODEL_Y_PVT.fvf(120.0) > MODEL_Y_PVT.fvf(141.0)


def test_constant_bw_would_be_a_different_number() -> None:
    """Постоянные 1.02 -- не то же самое, что PVT-значение."""

    assert MODEL_Y_PVT.fvf(162.5712) != pytest.approx(1.02, abs=1e-5)


def test_non_finite_pressure_is_rejected() -> None:
    with pytest.raises(VrrError, match="finite"):
        MODEL_Y_PVT.fvf(math.nan)


def test_pvt_loads_from_the_real_deck_when_available(model_y_case) -> None:
    """Путь к колоде берётся из кейса, а не из машины разработчика."""

    include = model_y_case.history.deck_root / "INCLUDE" / "DemoSpe_002_2_pvt.inc"
    if not include.is_file():
        pytest.skip("Model Y PVT include is not available in this environment")

    pvt = load_water_pvt(include)

    assert pvt.reference_pressure_bar == pytest.approx(141.0)
    assert pvt.formation_volume_factor == pytest.approx(1.023)


# --- формула ---------------------------------------------------------------


def test_vrr_uses_simulated_reservoir_voidage() -> None:
    points = vrr_series(_summary(), MODEL_Y_PVT)

    first = points[0]
    assert first.fvpr == pytest.approx(2959.4368)
    assert first.bw_injection == pytest.approx(1.022041, abs=1e-6)
    exact_bw = MODEL_Y_PVT.fvf(162.5712)
    assert first.reservoir_injection_rm3_per_day == pytest.approx(3027.5952 * exact_bw, rel=1e-12)
    assert first.vrr == pytest.approx(1.04558, abs=1e-5)


def test_vrr_is_not_the_surface_water_ratio() -> None:
    """FWIR/FWPR -- другое число и другой смысл."""

    points = vrr_series(_summary(), MODEL_Y_PVT)

    surface_ratio = 3027.5952 / 2452.6787
    assert surface_ratio > 1.2
    assert points[0].vrr < 1.1
    assert points[0].vrr != pytest.approx(surface_ratio, abs=1e-3)


def test_missing_voidage_vector_fails_closed() -> None:
    summary = _summary()
    del summary._vectors["FVPR"]

    with pytest.raises(VrrError, match="FVPR"):
        vrr_series(summary, MODEL_Y_PVT)


def test_non_positive_denominator_step_is_skipped() -> None:
    points = vrr_series(_summary(FVPR=(0.0, 2950.0)), MODEL_Y_PVT)

    assert len(points) == 1
    assert points[0].fvpr == pytest.approx(2950.0)


def test_all_degenerate_denominators_fail_closed() -> None:
    with pytest.raises(VrrError, match="positive reservoir voidage"):
        vrr_series(_summary(FVPR=(0.0, MIN_VOIDAGE_RM3_PER_DAY / 2)), MODEL_Y_PVT)


def test_zero_injection_gives_zero_vrr_not_an_error() -> None:
    points = vrr_series(_summary(FWIR=(0.0, 0.0)), MODEL_Y_PVT)

    assert points[0].vrr == pytest.approx(0.0)


# --- накопленный против мгновенного ---------------------------------------


def test_cumulative_vrr_is_a_ratio_of_sums() -> None:
    result = cumulative_vrr(_summary(), MODEL_Y_PVT)

    surface = 180000.0 - 90000.0
    pressure = 0.5 * (162.5712 + 160.0)
    expected = surface * MODEL_Y_PVT.fvf(pressure) / (188000.0 - 100000.0)
    assert result["cumulative_vrr"] == pytest.approx(expected, rel=1e-12)
    assert result["injected_surface_sm3"] == pytest.approx(surface)


def test_cumulative_and_mean_instantaneous_are_distinct() -> None:
    """Отношение сумм и среднее отношений -- разные величины."""

    summary = _summary(FVPR=(1000.0, 5000.0), FWIR=(1000.0, 1000.0))
    points = vrr_series(summary, MODEL_Y_PVT)
    mean_instantaneous = sum(p.vrr for p in points) / len(points)
    ratio_of_sums = cumulative_vrr(summary, MODEL_Y_PVT)["cumulative_vrr"]

    assert mean_instantaneous != pytest.approx(ratio_of_sums, rel=1e-3)


def test_cumulative_requires_the_cumulative_vector() -> None:
    summary = _summary()
    del summary._vectors["FVPT"]

    with pytest.raises(VrrError, match="FVPT"):
        cumulative_vrr(summary, MODEL_Y_PVT)


def test_cumulative_rejects_non_positive_voidage() -> None:
    with pytest.raises(VrrError, match="not positive"):
        cumulative_vrr(_summary(FVPT=(100000.0, 100000.0)), MODEL_Y_PVT)


# --- огибающая -------------------------------------------------------------


def test_envelope_reports_distribution_and_runs() -> None:
    summary = FakeSummary(
        {
            "FOPR": (10.0,) * 5,
            "FWPR": (10.0,) * 5,
            "FLPR": (20.0,) * 5,
            "FWIR": (0.0, 1000.0, 1000.0, 1000.0, 0.0),
            "FVPR": (1000.0,) * 5,
            "FPR": (141.0,) * 5,
        }
    )

    envelope = vrr_envelope(vrr_series(summary, MODEL_Y_PVT))

    assert envelope["count"] == 5
    assert envelope["min"] == pytest.approx(0.0)
    assert envelope["max"] == pytest.approx(1.023, abs=1e-9)
    assert envelope["longest_run_above_1"] == 3
    assert envelope["longest_run_below_1"] == 1


def test_window_start_filters_early_history() -> None:
    points = vrr_series(_summary(), MODEL_Y_PVT, window_start="2014-01-31")

    assert len(points) == 1
    assert points[0].date == "2014-01-31"
