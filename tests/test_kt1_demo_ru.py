"""Русификация демонстрации КТ1.

Проверяется разделение двух слоёв. На экран выходит русский текст; в снимке
и в контрактах кода остаются машинные значения. Смешение этих слоёв ломает
обе стороны сразу: переведённый enum перестаёт годиться для сверки, а
непереведённый заголовок -- для доклада.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aios.demo import build_snapshot, serialize, snapshot_path
from aios.demo.cli import (
    MODE_LABELS,
    PIPELINE_LABELS,
    STATUS_LABELS,
    constraint_label,
    render,
    status_label,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

BASELINE_NPV = 1030.8776958837277
EXP001_BEST_NPV = 1051.6400268038235
EXP002_BEST_NPV = 1166.9843692054437
EXP003_BEST_NPV = 1190.5933863822445
BEST_SCHEDULE_SHA = "b53dc873efadcc1f33de49d755f1c97ec8e0992b5d0685bed929e46b1eb58112"

# Латиница, допустимая в пользовательском выводе: технические имена,
# идентификаторы и единицы.
ALLOWED_LATIN = (
    "AIOS TRACK 1",
    "OPM Flow",
    "OPM FLOW",
    "wells_schedule.inc",
    "tNavigator",
    "SHA",
    "WBHP",
    "BHP",
    "OPEX",
    "CAPEX",
    "EXP-001",
    "EXP-002",
    "EXP-003",
    "bar",
    "experiments/",
    "EXP001",
    "EXP002",
    "EXP003",
    "NOT_OFFICIAL_PENDING_TNAV_REFERENCE",
    "ORGANIZER_RULE_UNRESOLVED",
    "ЭЦН",
)


@pytest.fixture
def snapshot():
    return build_snapshot(REPO_ROOT)


@pytest.fixture
def text(snapshot) -> str:
    return render(snapshot)


# --- русский пользовательский слой -----------------------------------------


def test_header_is_russian(text: str) -> None:
    assert "AIOS TRACK 1" in text
    assert "ПРОВЕРЕННЫЕ РЕЗУЛЬТАТЫ ВЫЧИСЛИТЕЛЬНЫХ ЭКСПЕРИМЕНТОВ" in text
    assert "Источник:" in text
    assert "ранее выполненные полные расчёты OPM Flow + ЧДД" in text


def test_pipeline_labels_are_russian(text: str) -> None:
    for label in (
        "РАСЧЁТНЫЙ КОНТУР",
        "КЕЙС",
        "ИСТОРИЯ + ОГРАНИЧЕНИЯ",
        "ПЛАН УПРАВЛЕНИЯ",
        "ТРАЕКТОРИЯ СОСТОЯНИЯ",
        "РАСЧЁТ ЧДД",
        "ПРОВЕРКА ОГРАНИЧЕНИЙ",
        "ПРОВЕРЕННЫЙ РЕЗУЛЬТАТ",
    ):
        assert label in text


def test_technical_names_are_not_translated(text: str) -> None:
    assert "wells_schedule.inc" in text
    assert "OPM FLOW" in text
    for wrong in ("СКВАЖИННОЕ_РАСПИСАНИЕ", "ОПМ ПОТОК", "ОПМ ФЛОУ"):
        assert wrong not in text


def test_progression_table_headers_are_russian(text: str) -> None:
    for label in ("ИЗМЕРЕННЫЙ ПРОГРЕСС", "Этап", "Что проверяли", "Расчётов"):
        assert label in text
    assert "ЧДД, млн ₽" in text
    assert "Прирост, млн ₽" in text
    assert "Прирост, %" in text


def test_progression_stage_subjects_are_russian(text: str) -> None:
    for subject in (
        "контрольный расчёт",
        "перераспределение закачки",
        "поиск границ и комбинаций",
        "устойчивость и компенсация закачки",
    ):
        assert subject in text
    assert "Базовый" in text


def test_best_section_is_russian(text: str) -> None:
    for label in (
        "ЛУЧШИЙ ИЗ ПОЛНОСТЬЮ ПРОСЧИТАННЫХ НА ДАННЫЙ МОМЕНТ СЦЕНАРИЕВ",
        "Управление:",
        "Прирост ЧДД:",
        "SHA расписания:",
        "Гидродинамический расчёт:",
        "Расчёт ЧДД:",
        "Проверка ограничений:",
    ):
        assert label in text


def test_control_description_is_russian(text: str) -> None:
    assert "Блок 2: перенос закачки 41 → 19, доля 35%" in text
    assert "Блок 3: перенос закачки 32 → 4, доля 82.5%" in text
    assert "Блок 4: перенос закачки 7 → 42, доля 97.5%" in text
    assert "Блок 5: перенос закачки 26 → 2, доля 97.5%" in text
    assert "Дополнительно: скважина 42, +100 м³/сут" in text


def test_robustness_labels_are_russian(text: str) -> None:
    for label in (
        "УСТОЙЧИВОСТЬ ЛУЧШЕГО СЦЕНАРИЯ",
        "Фактическая закачка, базовый сценарий:",
        "Фактическая закачка, кандидат:",
        "Разница:",
        "Отклонение:",
        "Максимальное WBHP скважины 42:",
        "Ограничение BHP:",
        "Число состояний на ограничении:",
        "Среднее изменение пластового давления:",
        "Изменение давления в конце периода:",
    ):
        assert label in text
    # Единицы сохраняются как есть.
    assert " bar" in text
    assert " м³" in text


def test_mode_labels_are_russian(text: str) -> None:
    for label in ("МАКСИМАЛЬНЫЙ ЧДД", "КОНСЕРВАТИВНЫЙ", "МИН. ОТКЛОНЕНИЕ ДАВЛЕНИЯ"):
        assert label in text
    assert "Система может выбирать план управления не только по максимальному ЧДД" in text


def test_economics_labels_are_russian(text: str) -> None:
    for label in (
        "Дополнительная выручка:",
        "Рост отчислений:",
        "Рост OPEX:",
        "Рост затрат на закачку:",
        "Дополнительный CAPEX по ЭЦН:",
        "Прирост ЧДД:",
    ):
        assert label in text
    assert "Основной источник экономического эффекта — рост добычи нефти." in text


def test_economics_does_not_oversimplify_tax_and_discounting(text: str) -> None:
    assert "не воспроизводит сама по" in text
    assert "годовую агрегацию налога и дисконтирование" in text


# --- статусы ---------------------------------------------------------------


def test_pass_renders_as_projden(text: str) -> None:
    assert status_label("PASS") == "ПРОЙДЕН"
    assert "Гидродинамический расчёт:  ПРОЙДЕН" in text
    assert "Расчёт ЧДД:                ПРОЙДЕН" in text


def test_constraints_render_as_soblyudeny(text: str) -> None:
    assert constraint_label("PASS") == "СОБЛЮДЕНЫ"
    assert "Проверка ограничений:      СОБЛЮДЕНЫ" in text


def test_failure_statuses_have_russian_labels() -> None:
    assert status_label("FAIL") == "НЕ ПРОЙДЕН"
    assert status_label("FAILED") == "НЕ ПРОЙДЕН"
    assert constraint_label("FAIL") == "НАРУШЕНЫ"


def test_unknown_status_passes_through_unchanged() -> None:
    # Неизвестное значение не выдумывается: оно показывается как есть.
    assert status_label("PARTIAL") == "PARTIAL"


# --- машинный слой не тронут ------------------------------------------------


def test_machine_status_values_are_unchanged(snapshot) -> None:
    assert snapshot.best.simulation_status == "PASS"
    assert snapshot.best.chdd_status == "PASS"
    assert snapshot.best.constraints_status == "PASS"
    assert snapshot.pipeline["validation_status"] == "PASS"


def test_machine_enums_are_unchanged(snapshot) -> None:
    assert snapshot.provenance == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
    assert snapshot.water_balance_status == "ORGANIZER_RULE_UNRESOLVED"


def test_raw_enums_are_still_visible_for_traceability(text: str) -> None:
    assert "NOT_OFFICIAL_PENDING_TNAV_REFERENCE" in text
    assert "ORGANIZER_RULE_UNRESOLVED" in text


def test_scenario_identifiers_are_not_translated(text: str, snapshot) -> None:
    assert snapshot.best.scenario_id == "EXP003_D_COMP_ADD_42_100"
    assert "EXP003_D_COMP_ADD_42_100" in text
    assert "EXP003_C_SCALE_F55" in text
    assert "EXP003_A_B4_7_TO_42_F92p5" in text


def test_snapshot_json_keys_stay_machine_readable() -> None:
    payload = json.loads(snapshot_path(REPO_ROOT).read_text(encoding="utf-8"))

    for key in (
        "baseline",
        "progression",
        "best_candidate",
        "conservative",
        "low_pressure_deviation",
        "robustness",
        "economics_delta_vs_baseline",
        "pipeline",
        "provenance",
        "water_balance_status",
        "source_artifacts",
    ):
        assert key in payload
    assert payload["provenance"] == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
    assert payload["water_balance_status"] == "ORGANIZER_RULE_UNRESOLVED"
    assert payload["best_candidate"]["simulation_status"] == "PASS"
    # Этапы контура в снимке остаются машинными; перевод живёт в выводе.
    assert payload["pipeline"]["stages"][0] == "CASE"
    assert set(PIPELINE_LABELS) >= set(payload["pipeline"]["stages"])


def test_mode_labels_map_from_machine_identifiers(snapshot) -> None:
    assert snapshot.best.label == "MAX_NPV"
    assert snapshot.conservative.label == "CONSERVATIVE"
    assert snapshot.low_pressure.label == "LOW_PRESSURE_DEVIATION"
    assert set(MODE_LABELS) == {"MAX_NPV", "CONSERVATIVE", "LOW_PRESSURE_DEVIATION"}
    assert set(STATUS_LABELS) >= {"PASS", "FAIL"}


# --- числа не изменились ---------------------------------------------------


def test_numerical_snapshot_is_unchanged(snapshot) -> None:
    assert snapshot.baseline_npv_mln_rub == pytest.approx(BASELINE_NPV, abs=1e-12)
    assert snapshot.progression[1].npv_mln_rub == pytest.approx(EXP001_BEST_NPV, abs=1e-12)
    assert snapshot.progression[2].npv_mln_rub == pytest.approx(EXP002_BEST_NPV, abs=1e-12)
    assert snapshot.progression[3].npv_mln_rub == pytest.approx(EXP003_BEST_NPV, abs=1e-12)
    assert snapshot.best.schedule_sha256 == BEST_SCHEDULE_SHA


def test_rendered_numbers_match_the_snapshot(text: str) -> None:
    for value in ("1030.877696", "1051.640027", "1166.984369", "1190.593386", "159.715690"):
        assert value in text
    assert BEST_SCHEDULE_SHA in text


def test_run_counts_are_rendered_from_structured_fields(snapshot, text: str) -> None:
    assert snapshot.progression[1].candidate_runs == 70
    assert snapshot.progression[3].candidate_runs == 25
    assert snapshot.progression[3].reconciliation_runs == 2
    assert "70 сценариев" in text
    assert "25 сценариев (+2 сверочных)" in text


# --- детерминизм -----------------------------------------------------------


def test_russian_rendering_is_deterministic(snapshot) -> None:
    assert render(snapshot) == render(build_snapshot(REPO_ROOT))


def test_tracked_snapshot_still_matches_the_evidence(snapshot) -> None:
    assert snapshot_path(REPO_ROOT).read_text(encoding="utf-8") == serialize(snapshot)


def test_no_leftover_english_user_prose(text: str) -> None:
    """В пользовательском тексте не должно остаться английских фраз.

    Технические имена, идентификаторы и единицы разрешены явным списком --
    их перевод сделал бы вывод хуже, а не лучше.
    """

    stripped = text
    for allowed in ALLOWED_LATIN:
        stripped = stripped.replace(allowed, "")
    leftovers = [
        word
        for word in ("Stage", "Baseline", "Mode", "Source", "PASS", "verified", "candidate")
        if word in stripped
    ]

    assert leftovers == [], leftovers
