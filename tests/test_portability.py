"""Переносимость репозитория.

Клон не должен предполагать чужую файловую систему. Model Y и официальный
пакет расчёта ЧДД остаются внешними: они не кладутся в репозиторий и
задаются переменными окружения. Отсутствие этих материалов -- законное
состояние клона, а не поломка, поэтому тесты и демонстрация обязаны
работать без них, а живой прогон -- падать внятно.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aios.cases import CaseError, load_case
from aios.demo import build_snapshot
from aios.demo.cli import main as demo_main

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = REPO_ROOT / "cases" / "model_y_validation.json"

EXTERNAL_VARIABLES = ("MODEL_Y_ROOT", "CHDD_ROOT")

# Маркеры чужой файловой системы и чужих рабочих каталогов.
FORBIDDEN_MARKERS = ("/Users/", "/Volumes/", "/home/", "aios-pret0", "worktrees/aios-")

# Каталоги, где сравнение с маркерами имеет смысл.
TRACKED_TEXT_SUFFIXES = {".py", ".json", ".toml", ".md", ".csv", ".cfg", ".ini", ".yml", ".yaml"}

# Тесты, которые сами проверяют отсутствие маркеров и потому обязаны их
# упоминать в собственных утверждениях.
MARKER_ASSERTION_FILES = {
    "tests/test_portability.py",
    "tests/test_kt1_demo.py",
    "tests/test_exp004.py",
}


def _tracked_text_files() -> list[Path]:
    """Именно отслеживаемые файлы, а не обход каталога.

    Локальные рабочие каталоги вроде `output/` игнорируются гитом и в
    публикацию не попадают, поэтому проверять их на чужие пути бессмысленно
    -- это давало бы ложный отказ на машине разработчика.
    """

    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("git is not available to enumerate tracked files")
    files: list[Path] = []
    for name in result.stdout.split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        if path.suffix in TRACKED_TEXT_SUFFIXES and path.is_file():
            files.append(path)
    return files


# --- отсутствие чужих путей ------------------------------------------------


def test_no_machine_specific_paths_in_tracked_text_files() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in MARKER_ASSERTION_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders.extend(f"{relative}: {marker}" for marker in FORBIDDEN_MARKERS if marker in text)

    assert offenders == [], offenders


def test_case_file_uses_environment_variables_for_external_inputs() -> None:
    payload = json.loads(CASE_PATH.read_text(encoding="utf-8"))

    assert payload["history"]["deck_root"] == "${MODEL_Y_ROOT}"
    assert payload["economics"]["calculator_root"] == "${CHDD_ROOT}"
    assert payload["economics"]["norms_workbook"].startswith("${CHDD_ROOT}/")


def test_example_environment_file_documents_both_variables() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    for name in EXTERNAL_VARIABLES:
        assert name in text
    for marker in FORBIDDEN_MARKERS:
        assert marker not in text


# --- поведение без внешних материалов --------------------------------------


def test_case_fails_loudly_when_external_resources_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in EXTERNAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(CaseError, match="environment variable"):
        load_case(CASE_PATH)


def test_error_names_the_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_Y_ROOT", raising=False)
    monkeypatch.setenv("CHDD_ROOT", "/tmp/chdd")

    with pytest.raises(CaseError, match="MODEL_Y_ROOT"):
        load_case(CASE_PATH)


def test_case_loads_once_the_variables_point_somewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MODEL_Y_ROOT", str(tmp_path / "model-y"))
    monkeypatch.setenv("CHDD_ROOT", str(tmp_path / "chdd"))

    case = load_case(CASE_PATH)

    # Пути собрались; существование внешних материалов проверяется позже,
    # когда их действительно читают.
    assert case.history.deck_root == tmp_path / "model-y"
    assert case.economics.calculator_root == tmp_path / "chdd"
    assert case.economics.norms_workbook.parent == tmp_path / "chdd" / "input"


def test_demo_snapshot_builds_without_external_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in EXTERNAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    snapshot = build_snapshot(REPO_ROOT)

    assert snapshot.baseline_npv_mln_rub > 0.0
    assert snapshot.provenance == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"


def test_demo_cli_succeeds_without_external_resources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in EXTERNAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    code = demo_main(["--root", str(REPO_ROOT)])
    captured = capsys.readouterr()

    assert code == 0
    assert "ПРОВЕРЕННЫЕ РЕЗУЛЬТАТЫ ВЫЧИСЛИТЕЛЬНЫХ ЭКСПЕРИМЕНТОВ" in captured.out


# --- изоляция от других рабочих каталогов ----------------------------------


def test_repository_does_not_reference_sibling_worktrees() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in MARKER_ASSERTION_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in ("aios-exp-001", "aios-exp-002", "aios-exp-003", "aios-kt1-demo"):
            if marker in text:
                offenders.append(f"{relative}: {marker}")

    assert offenders == [], offenders


def test_external_materials_are_not_committed() -> None:
    """Внешние материалы организаторов не должны попасть в репозиторий.

    Проверка по индексу git, а не обходу каталога (rglob): локально Model Y и
    CHDD присутствуют как нетрекаемые рабочие каталоги, и обход всего дерева
    давал бы ложный красный семафор на машине разработчика. Контракт --
    публикация (отслеживаемые файлы), а не локальная файловая система, на
    которой материалы лежат законно.
    """
    forbidden = (
        "Model_Y.zip",
        "MODEL_Y.EGRID",
        "MODEL_Y.INIT",
        "MODEL_Y.UNRST",
        "MODEL_Y.UNSMRY",
        "MODEL_Y.SMSPEC",
        "chdd_model.py",
        "excel_io.py",
    )
    tracked = {path.name for path in _all_tracked_files()}
    present = [name for name in forbidden if name in tracked]

    assert present == [], present


def _all_tracked_files() -> list[Path]:
    """Все отслеживаемые git-файлы (включая бинарные), без R-дерева проекта."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("git is not available to enumerate tracked files")
    return [REPO_ROOT / name for name in result.stdout.split("\0") if name]


def test_failed_experiment_run_does_not_destroy_tracked_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Незаданное окружение не должно стирать результаты прошлых прогонов.

    Артефакты экспериментов отслеживаются, и снятие прошлого пакета до
    чтения кейса означало бы потерю свидетельств из-за опечатки в
    переменной окружения.
    """

    from aios.experiments import exp003_cli

    for name in EXTERNAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    evidence = output_dir / "summary.json"
    evidence.write_text('{"kept": true}', encoding="utf-8")

    with pytest.raises(CaseError, match="environment variable"):
        exp003_cli.main(
            [
                "--case",
                str(CASE_PATH),
                "--output",
                str(output_dir),
                "--runtime",
                str(tmp_path / "rt"),
            ]
        )

    assert evidence.read_text(encoding="utf-8") == '{"kept": true}'
