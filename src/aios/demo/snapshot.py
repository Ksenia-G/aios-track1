"""Сборка снимка КТ1 из уже проверенных артефактов экспериментов.

Демонстрация не считает ничего заново. Она читает то, что уже посчитано
полными прогонами OPM Flow и официального калькулятора ЧДД, и показывает
это как есть.

Отсюда главное правило модуля: второй копии чисел здесь нет. Ни одно
значение не вписано в исходник -- всё поднимается из отслеживаемых
артефактов. Ожидаемые величины живут в тестах, где им и место: тест
обязан ловить расхождение, а демонстрация обязана показывать то, что
лежит в файлах, даже если это расходится с чьей-то памятью.

Режим отказа закрытый. Отсутствующий, битый или внутренне противоречивый
артефакт останавливает сборку. Показать «примерно то же самое» на
подставленных константах хуже, чем не показать ничего: доклад с тихо
подменённым числом невозможно отличить от честного.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROVENANCE = "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
WATER_BALANCE_STATUS = "ORGANIZER_RULE_UNRESOLVED"
PRESSURE_FLOOR_STATUS = "NO OFFICIAL FLOOR SUPPLIED"

EXPERIMENTS_DIRNAME = "experiments"
DEMO_DIRNAME = "kt1"
DEMO_SNAPSHOT_FILENAME = "demo_snapshot.json"

# Сверка значений, пришедших из разных артефактов. Допуск маленький и
# абсолютный: числа обязаны совпадать побитово с точностью до печати JSON,
# а не «примерно».
CONSISTENCY_TOLERANCE = 1e-9

CONSERVATIVE_SCENARIO_ID = "EXP003_C_SCALE_F55"

SOURCE_ARTIFACTS: tuple[str, ...] = (
    "experiments/exp001/summary.json",
    "experiments/exp002/summary.json",
    "experiments/exp003/summary.json",
    "experiments/exp003/results.csv",
    "experiments/exp003/compensation.csv",
    "experiments/exp003/kt1_snapshot.json",
)

PIPELINE_STAGES: tuple[str, ...] = (
    "CASE",
    "HISTORY + CONSTRAINTS",
    "CONTROL PLAN",
    "wells_schedule.inc",
    "OPM FLOW",
    "TRAJECTORY",
    "CHDD",
    "CONSTRAINT AUDIT",
    "VERIFIED NPV",
)


class EvidenceError(RuntimeError):
    """Канонические свидетельства нельзя прочитать или они противоречивы."""


def repository_root() -> Path:
    """Корень репозитория относительно этого файла.

    Абсолютный путь машины сюда не попадает: он вычисляется, а не хранится.
    """

    return Path(__file__).resolve().parents[3]


def _read_json(root: Path, relative: str) -> Any:
    path = root / relative
    if not path.is_file():
        raise EvidenceError(f"required evidence is missing: {relative}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise EvidenceError(f"evidence is not readable JSON: {relative}: {exc}") from exc


def _read_csv(root: Path, relative: str) -> list[dict[str, str]]:
    path = root / relative
    if not path.is_file():
        raise EvidenceError(f"required evidence is missing: {relative}")
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error) as exc:
        raise EvidenceError(f"evidence is not readable CSV: {relative}: {exc}") from exc
    if not rows:
        raise EvidenceError(f"evidence has no rows: {relative}")
    return rows


def _require_mapping(payload: Any, key: str, where: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise EvidenceError(f"{where} is not an object")
    if key not in payload:
        raise EvidenceError(f"{where}: missing required section {key!r}")
    value = payload[key]
    if not isinstance(value, Mapping):
        raise EvidenceError(f"{where}.{key} must be an object")
    return value


def _require_number(payload: Mapping[str, Any], key: str, where: str) -> float:
    if key not in payload:
        raise EvidenceError(f"{where}: missing required field {key!r}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvidenceError(f"{where}.{key} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceError(f"{where}.{key} must be finite")
    return number


def _require_text(payload: Mapping[str, Any], key: str, where: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{where}.{key} must be a non-empty string")
    return value


def _csv_number(row: Mapping[str, str], key: str, where: str) -> float:
    raw = row.get(key)
    if raw in (None, ""):
        raise EvidenceError(f"{where}: missing required column {key!r}")
    try:
        number = float(raw)
    except ValueError as exc:
        raise EvidenceError(f"{where}.{key} is not a number: {raw!r}") from exc
    if not math.isfinite(number):
        raise EvidenceError(f"{where}.{key} must be finite")
    return number


def _agree(label: str, *values: float) -> float:
    """Убедиться, что одно и то же число во всех источниках совпадает."""

    first = values[0]
    for other in values[1:]:
        if abs(first - other) > CONSISTENCY_TOLERANCE:
            raise EvidenceError(
                f"{label}: sources disagree ({first!r} vs {other!r}); evidence is inconsistent"
            )
    return first


def _row_by_id(
    rows: Sequence[Mapping[str, str]], scenario_id: str, where: str
) -> Mapping[str, str]:
    for row in rows:
        if row.get("scenario_id") == scenario_id:
            return row
    raise EvidenceError(f"{where}: no row for scenario {scenario_id!r}")


def _all_pass(row: Mapping[str, str], where: str) -> None:
    for column in ("simulation_status", "constraints_status", "chdd_status"):
        if row.get(column) != "PASS":
            raise EvidenceError(f"{where}: {column} is {row.get(column)!r}, expected PASS")


@dataclass(frozen=True, slots=True)
class StageEntry:
    """Одна строка прогрессии экспериментов."""

    stage: str
    evidence: str
    scenario_id: str | None
    npv_mln_rub: float
    delta_npv_mln_rub: float
    delta_npv_pct: float
    simulation_note: str
    # Счётчики хранятся числами, а не готовой фразой: подпись собирается
    # на стороне вывода, и её язык не должен требовать разбора текста.
    candidate_runs: int
    reconciliation_runs: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "evidence": self.evidence,
            "scenario_id": self.scenario_id,
            "npv_mln_rub": self.npv_mln_rub,
            "delta_npv_mln_rub": self.delta_npv_mln_rub,
            "delta_npv_pct": self.delta_npv_pct,
            "simulation_note": self.simulation_note,
            "candidate_runs": self.candidate_runs,
            "reconciliation_runs": self.reconciliation_runs,
        }


@dataclass(frozen=True, slots=True)
class CandidateEntry:
    """Кандидат вместе с его инженерными показателями."""

    label: str
    scenario_id: str
    schedule_sha256: str
    npv_mln_rub: float
    delta_npv_mln_rub: float
    delta_npv_pct: float
    injection_m3: float
    delta_injection_m3: float
    injection_deviation_pct: float
    mean_pressure_delta_bar: float
    final_pressure_delta_bar: float
    delta_oil_t: float
    delta_water_t: float
    controls: list[dict[str, Any]]
    supplements: list[dict[str, Any]]
    simulation_status: str
    constraints_status: str
    chdd_status: str

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "scenario_id": self.scenario_id,
            "schedule_sha256": self.schedule_sha256,
            "npv_mln_rub": self.npv_mln_rub,
            "delta_npv_mln_rub": self.delta_npv_mln_rub,
            "delta_npv_pct": self.delta_npv_pct,
            "injection_m3": self.injection_m3,
            "delta_injection_m3": self.delta_injection_m3,
            "injection_deviation_pct": self.injection_deviation_pct,
            "mean_pressure_delta_bar": self.mean_pressure_delta_bar,
            "final_pressure_delta_bar": self.final_pressure_delta_bar,
            "delta_oil_t": self.delta_oil_t,
            "delta_water_t": self.delta_water_t,
            "controls": self.controls,
            "supplements": self.supplements,
            "simulation_status": self.simulation_status,
            "constraints_status": self.constraints_status,
            "chdd_status": self.chdd_status,
        }


@dataclass(frozen=True, slots=True)
class DemoSnapshot:
    """Полный снимок для демонстрации."""

    baseline_npv_mln_rub: float
    baseline_injection_m3: float
    progression: tuple[StageEntry, ...]
    best: CandidateEntry
    conservative: CandidateEntry
    low_pressure: CandidateEntry
    robustness: dict[str, Any]
    economics: dict[str, float]
    pipeline: dict[str, Any]
    provenance: str
    water_balance_status: str
    pressure_floor_status: str
    source_artifacts: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "baseline": {
                "npv_mln_rub": self.baseline_npv_mln_rub,
                "actual_injection_m3": self.baseline_injection_m3,
            },
            "progression": [entry.to_json() for entry in self.progression],
            "exp001_best": self.progression[1].to_json(),
            "exp002_best": self.progression[2].to_json(),
            "exp003_best": self.progression[3].to_json(),
            "best_candidate": self.best.to_json(),
            "conservative": self.conservative.to_json(),
            "low_pressure_deviation": self.low_pressure.to_json(),
            "robustness": self.robustness,
            "economics_delta_vs_baseline": self.economics,
            "pipeline": self.pipeline,
            "provenance": self.provenance,
            "water_balance_status": self.water_balance_status,
            "pressure_floor_status": self.pressure_floor_status,
            "source_artifacts": list(self.source_artifacts),
        }


def _candidate_from_row(label: str, row: Mapping[str, str], where: str) -> CandidateEntry:
    _all_pass(row, where)
    scenario_id = row.get("scenario_id") or ""
    sha = row.get("schedule_sha256") or ""
    if len(sha) != 64:
        raise EvidenceError(f"{where}: schedule SHA has unexpected length")
    try:
        controls = json.loads(row.get("transfers") or "[]")
        supplements = json.loads(row.get("supplements") or "[]")
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{where}: control definition is not valid JSON: {exc}") from exc
    return CandidateEntry(
        label=label,
        scenario_id=scenario_id,
        schedule_sha256=sha,
        npv_mln_rub=_csv_number(row, "npv_mln_rub", where),
        delta_npv_mln_rub=_csv_number(row, "delta_npv_mln_rub", where),
        delta_npv_pct=_csv_number(row, "delta_npv_pct", where),
        injection_m3=_csv_number(row, "injection_m3", where),
        delta_injection_m3=_csv_number(row, "delta_injection_m3", where),
        injection_deviation_pct=_csv_number(row, "injection_deviation_pct", where),
        mean_pressure_delta_bar=_csv_number(row, "field_mean_pressure_delta_bar", where),
        final_pressure_delta_bar=_csv_number(row, "field_final_pressure_delta_bar", where),
        delta_oil_t=_csv_number(row, "delta_oil_t", where),
        delta_water_t=_csv_number(row, "delta_water_t", where),
        controls=controls,
        supplements=supplements,
        simulation_status=row["simulation_status"],
        constraints_status=row["constraints_status"],
        chdd_status=row["chdd_status"],
    )


def build_snapshot(root: Path | None = None) -> DemoSnapshot:
    """Собрать снимок и проверить согласованность источников."""

    base = root or repository_root()

    exp001 = _read_json(base, "experiments/exp001/summary.json")
    exp002 = _read_json(base, "experiments/exp002/summary.json")
    exp003 = _read_json(base, "experiments/exp003/summary.json")
    kt1 = _read_json(base, "experiments/exp003/kt1_snapshot.json")
    exp003_rows = _read_csv(base, "experiments/exp003/results.csv")
    compensation_rows = _read_csv(base, "experiments/exp003/compensation.csv")

    if not isinstance(exp001, Mapping):
        raise EvidenceError("experiments/exp001/summary.json is not an object")

    # --- происхождение и статусы ------------------------------------------
    provenance = _require_text(exp003, "provenance", "exp003/summary.json")
    kt1_provenance = _require_text(kt1, "provenance", "kt1_snapshot.json")
    if provenance != PROVENANCE or kt1_provenance != PROVENANCE:
        raise EvidenceError(
            f"provenance must be {PROVENANCE}, got {provenance!r} / {kt1_provenance!r}"
        )
    water_status = _require_text(exp003, "water_balance_status", "exp003/summary.json")
    kt1_water = _require_text(kt1, "water_balance_status", "kt1_snapshot.json")
    if water_status != WATER_BALANCE_STATUS or kt1_water != WATER_BALANCE_STATUS:
        raise EvidenceError(
            f"water balance status must be {WATER_BALANCE_STATUS}, "
            f"got {water_status!r} / {kt1_water!r}"
        )

    # --- baseline ---------------------------------------------------------
    reconciliation = _require_mapping(exp003, "reconciliation", "exp003/summary.json")
    recon_baseline = _require_mapping(reconciliation, "baseline", "exp003 reconciliation")
    baseline_npv = _agree(
        "baseline NPV",
        _require_number(exp001, "baseline_npv", "exp001/summary.json"),
        _require_number(
            _require_mapping(exp002, "baseline", "exp002/summary.json"),
            "npv_mln_rub",
            "exp002 baseline",
        ),
        _require_number(recon_baseline, "npv", "exp003 reconciliation.baseline"),
        _require_number(
            _require_mapping(kt1, "baseline", "kt1_snapshot.json"), "npv", "kt1 baseline"
        ),
    )
    if not bool(recon_baseline.get("match")):
        raise EvidenceError("exp003 reconciliation did not confirm the baseline")

    # --- лучший кандидат ---------------------------------------------------
    key_candidates = _require_mapping(exp003, "key_candidates", "exp003/summary.json")
    max_npv = _require_mapping(key_candidates, "MAX_NPV", "exp003 key_candidates")
    kt1_max = _require_mapping(kt1, "exp003_max_npv", "kt1_snapshot.json")
    best_id = _require_text(max_npv, "scenario_id", "exp003 MAX_NPV")
    if _require_text(kt1_max, "scenario_id", "kt1 exp003_max_npv") != best_id:
        raise EvidenceError("kt1_snapshot and exp003 summary disagree on the best scenario")
    best_sha = _require_text(max_npv, "schedule_sha256", "exp003 MAX_NPV")
    if _require_text(kt1_max, "schedule_sha256", "kt1 exp003_max_npv") != best_sha:
        raise EvidenceError("kt1_snapshot and exp003 summary disagree on the best schedule SHA")

    best_row = _row_by_id(exp003_rows, best_id, "exp003/results.csv")
    best = _candidate_from_row("MAX_NPV", best_row, f"exp003/results.csv[{best_id}]")
    if best.schedule_sha256 != best_sha:
        raise EvidenceError("results.csv and summary disagree on the best schedule SHA")
    exp003_npv = _agree(
        "EXP-003 best NPV",
        _require_number(max_npv, "npv_mln_rub", "exp003 MAX_NPV"),
        _require_number(kt1_max, "npv", "kt1 exp003_max_npv"),
        best.npv_mln_rub,
    )

    # --- прогрессия --------------------------------------------------------
    exp001_best_npv = _agree(
        "EXP-001 best NPV",
        _require_number(exp001, "best_npv", "exp001/summary.json"),
        _require_number(
            _require_mapping(kt1, "exp001_best", "kt1_snapshot.json"), "npv", "kt1 exp001_best"
        ),
    )
    exp002_best = _require_mapping(exp002, "overall_best_verified_candidate", "exp002/summary.json")
    exp002_best_npv = _agree(
        "EXP-002 best NPV",
        _require_number(exp002_best, "npv_mln_rub", "exp002 best"),
        _require_number(
            _require_mapping(kt1, "exp002_best", "kt1_snapshot.json"), "npv", "kt1 exp002_best"
        ),
    )
    for label, npv, previous in (
        ("EXP-001", exp001_best_npv, baseline_npv),
        ("EXP-002", exp002_best_npv, exp001_best_npv),
        ("EXP-003", exp003_npv, exp002_best_npv),
    ):
        if npv <= previous:
            raise EvidenceError(f"{label} NPV {npv} does not improve on {previous}")

    exp001_counts = _require_number(exp001, "scenario_count", "exp001/summary.json")
    exp002_counts = _require_mapping(exp002, "counts", "exp002/summary.json")
    exp003_counts = _require_mapping(exp003, "counts", "exp003/summary.json")
    exp002_candidates = (
        _require_number(exp002_counts, "stage_a", "exp002 counts")
        + _require_number(exp002_counts, "decomposition", "exp002 counts")
        + _require_number(exp002_counts, "combinations", "exp002 counts")
    )
    exp003_new = _require_number(exp003_counts, "new_simulations", "exp003 counts")
    exp003_recon = _require_number(exp003_counts, "reconciliation_simulations", "exp003 counts")
    exp003_total = _require_number(exp003_counts, "total_simulations", "exp003 counts")
    if abs(exp003_new + exp003_recon - exp003_total) > CONSISTENCY_TOLERANCE:
        raise EvidenceError("exp003 simulation counts do not add up")

    def _entry(
        stage: str,
        evidence: str,
        scenario_id: str | None,
        npv: float,
        note: str,
        candidates: int,
        reconciliation: int = 0,
    ) -> StageEntry:
        delta = npv - baseline_npv
        return StageEntry(
            stage=stage,
            evidence=evidence,
            scenario_id=scenario_id,
            npv_mln_rub=npv,
            delta_npv_mln_rub=delta,
            delta_npv_pct=delta / baseline_npv * 100.0,
            simulation_note=note,
            candidate_runs=candidates,
            reconciliation_runs=reconciliation,
        )

    progression = (
        _entry("Baseline", "validated control run", None, baseline_npv, "1 control run", 1),
        _entry(
            "EXP-001",
            "directional injection screen",
            _require_text(exp001, "best_scenario", "exp001/summary.json"),
            exp001_best_npv,
            f"{int(exp001_counts)} candidate scenarios",
            int(exp001_counts),
        ),
        _entry(
            "EXP-002",
            "boundary search + combinations",
            _require_text(exp002_best, "scenario_id", "exp002 best"),
            exp002_best_npv,
            f"{int(exp002_candidates)} candidate scenarios",
            int(exp002_candidates),
        ),
        _entry(
            "EXP-003",
            "robustness envelope",
            best_id,
            exp003_npv,
            f"{int(exp003_new)} candidate scenarios (+{int(exp003_recon)} reconciliation)",
            int(exp003_new),
            int(exp003_recon),
        ),
    )

    # --- запасные режимы ---------------------------------------------------
    conservative_row = _row_by_id(exp003_rows, CONSERVATIVE_SCENARIO_ID, "exp003/results.csv")
    conservative = _candidate_from_row(
        "CONSERVATIVE", conservative_row, f"exp003/results.csv[{CONSERVATIVE_SCENARIO_ID}]"
    )
    low_pressure_id = _require_text(
        _require_mapping(key_candidates, "LOW_PRESSURE_DEVIATION", "exp003 key_candidates"),
        "scenario_id",
        "exp003 LOW_PRESSURE_DEVIATION",
    )
    low_pressure = _candidate_from_row(
        "LOW_PRESSURE_DEVIATION",
        _row_by_id(exp003_rows, low_pressure_id, "exp003/results.csv"),
        f"exp003/results.csv[{low_pressure_id}]",
    )

    # --- робастность -------------------------------------------------------
    compensation = _row_by_id(compensation_rows, best_id, "exp003/compensation.csv")
    where = f"exp003/compensation.csv[{best_id}]"
    baseline_injection = best.injection_m3 - best.delta_injection_m3
    robustness = {
        "baseline_actual_injection_m3": baseline_injection,
        "candidate_actual_injection_m3": best.injection_m3,
        "delta_actual_injection_m3": best.delta_injection_m3,
        "injection_deviation_pct": best.injection_deviation_pct,
        "supplement_well": compensation.get("supplement_wells"),
        "supplement_rate_m3_per_day": _csv_number(
            compensation, "supplement_total_m3_per_day", where
        ),
        "scheduled_added_injection_m3": _csv_number(
            compensation, "scheduled_added_injection_m3", where
        ),
        "realized_added_injection_m3": _csv_number(
            compensation, "actual_added_injection_m3", where
        ),
        "realization_ratio": _csv_number(compensation, "supplement_realization_ratio", where),
        "supplement_well_max_wbhp_bar": _csv_number(compensation, "well42_max_wbhp_bar", where),
        "supplement_well_bhp_headroom_bar": _csv_number(compensation, "well42_headroom_bar", where),
        "supplement_well_cap_bound_states": int(
            _csv_number(compensation, "well42_bhp_hit_count", where)
        ),
        "saturation_status": compensation.get("saturation_status"),
        "mean_field_pressure_delta_bar": best.mean_pressure_delta_bar,
        "final_field_pressure_delta_bar": best.final_pressure_delta_bar,
    }
    bhp_cap = (
        robustness["supplement_well_max_wbhp_bar"] + robustness["supplement_well_bhp_headroom_bar"]
    )
    robustness["bhp_cap_bar"] = bhp_cap

    # --- экономика ---------------------------------------------------------
    decomposition = _require_mapping(exp003, "economic_decomposition", "exp003/summary.json")
    delta = _require_mapping(decomposition, "max_npv_delta", "exp003 economic_decomposition")
    economics = {
        key: _require_number(delta, key, "exp003 max_npv_delta")
        for key in (
            "revenue_m",
            "deductions_m",
            "opex_m",
            "injection_opex_m",
            "pump_capex_m",
            "profit_tax_m",
            "npv_m",
        )
    }
    if abs(economics["npv_m"] - best.delta_npv_mln_rub) > CONSISTENCY_TOLERANCE:
        raise EvidenceError("economic decomposition NPV delta disagrees with the best candidate")

    pipeline = {
        "stages": list(PIPELINE_STAGES),
        "validation_status": "PASS",
        "baseline_schedule_sha256": _require_text(
            recon_baseline, "schedule_sha256", "exp003 reconciliation.baseline"
        ),
        "simulator": "OPM Flow (pinned image), full runs",
        "economics": "official CHDD_PYTHON",
    }

    return DemoSnapshot(
        baseline_npv_mln_rub=baseline_npv,
        baseline_injection_m3=baseline_injection,
        progression=progression,
        best=best,
        conservative=conservative,
        low_pressure=low_pressure,
        robustness=robustness,
        economics=economics,
        pipeline=pipeline,
        provenance=provenance,
        water_balance_status=water_status,
        pressure_floor_status=PRESSURE_FLOOR_STATUS,
        source_artifacts=SOURCE_ARTIFACTS,
    )


def snapshot_path(root: Path | None = None) -> Path:
    base = root or repository_root()
    return base / EXPERIMENTS_DIRNAME / DEMO_DIRNAME / DEMO_SNAPSHOT_FILENAME


def serialize(snapshot: DemoSnapshot) -> str:
    """Детерминированная сериализация снимка."""

    return json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_snapshot(snapshot: DemoSnapshot, root: Path | None = None) -> Path:
    path = snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize(snapshot), encoding="utf-8")
    return path
