"""EXP-003: одна команда, пять стадий, один конверт робастности.

Порядок стадий не декоративный. Сначала сверка: если контроль и победитель
EXP-002 не воспроизводятся, все дальнейшие разности не с чем сравнивать, и
эксперимент останавливается. Затем уточнение по каждому направлению,
комбинированный квад на уточнённых долях, ослабление всего квада разом,
и только потом компенсация закачки -- она единственная меняет суммарный
плановый объём и потому идёт последней, когда остальное уже измерено.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any

from aios.cases import load_case
from aios.experiments.blocks import all_injectors
from aios.experiments.exp002 import CandidateType, _float_or_none
from aios.experiments.exp003 import (
    BASELINE_NPV_MLN_RUB,
    BASELINE_SCHEDULE_SHA256,
    COMPENSATION_COLUMNS,
    COMPENSATION_TRIGGER_SHORTFALL_M3,
    EXP001_BEST_NPV_MLN_RUB,
    EXP002_WINNER_NPV_MLN_RUB,
    EXP002_WINNER_SCHEDULE_SHA256,
    PARETO_COLUMNS,
    PRIMARY_SUPPLEMENT_WELL,
    RECONCILE_TOLERANCE,
    RESULT_COLUMNS,
    SCALED_COLUMNS,
    SCALING_ALPHAS,
    STAGE_A_COLUMNS,
    Exp003Scenario,
    KeyCandidate,
    RefinementClass,
    WaterBalanceStatus,
    classify_refinement,
    compensation_scenarios,
    decomposition_delta,
    exp002_winner_transfers,
    headroom_ranking,
    kt1_snapshot,
    nearest_by_injection,
    pareto_frontier,
    pressure_comparison,
    refined_quad_scenario,
    scaled_quad_scenarios,
    select_key_candidates,
    split_compensation_scenarios,
    stage_a_scenarios,
    water_comparison,
    write_csv,
)
from aios.experiments.exp003 import (
    reconciliation_scenario as exp002_winner_scenario,
)
from aios.experiments.exp003_runner import (
    Exp003Result,
    run_exp003_batch,
    run_exp003_scenario,
)
from aios.pipeline.validation import read_baseline_inputs

DEFAULT_CASE = Path("cases/model_y_validation.json")
DEFAULT_OUTPUT = Path("experiments/exp003")
DEFAULT_RUNTIME = Path("experiments/exp003/runtime")
MAX_NEW_SIMULATIONS = 40

OUTPUT_FILES = (
    "stage_a.csv",
    "scaled_quad.csv",
    "compensation.csv",
    "pareto.csv",
    "results.csv",
    "summary.json",
    "kt1_snapshot.json",
)


class ReconciliationError(RuntimeError):
    """Опорные значения не воспроизведены. Эксперимент не запускается."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp003",
        description="EXP-003 -- конверт робастности вокруг победителя EXP-002.",
    )
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--parallelism", type=int, default=4)
    parser.add_argument("--skip-compensation", action="store_true")
    return parser


def _progress(stage: str, total: int) -> Any:
    lock = threading.Lock()
    done = [0]

    def report(result: Exp003Result) -> None:
        with lock:
            done[0] += 1
            npv = result.npv_mln_rub
            value = f"{npv:.6f}" if npv is not None else "FAILED"
            print(
                f"  [{stage} {done[0]:>2}/{total}] {result.scenario.scenario_id:<62} "
                f"{value:>14} {result.wall_seconds:6.1f}s",
                flush=True,
            )

    return report


def _control_scenario() -> Exp003Scenario:
    return Exp003Scenario(
        scenario_id="EXP003_R_BASELINE",
        stage="R",
        candidate_type=CandidateType.ADMISSIBLE,
        transfers=(),
        label="baseline control",
        # Контроль обязан выдать тот же файл, что принятый baseline, включая
        # метку политики в шапке -- иначе SHA не совпадёт.
        render_policy_id="BASELINE_CONTINUATION",
    )


def _run_one(
    scenario: Exp003Scenario,
    case: Any,
    *,
    runtime_root: Path,
    baseline_metrics: Any,
    injector_wells: tuple[str, ...],
    keep_workspace: bool = False,
) -> Exp003Result:
    state, baseline, history_text = read_baseline_inputs(case)
    return run_exp003_scenario(
        scenario,
        case,
        runtime_root=runtime_root,
        state=state,
        baseline=baseline,
        history_text=history_text,
        baseline_metrics=baseline_metrics,
        injector_wells=injector_wells,
        keep_workspace=keep_workspace,
    )


def _reconcile(result: Exp003Result, *, npv: float, sha: str, what: str) -> None:
    if not result.is_pass:
        raise ReconciliationError(f"{what} run did not pass: {'; '.join(result.notes)}")
    if result.schedule_sha256 != sha:
        raise ReconciliationError(f"{what} schedule SHA {result.schedule_sha256} != {sha}")
    actual = result.npv_mln_rub
    if actual is None or abs(actual - npv) > RECONCILE_TOLERANCE:
        raise ReconciliationError(f"{what} NPV {actual} != {npv}")


def _best_fraction_per_direction(
    rows: list[dict[str, Any]],
) -> tuple[dict[tuple[int, str, str], float], dict[str, str]]:
    """Лучшая уточнённая доля по каждому направлению и класс поведения."""

    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (int(row["block"]), str(row["donor"]), str(row["receiver"]))
        grouped.setdefault(key, []).append(row)

    best: dict[tuple[int, str, str], float] = {}
    classes: dict[str, str] = {}
    for key, group in grouped.items():
        usable = [r for r in group if _float_or_none(r.get("delta_npv_mln_rub")) is not None]
        if not usable:
            continue
        winner = max(usable, key=lambda r: (float(r["delta_npv_mln_rub"]), -float(r["fraction"])))
        best[key] = float(winner["fraction"])
        points = [
            (
                float(r["fraction"]),
                float(r["delta_npv_mln_rub"]),
                _bhp_hits(r),
            )
            for r in usable
        ]
        classes[f"B{key[0]}_{key[1]}_TO_{key[2]}"] = classify_refinement(points).value
    return best, classes


def _bhp_hits(row: dict[str, Any]) -> int:
    """Насыщение получателя -- именно его забойное давление ограничивает перенос."""

    return int(row.get("receiver_bhp_hit_count") or 0)


def _attach_stage_a_fields(row: dict[str, Any], result: Exp003Result) -> dict[str, Any]:
    spec = result.scenario.transfers[0]
    saturation = result.saturation or {}
    donor = saturation.get(spec.donor, {})
    receiver = saturation.get(spec.receiver, {})
    row.update(
        {
            "block": spec.block_id,
            "donor": spec.donor,
            "receiver": spec.receiver,
            "fraction": spec.fraction,
            "donor_max_wbhp_bar": donor.get("max_wbhp_bar"),
            "donor_bhp_hit_count": int(donor.get("hit_count") or 0),
            "receiver_max_wbhp_bar": receiver.get("max_wbhp_bar"),
            "receiver_bhp_hit_count": int(receiver.get("hit_count") or 0),
        }
    )
    return row


def _compensation_fields(
    row: dict[str, Any],
    result: Exp003Result,
    case: Any,
    *,
    reference_row: dict[str, Any],
) -> dict[str, Any]:
    """Дополнить строку компенсации её собственными величинами.

    `reference_row` -- тот же квад без компенсации. Реализованная добавка
    меряется относительно него: относительно baseline в неё попал бы весь
    эффект переносов, и «реализация добавки» перестала бы быть про добавку.
    """

    supplements = result.scenario.supplements
    total_rate = sum(item.added_rate_m3_per_day for item in supplements)
    days = (case.management_end_date - case.management_start_date).days
    scheduled = total_rate * days
    saturation = (result.saturation or {}).get(PRIMARY_SUPPLEMENT_WELL, {})
    hits = int(saturation.get("hit_count") or 0)

    field_delta = _float_or_none(row.get("delta_injection_m3"))
    reference_delta = _float_or_none(reference_row.get("delta_injection_m3"))
    actual_added = (
        None if field_delta is None or reference_delta is None else field_delta - reference_delta
    )
    row.update(
        {
            "supplement_wells": ",".join(item.well for item in supplements),
            "supplement_total_m3_per_day": total_rate,
            "scheduled_added_injection_m3": scheduled,
            "actual_added_injection_m3": actual_added,
            "supplement_realization_ratio": None
            if actual_added is None or not scheduled
            else actual_added / scheduled,
            "field_delta_injection_vs_baseline_m3": field_delta,
            "well42_max_wbhp_bar": saturation.get("max_wbhp_bar"),
            "well42_bhp_hit_count": hits,
            "well42_headroom_bar": saturation.get("headroom_bar"),
            "saturation_status": "BHP_SATURATED" if hits > 0 else "HEADROOM_REMAINS",
        }
    )
    return row


def _pareto_row(row: dict[str, Any], source: str, labels: list[str]) -> dict[str, Any]:
    return {
        "scenario_id": row["scenario_id"],
        "source": source,
        "npv_mln_rub": row.get("npv_mln_rub"),
        "delta_npv_mln_rub": row.get("delta_npv_mln_rub"),
        "delta_npv_pct": row.get("delta_npv_pct"),
        "delta_injection_m3": row.get("delta_injection_m3"),
        "injection_deviation_pct": row.get("injection_deviation_pct"),
        "field_mean_pressure_delta_bar": row.get("field_mean_pressure_delta_bar"),
        "field_final_pressure_delta_bar": row.get("field_final_pressure_delta_bar"),
        "delta_oil_t": row.get("delta_oil_t"),
        "delta_water_t": row.get("delta_water_t"),
        "schedule_sha256": row.get("schedule_sha256"),
        "key_labels": ";".join(labels),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output.expanduser().resolve()
    runtime_root = args.runtime.expanduser().resolve()
    # Кейс читается ДО снятия прошлых артефактов. Порядок принципиален:
    # если кейс не настроен, прогон не начинается, и результаты прошлого
    # эксперимента обязаны остаться на месте. Снимать их раньше означало бы
    # уничтожать свидетельства из-за незаданной переменной окружения.
    case = load_case(args.case)

    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILES:
        (output_dir / name).unlink(missing_ok=True)

    injectors = all_injectors()
    started = time.monotonic()
    simulations = 0

    # --- сверка -----------------------------------------------------------
    print("EXP-003 — reconciliation", flush=True)
    control = _run_one(
        _control_scenario(),
        case,
        runtime_root=runtime_root,
        baseline_metrics=None,
        injector_wells=injectors,
        keep_workspace=True,
    )
    simulations += 1
    try:
        _reconcile(control, npv=BASELINE_NPV_MLN_RUB, sha=BASELINE_SCHEDULE_SHA256, what="baseline")
    except ReconciliationError as exc:
        print(f"\nRECONCILIATION FAILED: {exc}\n\nVERDICT:\nEXP_003_BLOCKED\n")
        return 2
    print(f"  baseline NPV {control.npv_mln_rub!r} sha {control.schedule_sha256[:16]}", flush=True)

    winner = _run_one(
        exp002_winner_scenario(),
        case,
        runtime_root=runtime_root,
        baseline_metrics=control.metrics,
        injector_wells=injectors,
    )
    simulations += 1
    try:
        _reconcile(
            winner,
            npv=EXP002_WINNER_NPV_MLN_RUB,
            sha=EXP002_WINNER_SCHEDULE_SHA256,
            what="EXP-002 winner",
        )
    except ReconciliationError as exc:
        print(f"\nRECONCILIATION FAILED: {exc}\n\nVERDICT:\nEXP_003_BLOCKED\n")
        return 2
    print(f"  EXP-002 winner NPV {winner.npv_mln_rub!r} sha {winner.schedule_sha256[:16]}\n")

    from aios.experiments.exp003 import exp003_row

    rows: list[dict[str, Any]] = [exp003_row(winner, control)]

    # --- стадия A ---------------------------------------------------------
    stage_a = stage_a_scenarios()
    print(f"stage A — local refinement: {len(stage_a)} scenarios", flush=True)
    stage_a_results = run_exp003_batch(
        stage_a,
        case,
        runtime_root=runtime_root,
        baseline_metrics=control.metrics,
        injector_wells=injectors,
        max_parallelism=args.parallelism,
        on_result=_progress("A", len(stage_a)),
    )
    simulations += len(stage_a_results)
    stage_a_rows = [
        _attach_stage_a_fields(exp003_row(result, control), result) for result in stage_a_results
    ]
    best_fractions, refinement_classes = _best_fraction_per_direction(stage_a_rows)
    for row in stage_a_rows:
        key = f"B{row['block']}_{row['donor']}_TO_{row['receiver']}"
        row["refinement_class"] = refinement_classes.get(
            key, RefinementClass.INSUFFICIENT_DATA.value
        )
    rows.extend(stage_a_rows)
    write_csv(output_dir / "stage_a.csv", stage_a_rows, STAGE_A_COLUMNS)

    # --- стадия B ---------------------------------------------------------
    quad = refined_quad_scenario(best_fractions)
    print(f"\nstage B — refined quad: {quad.scenario_id}", flush=True)
    quad_result = _run_one(
        quad,
        case,
        runtime_root=runtime_root,
        baseline_metrics=control.metrics,
        injector_wells=injectors,
    )
    simulations += 1
    quad_row = exp003_row(quad_result, control)
    rows.append(quad_row)
    print(
        f"  refined quad NPV {quad_result.npv_mln_rub} "
        f"(EXP-002 winner {EXP002_WINNER_NPV_MLN_RUB})",
        flush=True,
    )

    winner_row = rows[0]
    best_quad_row = max(
        (r for r in (quad_row, winner_row) if r.get("npv_mln_rub") is not None),
        key=lambda r: float(r["npv_mln_rub"]),
    )
    base_transfers = quad.transfers if best_quad_row is quad_row else exp002_winner_transfers()

    # --- стадия C ---------------------------------------------------------
    scaled = scaled_quad_scenarios(base_transfers)
    print(f"\nstage C — strength scaling: {len(scaled)} scenarios", flush=True)
    scaled_results = run_exp003_batch(
        scaled,
        case,
        runtime_root=runtime_root,
        baseline_metrics=control.metrics,
        injector_wells=injectors,
        max_parallelism=args.parallelism,
        on_result=_progress("C", len(scaled)),
    )
    simulations += len(scaled_results)
    scaled_rows = []
    for alpha, result in zip(SCALING_ALPHAS, scaled_results, strict=True):
        row = exp003_row(result, control)
        row["alpha"] = alpha
        scaled_rows.append(row)
    full_row = dict(best_quad_row)
    full_row["alpha"] = 1.0
    write_csv(output_dir / "scaled_quad.csv", [*scaled_rows, full_row], SCALED_COLUMNS)
    rows.extend(scaled_rows)

    # --- стадия D ---------------------------------------------------------
    compensation_rows: list[dict[str, Any]] = []
    if not args.skip_compensation:
        comp = compensation_scenarios(base_transfers, well=PRIMARY_SUPPLEMENT_WELL)
        print(f"\nstage D — supplemental injection at {PRIMARY_SUPPLEMENT_WELL}: {len(comp)}")
        comp_results = run_exp003_batch(
            comp,
            case,
            runtime_root=runtime_root,
            baseline_metrics=control.metrics,
            injector_wells=injectors,
            max_parallelism=args.parallelism,
            on_result=_progress("D", len(comp)),
        )
        simulations += len(comp_results)
        for result in comp_results:
            row = _compensation_fields(
                exp003_row(result, control), result, case, reference_row=best_quad_row
            )
            compensation_rows.append(row)
        rows.extend(compensation_rows)

        # --- стадия E (по условию) ---------------------------------------
        strongest = comp_results[-1] if comp_results else None
        secondary_rows: list[dict[str, Any]] = []
        if strongest is not None and strongest.is_pass:
            shortfall = -float(compensation_rows[-1].get("delta_injection_m3") or 0.0)
            saturated = int(compensation_rows[-1].get("well42_bhp_hit_count") or 0) > 0
            if shortfall > COMPENSATION_TRIGGER_SHORTFALL_M3 or saturated:
                table = strongest.saturation or {}
                used = {w for t in base_transfers for w in t.wells} | {PRIMARY_SUPPLEMENT_WELL}
                ranked = headroom_ranking(table, exclude=sorted(used))
                picked = [well for well, _headroom in ranked[:2]]
                print(
                    f"\nstage E — secondary compensation; headroom picks {picked} "
                    f"(shortfall {shortfall:.0f} m3, well42 saturated={saturated})",
                    flush=True,
                )
                allocations = []
                if picked:
                    allocations.append(((PRIMARY_SUPPLEMENT_WELL, 100.0), (picked[0], 50.0)))
                    allocations.append(((PRIMARY_SUPPLEMENT_WELL, 100.0), (picked[0], 100.0)))
                if len(picked) > 1:
                    allocations.append(
                        (
                            (PRIMARY_SUPPLEMENT_WELL, 100.0),
                            (picked[0], 100.0),
                            (picked[1], 100.0),
                        )
                    )
                    allocations.append(
                        (
                            (PRIMARY_SUPPLEMENT_WELL, 100.0),
                            (picked[0], 150.0),
                            (picked[1], 150.0),
                        )
                    )
                allocations = allocations[:4]
                if allocations and simulations + len(allocations) <= MAX_NEW_SIMULATIONS + 2:
                    secondary = split_compensation_scenarios(
                        base_transfers, allocations=allocations
                    )
                    secondary_results = run_exp003_batch(
                        secondary,
                        case,
                        runtime_root=runtime_root,
                        baseline_metrics=control.metrics,
                        injector_wells=injectors,
                        max_parallelism=args.parallelism,
                        on_result=_progress("E", len(secondary)),
                    )
                    simulations += len(secondary_results)
                    for result in secondary_results:
                        row = _compensation_fields(
                            exp003_row(result, control), result, case, reference_row=best_quad_row
                        )
                        secondary_rows.append(row)
                    compensation_rows.extend(secondary_rows)
                    rows.extend(secondary_rows)
        write_csv(output_dir / "compensation.csv", compensation_rows, COMPENSATION_COLUMNS)

    # --- сводки -----------------------------------------------------------
    write_csv(output_dir / "results.csv", _rank(rows), RESULT_COLUMNS)

    base_injection = float(rows[0]["injection_m3"]) - float(rows[0]["delta_injection_m3"])
    pool = [r for r in rows if r.get("npv_mln_rub") is not None]
    frontier = pareto_frontier(pool)
    keys = select_key_candidates(pool, baseline_injection_m3=base_injection)
    nearest = nearest_by_injection(pool, baseline_injection_m3=base_injection)

    labels_by_id: dict[str, list[str]] = {}
    for label, row in keys.items():
        if row is not None:
            labels_by_id.setdefault(str(row["scenario_id"]), []).append(label)
    pareto_rows = [
        _pareto_row(row, "EXP003", labels_by_id.get(str(row["scenario_id"]), []))
        for row in frontier
    ]
    write_csv(output_dir / "pareto.csv", pareto_rows, PARETO_COLUMNS)

    max_npv_row = keys[KeyCandidate.MAX_NPV.value]
    near_row = keys[KeyCandidate.NEAR_BASELINE_INJECTION.value]
    low_p_row = keys[KeyCandidate.LOW_PRESSURE_DEVIATION.value]

    snapshot = kt1_snapshot(
        baseline_npv=BASELINE_NPV_MLN_RUB,
        exp001_best_npv=EXP001_BEST_NPV_MLN_RUB,
        exp002_best_npv=EXP002_WINNER_NPV_MLN_RUB,
        max_npv_row=max_npv_row,
        near_baseline_row=near_row,
        simulation_count_total=simulations,
        all_full_opm=all(r.get("simulation_status") == "PASS" for r in pool),
        all_chdd_verified=all(r.get("chdd_status") == "PASS" for r in pool),
    )
    (output_dir / "kt1_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    decomposition: dict[str, Any] = {
        "baseline": control.decomposition,
        "exp002_winner": winner.decomposition,
        "exp002_winner_delta": decomposition_delta(
            winner.decomposition or {}, control.decomposition or {}
        ),
    }
    for label, row in (
        (KeyCandidate.MAX_NPV.value, max_npv_row),
        (KeyCandidate.NEAR_BASELINE_INJECTION.value, near_row),
    ):
        parts = _decomposition_for(row, runtime_root)
        decomposition[label.lower()] = parts
        decomposition[f"{label.lower()}_delta"] = (
            None if parts is None else decomposition_delta(parts, control.decomposition or {})
        )
    by_id = {r["scenario_id"]: r for r in rows}
    summary = {
        "reconciliation": {
            "baseline": {
                "npv": control.npv_mln_rub,
                "schedule_sha256": control.schedule_sha256,
                "expected_npv": BASELINE_NPV_MLN_RUB,
                "expected_sha256": BASELINE_SCHEDULE_SHA256,
                "match": True,
            },
            "exp002_winner": {
                "npv": winner.npv_mln_rub,
                "schedule_sha256": winner.schedule_sha256,
                "expected_npv": EXP002_WINNER_NPV_MLN_RUB,
                "expected_sha256": EXP002_WINNER_SCHEDULE_SHA256,
                "match": True,
            },
        },
        "refinement_classes": refinement_classes,
        "best_fractions": {f"B{k[0]}_{k[1]}_TO_{k[2]}": v for k, v in best_fractions.items()},
        "refined_quad": {
            "scenario_id": quad.scenario_id,
            "npv": quad_result.npv_mln_rub,
            "better_than_exp002_winner": (quad_result.npv_mln_rub or 0.0)
            > EXP002_WINNER_NPV_MLN_RUB,
        },
        "key_candidates": {
            label: None if row is None else _key_summary(row) for label, row in keys.items()
        },
        "nearest_by_injection": None if nearest is None else _key_summary(nearest),
        "pareto_size": len(pareto_rows),
        "water_accounting": {
            "basis": "surface/model water accounting; NOT voidage replacement",
            "baseline": None if control.water is None else control.water.to_json(),
            "exp002_winner_vs_baseline": None
            if control.water is None or winner.water is None
            else water_comparison(winner.water, control.water),
            "max_npv_vs_baseline": _water_for(by_id, max_npv_row, control, runtime_root),
        },
        "pressure": {
            "baseline": control.pressure,
            "exp002_winner": winner.pressure,
            "exp002_winner_delta": None
            if control.pressure is None or winner.pressure is None
            else pressure_comparison(winner.pressure, control.pressure),
        },
        "economic_decomposition": decomposition,
        "water_balance_status": WaterBalanceStatus.ORGANIZER_RULE_UNRESOLVED.value,
        "counts": {
            "new_simulations": simulations - 2,
            "reconciliation_simulations": 2,
            "total_simulations": simulations,
            "limit": MAX_NEW_SIMULATIONS,
            "stage_a": len(stage_a_rows),
            "scaled": len(scaled_rows),
            "compensation": len(compensation_rows),
        },
        "parallelism": args.parallelism,
        "wall_seconds": round(time.monotonic() - started, 1),
        "provenance": "NOT_OFFICIAL_PENDING_TNAV_REFERENCE",
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    _print_report(
        rows=rows,
        frontier=pareto_rows,
        max_npv_row=max_npv_row,
        near_row=near_row,
        low_p_row=low_p_row,
        nearest=nearest,
        simulations=simulations,
        parallelism=args.parallelism,
        wall=time.monotonic() - started,
    )
    return 0


def _water_for(
    by_id: dict[str, Any], row: Any, control: Exp003Result, runtime_root: Path
) -> dict[str, Any] | None:
    if row is None or control.water is None:
        return None
    path = runtime_root / str(row["scenario_id"]) / "result.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text("utf-8"))
    water = payload.get("water_accounting")
    if not water:
        return None
    return {
        "basis": "surface/model water accounting; NOT voidage replacement",
        "candidate_produced_water_m3": water["produced_water_m3"],
        "candidate_injected_water_m3": water["injected_water_m3"],
        "baseline_produced_water_m3": control.water.produced_water_m3,
        "baseline_injected_water_m3": control.water.injected_water_m3,
        "delta_produced_water_m3": water["produced_water_m3"] - control.water.produced_water_m3,
        "delta_injected_water_m3": water["injected_water_m3"] - control.water.injected_water_m3,
        "delta_injection_minus_production_m3": water["injection_minus_production_m3"]
        - control.water.injection_minus_production_m3,
    }


def _decomposition_for(row: Any, runtime_root: Path) -> dict[str, float] | None:
    """Слагаемые ЧДД кандидата из его же записанного результата."""

    if row is None:
        return None
    path = runtime_root / str(row["scenario_id"]) / "result.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text("utf-8"))
    return payload.get("economic_decomposition")


def _key_summary(row: Any) -> dict[str, Any]:
    return {
        "scenario_id": row["scenario_id"],
        "schedule_sha256": row.get("schedule_sha256"),
        "npv_mln_rub": row.get("npv_mln_rub"),
        "delta_npv_mln_rub": row.get("delta_npv_mln_rub"),
        "delta_npv_pct": row.get("delta_npv_pct"),
        "delta_injection_m3": row.get("delta_injection_m3"),
        "injection_deviation_pct": row.get("injection_deviation_pct"),
        "field_mean_pressure_delta_bar": row.get("field_mean_pressure_delta_bar"),
        "field_final_pressure_delta_bar": row.get("field_final_pressure_delta_bar"),
        "delta_oil_t": row.get("delta_oil_t"),
        "delta_water_t": row.get("delta_water_t"),
        "transfers": row.get("transfers"),
        "supplements": row.get("supplements"),
        "water_balance_status": WaterBalanceStatus.ORGANIZER_RULE_UNRESOLVED.value,
    }


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed = [r for r in rows if r.get("npv_mln_rub") is not None]
    failed = [r for r in rows if r.get("npv_mln_rub") is None]
    passed.sort(key=lambda r: (-float(r["npv_mln_rub"]), r["scenario_id"]))
    failed.sort(key=lambda r: r["scenario_id"])
    return [*passed, *failed]


def _print_report(**kwargs: Any) -> None:
    rows = kwargs["rows"]
    lines = ["", "AIOS TRACK 1 — EXP-003 ROBUSTNESS ENVELOPE", ""]
    lines.append(f"Baseline NPV: {BASELINE_NPV_MLN_RUB:.6f} mln RUB")
    lines.append(f"EXP-002 winner: {EXP002_WINNER_NPV_MLN_RUB:.6f} mln RUB")
    lines.append("")
    lines.append("TOP 10 by NPV")
    header = f"{'scenario':<58} {'NPV':>13} {'ΔNPV':>11} {'Δinj m3':>12} {'ΔmeanFPR':>10}"
    lines.append(header)
    for row in _rank(rows)[:10]:
        lines.append(
            f"{row['scenario_id']:<58} {float(row['npv_mln_rub']):>13.4f} "
            f"{float(row['delta_npv_mln_rub']):>11.4f} "
            f"{float(row['delta_injection_m3'] or 0.0):>12.0f} "
            f"{float(row['field_mean_pressure_delta_bar'] or 0.0):>10.3f}"
        )
    lines.append("")
    for label, row in (
        ("MAX_NPV", kwargs["max_npv_row"]),
        ("NEAR_BASELINE_INJECTION", kwargs["near_row"]),
        ("LOW_PRESSURE_DEVIATION", kwargs["low_p_row"]),
    ):
        if row is None:
            lines.append(f"{label}: none")
            continue
        lines.append(
            f"{label}: {row['scenario_id']}  NPV {float(row['npv_mln_rub']):.4f}  "
            f"Δinj {float(row['delta_injection_m3'] or 0.0):.0f} m3  "
            f"dev {float(row['injection_deviation_pct'] or 0.0):.3f}%"
        )
    nearest = kwargs["nearest"]
    if nearest is not None:
        lines.append(
            f"nearest by injection: {nearest['scenario_id']}  "
            f"dev {float(nearest['injection_deviation_pct'] or 0.0):.3f}%"
        )
    lines.append("")
    lines.append(f"Pareto frontier: {len(kwargs['frontier'])} candidates")
    lines.append(
        f"simulations {kwargs['simulations']}  parallelism {kwargs['parallelism']}  "
        f"wall {kwargs['wall']:.0f}s"
    )
    lines.append("")
    lines.append("VERDICT:")
    lines.append("EXP_003_ROBUSTNESS_ENVELOPE_READY")
    lines.append("")
    print("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
