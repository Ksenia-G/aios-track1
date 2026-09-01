"""Исполнение прогонов: полный путь, restart-цепочка, reconciliation.

Restart -- ускорение поиска, а не другой способ считать результат. Окончательным
авторитетом для заявляемого NPV остаётся только чистый полный прогон; поэтому
каждое restart-исполнение обязано сопровождаться сверкой (reconciliation)
продолжения с чистым прогоном на общем участке горизонта.

Факты о restart-механике этой колоды установлены измерением (MAS-SPIKE-001) и
живут в ``aios.opm.restart``; этот модуль их не переоткрывает, а собирает в
исполнительную цепочку с типизированными ошибками. Соответствие шагов
устанавливается строго по дате из INTEHEAD: ближайшее состояние не
подставляется.

Reconciliation сравнивает нормализованные физические документы двух прогонов.
Дефолтный допуск -- малый численный epsilon, а не ноль: побитовая
идентичность между процессами не гарантируется окружением docker/BLAS, и
падение сверки из-за шума последнего бита измеряло бы окружение, а не физику.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from aios.cases.contract import Case
from aios.evaluation.artifacts import TrajectoryArtifactRef
from aios.evaluation.canonical import JsonValue, digest_bytes
from aios.opm.restart import (
    RESTART_SOURCE_STEM,
    RestartError,
    RestartSpec,
    enable_restart_output,
    find_restart_record,
    find_solution_include,
    prepare_restart_workspace,
    read_restart_index,
)
from aios.opm.runner import OpmRunResult, SimulationError, run_simulation
from aios.opm.summary import SummaryData, SummaryReadError
from aios.opm.workspace import Workspace, WorkspaceError, prepare_workspace

# Малый численный допуск сверки restart/full. Значение много ниже любой
# физической разницы и много выше шума округления при сериализации float.
DEFAULT_RECONCILIATION_TOLERANCE = 1e-6

# Физические векторы, входящие в нормализованный документ траектории.
_FIELD_VECTORS = ("FOPT", "FOPR", "FWPT", "FWPR", "FWIT", "FWIR")
_WELL_VECTORS = ("WBHP", "WLPR", "WOPR", "WWPR", "WWIR")

TRAJECTORY_FORMAT = "aios.execution.opm_summary"
TRAJECTORY_FORMAT_VERSION = 1


class RestartExecutionError(RuntimeError):
    """Restart-исполнение невозможно. Подстановка «похожего» запрещена."""


def normalized_summary_document(summary: SummaryData) -> dict[str, JsonValue]:
    """Нормализованный физический документ прогона.

    Только физика: даты отчётных шагов и значения векторов по скважинам и по
    полю. Никаких NPV, ЧДД, ограничений и экономических оценок -- это разные
    authority ниже по конвейеру.
    """
    field_vectors: dict[str, list[float]] = {}
    for vector in _FIELD_VECTORS:
        if summary.has_field_vector(vector):
            field_vectors[vector] = list(summary.field_series(vector))
    well_vectors: dict[str, dict[str, list[float]]] = {}
    for vector in _WELL_VECTORS:
        series = {
            well: list(summary.well_series(vector, well))
            for well in summary.wells
            if summary.has_well_vector(vector, well)
        }
        if series:
            well_vectors[vector] = series
    unit_names = (*field_vectors, *well_vectors)
    return {
        "format": TRAJECTORY_FORMAT,
        "format_version": TRAJECTORY_FORMAT_VERSION,
        "physical_only": True,
        "start": summary.start.date().isoformat(),
        "dates": [moment.date().isoformat() for moment in summary.dates],
        "wells": list(summary.wells),
        "units": {vector: summary.units.get(vector, "") for vector in unit_names},
        "field_vectors": field_vectors,
        "well_vectors": well_vectors,
    }


def serialize_trajectory_document(document: dict[str, JsonValue]) -> bytes:
    """Детерминированные байты документа: сортировка ключей, без пробелов."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


TRAJECTORY_FILENAME = "trajectory.json"
TRAJECTORY_MEDIA_TYPE = "application/json"


def write_trajectory_artifact(
    summary: SummaryData, *, workspace_root: Path, producer_id: str
) -> TrajectoryArtifactRef:
    """Записать нормализованный документ в workspace и вернуть дескриптор.

    Используется и адаптером протокола Simulator, и restart-исполнением,
    чтобы физический результат обоих путей имел один формат и одну адресацию.
    """
    data = serialize_trajectory_document(normalized_summary_document(summary))
    workspace_root = Path(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / TRAJECTORY_FILENAME).write_bytes(data)
    return TrajectoryArtifactRef(
        relative_path=TRAJECTORY_FILENAME,
        content_digest=digest_bytes(data),
        media_type=TRAJECTORY_MEDIA_TYPE,
        byte_size=len(data),
        producer_id=producer_id,
    )


@dataclass(frozen=True, slots=True)
class FullOpmRun:
    """Итог одного полного прогона OPM Flow."""

    workspace: Workspace
    run_result: OpmRunResult
    summary: SummaryData | None

    @property
    def is_pass(self) -> bool:
        return self.summary is not None and self.run_result.is_pass


def run_full_opm(
    case: Case,
    *,
    root: Path,
    history_text: str,
    schedule_text: str,
) -> FullOpmRun:
    """Собрать рабочую копию и запустить Flow. Сводка None, если прогон провален."""
    workspace = prepare_workspace(
        case, root=root, history_text=history_text, schedule_text=schedule_text
    )
    run_result, summary = run_simulation(case, workspace)
    return FullOpmRun(workspace=workspace, run_result=run_result, summary=summary)


@dataclass(frozen=True, slots=True)
class RestartChainOutcome:
    """Полная restart-цепочка: источник состояния и продолжение."""

    source_run: FullOpmRun
    unrst_path: Path
    spec: RestartSpec
    solution_include: str
    continuation_run: FullOpmRun

    @property
    def source_summary(self) -> SummaryData | None:
        return self.source_run.summary

    @property
    def continuation_summary(self) -> SummaryData | None:
        return self.continuation_run.summary


def run_restart_chain(
    case: Case,
    *,
    root: Path,
    history_text: str,
    full_schedule_text: str,
    restart_date: date,
) -> RestartChainOutcome:
    """Продолжить горизонт с сохранённого состояния на ``restart_date``.

    ``full_schedule_text`` -- полное расписание (история + окно), ровно то же,
    что у опорного полного прогона: пропуск уже пройденной части задаётся
    SKIPREST, а не вырезанием строк. Источник состояний готовится здесь же
    повторным RPTRST внутри окна, чтобы цепочка была самодостаточной.
    """
    try:
        source_run = run_full_opm(
            case,
            root=root / "source",
            history_text=history_text,
            schedule_text=enable_restart_output(full_schedule_text),
        )
        if source_run.summary is None:
            raise RestartExecutionError(
                f"restart source run failed: {source_run.run_result.to_json()['status']}"
            )
        unrst_path = (
            source_run.workspace.output_dir / f"{source_run.workspace.data_file.stem}.UNRST"
        )
        records = read_restart_index(unrst_path)
        record = find_restart_record(records, restart_date)
        data_text = case.history.data_path.read_text(encoding="utf-8")
        solution_include = find_solution_include(data_text)
        spec = RestartSpec(
            source_stem=RESTART_SOURCE_STEM, seqnum=record.seqnum, restart_date=restart_date
        )
        continuation_workspace = prepare_restart_workspace(
            case,
            root=root / "continuation",
            history_text=history_text,
            schedule_text=full_schedule_text,
            solution_include=solution_include,
            spec=spec,
            restart_source=unrst_path,
        )
        continuation_result, continuation_summary = run_simulation(case, continuation_workspace)
    except RestartExecutionError:
        raise
    except (RestartError, WorkspaceError, SummaryReadError, SimulationError, OSError) as error:
        raise RestartExecutionError(f"{type(error).__name__}: {error}") from error
    return RestartChainOutcome(
        source_run=source_run,
        unrst_path=unrst_path,
        spec=spec,
        solution_include=solution_include,
        continuation_run=FullOpmRun(
            workspace=continuation_workspace,
            run_result=continuation_result,
            summary=continuation_summary,
        ),
    )


class ReconciliationVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Сверка продолжения с чистым прогоном на общем участке горизонта."""

    verdict: ReconciliationVerdict
    tolerance: float
    compared_values: int
    max_abs_deviation: float
    worst_date: str | None
    worst_vector: str | None
    deviations_by_vector: dict[str, float]
    missing_overlap: tuple[str, ...]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "verdict": str(self.verdict),
            "tolerance": self.tolerance,
            "compared_values": self.compared_values,
            "max_abs_deviation": self.max_abs_deviation,
            "worst_date": self.worst_date,
            "worst_vector": self.worst_vector,
            "deviations_by_vector": dict(sorted(self.deviations_by_vector.items())),
            "missing_overlap": list(self.missing_overlap),
        }


def reconcile_documents(
    full_document: dict[str, JsonValue],
    continuation_document: dict[str, JsonValue],
    *,
    tolerance: float = DEFAULT_RECONCILIATION_TOLERANCE,
) -> ReconciliationReport:
    """Сравнить два нормализованных документа на пересечении дат.

    Сверяются только векторы, присутствующие в обоих прогонах; векторы,
    отсутствующие в одном из них на общем участке, перечисляются в
    ``missing_overlap`` и роняют вердикт: молча пропустить канал нельзя.
    """
    full_dates = [str(day) for day in full_document.get("dates", [])]
    continuation_dates = [str(day) for day in continuation_document.get("dates", [])]
    common_dates = sorted(set(full_dates) & set(continuation_dates))
    full_index = {day: index for index, day in enumerate(full_dates)}
    continuation_index = {day: index for index, day in enumerate(continuation_dates)}

    compared = 0
    max_deviation = 0.0
    worst_date: str | None = None
    worst_vector: str | None = None
    deviations_by_vector: dict[str, float] = {}
    missing: list[str] = []

    def compare_series(label: str, first: list[float], second: list[float]) -> None:
        nonlocal compared, max_deviation, worst_date, worst_vector
        deviation = 0.0
        for day in common_dates:
            difference = abs(first[full_index[day]] - second[continuation_index[day]])
            deviation = max(deviation, difference)
            compared += 1
            if difference > max_deviation:
                max_deviation = difference
                worst_date = day
                worst_vector = label
        deviations_by_vector[label] = deviation

    full_field = full_document.get("field_vectors", {})
    continuation_field = continuation_document.get("field_vectors", {})
    for vector in sorted(set(full_field) | set(continuation_field)):
        first = full_field.get(vector)
        second = continuation_field.get(vector)
        if first is None or second is None:
            missing.append(f"field:{vector}")
            continue
        compare_series(f"field:{vector}", first, second)

    full_wells = full_document.get("well_vectors", {})
    continuation_wells = continuation_document.get("well_vectors", {})
    for vector in sorted(set(full_wells) | set(continuation_wells)):
        first_by_well = full_wells.get(vector, {})
        second_by_well = continuation_wells.get(vector, {})
        for well in sorted(set(first_by_well) | set(second_by_well)):
            first = first_by_well.get(well)
            second = second_by_well.get(well)
            if first is None or second is None:
                missing.append(f"well:{vector}:{well}")
                continue
            compare_series(f"well:{vector}:{well}", first, second)

    if not common_dates:
        missing.append("dates:no-overlap")
    verdict = (
        ReconciliationVerdict.PASS
        if not missing and max_deviation <= tolerance
        else ReconciliationVerdict.FAIL
    )
    return ReconciliationReport(
        verdict=verdict,
        tolerance=tolerance,
        compared_values=compared,
        max_abs_deviation=max_deviation,
        worst_date=worst_date,
        worst_vector=worst_vector,
        deviations_by_vector=deviations_by_vector,
        missing_overlap=tuple(missing),
    )


def reconcile_summaries(
    full_summary: SummaryData,
    continuation_summary: SummaryData,
    *,
    tolerance: float = DEFAULT_RECONCILIATION_TOLERANCE,
) -> ReconciliationReport:
    """Сверка двух прогонов по их сводкам."""
    return reconcile_documents(
        normalized_summary_document(full_summary),
        normalized_summary_document(continuation_summary),
        tolerance=tolerance,
    )
