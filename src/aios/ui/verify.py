"""Пересчёт сданного расписания так же, как это сделают организаторы.

Организаторы получат `submission_evidence/wells_schedule.inc` вместе с
заявленным ЧДД, посчитают файл на своей модели и сравнят числа; расхождение
означает дисквалификацию. Здесь тот же путь на нашей стороне. Файл уходит в
OPM целиком -- история плюс окно управления, ровно в том виде, в каком он
сдан, -- траектория идёт в официальный калькулятор, и результат сверяется с
заявленным в `final_result.json`.

Сначала сверяется SHA файла. Если файл не тот, что заморожен, симулятор не
запускается: пересчёт другого файла дал бы совпадение или расхождение,
которое ничего не говорит о нашей заявке.

Допуск здесь не задаётся, а берётся из кейса
(`acceptance.npv_tolerance_mln_rub`) -- тот же, с которым `validation-demo`
сверяет опорный прогон.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from aios.ui.events import emit_event as _event
from aios.ui.progress import FlowProgress

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = REPO_ROOT / "submission_evidence"
CASE_PATH = REPO_ROOT / "cases" / "model_y_validation.json"
SCHEDULE_FILENAME = "wells_schedule.inc"
FINAL_RESULT_FILENAME = "final_result.json"

Emit = Callable[[str], None]


class VerificationError(RuntimeError):
    """Пересчёт нельзя выполнить так, чтобы он что-то доказывал."""


@dataclass(frozen=True, slots=True)
class SubmissionClaim:
    """То, что заявлено в замороженном пакете сабмишена."""

    schedule_sha256: str
    objective_mln_rub: float
    organizer_compatible_mln_rub: float | None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Пересчитанный ЧДД рядом с заявленным."""

    claim: SubmissionClaim
    recomputed_mln_rub: float
    tolerance_mln_rub: float

    @property
    def difference_mln_rub(self) -> float:
        return self.recomputed_mln_rub - self.claim.objective_mln_rub

    @property
    def matches(self) -> bool:
        return abs(self.difference_mln_rub) <= self.tolerance_mln_rub


def _print(line: str) -> None:
    print(line, flush=True)


def load_claim(evidence_dir: Path = EVIDENCE_DIR) -> SubmissionClaim:
    """Прочитать заявку из `final_result.json`."""
    path = evidence_dir / FINAL_RESULT_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        sha = str(payload["schedule"]["standalone_sha256"])
        objective = float(payload["ai_period_objective_mln_rub"]["B4_MAS_ON"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise VerificationError(f"{path.name}: заявка не читается ({exc})") from exc
    compatible = (payload.get("organizer_compatible_mln_rub") or {}).get("B4_MAS_ON")
    return SubmissionClaim(
        schedule_sha256=sha,
        objective_mln_rub=objective,
        organizer_compatible_mln_rub=None if compatible is None else float(compatible),
    )


def verify(
    workspace_root: Path,
    *,
    evidence_dir: Path = EVIDENCE_DIR,
    case_path: Path = CASE_PATH,
    emit: Emit = _print,
) -> VerificationResult:
    """Посчитать сданный файл заново и поставить результат рядом с заявкой."""
    from aios.schedule.generator import schedule_sha256

    _event("sha", "active")
    claim = load_claim(evidence_dir)
    try:
        text = (evidence_dir / SCHEDULE_FILENAME).read_text(encoding="utf-8")
    except OSError as exc:
        raise VerificationError(f"{SCHEDULE_FILENAME}: файл не читается ({exc})") from exc

    actual = schedule_sha256(text)
    emit(f"Файл:          {SCHEDULE_FILENAME}")
    emit(f"SHA-256:       {actual}")
    if actual != claim.schedule_sha256:
        _event("sha", "fail")
        raise VerificationError(
            f"SHA файла не совпадает с заявленным ({claim.schedule_sha256}): "
            "это не тот файл, что заморожен, и симулятор не запускается"
        )
    emit("               совпадает с final_result.json")
    _event("sha", "done")

    from aios.cases import load_case
    from aios.chdd.runner import run_chdd
    from aios.chdd.trajectory import build_chdd_input, write_chdd_csv
    from aios.execution.execution import run_full_opm

    case = load_case(case_path)
    if case.acceptance is None:
        raise VerificationError("в кейсе нет acceptance: не с чем взять допуск сверки")

    _event("opm", "active")
    emit("OPM Flow:      полный прогон истории и окна управления, это несколько минут")
    # Файл сдан целиком, поэтому история не подклеивается отдельно -- так же
    # его передаёт в симулятор и `validation-demo`.
    with FlowProgress(workspace_root, emit):
        run = run_full_opm(case, root=workspace_root, history_text="", schedule_text=text)
    if not run.is_pass or run.summary is None:
        _event("opm", "fail")
        raise VerificationError(
            f"OPM не завершился успешно (exit_code={run.run_result.exit_code}); "
            "упавший прогон не является результатом"
        )
    emit("OPM Flow:      PASS")
    _event("opm", "done")

    _event("chdd", "active")
    chdd_input = build_chdd_input(run.summary, case)
    source = write_chdd_csv(chdd_input, workspace_root / "chdd_input.csv")
    recomputed = run_chdd(case, chdd_input, source_file=source).npv_mln_rub
    emit("ЧДД:           PASS")
    _event("chdd", "done")
    return VerificationResult(
        claim=claim,
        recomputed_mln_rub=recomputed,
        tolerance_mln_rub=case.acceptance.npv_tolerance_mln_rub,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aios.ui.verify",
        description="Пересчитать сданный wells_schedule.inc и сверить ЧДД с заявленным.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Каталог для рабочих файлов прогона. По умолчанию -- временный.",
    )
    args = parser.parse_args(argv)
    base = args.workspace_root
    if base is None:
        base = Path(tempfile.mkdtemp(prefix="aios-verify-"))

    from aios.cases import CaseError

    _print("AIOS TRACK 1 · ПЕРЕСЧЁТ СДАННОГО ЧДД")
    _print("")
    try:
        result = verify(base / "submission")
    except (VerificationError, CaseError) as exc:
        _print("")
        _print("ВЕРДИКТ:")
        _print("НЕ ПРОВЕРЕНО")
        _print(f"Причина: {exc}")
        return 2

    claim = result.claim
    _event("compare", "active")
    _print("")
    _print(f"Пересчитано:   {result.recomputed_mln_rub!r} млн ₽")
    _print(f"Заявлено:      {claim.objective_mln_rub!r} млн ₽")
    _print(
        f"Расхождение:   {result.difference_mln_rub:+.9f} млн ₽ "
        f"(допуск {result.tolerance_mln_rub:g})"
    )
    if claim.organizer_compatible_mln_rub is not None:
        _print(
            f"Сопоставимый:  {claim.organizer_compatible_mln_rub:.6f} млн ₽ "
            "(заявлено в final_result.json)"
        )
    _print("")
    _print("ВЕРДИКТ:")
    _print("СОВПАДАЕТ" if result.matches else "НЕ СОВПАДАЕТ")
    _event("compare", "done" if result.matches else "fail")
    return 0 if result.matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
