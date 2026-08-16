"""Терминальный вывод демонстрации КТ1.

Модуль только печатает. Все числа приходят из снимка, собранного по
отслеживаемым артефактам, поэтому рендер не может «поправить» результат:
если в файлах лежит другое число, доклад покажет другое число.

Здесь же и только здесь живёт перевод. Машинные значения -- статусы,
происхождение, идентификаторы сценариев -- остаются как есть и в снимке, и
в контрактах кода; на экран они выходят по-русски. Смешивать эти два слоя
нельзя: переведённый enum перестаёт быть пригодным для сверки, а
непереведённый заголовок -- для доклада.

Формулировки выбраны намеренно осторожно. Это лучший из просчитанных
сценариев, а не глобальный оптимум и не подтверждённый организатором
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

# Русские подписи для машинных значений. Слева -- то, что лежит в снимке и
# в артефактах; справа -- то, что видит человек.
STATUS_LABELS = {"PASS": "ПРОЙДЕН", "FAIL": "НЕ ПРОЙДЕН", "FAILED": "НЕ ПРОЙДЕН"}
CONSTRAINT_LABELS = {"PASS": "СОБЛЮДЕНЫ", "FAIL": "НАРУШЕНЫ", "FAILED": "НАРУШЕНЫ"}

PIPELINE_LABELS = {
    "CASE": "КЕЙС",
    "HISTORY + CONSTRAINTS": "ИСТОРИЯ + ОГРАНИЧЕНИЯ",
    "CONTROL PLAN": "ПЛАН УПРАВЛЕНИЯ",
    "wells_schedule.inc": "wells_schedule.inc",
    "OPM FLOW": "OPM FLOW",
    "TRAJECTORY": "ТРАЕКТОРИЯ СОСТОЯНИЯ",
    "CHDD": "РАСЧЁТ ЧДД",
    "CONSTRAINT AUDIT": "ПРОВЕРКА ОГРАНИЧЕНИЙ",
    "VERIFIED NPV": "ПРОВЕРЕННЫЙ РЕЗУЛЬТАТ",
}

STAGE_LABELS = {
    "Baseline": "Базовый",
    "EXP-001": "EXP-001",
    "EXP-002": "EXP-002",
    "EXP-003": "EXP-003",
}

STAGE_SUBJECTS = {
    "Baseline": "контрольный расчёт",
    "EXP-001": "перераспределение закачки",
    "EXP-002": "поиск границ и комбинаций",
    "EXP-003": "устойчивость и компенсация закачки",
}

MODE_LABELS = {
    "MAX_NPV": "МАКСИМАЛЬНЫЙ ЧДД",
    "CONSERVATIVE": "КОНСЕРВАТИВНЫЙ",
    "LOW_PRESSURE_DEVIATION": "МИН. ОТКЛОНЕНИЕ ДАВЛЕНИЯ",
}

PROVENANCE_LABEL = "неофициальное — ожидается сверка с tNavigator"
WATER_BALANCE_LABEL = "точное правило требует подтверждения организатора"
PRESSURE_FLOOR_LABEL = "официальный минимальный порог не предоставлен"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kt1-demo",
        description="AIOS Track 1 — проверенные результаты расчётов (без пересчёта).",
    )
    parser.add_argument(
        "--root", type=Path, default=None, help="корень репозитория (по умолчанию — свой)"
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


def status_label(value: str) -> str:
    """Машинный статус -> подпись для человека. Само значение не меняется."""

    return STATUS_LABELS.get(value, value)


def constraint_label(value: str) -> str:
    return CONSTRAINT_LABELS.get(value, value)


def _runs_label(candidates: int, reconciliation: int) -> str:
    if candidates == 1 and reconciliation == 0:
        return "1 контрольный расчёт"
    text = f"{candidates} сценариев"
    if reconciliation:
        text += f" (+{reconciliation} сверочных)"
    return text


def _controls_line(candidate: CandidateEntry) -> list[str]:
    lines: list[str] = []
    for control in sorted(candidate.controls, key=lambda c: int(c.get("block", 0))):
        share = float(control["fraction"]) * 100.0
        lines.append(
            f"    Блок {control['block']}: перенос закачки "
            f"{control['donor']} → {control['receiver']}, доля {share:g}%"
        )
    for supplement in candidate.supplements:
        lines.append(
            f"    Дополнительно: скважина {supplement['well']}, "
            f"+{float(supplement['added_rate_m3_per_day']):g} м³/сут"
        )
    return lines


def _header(snapshot: DemoSnapshot) -> list[str]:
    return [
        "",
        "AIOS TRACK 1",
        "ПРОВЕРЕННЫЕ РЕЗУЛЬТАТЫ ВЫЧИСЛИТЕЛЬНЫХ ЭКСПЕРИМЕНТОВ",
        "",
        "Источник:",
        "  ранее выполненные полные расчёты OPM Flow + ЧДД",
        "  значения читаются из сохранённых материалов экспериментов,",
        "  здесь они не пересчитываются",
        "",
    ]


def _pipeline(snapshot: DemoSnapshot) -> list[str]:
    lines = ["РАСЧЁТНЫЙ КОНТУР", ""]
    stages = snapshot.pipeline["stages"]
    for index, stage in enumerate(stages):
        lines.append(f"  {PIPELINE_LABELS.get(stage, stage)}")
        if index < len(stages) - 1:
            lines.append(ARROW)
    lines += [
        "",
        f"Контур проверки: {status_label(snapshot.pipeline['validation_status'])}",
        "Гидродинамический расчёт: OPM Flow, закреплённый образ, полные прогоны",
        "Экономика: официальный пакет расчёта ЧДД",
        "",
    ]
    return lines


def _progression(snapshot: DemoSnapshot) -> list[str]:
    lines = ["ИЗМЕРЕННЫЙ ПРОГРЕСС", ""]
    lines.append(
        f"  {'Этап':<10} {'Что проверяли':<38} {'Расчётов':<28} "
        f"{'ЧДД, млн ₽':>13} {'Прирост, млн ₽':>16} {'Прирост, %':>11}"
    )
    for entry in snapshot.progression:
        runs = _runs_label(entry.candidate_runs, entry.reconciliation_runs)
        growth = "—" if entry.stage == "Baseline" else f"{entry.delta_npv_pct:.3f}%"
        delta = "—" if entry.stage == "Baseline" else _money(entry.delta_npv_mln_rub)
        lines.append(
            f"  {STAGE_LABELS.get(entry.stage, entry.stage):<10} "
            f"{STAGE_SUBJECTS.get(entry.stage, entry.evidence):<38} {runs:<28} "
            f"{_money(entry.npv_mln_rub):>13} {delta:>16} {growth:>11}"
        )
    lines.append("")
    lines.append("  Каждая строка получена полными расчётами OPM Flow и ЧДД.")
    lines.append("")
    return lines


def _best(snapshot: DemoSnapshot) -> list[str]:
    best = snapshot.best
    lines = ["ЛУЧШИЙ ИЗ ПОЛНОСТЬЮ ПРОСЧИТАННЫХ НА ДАННЫЙ МОМЕНТ СЦЕНАРИЕВ", ""]
    lines.append(f"  Сценарий: {best.scenario_id}")
    lines.append("")
    lines.append("  Управление:")
    lines += _controls_line(best)
    lines += [
        "",
        f"  ЧДД:                       {_money(best.npv_mln_rub)} млн ₽",
        f"  Прирост ЧДД:               {_money(best.delta_npv_mln_rub)} млн ₽",
        f"  Прирост:                   {best.delta_npv_pct:.3f}%",
        f"  SHA расписания:            {best.schedule_sha256}",
        f"  Гидродинамический расчёт:  {status_label(best.simulation_status)}",
        f"  Расчёт ЧДД:                {status_label(best.chdd_status)}",
        f"  Проверка ограничений:      {constraint_label(best.constraints_status)}",
        "",
        "  Это лучший из просчитанных сценариев. Он не является глобальным",
        "  оптимумом, официальным результатом или подтверждённым организатором",
        "  значением ЧДД.",
        "",
    ]
    return lines


def _robustness(snapshot: DemoSnapshot) -> list[str]:
    r = snapshot.robustness
    well = r["supplement_well"]
    return [
        "УСТОЙЧИВОСТЬ ЛУЧШЕГО СЦЕНАРИЯ",
        "",
        f"  Фактическая закачка, базовый сценарий:  {r['baseline_actual_injection_m3']:.4f} м³",
        f"  Фактическая закачка, кандидат:          {r['candidate_actual_injection_m3']:.4f} м³",
        f"  Разница:                                {r['delta_actual_injection_m3']:+.4f} м³",
        f"  Отклонение:                             {r['injection_deviation_pct']:.6f} %",
        "",
        (
            f"  Дополнительная закачка:                 скважина {well}, "
            f"+{r['supplement_rate_m3_per_day']:g} м³/сут"
        ),
        f"  Запланировано дополнительно:            {r['scheduled_added_injection_m3']:.1f} м³",
        (
            f"  Фактически реализовано:                 "
            f"{r['realized_added_injection_m3']:.1f} м³ "
            f"({r['realization_ratio'] * 100:.2f} %)"
        ),
        "",
        (
            f"  {f'Максимальное WBHP скважины {well}:':<39}"
            f" {r['supplement_well_max_wbhp_bar']:.4f} bar"
        ),
        f"  Ограничение BHP:                        {r['bhp_cap_bar']:.0f} bar",
        f"  Число состояний на ограничении:         {r['supplement_well_cap_bound_states']}",
        (
            f"  Запас до ограничения:                   "
            f"{r['supplement_well_bhp_headroom_bar']:.4f} bar"
        ),
        "",
        f"  Среднее изменение пластового давления:  {r['mean_field_pressure_delta_bar']:+.6f} bar",
        f"  Изменение давления в конце периода:     {r['final_field_pressure_delta_bar']:+.6f} bar",
        "",
    ]


def _modes(snapshot: DemoSnapshot) -> list[str]:
    lines = ["ВАРИАНТЫ ВЫБОРА ПЛАНА УПРАВЛЕНИЯ", ""]
    lines.append(
        f"  {'Вариант':<26} {'ЧДД, млн ₽':>13} {'Прирост':>13} "
        f"{'Δ закачки, м³':>15} {'Откл., %':>10} {'Δ давл., bar':>13}"
    )
    for candidate in (snapshot.best, snapshot.conservative, snapshot.low_pressure):
        lines.append(
            f"  {MODE_LABELS.get(candidate.label, candidate.label):<26} "
            f"{_money(candidate.npv_mln_rub):>13} "
            f"{_money(candidate.delta_npv_mln_rub):>13} "
            f"{candidate.delta_injection_m3:>15.1f} "
            f"{candidate.injection_deviation_pct:>9.4f}% "
            f"{candidate.mean_pressure_delta_bar:>13.3f}"
        )
    lines += [
        "",
        "  Идентификаторы сценариев:",
        f"    МАКСИМАЛЬНЫЙ ЧДД            {snapshot.best.scenario_id}",
        f"    КОНСЕРВАТИВНЫЙ              {snapshot.conservative.scenario_id}",
        f"    МИН. ОТКЛОНЕНИЕ ДАВЛЕНИЯ    {snapshot.low_pressure.scenario_id}",
        "",
        "  Система может выбирать план управления не только по максимальному ЧДД,",
        "  но и с учётом инженерных ограничений и допустимого уровня риска.",
        "",
    ]
    return lines


def _economics(snapshot: DemoSnapshot) -> list[str]:
    e = snapshot.economics
    return [
        "ЭКОНОМИКА: ЛУЧШИЙ СЦЕНАРИЙ ОТНОСИТЕЛЬНО БАЗОВОГО",
        "",
        f"  Дополнительная выручка:            {e['revenue_m']:+.6f} млн ₽",
        f"  Рост отчислений:                   {e['deductions_m']:+.6f} млн ₽",
        f"  Рост OPEX:                         {e['opex_m']:+.6f} млн ₽",
        f"  Рост затрат на закачку:            {e['injection_opex_m']:+.6f} млн ₽",
        f"  Дополнительный CAPEX по ЭЦН:       {e['pump_capex_m']:+.6f} млн ₽",
        f"  Рост налога на прибыль:            {e['profit_tax_m']:+.6f} млн ₽",
        f"  Прирост ЧДД:                       {e['npv_m']:+.6f} млн ₽",
        "",
        "  Основной источник экономического эффекта — рост добычи нефти.",
        "  Таблица перечисляет слагаемые расчёта ЧДД и не воспроизводит сама по",
        "  себе годовую агрегацию налога и дисконтирование.",
        "",
    ]


def _limitations(snapshot: DemoSnapshot) -> list[str]:
    return [
        "НАУЧНЫЙ СТАТУС РЕЗУЛЬТАТА",
        "",
        "  ПРОИСХОЖДЕНИЕ РЕЗУЛЬТАТА:",
        f"    {PROVENANCE_LABEL}",
        f"    ({snapshot.provenance})",
        "",
        "  ВОДНЫЙ БАЛАНС:",
        f"    {WATER_BALANCE_LABEL}",
        f"    ({snapshot.water_balance_status})",
        "",
        "  ОГРАНИЧЕНИЕ ПО ДАВЛЕНИЮ:",
        f"    {PRESSURE_FLOOR_LABEL}",
        "",
        "  Это не скрытые предупреждения. Они фиксируют, что именно уже",
        "  подтверждено, а что ещё предстоит сверить.",
        "",
        "ИСХОДНЫЕ МАТЕРИАЛЫ",
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
        print(f"Демонстрацию запустить нельзя: {exc}", file=sys.stderr)
        return 2

    if args.write_snapshot:
        path = write_snapshot(snapshot, args.root)
        print(f"снимок записан: {path.relative_to(path.parents[2])}")
        return 0
    if args.json:
        print(serialize(snapshot), end="")
        return 0

    print(render(snapshot))
    stored = snapshot_path(args.root)
    if stored.is_file() and stored.read_text(encoding="utf-8") != serialize(snapshot):
        print(
            "ВНИМАНИЕ: experiments/kt1/demo_snapshot.json устарел; "
            "обновите командой: mise run kt1-demo -- --write-snapshot",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
