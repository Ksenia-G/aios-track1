"""Разметка шагов для графа: молчит в терминале, говорит JSON для страницы."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aios.ui.events import EVENTS_ENV, emit_event
from aios.ui.verify import VerificationError, verify

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "submission_evidence"


def test_events_are_silent_in_a_plain_terminal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(EVENTS_ENV, raising=False)

    emit_event("opm", "active")

    assert capsys.readouterr().out == ""


def test_events_are_json_lines_for_the_page(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(EVENTS_ENV, "1")

    emit_event("chdd", "done", text="ЧДД 1249.44")

    line = capsys.readouterr().out.strip()
    assert line.startswith("::aios ")
    assert json.loads(line.removeprefix("::aios ")) == {
        "node": "chdd",
        "state": "done",
        "text": "ЧДД 1249.44",
    }


def test_failed_step_is_marked_for_the_graph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(EVENTS_ENV, "1")
    evidence = tmp_path / "evidence"
    shutil.copytree(EVIDENCE_DIR, evidence)
    (evidence / "wells_schedule.inc").write_text("-- не наш файл\n", encoding="utf-8")

    with pytest.raises(VerificationError):
        verify(tmp_path / "run", evidence_dir=evidence, emit=lambda _line: None)

    events = [
        json.loads(line.removeprefix("::aios "))
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("::aios ")
    ]
    assert events == [{"node": "sha", "state": "active"}, {"node": "sha", "state": "fail"}]
