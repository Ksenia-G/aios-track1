"""Приросты интервала относятся к отчётному шагу, на котором они сообщены.

Строка входа ЧДД описывает интервал [шаг i, шаг i+1]. Все значения состояния в
ней уже берутся на КОНЦЕ интервала, а метка даты ставилась на его НАЧАЛЕ --
метка и значения расходились на один шаг. Официальная методика относит добычу
к отчётному шагу, на котором она сообщена, поэтому годовые корзины у нас
съезжали: дисконтирование годовое, и сдвиг через границу года меняет ЧДД.

Проверено на неизменённых артефактах базового прогона: при отнесении к концу
интервала годовая добыча 2008-2015 совпадает с официальной, при отнесении к
началу -- расходится до +26.7% в 2007 и -8.9% в 2015.
"""

from __future__ import annotations

import datetime

import pytest

from aios.chdd.trajectory import build_chdd_input
from aios.opm.summary import SummaryData

#: Официальные годовые значения нефти OPM, тыс. т.
ORGANIZER_OIL_KT = {
    2007: 57.29563842912,
    2008: 194.6714316338688,
    2009: 237.6067760768,
    2010: 216.7150518208,
    2011: 181.613564704,
    2012: 151.4483084463232,
    2013: 134.343604635136,
    2014: 120.095914525184,
    2015: 104.341651105536,
}


def _t(y, m, d=1):
    return datetime.datetime(y, m, d)  # noqa: DTZ001 -- модельное время OPM


def _summary(dates, per_well):
    """Синтетическая сводка: per_well[well][vector] -- значения по шагам."""
    wells = tuple(per_well)
    vectors = sorted({v for w in per_well.values() for v in w})
    well_index = {}
    params = []
    col = 0
    for vec in vectors:
        for well in wells:
            well_index[(vec, well)] = col
            col += 1
    for step in range(len(dates)):
        row = []
        for vec in vectors:
            for well in wells:
                row.append(per_well[well].get(vec, [0.0] * len(dates))[step])
        params.append(tuple(row))
    return SummaryData(
        start=dates[0],
        dates=tuple(dates),
        wells=wells,
        units={},
        _field_index={},
        _well_index=well_index,
        _params=tuple(params),
    )


def _two_step_summary():
    """Один интервал: декабрь 2014 -> январь 2015."""
    dates = [_t(2014, 12), _t(2015, 1)]
    per_well = {
        "P1": {
            "WOPT": [1000.0, 1100.0],
            "WOPR": [10.0, 12.0],
            "WWPT": [500.0, 560.0],
            "WLPR": [40.0, 44.0],
            "WWIR": [0.0, 0.0],
            "WWIT": [0.0, 0.0],
            "WBHP": [150.0, 152.0],
            "WBP9": [160.0, 162.0],
        }
    }
    return _summary(dates, per_well)


def _rows(case, summary):
    result = build_chdd_input(summary, case)
    return [dict(zip(result.headers, r, strict=True)) for r in result.rows]


# ===========================================================================
# S1/S2/S3 -- метка интервала
# ===========================================================================


def test_interval_is_stamped_with_the_end_report_date(mini_case) -> None:
    rows = _rows(mini_case, _two_step_summary())
    assert len(rows) == 1
    assert rows[0]["DATA"] == "2015-01-01", (
        "an interval must be attributed to the report step that reports it"
    )


def test_december_interval_belongs_to_the_next_year(mini_case) -> None:
    """S2: граница года -- там, где годовое дисконтирование и кусается."""
    rows = _rows(mini_case, _two_step_summary())
    assert rows[0]["DATA"][:4] == "2015"


def test_interval_crossing_a_month_boundary_belongs_to_the_ending_month(mini_case) -> None:
    dates = [_t(2015, 3), _t(2015, 4)]
    per_well = {
        "P1": {
            "WOPT": [0.0, 100.0],
            "WOPR": [0.0, 5.0],
            "WWPT": [0.0, 50.0],
            "WLPR": [0.0, 20.0],
            "WWIR": [0.0, 0.0],
            "WWIT": [0.0, 0.0],
            "WBHP": [0.0, 150.0],
            "WBP9": [0.0, 160.0],
        }
    }
    rows = _rows(mini_case, _summary(dates, per_well))
    assert rows[0]["DATA"][:7] == "2015-04"


# ===========================================================================
# S4 -- приросты не меняются
# ===========================================================================


def test_interval_increments_are_unchanged_by_the_stamp(mini_case) -> None:
    rho_o = mini_case.economics.oil_density_t_per_sm3
    rho_w = mini_case.economics.water_density_t_per_sm3
    rows = _rows(mini_case, _two_step_summary())
    assert float(rows[0]["WOMT_Diff"]) == pytest.approx(100.0 * rho_o)
    expected_liquid = (1100.0 * rho_o + 560.0 * rho_w) - (1000.0 * rho_o + 500.0 * rho_w)
    assert float(rows[0]["WLPT_Diff"]) == pytest.approx(expected_liquid)
    assert float(rows[0]["WWIT_Diff"]) == pytest.approx(0.0)


# ===========================================================================
# S5 -- мгновенные величины по-прежнему берутся на конце интервала
# ===========================================================================


def test_instantaneous_fields_still_come_from_the_interval_end(mini_case) -> None:
    rows = _rows(mini_case, _two_step_summary())
    assert float(rows[0]["WLPR"]) == pytest.approx(44.0)
    assert float(rows[0]["BHP"]) == pytest.approx(152.0)
    assert float(rows[0]["THP"]) == pytest.approx(162.0)
    assert float(rows[0]["WWIR"]) == pytest.approx(0.0)
    assert rows[0]["WEFF"] == "1"


# ===========================================================================
# S6 -- несколько интервалов внутри одного месяца остаются законными
# ===========================================================================


def test_multiple_intervals_inside_one_month_are_kept(mini_case) -> None:
    dates = [_t(2015, 5, 2), _t(2015, 5, 5), _t(2015, 5, 14), _t(2015, 6, 1)]
    n = len(dates)
    per_well = {
        "P1": {
            "WOPT": [0.0, 10.0, 20.0, 40.0],
            "WOPR": [0.0] * n,
            "WWPT": [0.0] * n,
            "WLPR": [10.0] * n,
            "WWIR": [0.0] * n,
            "WWIT": [0.0] * n,
            "WBHP": [150.0] * n,
            "WBP9": [160.0] * n,
        }
    }
    rows = _rows(mini_case, _summary(dates, per_well))
    assert len(rows) == 3, "one row per interval; no month aggregation"
    assert [r["DATA"] for r in rows] == ["2015-05-05", "2015-05-14", "2015-06-01"]


# ===========================================================================
# S10 -- суммарные объёмы не меняются
# ===========================================================================


def test_total_volumes_are_unchanged_by_the_stamp(mini_case) -> None:
    dates = [_t(2015, 1), _t(2015, 2), _t(2015, 3)]
    per_well = {
        "P1": {
            "WOPT": [0.0, 100.0, 250.0],
            "WOPR": [0.0, 5.0, 6.0],
            "WWPT": [0.0, 50.0, 90.0],
            "WLPR": [20.0, 21.0, 22.0],
            "WWIR": [0.0, 0.0, 0.0],
            "WWIT": [0.0, 0.0, 0.0],
            "WBHP": [150.0] * 3,
            "WBP9": [160.0] * 3,
        }
    }
    rows = _rows(mini_case, _summary(dates, per_well))
    rho_o = mini_case.economics.oil_density_t_per_sm3
    assert sum(float(r["WOMT_Diff"]) for r in rows) == pytest.approx(250.0 * rho_o)


# ===========================================================================
# S7/S8/S9 -- сверка с официальными годовыми объёмами на реальном прогоне
# ===========================================================================
#
# Внешний артефакт: сводка OPM базового прогона. В репозиторий она не кладётся,
# как и сама колода. Без неё тест пропускается -- это заявленное поведение для
# отсутствующего внешнего входа, а не способ спрятать расхождение.


@pytest.fixture
def baseline_annual_oil_kt(model_y_case):
    """Годовая добыча нефти базового прогона, тыс. т, по меткам строк ЧДД."""
    import collections
    import os
    from pathlib import Path

    from aios.opm.summary import read_summary

    root = os.environ.get("AIOS_BASELINE_SUMMARY_DIR")
    if not root:
        pytest.skip("AIOS_BASELINE_SUMMARY_DIR is not configured")
    out = Path(root)
    smspec, unsmry = out / "MODEL_Y.SMSPEC", out / "MODEL_Y.UNSMRY"
    if not (smspec.is_file() and unsmry.is_file()):
        pytest.skip(f"baseline OPM summary is not available under {out}")

    result = build_chdd_input(read_summary(smspec, unsmry), model_y_case)
    date_col = result.headers.index("DATA")
    oil_col = result.headers.index("WOMT_Diff")
    per_year: collections.Counter[int] = collections.Counter()
    for row in result.rows:
        per_year[int(row[date_col][:4])] += float(row[oil_col])
    return {year: mass / 1000.0 for year, mass in per_year.items()}


@pytest.mark.parametrize("year", sorted(y for y in ORGANIZER_OIL_KT if y >= 2008))
def test_annual_oil_matches_the_official_figures(baseline_annual_oil_kt, year) -> None:
    """S7: полные годы обязаны сойтись с официальными, а не примерно сойтись."""
    official = ORGANIZER_OIL_KT[year]
    assert baseline_annual_oil_kt[year] == pytest.approx(official, rel=1e-6)


def test_first_year_keeps_its_known_partial_interval_residual(baseline_annual_oil_kt) -> None:
    """S7: 2007 не сходится, и это ИЗВЕСТНЫЙ остаток, а не свобода допуска.

    Первый отчётный шаг сводки -- 2007-05-02, а добыча за интервал от старта
    модели до него ни одной строкой не сообщается: интервалов на шаг меньше,
    чем шагов. Официальный расчёт этот кусок учитывает. Здесь он фиксируется
    как остаток и НЕ подгоняется: закрывать его -- отдельная работа.
    """
    official = ORGANIZER_OIL_KT[2007]
    ours = baseline_annual_oil_kt[2007]
    relative = (ours - official) / official
    assert -0.0011 < relative < -0.0009, f"2007 residual moved: {relative:+.6%}"

    # Остаток целиком объясняется 2007 годом. Порог абсолютный: сводка OPM
    # хранит кумулятивы в float32, и сумма 5243 строк накапливает единицы
    # грамм. Относительный допуск здесь врал бы о точности входных данных.
    total_shortfall = sum(ORGANIZER_OIL_KT.values()) - sum(
        baseline_annual_oil_kt[y] for y in ORGANIZER_OIL_KT
    )
    outside_2007 = abs(total_shortfall - (official - ours))
    assert outside_2007 < 1e-4, f"other years lose {outside_2007:.3e} kt"


def test_2014_annual_oil_is_pinned(baseline_annual_oil_kt) -> None:
    """S8."""
    assert baseline_annual_oil_kt[2014] == pytest.approx(120.095914525184, rel=1e-6)


def test_2015_annual_oil_is_pinned(baseline_annual_oil_kt) -> None:
    """S9."""
    assert baseline_annual_oil_kt[2015] == pytest.approx(104.341651105536, rel=1e-6)


# ===========================================================================
# S11/S12 -- посторонние контракты не тронуты
# ===========================================================================


def _porcelain(*paths: str) -> str:
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_official_calculator_is_the_version_we_are_reconciling_against() -> None:
    """S11: мы сверяемся с ЧУЖИМ калькулятором и не правим его.

    Хеш здесь -- не украшение: расхождение годовых объёмов имеет смысл
    только против зафиксированной версии официального расчёта.
    """
    import hashlib
    import os
    from pathlib import Path

    root = os.environ.get("CHDD_ROOT")
    if not root:
        pytest.skip("CHDD_ROOT is not configured")
    model = Path(root) / "chdd_model.py"
    if not model.is_file():
        pytest.skip(f"official calculator is not available at {model}")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    assert digest == "7d52f292e61b4821d00e3e0a34acf43ad9dac6817680001f3ebaea70f45ed61e", (
        "the official CHDD calculator changed; annual reconciliation must be redone"
    )


def test_the_chdd_and_setpoint_authority_boundary_is_not_touched_by_this_change() -> None:
    """S12: CHDD math and NO_SETPOINTS_CROSS authority stay unchanged."""
    assert (
        _porcelain(
            "src/aios/mas/specialists/authority.py",
            "src/aios/chdd/runner.py",
        )
        == ""
    ), "this change may add post-simulation legality but must not change CHDD or setpoint authority"
