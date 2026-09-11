"""Сигнатура модели: похоже ли это на колоду нового кейса.

Диагностика, а не ограничение. Организатор называет состояние конца 2011 года
примерно 5425 м3/сут жидкости и 5550 м3/сут закачки; унаследованная колода
Model Y даёт совсем другие величины, и считать на ней экономику нового кейса
бессмысленно. Сигнатура говорит об этом вслух и ничего не запрещает: ни одна
траектория по ней не отвергается и ни один тест на синтетических фикстурах не
падает.
"""

from __future__ import annotations

import pytest

from aios.newcase.signature import (
    REFERENCE_INJECTION_M3_DAY,
    REFERENCE_LIQUID_M3_DAY,
    SIGNATURE_TOLERANCE,
    ModelSignature,
    model_signature,
)


def test_reference_values_are_the_ones_the_organizer_stated() -> None:
    assert REFERENCE_LIQUID_M3_DAY == 5425.0
    assert REFERENCE_INJECTION_M3_DAY == 5550.0


def test_a_deck_at_the_reference_state_matches() -> None:
    result = model_signature(liquid=5425.0, injection=5550.0)

    assert result.verdict is ModelSignature.MATCH


def test_small_deviation_inside_the_tolerance_still_matches() -> None:
    result = model_signature(
        liquid=REFERENCE_LIQUID_M3_DAY * (1 + SIGNATURE_TOLERANCE / 2),
        injection=REFERENCE_INJECTION_M3_DAY,
    )

    assert result.verdict is ModelSignature.MATCH


def test_the_legacy_model_state_is_reported_as_a_mismatch() -> None:
    """Унаследованная Model Y: 3260 / 2740 -- это не колода нового кейса."""
    result = model_signature(liquid=3260.0, injection=2740.0)

    assert result.verdict is ModelSignature.MISMATCH
    assert result.liquid_ratio == pytest.approx(3260.0 / 5425.0)


def test_missing_measurements_are_unavailable_not_a_mismatch() -> None:
    assert model_signature(liquid=None, injection=None).verdict is ModelSignature.UNAVAILABLE
    assert model_signature(liquid=5425.0, injection=None).verdict is ModelSignature.UNAVAILABLE


def test_the_signature_never_raises_and_never_rejects() -> None:
    """Диагностика не может уронить прогон: она только сообщает."""
    for liquid, injection in ((0.0, 0.0), (-1.0, 1e9), (None, 5550.0)):
        result = model_signature(liquid=liquid, injection=injection)
        assert isinstance(result.verdict, ModelSignature)
        assert isinstance(result.to_json(), dict)


def test_the_report_names_both_measured_values_and_the_reference() -> None:
    payload = model_signature(liquid=3260.0, injection=2740.0).to_json()

    assert payload["measured"] == {"liquid": 3260.0, "injection": 2740.0}
    assert payload["reference"] == {"liquid": 5425.0, "injection": 5550.0}
    assert payload["verdict"] == "MISMATCH"
    assert "tolerance" in payload
