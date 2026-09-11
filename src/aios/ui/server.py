"""HTTP-сервер страницы запуска AIOS Track 1.

Только стандартная библиотека: `ThreadingHTTPServer` отдаёт одну страницу и
небольшой JSON API. Сервер по умолчанию слушает только 127.0.0.1, запускает
только задачи из белого списка и принимает POST лишь с собственным
заголовком. Нестандартный заголовок заставляет браузер делать CORS preflight,
на который сервер не отвечает, поэтому вкладка с чужого сайта не может
запустить OPM на машине того, кто открыл страницу.

Одновременно идёт не больше одного прогона. Полный прогон OPM занимает
несколько минут и нагружает машину целиком: два таких прогона параллельно
не ускоряют ни один, а лог двух процессов в одном окне читать нельзя.

Готовность окружения проверяется теми же путями, что и живой прогон:
кейс грузится `load_case`, провайдер определяется `detect_provider`,
контрольные суммы пакета сверяются с `SHA256SUMS.txt`. Проверка ничего не
чинит и не угадывает -- она только называет, чего не хватает.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from aios.ui.progress import FlowProgress

REPO_ROOT = Path(__file__).resolve().parents[3]
INDEX_PATH = Path(__file__).resolve().parent / "static" / "index.html"
FROZEN_RATIONALE_PATH = Path(__file__).resolve().parent / "frozen_rationale.json"
EVIDENCE_DIR = REPO_ROOT / "submission_evidence"
README_PATH = REPO_ROOT / "README.md"
CASE_PATH = REPO_ROOT / "cases" / "model_y_validation.json"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

#: Заголовок, без которого действие не принимается.
ACTION_HEADER = "X-AIOS-UI"
#: Сколько последних строк лога прогона держать в памяти.
LOG_TAIL_LINES = 5000
#: Таблица закоммиченных решений по эпохам живёт в README, а не в JSON
#: пакета; читается она оттуда, чтобы у текста была одна правда.
EPOCH_TABLE_HEADING = "Механизм закоммиченной политики"

OK = "ok"
WARN = "warn"
FAIL = "fail"

#: Переменные, которые изолированная задача не наследует: тесты обязаны
#: проходить так же, как в CI, где нет ни материалов организаторов, ни ключа.
ISOLATED_VARIABLES = ("MODEL_Y_ROOT", "CHDD_ROOT", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
ISOLATED_PREFIXES = ("MAS_LLM_",)

ArgvBuilder = Callable[[Path], tuple[str, ...]]
ProgressDir = Callable[[Path], Path]
Probe = Callable[[Sequence[str]], bool]


class UnknownTaskError(LookupError):
    """Задачи с таким идентификатором нет в белом списке."""


class RunInProgressError(RuntimeError):
    """Уже идёт другой прогон."""


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Task:
    """Одно действие страницы: заранее известная команда, а не произвольная строка."""

    task_id: str
    title: str
    summary: str
    duration: str
    requires: tuple[str, ...]
    #: Аргументы интерпретатора Python для рабочего каталога прогона.
    argv: ArgvBuilder
    primary: bool = False
    confirm: str | None = None
    isolated: bool = False
    #: Каталог, куда задача пишет журналы OPM: по нему страница показывает ход
    #: прогона, пока сам процесс молчит. None -- задача печатает ход сама.
    progress_dir: ProgressDir | None = None
    many_simulations: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "title": self.title,
            "summary": self.summary,
            "duration": self.duration,
            "requires": list(self.requires),
            "primary": self.primary,
            "confirm": self.confirm,
        }


def default_tasks() -> tuple[Task, ...]:
    """Действия страницы. Каждое -- существующий модуль проекта."""
    return (
        Task(
            task_id="verify-submission",
            title="Пересчитать сданный ЧДД",
            summary=(
                "Берёт сданный wells_schedule.inc, считает его полным прогоном OPM и "
                "официальным калькулятором и сверяет с заявленным ЧДД, ровно так, как это "
                "сделают организаторы."
            ),
            duration="3-5 минут",
            requires=("model_y", "chdd", "docker"),
            argv=lambda workspace: ("-m", "aios.ui.verify", "--workspace-root", str(workspace)),
            primary=True,
        ),
        Task(
            task_id="validation-demo",
            title="Сквозной прогон опорной политики",
            summary=(
                "Кейс → расписание → проверка ограничений → OPM → ЧДД → пакет сабмишена. "
                "Опорный ЧДД сверяется с принятым значением в кейсе."
            ),
            duration="3-5 минут",
            requires=("model_y", "chdd", "docker"),
            argv=lambda _workspace: ("-m", "aios.pipeline"),
            progress_dir=lambda _workspace: REPO_ROOT / "runtime",
        ),
        Task(
            task_id="mas-benchmark",
            title="Замкнутый контур MAS",
            summary=(
                "Три эпохи решения: LLM-специалисты предлагают гипотезы, каждый кандидат "
                "считается в OPM, проходит Guardian и оценивается по ЧДД. Рядом "
                "детерминированная абляция MAS OFF."
            ),
            duration="45-60 минут",
            requires=("model_y", "chdd", "docker", "llm"),
            argv=lambda workspace: (
                "-m",
                "aios.benchmark",
                "--variants",
                "mas-off,mas-on",
                "--decision-steps",
                "3",
                "--repeats",
                "1",
                "--workspace-root",
                str(workspace / "workspace"),
                "--output",
                str(workspace / "results"),
            ),
            confirm="Прогон вызывает LLM-провайдера и идёт около часа. Запустить?",
            progress_dir=lambda workspace: workspace / "workspace",
            many_simulations=True,
        ),
        Task(
            task_id="check",
            title="Тесты проекта (как в CI)",
            summary=(
                "Полный набор тестов без внешних материалов: контракты, инварианты, граница "
                "полномочий, упаковка сабмишена."
            ),
            duration="меньше минуты",
            requires=(),
            argv=lambda _workspace: ("-m", "pytest", "-q"),
            isolated=True,
        ),
    )


# ---------------------------------------------------------------------------
# Прогоны
# ---------------------------------------------------------------------------


@dataclass
class Run:
    """Один запущенный процесс и хвост его вывода."""

    run_id: str
    task: Task
    started_at: float
    process: subprocess.Popen[str]
    status: str = "running"
    exit_code: int | None = None
    finished_at: float | None = None
    stop_requested: bool = False
    lines: list[str] = field(default_factory=list)
    #: Сколько ранних строк уже отброшено: номера строк сквозные, поэтому
    #: клиент, читающий лог частями, не получит одну строку дважды.
    dropped: int = 0
    progress: FlowProgress | None = None

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        end = self.finished_at if self.finished_at is not None else time.time()
        first = max(since - self.dropped, 0)
        return {
            "run_id": self.run_id,
            "task_id": self.task.task_id,
            "title": self.task.title,
            "status": self.status,
            "exit_code": self.exit_code,
            "elapsed_seconds": round(end - self.started_at, 1),
            "lines": self.lines[first:],
            "next": self.dropped + len(self.lines),
        }


def _terminate(process: subprocess.Popen[str]) -> None:
    """Остановить процесс вместе с его потомками, если это возможно."""
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        return
    process.terminate()


class RunManager:
    """Запускает задачи из белого списка и держит их вывод."""

    def __init__(
        self,
        tasks: Sequence[Task],
        *,
        workdir_root: Path,
        python: str = sys.executable,
        cwd: Path = REPO_ROOT,
    ) -> None:
        self._tasks = {task.task_id: task for task in tasks}
        self._workdir_root = workdir_root
        self._python = python
        self._cwd = cwd
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._current: Run | None = None

    @property
    def tasks(self) -> tuple[Task, ...]:
        return tuple(self._tasks.values())

    def _child_env(self, task: Task) -> dict[str, str]:
        env = dict(os.environ)
        if task.isolated:
            for name in list(env):
                if name in ISOLATED_VARIABLES or name.startswith(ISOLATED_PREFIXES):
                    del env[name]
        source = str(self._cwd / "src")
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = source if not existing else source + os.pathsep + existing
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Процессы, запущенные страницей, размечают свои шаги для графа.
        env["AIOS_UI_EVENTS"] = "1"
        return env

    def start(self, task_id: str) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise UnknownTaskError(task_id)
        with self._lock:
            if self._current is not None and self._current.status == "running":
                raise RunInProgressError(self._current.run_id)
            run_id = uuid.uuid4().hex[:12]
            workspace = self._workdir_root / run_id
            workspace.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(
                [self._python, *task.argv(workspace)],
                cwd=self._cwd,
                env=self._child_env(task),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=os.name == "posix",
            )
            run = Run(run_id=run_id, task=task, started_at=time.time(), process=process)
            self._runs[run_id] = run
            self._current = run
            snapshot = run.snapshot()
        if task.progress_dir is not None:
            run.progress = FlowProgress(
                task.progress_dir(workspace),
                lambda line: self._append(run, line),
                since=run.started_at,
                label_simulations=task.many_simulations,
            ).start()
        threading.Thread(
            target=self._pump, args=(run,), name=f"aios-ui-{run_id}", daemon=True
        ).start()
        return snapshot

    def _append(self, run: Run, line: str) -> None:
        with self._lock:
            run.lines.append(line)
            overflow = len(run.lines) - LOG_TAIL_LINES
            if overflow > 0:
                del run.lines[:overflow]
                run.dropped += overflow

    def _pump(self, run: Run) -> None:
        stream = run.process.stdout
        if stream is not None:
            for raw in stream:
                self._append(run, raw.rstrip("\n"))
        code = run.process.wait()
        if run.progress is not None:
            run.progress.stop(final=False)
        with self._lock:
            run.exit_code = code
            run.finished_at = time.time()
            if run.stop_requested:
                run.status = "stopped"
            elif code == 0:
                run.status = "passed"
            else:
                run.status = "failed"

    def get(self, run_id: str, *, since: int = 0) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else run.snapshot(since)

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._current is None else self._current.snapshot()

    def stop(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            if run.status == "running":
                run.stop_requested = True
                _terminate(run.process)
            return run.snapshot(len(run.lines) + run.dropped)

    def stop_all(self) -> None:
        with self._lock:
            for run in self._runs.values():
                if run.status == "running":
                    run.stop_requested = True
                    _terminate(run.process)


# ---------------------------------------------------------------------------
# Готовность окружения
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    """Одна проверка готовности: состояние и то, что с ним делать."""

    check_id: str
    title: str
    state: str
    detail: str

    def to_json(self) -> dict[str, str]:
        return {
            "id": self.check_id,
            "title": self.title,
            "state": self.state,
            "detail": self.detail,
        }


def _probe(argv: Sequence[str]) -> bool:
    try:
        completed = subprocess.run(list(argv), capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def environment_checks(
    *,
    case_path: Path = CASE_PATH,
    evidence_dir: Path = EVIDENCE_DIR,
    probe: Probe = _probe,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[Check, ...]:
    """Что есть на этой машине для живого прогона и чего не хватает."""
    from aios.cases import CaseError, load_case

    checks: list[Check] = []
    case = None
    try:
        case = load_case(case_path)
    except (CaseError, OSError, ValueError) as exc:
        checks.append(
            Check(
                "model_y",
                "Колода Model Y",
                FAIL,
                f"Кейс не загружается: {exc}. Скопируйте .env.example в .env и укажите "
                "MODEL_Y_ROOT, каталог с MODEL_Y.DATA.",
            )
        )
        checks.append(
            Check(
                "chdd",
                "Калькулятор ЧДД",
                FAIL,
                "Нужен тот же .env: CHDD_ROOT, каталог с chdd_model.py и нормативами.",
            )
        )
    else:
        data = Path(case.history.data_path)
        checks.append(
            Check(
                "model_y",
                "Колода Model Y",
                OK if data.is_file() else FAIL,
                f"{data.name} найден: {data.parent}" if data.is_file() else f"Нет файла {data}",
            )
        )
        root = Path(case.economics.calculator_root)
        norms = Path(case.economics.norms_workbook)
        missing = [path.name for path in (root / "chdd_model.py", norms) if not path.is_file()]
        checks.append(
            Check(
                "chdd",
                "Калькулятор ЧДД",
                FAIL if missing else OK,
                f"Не найдено: {', '.join(missing)} в {root}"
                if missing
                else f"Официальный пакет и нормативы на месте: {root}",
            )
        )

    image = None if case is None else case.simulator.docker_image
    title = "Docker и образ OPM"
    if which("docker") is None:
        checks.append(
            Check(
                "docker", title, FAIL, "Docker не установлен, а OPM Flow запускается в контейнере."
            )
        )
    elif not probe(["docker", "info"]):
        checks.append(
            Check("docker", title, FAIL, "Docker установлен, но не запущен. Запустите Docker.")
        )
    elif image is None:
        checks.append(
            Check(
                "docker",
                title,
                WARN,
                "Docker работает; образ не проверен, потому что кейс не загрузился.",
            )
        )
    elif probe(["docker", "image", "inspect", image]):
        checks.append(
            Check("docker", title, OK, "Docker работает, образ OPM закреплён по digest и скачан.")
        )
    else:
        checks.append(
            Check(
                "docker",
                title,
                WARN,
                f"Docker работает, образ OPM ещё не скачан, первый прогон скачает его сам "
                f"(docker pull {image}).",
            )
        )

    try:
        from aios.benchmark.provider import detect_provider

        availability = detect_provider()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        checks.append(Check("llm", "LLM-провайдер", WARN, f"Проверка не выполнилась: {exc}"))
    else:
        if availability.available:
            checks.append(
                Check(
                    "llm",
                    "LLM-провайдер",
                    OK,
                    f"{availability.provider_id} · {availability.model_id}",
                )
            )
        else:
            checks.append(
                Check(
                    "llm",
                    "LLM-провайдер",
                    WARN,
                    f"Нужен только для замкнутого контура MAS. {availability.reason}",
                )
            )

    summary = evidence_summary(evidence_dir)
    broken = [item["file"] for item in summary["files"] if not item["ok"]]
    if summary["all_ok"]:
        checks.append(
            Check(
                "submission",
                "Пакет сабмишена",
                OK,
                f"Файлы пакета ({len(summary['files'])}) совпадают с SHA256SUMS.txt.",
            )
        )
    else:
        checks.append(
            Check(
                "submission",
                "Пакет сабмишена",
                FAIL,
                "Не совпадают контрольные суммы: "
                + (", ".join(broken) if broken else "SHA256SUMS.txt не найден"),
            )
        )
    return tuple(checks)


def readiness(checks: Sequence[Check]) -> dict[str, bool]:
    """Какие требования задач выполнены. Нескачанный образ -- не препятствие."""
    states = {check.check_id: check.state for check in checks}
    return {
        "model_y": states.get("model_y") == OK,
        "chdd": states.get("chdd") == OK,
        "docker": states.get("docker") in (OK, WARN),
        "llm": states.get("llm") == OK,
    }


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------


def evidence_summary(evidence_dir: Path = EVIDENCE_DIR) -> dict[str, Any]:
    """Сверить файлы пакета с `SHA256SUMS.txt`."""
    sums = evidence_dir / "SHA256SUMS.txt"
    files: list[dict[str, Any]] = []
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            expected, name = parts[0], parts[1].strip().lstrip("*")
            path = evidence_dir / name
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            files.append({"file": name, "sha256": expected, "ok": actual == expected})
    return {"files": files, "all_ok": bool(files) and all(item["ok"] for item in files)}


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_epoch_table(text: str) -> list[dict[str, Any]]:
    """Строки таблицы закоммиченных решений из README.

    Нет таблицы -- нет строк: подставлять решения, которых нет в тексте,
    значило бы показать то, чего система не делала.
    """
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if EPOCH_TABLE_HEADING in line), None)
    if start is None:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or not _DATE.fullmatch(cells[0]):
            continue
        decision = cells[1].replace("`", "")
        rows.append({"date": cells[0], "decision": decision, "floor": decision.startswith("HOLD")})
    return rows


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def frozen_rationale(path: Path = FROZEN_RATIONALE_PATH) -> dict[str, Any] | None:
    """Обоснования агентов, записанные в замороженном прогоне MAS.

    Файл переносит текст из артефакта прогона дословно (см.
    `scripts/extract_frozen_rationale.py`). Нет файла -- нет обоснований:
    страница честно скажет, что для шага объяснение не сохранялось, и ничего
    не придумает.
    """
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else None


def result_payload(
    evidence_dir: Path = EVIDENCE_DIR,
    readme_path: Path = README_PATH,
    rationale_path: Path = FROZEN_RATIONALE_PATH,
) -> dict:
    """Замороженный результат ровно в том виде, в каком он лежит в пакете."""
    readme = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else ""
    return {
        "final": _read_json(evidence_dir / "final_result.json"),
        "reproducibility": _read_json(evidence_dir / "reproducibility.json"),
        "epochs": parse_epoch_table(readme),
        "evidence": evidence_summary(evidence_dir),
        "rationale": frozen_rationale(rationale_path),
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_START_PATH = re.compile(r"^/api/tasks/([a-z0-9-]+)/run$")
_RUN_PATH = re.compile(r"^/api/runs/([0-9a-f]{12})$")
_STOP_PATH = re.compile(r"^/api/runs/([0-9a-f]{12})/stop$")


def _int(value: str) -> int:
    try:
        return max(int(value), 0)
    except ValueError:
        return 0


class LaunchServer(ThreadingHTTPServer):
    """HTTP-сервер вместе с состоянием страницы."""

    def __init__(
        self,
        address: tuple[str, int],
        manager: RunManager,
        *,
        case_path: Path,
        evidence_dir: Path,
        readme_path: Path,
        index_path: Path,
    ) -> None:
        super().__init__(address, _Handler)
        self.manager = manager
        self.case_path = case_path
        self.evidence_dir = evidence_dir
        self.readme_path = readme_path
        self.index_path = index_path


class _Handler(BaseHTTPRequestHandler):
    server: LaunchServer
    server_version = "AIOS-UI/1.0"

    def log_message(self, *args: Any) -> None:
        return

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            try:
                body = self.server.index_path.read_bytes()
            except OSError:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, b"page is missing", "text/plain")
                return
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            checks = environment_checks(
                case_path=self.server.case_path, evidence_dir=self.server.evidence_dir
            )
            self._json(
                HTTPStatus.OK,
                {"checks": [check.to_json() for check in checks], "ready": readiness(checks)},
            )
            return
        if path == "/api/result":
            self._json(
                HTTPStatus.OK, result_payload(self.server.evidence_dir, self.server.readme_path)
            )
            return
        if path == "/api/tasks":
            tasks = [task.to_json() for task in self.server.manager.tasks]
            self._json(HTTPStatus.OK, {"tasks": tasks})
            return
        if path == "/api/runs/current":
            self._json(HTTPStatus.OK, {"run": self.server.manager.current()})
            return
        match = _RUN_PATH.match(path)
        if match:
            since = _int(parse_qs(url.query).get("since", ["0"])[0])
            snapshot = self.server.manager.get(match.group(1), since=since)
            if snapshot is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
            else:
                self._json(HTTPStatus.OK, {"run": snapshot})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.headers.get(ACTION_HEADER) != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": f"missing {ACTION_HEADER} header"})
            return
        path = urlparse(self.path).path
        match = _START_PATH.match(path)
        if match:
            try:
                snapshot = self.server.manager.start(match.group(1))
            except UnknownTaskError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "unknown task"})
                return
            except RunInProgressError as exc:
                self._json(
                    HTTPStatus.CONFLICT, {"error": "another run is in progress", "run_id": str(exc)}
                )
                return
            self._json(HTTPStatus.ACCEPTED, {"run": snapshot})
            return
        match = _STOP_PATH.match(path)
        if match:
            snapshot = self.server.manager.stop(match.group(1))
            if snapshot is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
            else:
                self._json(HTTPStatus.OK, {"run": snapshot})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def build_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    tasks: Sequence[Task] | None = None,
    workdir_root: Path | None = None,
    case_path: Path = CASE_PATH,
    evidence_dir: Path = EVIDENCE_DIR,
    readme_path: Path = README_PATH,
    index_path: Path = INDEX_PATH,
) -> LaunchServer:
    manager = RunManager(
        tuple(tasks) if tasks is not None else default_tasks(),
        workdir_root=workdir_root or Path(tempfile.gettempdir()) / "aios-ui",
    )
    return LaunchServer(
        (host, port),
        manager,
        case_path=case_path,
        evidence_dir=evidence_dir,
        readme_path=readme_path,
        index_path=index_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aios.ui", description="Страница запуска AIOS Track 1."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    args = parser.parse_args(argv)

    try:
        server = build_server(args.host, args.port)
    except OSError as exc:
        print(
            f"Не удалось занять порт {args.port}: {exc}. Попробуйте --port 8766.", file=sys.stderr
        )
        return 1

    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"AIOS Track 1, страница запуска: {url}", flush=True)
    print("Остановить: Ctrl+C", flush=True)
    if args.host not in LOOPBACK_HOSTS:
        print(
            "Внимание: страница запускает прогоны на этой машине, не открывайте её в сеть.",
            flush=True,
        )
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.manager.stop_all()
        server.server_close()
    return 0
