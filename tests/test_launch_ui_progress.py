"""Ход прогона OPM читается из его журнала и не выдаёт старый прогон за текущий."""

from __future__ import annotations

import os
import time
from pathlib import Path

from aios.ui.progress import FlowProgress, last_report_step

# Строки в том виде, в каком их пишет OPM Flow в flow.log.
SAMPLE = """Initializing report step 0/103 at 2007-05-01 0 DAYS line 103
Report step 0/103 at day 0/3136, date = 01-May-2007
Starting time step 0, stepsize 31 days, at day 0/31, date = 01-May-2007
Report step 51/103 at day 1553/3136, date = 01-Aug-2011
Starting time step 0, stepsize 31 days, at day 1553/1584, date = 01-Aug-2011
"""


def test_last_step_is_read_and_measured_in_model_days() -> None:
    step = last_report_step(SAMPLE)

    assert step is not None
    assert (step.step, step.total_steps, step.date) == (51, 103, "01-Aug-2011")
    assert step.percent == 49


def test_text_without_progress_gives_nothing() -> None:
    assert last_report_step("IOConfig: Reading GRIDFILE keyword\n") is None


def test_progress_names_each_new_simulation(tmp_path: Path) -> None:
    lines: list[str] = []
    watcher = FlowProgress(tmp_path, lines.append, since=0, label_simulations=True)

    watcher.poll()
    assert lines == []

    first = tmp_path / "a" / "flow.log"
    first.parent.mkdir()
    first.write_text(SAMPLE, encoding="utf-8")
    watcher.poll()
    assert "симуляция 1" in lines[0]
    assert "49%" in lines[1] and "01-Aug-2011" in lines[1]

    second = tmp_path / "b" / "flow.log"
    second.parent.mkdir()
    second.write_text("Report step 9/103 at day 300/3136, date = 01-Mar-2008\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(second, (later, later))
    watcher.poll()
    assert any("симуляция 2" in line for line in lines)


def test_repeated_polls_do_not_repeat_the_same_progress(tmp_path: Path) -> None:
    (tmp_path / "flow.log").write_text(SAMPLE, encoding="utf-8")
    lines: list[str] = []
    watcher = FlowProgress(tmp_path, lines.append, since=0)

    watcher.poll()
    watcher.poll()

    assert len(lines) == 1


def test_stale_log_is_not_taken_for_the_current_run(tmp_path: Path) -> None:
    old = tmp_path / "flow.log"
    old.write_text(SAMPLE, encoding="utf-8")
    os.utime(old, (1000, 1000))
    lines: list[str] = []

    FlowProgress(tmp_path, lines.append, since=time.time()).poll()

    assert lines == []


def test_live_progress_is_read_from_the_simulator_journal(tmp_path: Path) -> None:
    # Во время прогона flow.log ещё нет: ход пишется в OUT/<колода>.PRT.
    journal = tmp_path / "OUT" / "MODEL_Y.PRT"
    journal.parent.mkdir()
    journal.write_text(SAMPLE, encoding="utf-8")
    lines: list[str] = []

    FlowProgress(tmp_path, lines.append, since=0).poll()

    assert len(lines) == 1 and "49%" in lines[0]
