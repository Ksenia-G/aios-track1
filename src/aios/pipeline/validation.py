"""Сквозной контур: кейс -> расписание -> OPM -> ЧДД -> пакет сабмишена.

Контур закрытый по отказу, и это свойство обеспечивается транзакционно,
а не аккуратностью вызывающего кода.

1. Прогон начинается со снятия прошлого пакета: как только запущен новый
   расчёт, старый успех перестаёт быть действующим результатом. Иначе после
   упавшего прогона на диске остаётся вчерашний `claimed_npv.json`, и по
   каталогу невозможно отличить свежий успех от несостоявшегося.
2. Вся изменяемая работа идёт в `runtime/staging`. В `output/` ничего не
   попадает, пока не пройдены все ворота.
3. Публикация -- последний шаг успешного прогона. `claimed_npv.json`
   выкладывается последним, поэтому его наличие означает, что весь пакет
   уже на месте.
4. Любое исключение в любой стадии приводит к провалу прогона, а не к
   выходу мимо финализации. Отчёт о провале помечен явно и не содержит
   ни заявленного NPV, ни файла расписания.

`KeyboardInterrupt` и `SystemExit` не перехватываются: прерывание -- это не
результат расчёта, и пакет прошлого прогона к этому моменту уже снят.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aios.cases.contract import Case, CaseError, load_case
from aios.chdd.runner import ChddError, ChddResult, run_chdd
from aios.chdd.trajectory import ChddInput, TrajectoryError, build_chdd_input, write_chdd_csv
from aios.opm.runner import OpmRunResult, SimulationError, run_simulation
from aios.opm.summary import SummaryReadError
from aios.opm.workspace import WorkspaceError, prepare_workspace
from aios.schedule.deck import DeckParseError, parse_schedule_file, parse_start_date
from aios.schedule.events import Schedule
from aios.schedule.generator import (
    GENERATOR_ID,
    GENERATOR_VERSION,
    ScheduleRenderError,
    render_schedule,
    schedule_sha256,
)
from aios.schedule.policy import DEFAULT_POLICY_ID, get_policy
from aios.schedule.state import FieldState, reconstruct_state
from aios.schedule.validator import ScheduleValidationReport, validate_schedule

PIPELINE_ID = "aios.pipeline.validation"
PIPELINE_VERSION = "1.0.0"

SCHEDULE_FILENAME = "wells_schedule.inc"
CLAIMED_NPV_FILENAME = "claimed_npv.json"
VALIDATION_REPORT_FILENAME = "validation_report.json"
RUN_MANIFEST_FILENAME = "run_manifest.json"
CHDD_INPUT_FILENAME = "chdd_input.csv"
STAGING_DIRNAME = "staging"

# Полный пакет сабмишена. Ровно эти имена снимаются в начале прогона и
# ровно эти публикуются в конце успешного.
FINAL_ARTIFACTS = (
    SCHEDULE_FILENAME,
    CLAIMED_NPV_FILENAME,
    VALIDATION_REPORT_FILENAME,
    RUN_MANIFEST_FILENAME,
)

VERDICT_READY = "READY"
VERDICT_BLOCKED = "BLOCKED"

STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"


class PipelinePublishError(RuntimeError):
    """Успешный прогон нечем опубликовать. Пакет не выкладывается частично."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class PipelineReport:
    """Состояние прогона: собирается по ходу, пишется в любом исходе."""

    case_path: Path
    policy_id: str
    case: Case | None = None
    case_loaded: bool = False
    history_loaded: bool = False
    constraints_loaded: bool = False
    schedule_valid: bool = False
    simulation_pass: bool = False
    chdd_pass: bool = False
    baseline_reconciled: bool | None = None
    schedule_sha256: str | None = None
    schedule_report: ScheduleValidationReport | None = None
    opm_result: OpmRunResult | None = None
    chdd_result: ChddResult | None = None
    trajectory_stats: dict[str, Any] | None = None
    known_wells: int = 0
    reconstructed_wells: int = 0
    schedule_steps: int = 0
    npv_difference: float | None = None
    chdd_input_path: Path | None = None
    invalidated_artifacts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def all_hard_constraints_pass(self) -> bool:
        return self.schedule_valid and (
            self.chdd_result is not None and self.chdd_result.excluded_negative_rows == 0
        )

    @property
    def verdict(self) -> str:
        gates = (
            self.case_loaded,
            self.history_loaded,
            self.constraints_loaded,
            self.schedule_valid,
            self.simulation_pass,
            self.chdd_pass,
            self.all_hard_constraints_pass,
            self.baseline_reconciled is not False,
        )
        return VERDICT_READY if all(gates) else VERDICT_BLOCKED

    @property
    def status(self) -> str:
        return STATUS_PASSED if self.verdict == VERDICT_READY else STATUS_FAILED

    @property
    def npv(self) -> float | None:
        return None if self.chdd_result is None else self.chdd_result.npv_mln_rub

    def to_json(self) -> dict[str, Any]:
        chdd = self.chdd_result
        return {
            "status": self.status,
            "pipeline": {"id": PIPELINE_ID, "version": PIPELINE_VERSION},
            "case_id": None if self.case is None else self.case.case_id,
            "policy_id": self.policy_id,
            "case_loaded": self.case_loaded,
            "history_loaded": self.history_loaded,
            "constraints_loaded": self.constraints_loaded,
            "schedule_valid": self.schedule_valid,
            "simulation_pass": self.simulation_pass,
            "chdd_pass": self.chdd_pass,
            "all_hard_constraints_pass": self.all_hard_constraints_pass,
            "baseline_reconciled": self.baseline_reconciled,
            "economic_months": None if chdd is None else chdd.economic_month_count,
            "npv": self.npv,
            "npv_unit": "mln RUB",
            "npv_difference_vs_reference": self.npv_difference,
            "provenance": None if chdd is None else chdd.provenance,
            "schedule": {
                "sha256": self.schedule_sha256,
                "steps": self.schedule_steps,
                "generator": f"{GENERATOR_ID} {GENERATOR_VERSION}",
                "gates": None if self.schedule_report is None else self.schedule_report.to_json(),
            },
            "state": {
                "known_wells": self.known_wells,
                "reconstructed_wells": self.reconstructed_wells,
            },
            "simulation": None if self.opm_result is None else self.opm_result.to_json(),
            "trajectory": self.trajectory_stats,
            "economics": None if chdd is None else chdd.to_json(),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "invalidated_previous_artifacts": list(self.invalidated_artifacts),
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """Что пайплайн положил на диск."""

    report: PipelineReport
    output_dir: Path
    written_files: tuple[Path, ...]

    @property
    def verdict(self) -> str:
        return self.report.verdict

    @property
    def schedule_path(self) -> Path:
        return self.output_dir / SCHEDULE_FILENAME


def claimed_npv_payload(report: PipelineReport) -> dict[str, Any]:
    """Содержимое `claimed_npv.json` -- контракт заявки на результат."""

    case = report.case
    chdd = report.chdd_result
    assert case is not None and chdd is not None  # проверено вызывающей стороной
    return {
        "case_id": case.case_id,
        "npv_mln_rub": chdd.npv_mln_rub,
        "provenance": chdd.provenance,
        "calculator_version": chdd.calculator_version,
        "schedule_sha256": report.schedule_sha256,
        "simulation_status": "PASS" if report.simulation_pass else "FAIL",
        "constraints_status": "PASS" if report.all_hard_constraints_pass else "FAIL",
    }


def _run_manifest(
    report: PipelineReport,
    *,
    workspace_root: Path,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    case = report.case
    inputs: dict[str, Any] = {}
    if case is not None:
        for label, path in (
            ("case", report.case_path),
            ("deck_data", case.history.data_path),
            ("deck_schedule", case.history.schedule_path),
            ("chdd_norms", case.economics.norms_workbook),
        ):
            inputs[label] = {
                "path": str(path),
                "sha256": _sha256_file(path) if path.is_file() else None,
            }
    return {
        "status": report.status,
        "pipeline": {"id": PIPELINE_ID, "version": PIPELINE_VERSION},
        "generator": {"id": GENERATOR_ID, "version": GENERATOR_VERSION},
        "policy_id": report.policy_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "case": None if case is None else case.to_json(),
        "inputs": inputs,
        "workspace": str(workspace_root),
        "chdd_input": None if report.chdd_input_path is None else str(report.chdd_input_path),
        "schedule_sha256": report.schedule_sha256,
        "simulation": None if report.opm_result is None else report.opm_result.to_json(),
        "calculator_version": None
        if report.chdd_result is None
        else report.chdd_result.calculator_version,
        "npv_mln_rub": report.npv,
        "provenance": None if report.chdd_result is None else report.chdd_result.provenance,
        "verdict": report.verdict,
    }


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def build_schedule_for_case(case: Case, *, policy_id: str) -> tuple[Schedule, FieldState, str, str]:
    """Разобрать колоду, восстановить состояние и получить текст расписания."""

    start_date = parse_start_date(case.history.data_path.read_text(encoding="utf-8"))
    deck = parse_schedule_file(case.history.schedule_path, start_date=start_date)
    state = reconstruct_state(deck, case.management_start_date)
    baseline = deck.control_schedule(case.management_start_date, case.management_end_date)
    schedule = get_policy(policy_id).plan(state, baseline)
    history_text = deck.history_text(case.management_start_date)
    schedule_text = render_schedule(schedule, case, policy_id=policy_id)
    return schedule, state, schedule_text, history_text


def invalidate_package(output_dir: Path) -> tuple[str, ...]:
    """Снять пакет прошлого прогона. Возвращает имена снятых файлов.

    Вызывается до всякой работы. С этого момента на диске нет результата,
    который можно было бы принять за итог начатого расчёта.
    """

    removed: list[str] = []
    for name in FINAL_ARTIFACTS:
        path = output_dir / name
        if path.is_file():
            path.unlink()
            removed.append(name)
    return tuple(removed)


def _execute(
    report: PipelineReport,
    *,
    policy_id: str,
    staging_dir: Path,
    workspace_root: Path,
    runtime_dir: Path,
) -> None:
    """Пройти стадии контура, складывая всё изменяемое в `staging_dir`.

    Ожидаемые отказы записываются в `report.blockers` и обрывают проход.
    Неожиданные исключения уходят наверх, где их ловит закрытый по отказу
    контур `run_validation_pipeline`.
    """

    try:
        case = load_case(report.case_path)
    except CaseError as exc:
        report.blockers.append(f"case: {exc}")
        return
    report.case = case
    report.case_loaded = True
    report.constraints_loaded = True

    try:
        schedule, state, schedule_text, history_text = build_schedule_for_case(
            case, policy_id=policy_id
        )
    except (DeckParseError, ScheduleRenderError, ValueError, OSError) as exc:
        report.blockers.append(f"schedule: {exc}")
        return

    report.history_loaded = True
    report.known_wells = len(state.known_wells)
    report.reconstructed_wells = len(state.wells)
    report.schedule_steps = len(schedule.steps)
    report.schedule_sha256 = schedule_sha256(schedule_text)

    # Расписание кладётся в staging, а не в output: файл, не прошедший ворота,
    # нужен для разбора, но сабмишеном он не является.
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / SCHEDULE_FILENAME).write_text(schedule_text, encoding="utf-8")

    schedule_report = validate_schedule(schedule, state, case)
    report.schedule_report = schedule_report
    report.schedule_valid = schedule_report.is_valid
    if not schedule_report.is_valid:
        report.blockers.extend(
            f"schedule gate {gate.gate_id}: {gate.detail}" for gate in schedule_report.failed
        )
        return

    try:
        workspace = prepare_workspace(
            case,
            root=workspace_root,
            history_text=history_text,
            schedule_text=schedule_text,
        )
        opm_result, summary = run_simulation(case, workspace)
    except (WorkspaceError, SimulationError, SummaryReadError, OSError) as exc:
        report.blockers.append(f"simulation: {exc}")
        return

    report.opm_result = opm_result
    report.simulation_pass = opm_result.is_pass
    report.warnings.extend(f"opm: {item}" for item in opm_result.warnings)
    if not opm_result.is_pass or summary is None:
        report.blockers.extend(opm_result.errors[:10] or ["simulation produced no summary"])
        return

    chdd_input: ChddInput | None = None
    try:
        chdd_input = build_chdd_input(summary, case)
        report.chdd_input_path = write_chdd_csv(chdd_input, runtime_dir / CHDD_INPUT_FILENAME)
        chdd_result = run_chdd(case, chdd_input, source_file=report.chdd_input_path)
    except (TrajectoryError, ChddError, SummaryReadError, OSError) as exc:
        report.blockers.append(f"chdd: {exc}")
        if chdd_input is not None:
            report.trajectory_stats = chdd_input.stats.to_json()
        return

    report.trajectory_stats = chdd_input.stats.to_json()
    report.chdd_result = chdd_result
    report.chdd_pass = True
    report.warnings.extend(f"chdd: {item}" for item in chdd_result.warnings)

    if case.acceptance is not None:
        difference = chdd_result.npv_mln_rub - case.acceptance.baseline_npv_mln_rub
        report.npv_difference = difference
        report.baseline_reconciled = abs(difference) <= case.acceptance.npv_tolerance_mln_rub
        if not report.baseline_reconciled:
            report.blockers.append(
                f"baseline reconciliation: NPV {chdd_result.npv_mln_rub:.6f} differs from "
                f"{case.acceptance.baseline_npv_mln_rub:.6f} by {difference:.6f} mln RUB "
                f"(tolerance {case.acceptance.npv_tolerance_mln_rub})"
            )


def run_validation_pipeline(
    case_path: str | Path,
    *,
    output_dir: Path,
    runtime_dir: Path,
    policy_id: str = DEFAULT_POLICY_ID,
) -> PipelineOutcome:
    """Пройти весь контур и опубликовать пакет сабмишена только при успехе."""

    started_at = datetime.now(UTC).isoformat()
    report = PipelineReport(case_path=Path(case_path).expanduser().resolve(), policy_id=policy_id)
    workspace_root = runtime_dir / "workspace"
    staging_dir = runtime_dir / STAGING_DIRNAME

    output_dir.mkdir(parents=True, exist_ok=True)
    report.invalidated_artifacts.extend(invalidate_package(output_dir))
    # Расписание из прошлого прогона в staging тоже снимается: публиковать
    # разрешено только то, что записал текущий проход.
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / SCHEDULE_FILENAME).unlink(missing_ok=True)

    try:
        _execute(
            report,
            policy_id=policy_id,
            staging_dir=staging_dir,
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
        )
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ: провал, а не выход мимо финализации
        report.blockers.append(f"unhandled {type(exc).__name__}: {exc}")

    return _publish(
        report,
        output_dir=output_dir,
        staging_dir=staging_dir,
        workspace_root=workspace_root,
        started_at=started_at,
    )


def _publish(
    report: PipelineReport,
    *,
    output_dir: Path,
    staging_dir: Path,
    workspace_root: Path,
    started_at: str,
) -> PipelineOutcome:
    """Разложить итог прогона.

    При успехе публикуется полный пакет, причём `claimed_npv.json` -- в самую
    последнюю очередь: его наличие означает, что остальное уже лежит рядом.
    При провале публикуются только отчёт и манифест, оба помечены `FAILED`;
    ни заявки, ни файла расписания в `output/` не появляется.
    """

    finished_at = datetime.now(UTC).isoformat()
    manifest = _run_manifest(
        report,
        workspace_root=workspace_root,
        started_at=started_at,
        finished_at=finished_at,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if report.verdict == VERDICT_READY and report.case is not None and report.chdd_result:
        staged_schedule = staging_dir / SCHEDULE_FILENAME
        if not staged_schedule.is_file():
            raise PipelinePublishError(f"staged schedule is missing: {staged_schedule}")
        published = output_dir / SCHEDULE_FILENAME
        published.write_text(staged_schedule.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(published)
        written.append(_write_json(output_dir / VALIDATION_REPORT_FILENAME, report.to_json()))
        written.append(_write_json(output_dir / RUN_MANIFEST_FILENAME, manifest))
        written.append(_write_json(output_dir / CLAIMED_NPV_FILENAME, claimed_npv_payload(report)))
    else:
        written.append(_write_json(output_dir / VALIDATION_REPORT_FILENAME, report.to_json()))
        written.append(_write_json(output_dir / RUN_MANIFEST_FILENAME, manifest))

    return PipelineOutcome(report=report, output_dir=output_dir, written_files=tuple(written))
