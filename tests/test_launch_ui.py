"""Страница запуска: отдаёт страницу и честные данные, запускает только своё.

Сервер проверяется на настоящем сокете, но вместо прогонов OPM ему
подставляются короткие задачи на `python -c`: здесь проверяется механика
запуска, потока лога, конфликта и остановки, а не физика -- физику проверяет
`validation-demo`.

Ожидаемые числа замороженного пакета живут здесь и только здесь: страница
обязана показывать то, что лежит в артефактах, а тест -- ловить расхождение
между артефактами и заявкой.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from aios.ui.server import (
    ACTION_HEADER,
    Task,
    build_server,
    default_tasks,
    environment_checks,
    evidence_summary,
    parse_epoch_table,
    readiness,
    result_payload,
)
from aios.ui.verify import VerificationError, load_claim, verify

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "submission_evidence"
README_PATH = REPO_ROOT / "README.md"

FROZEN_SCHEDULE_SHA = "240dcdb1e94598b0269c307499abc355fb6cc2581d4b93a4e4f680ebba35bf0c"
FROZEN_OBJECTIVE = 1249.4366799710856
FROZEN_ORGANIZER_COMPATIBLE = 5412.255034450385


# --- данные замороженного пакета --------------------------------------------


def test_epoch_decisions_are_read_from_the_readme() -> None:
    epochs = parse_epoch_table(README_PATH.read_text(encoding="utf-8"))

    assert [epoch["date"] for epoch in epochs] == ["2014-01-01", "2014-09-01", "2015-04-01"]
    assert [epoch["floor"] for epoch in epochs] == [False, False, True]
    assert all("`" not in epoch["decision"] for epoch in epochs)


def test_missing_epoch_table_gives_no_rows_instead_of_guessing() -> None:
    assert parse_epoch_table("# README\n\nТаблицы здесь нет.\n") == []


def test_tracked_evidence_package_matches_its_checksums() -> None:
    summary = evidence_summary(EVIDENCE_DIR)

    assert summary["all_ok"] is True
    assert {"wells_schedule.inc", "final_result.json"} <= {
        item["file"] for item in summary["files"]
    }


def test_tampered_evidence_is_reported(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    shutil.copytree(EVIDENCE_DIR, evidence)
    schedule = evidence / "wells_schedule.inc"
    schedule.write_text(schedule.read_text(encoding="utf-8") + "\n-- правка\n", encoding="utf-8")

    summary = evidence_summary(evidence)

    assert summary["all_ok"] is False
    assert [item["file"] for item in summary["files"] if not item["ok"]] == ["wells_schedule.inc"]


def test_result_payload_carries_the_frozen_claim() -> None:
    payload = result_payload(EVIDENCE_DIR, README_PATH)
    final = payload["final"]

    assert final["organizer_compatible_mln_rub"]["B4_MAS_ON"] == pytest.approx(
        FROZEN_ORGANIZER_COMPATIBLE
    )
    assert final["schedule"]["standalone_sha256"] == FROZEN_SCHEDULE_SHA
    assert len(payload["epochs"]) == 3
    assert payload["evidence"]["all_ok"] is True


# --- готовность окружения ----------------------------------------------------


def test_checks_fail_closed_without_external_materials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_Y_ROOT", raising=False)
    monkeypatch.delenv("CHDD_ROOT", raising=False)

    checks = environment_checks(probe=lambda _argv: False, which=lambda name: name)
    by_id = {check.check_id: check for check in checks}
    ready = readiness(checks)

    assert by_id["model_y"].state == "fail"
    assert "MODEL_Y_ROOT" in by_id["model_y"].detail
    assert by_id["chdd"].state == "fail"
    assert by_id["docker"].state == "fail"
    assert by_id["submission"].state == "ok"
    assert not ready["model_y"] and not ready["chdd"] and not ready["docker"]


def test_missing_docker_binary_is_named() -> None:
    by_id = {
        check.check_id: check
        for check in environment_checks(probe=lambda _argv: True, which=lambda _name: None)
    }

    assert by_id["docker"].state == "fail"
    assert "не установлен" in by_id["docker"].detail


def test_default_tasks_are_existing_project_modules() -> None:
    tasks = {task.task_id: task for task in default_tasks()}

    assert set(tasks) == {"verify-submission", "validation-demo", "mas-benchmark", "check"}
    assert tasks["verify-submission"].primary
    assert "aios.ui.verify" in tasks["verify-submission"].argv(Path("workspace"))
    assert "aios.pipeline" in tasks["validation-demo"].argv(Path("workspace"))
    assert "llm" in tasks["mas-benchmark"].requires and tasks["mas-benchmark"].confirm
    assert tasks["check"].isolated and tasks["check"].requires == ()


# --- HTTP --------------------------------------------------------------------


def _task(task_id: str, code: str) -> Task:
    return Task(
        task_id=task_id,
        title=task_id,
        summary="",
        duration="",
        requires=(),
        argv=lambda _workspace: ("-c", code),
    )


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    instance = build_server(
        "127.0.0.1",
        0,
        tasks=(
            _task("echo", "print('готово')"),
            _task("sleep", "import time; time.sleep(30)"),
            _task("boom", "raise SystemExit(3)"),
        ),
        workdir_root=tmp_path,
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.manager.stop_all()
        instance.shutdown()
        instance.server_close()


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def _post(url: str, *, header: bool = True) -> tuple[int, dict]:
    headers = {ACTION_HEADER: "1"} if header else {}
    request = urllib.request.Request(url, data=b"", method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def _wait(base: str, run_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _status, body = _get(f"{base}/api/runs/{run_id}")
        run = json.loads(body)["run"]
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


def test_page_is_served_and_talks_to_the_api(server: str) -> None:
    status, body = _get(server + "/")
    page = body.decode("utf-8")

    assert status == 200
    assert "AIOS" in page and "/api/status" in page


def test_actions_without_the_ui_header_are_refused(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(server + "/api/tasks/echo/run", header=False)

    assert error.value.code == 403


def test_unknown_task_is_refused(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(server + "/api/tasks/not-a-task/run")

    assert error.value.code == 404


def test_run_streams_its_output_and_reports_success(server: str) -> None:
    status, body = _post(server + "/api/tasks/echo/run")
    run = _wait(server, body["run"]["run_id"])

    assert status == 202
    assert run["status"] == "passed" and run["exit_code"] == 0
    assert "готово" in run["lines"]


def test_failing_run_keeps_its_exit_code(server: str) -> None:
    _status, body = _post(server + "/api/tasks/boom/run")
    run = _wait(server, body["run"]["run_id"])

    assert run["status"] == "failed" and run["exit_code"] == 3


def test_log_is_read_incrementally(server: str) -> None:
    _status, body = _post(server + "/api/tasks/echo/run")
    run = _wait(server, body["run"]["run_id"])

    _status, tail = _get(f"{server}/api/runs/{run['run_id']}?since={run['next']}")

    assert json.loads(tail)["run"]["lines"] == []


def test_second_run_waits_for_the_first_and_stop_ends_it(server: str) -> None:
    _status, body = _post(server + "/api/tasks/sleep/run")
    run_id = body["run"]["run_id"]

    with pytest.raises(urllib.error.HTTPError) as error:
        _post(server + "/api/tasks/echo/run")
    assert error.value.code == 409

    _post(f"{server}/api/runs/{run_id}/stop")
    assert _wait(server, run_id)["status"] == "stopped"


# --- пересчёт сданного файла -------------------------------------------------


def test_claim_is_read_from_the_frozen_package() -> None:
    claim = load_claim(EVIDENCE_DIR)

    assert claim.schedule_sha256 == FROZEN_SCHEDULE_SHA
    assert claim.objective_mln_rub == FROZEN_OBJECTIVE
    assert claim.organizer_compatible_mln_rub == pytest.approx(FROZEN_ORGANIZER_COMPATIBLE)


def test_foreign_schedule_is_refused_before_the_simulator(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    shutil.copytree(EVIDENCE_DIR, evidence)
    (evidence / "wells_schedule.inc").write_text("-- не наш файл\n", encoding="utf-8")
    workspace = tmp_path / "run"

    with pytest.raises(VerificationError, match="SHA"):
        verify(workspace, evidence_dir=evidence, emit=lambda _line: None)

    assert not workspace.exists()
