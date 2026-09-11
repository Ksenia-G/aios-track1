"""Штатный прогон нового кейса: кейс -> расписание -> OPM -> Guardian -> ЧДД.

Один канонический путь в репозитории, а не внешний скрипт: всё, что делает
прогон, должно быть видно и воспроизводимо из самого дерева.

Первое, что печатает прогон, -- сигнатура входной модели. Если она не совпадает
с ориентирами кейса, прогон остаётся проверкой машинерии: результат помечается
`ENGINEERING_BASELINE_ON_LEGACY_MODEL_ONLY`, `is_competition_result` ставится
в `false`, и оптимизация не запускается.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aios.agents.trajectory_guardian import TrajectoryConstraintGuardian
from aios.cases import load_case
from aios.chdd.runner import run_chdd
from aios.chdd.trajectory import build_chdd_input, write_chdd_csv
from aios.newcase.calendar import REPAIRS, WellAvailabilityCalendar
from aios.newcase.guardian import FieldTrajectoryGuardian, drop_non_flowing_violations
from aios.newcase.policy import build_monthly_schedule
from aios.newcase.signature import ModelSignature, model_signature
from aios.opm.runner import run_simulation
from aios.opm.workspace import prepare_workspace
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.events import WellRole, WellStatus
from aios.schedule.generator import render_full_schedule
from aios.state.extractor import StateExtractor, well_controls_from_schedule

ENGINEERING_LABEL = "ENGINEERING_BASELINE_ON_LEGACY_MODEL_ONLY"
COMPETITION_LABEL = "STANDARD_RUNTIME_RESULT"


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Итог одного штатного прогона."""

    signature: dict[str, Any]
    label: str
    is_competition_result: bool
    payload: dict[str, Any]


def _field_series(summary: Any, name: str) -> list[float] | None:
    try:
        return summary.field_series(name)
    except Exception:  # noqa: BLE001 -- сводка может не нести этот вектор
        for attribute in ("field", "vectors", "data"):
            store = getattr(summary, attribute, None)
            if isinstance(store, dict) and name in store:
                return store[name]
    return None


def _baseline_vector(state: Any) -> tuple[dict[str, float], dict[str, float]]:
    producers = {
        well.well: float(well.target)
        for well in state.wells
        if well.role is WellRole.PRODUCER and well.status is WellStatus.OPEN and well.target
    }
    injectors = {
        well.well: float(well.target)
        for well in state.wells
        if well.role is WellRole.INJECTOR and well.status is WellStatus.OPEN and well.target
    }
    return producers, injectors


def run(case_path: Path, *, out_dir: Path, workspace: Path) -> RunOutcome:
    case = load_case(case_path)
    state, _baseline, history_text, history_start = read_baseline_inputs(case)
    producers, injectors = _baseline_vector(state)

    signature = model_signature(liquid=sum(producers.values()), injection=sum(injectors.values()))
    print(f"NEW_CASE_MODEL_SIGNATURE = {signature.verdict}")
    print(json.dumps(signature.to_json(), ensure_ascii=False, indent=2))
    engineering = signature.verdict is not ModelSignature.MATCH
    if engineering:
        print(f"\n{ENGINEERING_LABEL}: результат прогона конкурсным не является.")

    calendar = WellAvailabilityCalendar()
    schedule = build_monthly_schedule(
        producers=producers, injectors=injectors, merit={}, calendar=calendar
    )
    text = render_full_schedule(
        schedule,
        case,
        history_text=history_text,
        policy_id="newcase.standard_runtime",
        history_start_date=history_start,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = out_dir / "wells_schedule.inc"
    schedule_path.write_text(text, encoding="utf-8")
    schedule_sha = hashlib.sha256(schedule_path.read_bytes()).hexdigest()
    print(f"\nschedule: {len(schedule.steps)} months, sha256 {schedule_sha}")

    ws = prepare_workspace(case, root=workspace, history_text="", schedule_text=text)
    result, summary = run_simulation(case, ws)
    print(f"OPM: pass={result.is_pass} exit={getattr(result, 'exit_code', None)}")
    if not result.is_pass or summary is None:
        raise SystemExit("OPM did not complete; nothing is reported")

    liquid = _field_series(summary, "FLPR")
    injection = _field_series(summary, "FWIR")
    trajectory = [
        (stamp.date(), liquid[i] if liquid else None, injection[i] if injection else None)
        for i, stamp in enumerate(summary.dates)
    ]
    field_report = FieldTrajectoryGuardian().evaluate(trajectory)
    print(f"field guardian : {field_report.checks} checks, {field_report.failed} failed")

    extractor = StateExtractor()
    states = []
    rates: dict[tuple[str, str], float | None] = {}
    for index, stamp in enumerate(summary.dates):
        day = stamp.date()
        if not case.management_start_date <= day <= case.management_end_date:
            continue
        states.append(
            extractor.extract(
                summary=summary,
                case=case,
                run_id="newcase_standard_runtime",
                simulator_digest=case.simulator.docker_image,
                schedule_hash=schedule_sha,
                well_controls=well_controls_from_schedule(state, schedule, at_date=day),
                step=index,
            )
        )
        for well in producers:
            with contextlib.suppress(Exception):
                # Скважины может не быть в сводке: тогда признака нет и проверка
                # остаётся -- молчание не повод её снимать.
                rates[(day.isoformat(), well)] = summary.well_value("WLPR", well, index)
    well_report = TrajectoryConstraintGuardian().evaluate_states(tuple(states))
    real = drop_non_flowing_violations(well_report.violations, rates)
    print(
        f"per-well guardian: {well_report.summary.total_checks} checks, "
        f"{len(real)} failed (не работавших снято: "
        f"{well_report.summary.failed_checks - len(real)})"
    )

    chdd_input = build_chdd_input(summary, case)
    csv_path = write_chdd_csv(chdd_input, out_dir / "chdd_input.csv")
    chdd = run_chdd(case, chdd_input, source_file=csv_path)
    print(f"CHDD: {chdd.npv_mln_rub!r} mln RUB")

    payload = {
        "schema": "aios.newcase.standard_run/1.0.0",
        "case_id": case.case_id,
        "label": ENGINEERING_LABEL if engineering else COMPETITION_LABEL,
        "is_competition_result": not engineering,
        "model_signature": signature.to_json(),
        "management_window": {
            "start": str(case.management_start_date),
            "end": str(case.management_end_date),
            "months": len(schedule.steps),
        },
        "repairs": len(REPAIRS),
        "schedule_sha256": schedule_sha,
        "opm": {"pass": result.is_pass, "report_states": len(summary.dates)},
        "guardian": {
            "field": field_report.to_json(),
            "per_well": {
                "checks": well_report.summary.total_checks,
                "failed": len(real),
                "suppressed_not_flowing": well_report.summary.failed_checks - len(real),
            },
        },
        "chdd_mln_rub": chdd.npv_mln_rub,
    }
    (out_dir / "standard_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return RunOutcome(signature.to_json(), payload["label"], not engineering, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newcase-run")
    parser.add_argument("--case", type=Path, default=Path("cases/field_y_2012_2018.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    outcome = run(args.case, out_dir=args.out, workspace=args.workspace)
    print(f"\nLABEL = {outcome.label}")
    print(f"is_competition_result = {str(outcome.is_competition_result).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
