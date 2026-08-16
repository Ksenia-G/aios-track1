"""Терминальный вывод демонстрации КТ1.

Модуль только печатает. Все числа приходят из снимка, собранного по
отслеживаемым артефактам, поэтому рендер не может «поправить» результат:
если в файлах лежит другое число, доклад покажет другое число.

Формулировки выбраны намеренно осторожно. Это лучший из проверенных
кандидатов, а не глобальный оптимум и не подтверждённый организатором
результат, и в выводе это сказано прямо, а не спрятано в сноску.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from aios.demo.snapshot import (
    CandidateEntry,
    DemoSnapshot,
    EvidenceError,
    build_snapshot,
    serialize,
    snapshot_path,
    write_snapshot,
)

ARROW = "  ↓"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kt1-demo",
        description="AIOS Track 1 -- проверенный снимок экспериментов (без пересчёта).",
    )
    parser.add_argument(
        "--root", type=Path, default=None, help="корень репозитория (по умолчанию -- свой)"
    )
    parser.add_argument(
        "--write-snapshot",
        action="store_true",
        help="перезаписать experiments/kt1/demo_snapshot.json из канонических артефактов",
    )
    parser.add_argument("--json", action="store_true", help="печатать снимок как JSON")
    return parser


def _money(value: float) -> str:
    return f"{value:.6f}"


def _controls_line(candidate: CandidateEntry) -> list[str]:
    lines: list[str] = []
    for control in sorted(candidate.controls, key=lambda c: int(c.get("block", 0))):
        lines.append(
            f"    B{control['block']}: {control['donor']} → {control['receiver']}"
            f"   fraction {float(control['fraction']):.4g}"
        )
    for supplement in candidate.supplements:
        lines.append(
            f"    supplement: well {supplement['well']} "
            f"+{float(supplement['added_rate_m3_per_day']):g} m3/day"
        )
    return lines


def _header(snapshot: DemoSnapshot) -> list[str]:
    return [
        "",
        "AIOS TRACK 1",
        "VERIFIED EXPERIMENT SNAPSHOT",
        "",
        "Source:",
        "  previously completed full OPM Flow + CHDD runs",
        "  values are read from tracked experiment evidence, not recomputed here",
        "",
    ]


def _pipeline(snapshot: DemoSnapshot) -> list[str]:
    lines = ["PIPELINE", ""]
    stages = snapshot.pipeline["stages"]
    for index, stage in enumerate(stages):
        lines.append(f"  {stage}")
        if index < len(stages) - 1:
            lines.append(ARROW)
    lines += [
        "",
        f"Validation pipeline: {snapshot.pipeline['validation_status']}",
        f"Simulator: {snapshot.pipeline['simulator']}",
        f"Economics: {snapshot.pipeline['economics']}",
        "",
    ]
    return lines


def _progression(snapshot: DemoSnapshot) -> list[str]:
    lines = ["EXPERIMENT PROGRESSION", ""]
    lines.append(
        f"  {'Stage':<10} {'Full simulation evidence':<32} {'Runs':<44} "
        f"{'NPV':>13} {'Δ vs baseline':>15} {'Δ %':>8}"
    )
    for entry in snapshot.progression:
        lines.append(
            f"  {entry.stage:<10} {entry.evidence:<32} {entry.simulation_note:<44} "
            f"{_money(entry.npv_mln_rub):>13} {_money(entry.delta_npv_mln_rub):>15} "
            f"{entry.delta_npv_pct:>7.3f}%"
        )
    lines.append("")
    lines.append("  NPV in mln RUB. Every row is backed by full OPM Flow + CHDD runs.")
    lines.append("")
    return lines


def _best(snapshot: DemoSnapshot) -> list[str]:
    best = snapshot.best
    lines = ["BEST FULLY SIMULATED CANDIDATE TESTED SO FAR", ""]
    lines.append(f"  scenario: {best.scenario_id}")
    lines += _controls_line(best)
    lines += [
        "",
        f"  NPV:            {_money(best.npv_mln_rub)} mln RUB",
        f"  ΔNPV:           {_money(best.delta_npv_mln_rub)} mln RUB",
        f"  ΔNPV %:         {best.delta_npv_pct:.3f}%",
        f"  schedule SHA:   {best.schedule_sha256}",
        f"  OPM:            {best.simulation_status}",
        f"  CHDD:           {best.chdd_status}",
        f"  constraints:    {best.constraints_status}",
        "",
        "  This is the best candidate among those tested. It is not a global optimum,",
        "  not an official result, and not an organizer-confirmed NPV.",
        "",
    ]
    return lines


def _robustness(snapshot: DemoSnapshot) -> list[str]:
    r = snapshot.robustness
    return [
        "ROBUSTNESS OF THE BEST CANDIDATE",
        "",
        f"  baseline actual injection:   {r['baseline_actual_injection_m3']:.4f} m3",
        f"  candidate actual injection:  {r['candidate_actual_injection_m3']:.4f} m3",
        f"  delta:                       {r['delta_actual_injection_m3']:+.4f} m3",
        f"  deviation:                   {r['injection_deviation_pct']:.6f} %",
        "",
        (
            f"  supplement:                  well {r['supplement_well']} "
            f"+{r['supplement_rate_m3_per_day']:g} m3/day"
        ),
        f"  scheduled added injection:   {r['scheduled_added_injection_m3']:.1f} m3",
        (
            f"  realized added injection:    {r['realized_added_injection_m3']:.1f} m3 "
            f"({r['realization_ratio'] * 100:.2f} %)"
        ),
        "",
        (
            f"  well {r['supplement_well']} max WBHP:            "
            f"{r['supplement_well_max_wbhp_bar']:.4f} bar"
        ),
        f"  BHP cap:                     {r['bhp_cap_bar']:.0f} bar",
        f"  cap-bound states:            {r['supplement_well_cap_bound_states']}",
        f"  saturation:                  {r['saturation_status']}",
        "",
        f"  mean field pressure delta:   {r['mean_field_pressure_delta_bar']:+.6f} bar",
        f"  final field pressure delta:  {r['final_field_pressure_delta_bar']:+.6f} bar",
        "",
    ]


def _modes(snapshot: DemoSnapshot) -> list[str]:
    lines = ["ENGINEERING MODES", ""]
    lines.append(
        f"  {'Mode':<24} {'NPV':>13} {'ΔNPV':>13} {'Δinj m3':>12} {'dev %':>9} {'ΔmeanFPR':>10}"
    )
    for candidate in (snapshot.best, snapshot.conservative, snapshot.low_pressure):
        lines.append(
            f"  {candidate.label:<24} {_money(candidate.npv_mln_rub):>13} "
            f"{_money(candidate.delta_npv_mln_rub):>13} "
            f"{candidate.delta_injection_m3:>12.1f} "
            f"{candidate.injection_deviation_pct:>8.4f}% "
            f"{candidate.mean_pressure_delta_bar:>10.3f}"
        )
    lines += [
        "",
        "  scenarios:",
        f"    MAX_NPV                 {snapshot.best.scenario_id}",
        f"    CONSERVATIVE            {snapshot.conservative.scenario_id}",
        f"    LOW_PRESSURE_DEVIATION  {snapshot.low_pressure.scenario_id}",
        "",
        "  The same decision system can select a candidate according to economic",
        "  and engineering constraints.",
        "",
    ]
    return lines


def _economics(snapshot: DemoSnapshot) -> list[str]:
    e = snapshot.economics
    return [
        "ECONOMIC BREAKDOWN — BEST CANDIDATE vs BASELINE",
        "",
        f"  revenue delta:            {e['revenue_m']:+.6f} mln RUB",
        f"  deductions increase:      {e['deductions_m']:+.6f} mln RUB",
        f"  OPEX increase:            {e['opex_m']:+.6f} mln RUB",
        f"  injection OPEX increase:  {e['injection_opex_m']:+.6f} mln RUB",
        f"  additional pump CAPEX:    {e['pump_capex_m']:+.6f} mln RUB",
        f"  profit tax increase:      {e['profit_tax_m']:+.6f} mln RUB",
        f"  NPV delta:                {e['npv_m']:+.6f} mln RUB",
        "",
        "  The improvement is primarily driven by additional oil production.",
        "  This table lists CHDD components; it does not itself reproduce the",
        "  annual tax aggregation and discounting mechanics.",
        "",
    ]


def _limitations(snapshot: DemoSnapshot) -> list[str]:
    return [
        "SCIENTIFIC STATUS",
        "",
        f"  PROVENANCE:      {snapshot.provenance}",
        f"  WATER BALANCE:   {snapshot.water_balance_status}",
        f"  Pressure floor:  {snapshot.pressure_floor_status}",
        "",
        "  These are not hidden warnings. They record exactly what has and has not",
        "  been confirmed against organizer references.",
        "",
        "SOURCE ARTIFACTS",
        "",
        *[f"  {item}" for item in snapshot.source_artifacts],
        "",
    ]


def render(snapshot: DemoSnapshot) -> str:
    """Полный текст демонстрации. Детерминирован при том же снимке."""

    lines: list[str] = []
    lines += _header(snapshot)
    lines += _pipeline(snapshot)
    lines += _progression(snapshot)
    lines += _best(snapshot)
    lines += _robustness(snapshot)
    lines += _modes(snapshot)
    lines += _economics(snapshot)
    lines += _limitations(snapshot)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        snapshot = build_snapshot(args.root)
    except EvidenceError as exc:
        print(f"KT1 demo cannot run: {exc}", file=sys.stderr)
        return 2

    if args.write_snapshot:
        path = write_snapshot(snapshot, args.root)
        print(f"snapshot written: {path.relative_to(path.parents[2])}")
        return 0
    if args.json:
        print(serialize(snapshot), end="")
        return 0

    print(render(snapshot))
    stored = snapshot_path(args.root)
    if stored.is_file() and stored.read_text(encoding="utf-8") != serialize(snapshot):
        print(
            "WARNING: experiments/kt1/demo_snapshot.json is stale; "
            "regenerate with: mise run kt1-demo -- --write-snapshot",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
