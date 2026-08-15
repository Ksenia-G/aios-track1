"""Запуск OPM Flow по рабочей копии кейса.

Симулятор берётся из закреплённого docker-образа: на этом образе уже доказана
совместимость колоды Model Y и побитовая воспроизводимость прогона. Локально
установленный `flow` намеренно не ищется -- нельзя молча посчитать другим
симулятором и назвать это тем же baseline.

Провал симуляции -- это провал пайплайна. Частичный результат не
интерпретируется.
"""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.opm.summary import SummaryData, read_summary
from aios.opm.workspace import Workspace

LOG_NAME = "flow.log"

_ERROR_MARKERS = ("Error:", "error encountered", "Exception", "Program threw")
_WARNING_MARKERS = ("Warning:", "warning:")
# Model Y даёт около двух тысяч предупреждений, почти все -- один и тот же
# COMPDAT. В отчёт идут различающиеся тексты, полное число -- отдельным полем.
_MAX_DISTINCT_WARNINGS = 25


class SimulationError(RuntimeError):
    """Симулятор не отработал."""


@dataclass(frozen=True, slots=True)
class OpmRunResult:
    """Всё, что известно о прогоне после его завершения."""

    exit_code: int
    wall_seconds: float
    command: tuple[str, ...]
    log_path: Path
    output_files: tuple[str, ...]
    report_steps: int
    final_date: str | None
    warnings: tuple[str, ...]
    warning_count: int
    errors: tuple[str, ...]

    @property
    def is_pass(self) -> bool:
        return self.exit_code == 0 and not self.errors and self.report_steps > 0

    def to_json(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.is_pass else "FAIL",
            "exit_code": self.exit_code,
            "wall_seconds": round(self.wall_seconds, 3),
            "command": list(self.command),
            "log": self.log_path.name,
            "output_files": list(self.output_files),
            "report_steps": self.report_steps,
            "final_date": self.final_date,
            "warning_count": self.warning_count,
            "distinct_warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def build_command(case: Case, workspace: Workspace) -> tuple[str, ...]:
    """Команда запуска. Вынесена отдельно, чтобы её можно было проверить без docker."""

    return (
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workspace.root}:/data",
        case.simulator.docker_image,
        case.simulator.flow_command,
        f"/data/{workspace.data_file.name}",
        f"--output-dir=/data/{workspace.output_dir.name}",
        *case.simulator.extra_args,
    )


def _scan_log(text: str) -> tuple[tuple[str, ...], int, tuple[str, ...]]:
    """Разобрать лог на различающиеся предупреждения, их общее число и ошибки."""

    warnings: dict[str, None] = {}
    warning_count = 0
    errors: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker in stripped for marker in _ERROR_MARKERS):
            errors.append(stripped)
        elif any(marker in stripped for marker in _WARNING_MARKERS):
            warning_count += 1
            if len(warnings) < _MAX_DISTINCT_WARNINGS:
                warnings.setdefault(stripped, None)
    return tuple(warnings), warning_count, tuple(errors)


def run_simulation(case: Case, workspace: Workspace) -> tuple[OpmRunResult, SummaryData | None]:
    """Запустить Flow и прочитать сводку. Сводка `None`, если прогон провалился."""

    command = build_command(case, workspace)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=case.simulator.timeout_seconds,
            check=False,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except FileNotFoundError as exc:
        raise SimulationError("docker is not available on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nTIMEOUT after {case.simulator.timeout_seconds}s\n"
        exit_code = 124
    wall_seconds = time.monotonic() - started

    log_path = workspace.root / LOG_NAME
    log_text = stdout + ("\n" + stderr if stderr else "")
    log_path.write_text(log_text, encoding="utf-8")
    warnings, warning_count, errors = _scan_log(log_text)
    if exit_code != 0 and not errors:
        errors = (*errors, f"flow exited with code {exit_code}")

    output_files = tuple(
        sorted(path.name for path in workspace.output_dir.iterdir() if path.is_file())
    )

    summary: SummaryData | None = None
    report_steps = 0
    final_date: str | None = None
    stem = workspace.data_file.stem
    smspec = workspace.output_dir / f"{stem}.SMSPEC"
    unsmry = workspace.output_dir / f"{stem}.UNSMRY"
    if exit_code == 0 and smspec.is_file() and unsmry.is_file():
        summary = read_summary(smspec, unsmry)
        report_steps = summary.report_steps
        final_date = summary.dates[-1].date().isoformat() if summary.dates else None

    result = OpmRunResult(
        exit_code=exit_code,
        wall_seconds=wall_seconds,
        command=command,
        log_path=log_path,
        output_files=output_files,
        report_steps=report_steps,
        final_date=final_date,
        warnings=warnings,
        warning_count=warning_count,
        errors=errors,
    )
    return result, (summary if result.is_pass else None)
