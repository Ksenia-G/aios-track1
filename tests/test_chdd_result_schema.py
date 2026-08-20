"""Строгий разбор результата официального калькулятора.

Проверяется одно свойство: испорченный результат обязан стать отказом, а не
нулём. Заявленный NPV -- это ответ калькулятора, и если ответа нет, у нас
нет и заявки.
"""

import math
from typing import Any

import pytest

from aios.chdd import ChddResultSchemaError, parse_chdd_result

PROVENANCE = "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"


def valid_result() -> dict[str, Any]:
    """Минимальный результат той же формы, что отдаёт настоящий пакет."""

    return {
        "version": "7.0.2-negative-row-filter",
        "startDate": "2014-01-01",
        "summary": {
            "totalChddM": 1030.8776958837277,
            "profitabilityIndex": 1.2188373984248921,
            "totalOilKt": 213.69528122680654,
            "totalLiquidKt": 1976.6091847774,
            "totalInjectionK": 1962.9611875,
            "pumpChanges": 1.0,
            "conversionCount": 0,
        },
        "diagnostics": {
            "excludedNegativeRows": 0,
            "duplicateWellMonths": 196,
            "invalidDates": 0,
            "ignoredBlankWells": 0,
            "sourceMaxDate": "2015-11-01",
            "warnings": ["дубли «скважина-месяц»"],
        },
        "annual": [
            {
                "year": 2014,
                "months": 12,
                "activeWellMonths": 588.0,
                "stopCount": 0,
                "startCount": 0,
                "chddM": 600.0,
                "cumulativeChddM": 600.0,
            },
            {
                "year": 2015,
                "months": 11,
                "activeWellMonths": 539.0,
                "stopCount": 0,
                "startCount": 0,
                "chddM": 430.8776958837277,
                "cumulativeChddM": 1030.8776958837277,
            },
        ],
        "fieldMonthly": [{"month": "2014-01"}, {"month": "2015-11"}],
    }


def test_wellformed_result_parses() -> None:
    result = parse_chdd_result(valid_result(), provenance=PROVENANCE)

    assert result.npv_mln_rub == pytest.approx(1030.8776958837277)
    assert result.calculator_version == "7.0.2-negative-row-filter"
    assert result.months_by_year == {"2014": 12, "2015": 11}
    assert result.active_well_months == 1127
    assert result.pump_events == 1
    assert result.economic_month_count == 2
    assert result.last_economic_month == "2015-11"
    assert result.provenance == PROVENANCE


def test_missing_total_chdd_is_a_hard_failure_not_zero() -> None:
    payload = valid_result()
    del payload["summary"]["totalChddM"]

    with pytest.raises(ChddResultSchemaError, match="summary.totalChddM"):
        parse_chdd_result(payload, provenance=PROVENANCE)


@pytest.mark.parametrize("bad", ["1030.88", None, True, [1030.88], {"value": 1}])
def test_nonnumeric_total_chdd_is_rejected(bad: Any) -> None:
    payload = valid_result()
    payload["summary"]["totalChddM"] = bad

    with pytest.raises(ChddResultSchemaError, match="summary.totalChddM"):
        parse_chdd_result(payload, provenance=PROVENANCE)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nonfinite_total_chdd_is_rejected(bad: float) -> None:
    payload = valid_result()
    payload["summary"]["totalChddM"] = bad

    with pytest.raises(ChddResultSchemaError, match="must be finite"):
        parse_chdd_result(payload, provenance=PROVENANCE)


@pytest.mark.parametrize("section", ["summary", "diagnostics"])
def test_missing_required_section_is_rejected(section: str) -> None:
    payload = valid_result()
    del payload[section]

    with pytest.raises(ChddResultSchemaError, match=f"result.{section}"):
        parse_chdd_result(payload, provenance=PROVENANCE)


@pytest.mark.parametrize("section", ["summary", "diagnostics"])
def test_required_section_of_wrong_type_is_rejected(section: str) -> None:
    payload = valid_result()
    payload[section] = ["not", "an", "object"]

    with pytest.raises(ChddResultSchemaError, match="must be an object"):
        parse_chdd_result(payload, provenance=PROVENANCE)


@pytest.mark.parametrize("section", ["annual", "fieldMonthly"])
def test_required_list_must_be_a_list(section: str) -> None:
    payload = valid_result()
    payload[section] = {"year": 2014}

    with pytest.raises(ChddResultSchemaError, match="must be a list"):
        parse_chdd_result(payload, provenance=PROVENANCE)


def test_malformed_annual_entry_is_rejected() -> None:
    payload = valid_result()
    payload["annual"][1] = {"year": 2015, "months": 11}

    with pytest.raises(ChddResultSchemaError, match=r"annual\[1\].activeWellMonths"):
        parse_chdd_result(payload, provenance=PROVENANCE)


def test_non_object_annual_entry_is_rejected() -> None:
    payload = valid_result()
    payload["annual"][0] = [2014, 12]

    with pytest.raises(ChddResultSchemaError, match=r"annual\[0\] must be an object"):
        parse_chdd_result(payload, provenance=PROVENANCE)


def test_fractional_counter_is_rejected() -> None:
    payload = valid_result()
    payload["annual"][0]["activeWellMonths"] = 588.5

    with pytest.raises(ChddResultSchemaError, match="whole number"):
        parse_chdd_result(payload, provenance=PROVENANCE)


def test_missing_calculator_version_is_rejected() -> None:
    payload = valid_result()
    del payload["version"]

    with pytest.raises(ChddResultSchemaError, match="result.version"):
        parse_chdd_result(payload, provenance=PROVENANCE)


def test_missing_diagnostics_counter_is_rejected() -> None:
    payload = valid_result()
    del payload["diagnostics"]["excludedNegativeRows"]

    with pytest.raises(ChddResultSchemaError, match="excludedNegativeRows"):
        parse_chdd_result(payload, provenance=PROVENANCE)


def test_missing_warnings_list_is_rejected() -> None:
    payload = valid_result()
    del payload["diagnostics"]["warnings"]

    with pytest.raises(ChddResultSchemaError, match="warnings"):
        parse_chdd_result(payload, provenance=PROVENANCE)


def test_top_level_must_be_an_object() -> None:
    with pytest.raises(ChddResultSchemaError, match="top level"):
        parse_chdd_result([1, 2, 3], provenance=PROVENANCE)


def test_source_max_date_stays_optional() -> None:
    payload = valid_result()
    del payload["diagnostics"]["sourceMaxDate"]

    assert parse_chdd_result(payload, provenance=PROVENANCE).source_max_date is None


def test_empty_field_monthly_yields_no_last_month() -> None:
    payload = valid_result()
    payload["fieldMonthly"] = []

    result = parse_chdd_result(payload, provenance=PROVENANCE)

    assert result.economic_month_count == 0
    assert result.last_economic_month is None
