"""Обоснования финального прогона на странице: показываются как записаны.

Страница обязана показать, почему агент выбрал действие. Для финального
прогона это уже записанный текст специалиста, а не пересказ: тесты держат
границу между записанным обоснованием, честным «не сохранялось» и статическим
описанием узла.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aios.ui.server import FROZEN_RATIONALE_PATH, frozen_rationale, result_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "src" / "aios" / "ui" / "static" / "index.html"
EVIDENCE = REPO_ROOT / "submission_evidence"
SPECIALISTS = ("production", "injection")


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(FROZEN_RATIONALE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def _records(record: dict) -> list[dict]:
    return [item for epoch in record["epochs"] for name in SPECIALISTS for item in epoch[name]]


# --- A. записанное обоснование доезжает до страницы -------------------------


def test_frozen_rationale_is_published_in_the_result_payload(record: dict) -> None:
    payload = result_payload()

    assert payload["rationale"] == record


def test_every_frozen_record_carries_a_non_empty_rationale(record: dict) -> None:
    items = _records(record)

    assert items, "в пакете нет ни одного обоснования"
    assert all(item["rationale"].strip() for item in items)


def test_frozen_rationale_binds_to_the_published_submission(record: dict) -> None:
    published = json.loads((EVIDENCE / "reproducibility.json").read_text(encoding="utf-8"))

    assert record["source"]["committed_candidate_digest"] == published["committed_candidate_digest"]
    assert record["source"]["artifact_sha256"]


def test_frozen_epochs_match_the_committed_decision_dates(record: dict) -> None:
    final = json.loads((EVIDENCE / "final_result.json").read_text(encoding="utf-8"))

    assert [epoch["date"] for epoch in record["epochs"]] == final["decision_epochs"]


# --- B. отсутствие обоснования признаётся честно ---------------------------


def test_the_fallback_epoch_records_no_rationale(record: dict) -> None:
    """Эпоха, закрывшаяся детерминированным полом, обоснования не записала."""
    last = record["epochs"][-1]

    assert last["production"] == []
    assert last["injection"] == []


def test_absence_is_stated_plainly_and_nothing_is_invented(page: str) -> None:
    assert "Для этого шага объяснение агента не было сохранено." in page
    assert (
        "if (!frozenCount) body.append(whyBlock(PREFERRED_HEADING, null, [RATIONALE_ABSENT]" in page
    )


def test_missing_record_file_does_not_break_the_page(tmp_path: Path) -> None:
    payload = result_payload(rationale_path=tmp_path / "нет.json")

    assert payload["rationale"] is None
    assert frozen_rationale(tmp_path / "нет.json") is None


# --- C. правило узла не выдаётся за обоснование агента ---------------------


def test_agent_rationale_and_node_rule_are_separate_headings(page: str) -> None:
    assert "const PREFERRED_HEADING = 'Почему агент предложил это действие'" in page
    assert "const RULE_HEADING = 'Как этот узел принимает решения'" in page


# --- предпочтённое и рассмотренное это разные утверждения -------------------


def test_preferred_and_considered_get_different_headings(page: str) -> None:
    """Рассмотренный вариант нельзя подписать так же, как предложенный."""
    assert "const CONSIDERED_HEADING = 'Почему агент рассматривал этот вариант'" in page
    assert "const RATIONALE_HEADING" not in page


def test_considered_records_are_rendered_under_the_considered_heading(page: str) -> None:
    assert "whyBlock(CONSIDERED_HEADING, BADGE_FROZEN, frozen.considered, false)" in page
    assert "whyBlock(PREFERRED_HEADING, BADGE_FROZEN, frozen.preferred, false)" in page


def test_considered_is_never_folded_into_the_preferred_list(page: str) -> None:
    """Разделение идёт по записанному slot, а не по подписи внутри строки."""
    assert "rec.slot === 'considered' ? split.considered : split.preferred" in page
    assert "рассмотрено и не предпочтено" not in page


def test_both_slots_exist_in_the_frozen_record(record: dict) -> None:
    slots = {item["slot"] for item in _records(record)}

    assert slots == {"preferred", "considered"}


def test_specialist_rule_text_does_not_claim_the_proposal_was_committed(page: str) -> None:
    """Обоснование специалиста не объясняет выбор экономического отбора."""
    assert "Предложение это ещё не решение" in page
    assert "решает экономический отбор среди прошедших Guardian" in page


def test_recorded_rationale_is_badged_as_coming_from_the_frozen_run(page: str) -> None:
    assert "Записано в финальном MAS-прогоне" in page


def test_specialist_rule_text_no_longer_pretends_to_be_the_reason(page: str) -> None:
    """Раньше у специалистов правилом узла стоял текст про отсутствие обоснований."""
    assert "FROZEN_WHY" not in page


# --- D. живое обоснование по-прежнему живое --------------------------------


def test_live_rationale_is_kept_and_marked_as_live(page: str) -> None:
    assert "const BADGE_LIVE = 'Записано в идущем прогоне'" in page
    assert "node.why.map(" in page
    assert "whyBlock(PREFERRED_HEADING, BADGE_LIVE" in page


def test_provenance_badges_survive_the_relabelling(page: str) -> None:
    assert "const BADGE_FROZEN = 'Записано в финальном MAS-прогоне'" in page
    assert page.count("BADGE_FROZEN") == 3  # объявление + предпочтённое + рассмотренное
    assert page.count("BADGE_LIVE") == 2  # объявление + живой блок


def test_frozen_text_is_not_shown_for_a_node_running_live(page: str) -> None:
    """Пока узел идёт вживую, слова прошлого прогона не подставляются."""
    assert "!live && SPECIALISTS.has(id) && (!G.mode || node.state === 'frozen')" in page


# --- E. обоснование не несёт числовой власти -------------------------------


def test_recorded_rationale_carries_no_numeric_setpoint(record: dict) -> None:
    """Текст обоснования не может быть источником уставки: чисел в нём нет."""
    for item in _records(record):
        assert not re.search(r"\d+(?:[.,]\d+)?\s*(?:м3|m3|бар|bar|сут|day)", item["rationale"])


def test_nothing_parses_numbers_out_of_the_stored_rationale() -> None:
    server = (REPO_ROOT / "src" / "aios" / "ui" / "server.py").read_text(encoding="utf-8")
    page = INDEX.read_text(encoding="utf-8")

    assert "float(" not in server.split("def frozen_rationale")[1].split("def result_payload")[0]
    assert "parseFloat(rec.rationale" not in page
    assert "Number(rec.rationale" not in page
