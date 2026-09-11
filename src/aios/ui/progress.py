"""Ход прогона OPM по его собственному журналу.

Полный прогон OPM идёт минутами и ничего не печатает в наш вывод: всё, что
симулятор сообщает о ходе расчёта, уходит в его журнал `OUT/*.PRT`. Модуль
читает этот журнал со стороны и переводит строки
`Report step N/M at day d/D, date = ...` в короткую строку хода: какая доля
горизонта посчитана и на какой дате модели сейчас симулятор.

На прогон модуль не влияет никак -- только читает файл, который симулятор и
так пишет. Доля считается по дням модели, а не по номерам шагов: шаги разной
длины, и половина шагов -- не половина времени.

Командой `python -m aios.ui.progress <каталог>` за прогоном можно следить из
соседней вкладки терминала.
"""

from __future__ import annotations

import argparse
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

#: Журналы, по которым виден ход OPM. `*.PRT` симулятор пишет по мере
#: расчёта; `flow.log` -- вывод процесса, который здесь появляется целиком
#: только после завершения прогона, поэтому он лишь запасной.
LOG_PATTERNS = ("*.PRT", "flow.log")
#: Сколько байт с конца журнала читать за раз: последняя строка хода там.
TAIL_BYTES = 64 * 1024
_REPORT_STEP = re.compile(
    r"Report step\s+(\d+)/(\d+)\s+at day\s+([\d.]+)/([\d.]+),\s*date\s*=\s*(\S+)"
)

Emit = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ReportStep:
    """Одна строка хода из журнала OPM."""

    step: int
    total_steps: int
    day: float
    total_days: float
    date: str

    @property
    def percent(self) -> int:
        if self.total_days <= 0:
            return 0
        return max(0, min(100, int(self.day / self.total_days * 100)))


def last_report_step(text: str) -> ReportStep | None:
    """Последняя строка хода в куске журнала; None, если её там нет."""
    matches = _REPORT_STEP.findall(text)
    if not matches:
        return None
    step, total, day, total_days, date = matches[-1]
    return ReportStep(int(step), int(total), float(day), float(total_days), date)


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(size - TAIL_BYTES, 0))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


class FlowProgress:
    """Следит за журналами OPM в каталоге и сообщает о ходе расчёта.

    Учитываются только журналы, изменённые после `since`: старый прогон в том
    же каталоге не должен выдавать себя за текущий.
    """

    def __init__(
        self,
        directory: Path,
        emit: Emit,
        *,
        since: float | None = None,
        interval: float = 2.0,
        every_percent: int = 10,
        label_simulations: bool = False,
    ) -> None:
        self._directory = directory
        self._emit = emit
        self._since = time.time() if since is None else since
        self._interval = interval
        self._every = every_percent
        self._label_simulations = label_simulations
        self._seen: set[Path] = set()
        self._current: Path | None = None
        self._last_percent = -1
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _active_logs(self) -> list[tuple[float, Path]]:
        if not self._directory.is_dir():
            return []
        logs: list[tuple[float, Path]] = []
        for pattern in LOG_PATTERNS:
            for path in self._directory.rglob(pattern):
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                if modified >= self._since - 1:
                    logs.append((modified, path))
        return logs

    def poll(self) -> None:
        """Один взгляд на журналы. Печатает, только если есть что сказать."""
        logs = self._active_logs()
        if not logs:
            return
        newest = max(logs)[1]
        self._seen.update(path for _modified, path in logs)
        if newest != self._current:
            self._current = newest
            self._last_percent = -1
            if self._label_simulations:
                self._emit(f"OPM Flow:      симуляция {len(self._seen)}")
        step = last_report_step(_tail(newest))
        if step is None:
            return
        percent = step.percent
        if self._last_percent < 0 or percent >= self._last_percent + self._every:
            self._last_percent = percent
            self._emit(
                f"OPM Flow:      {percent:>3}% горизонта · шаг {step.step + 1}/{step.total_steps}"
                f" · {step.date}"
            )

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.poll()

    def start(self) -> Self:
        self._thread = threading.Thread(target=self._run, name="aios-flow-progress", daemon=True)
        self._thread.start()
        return self

    def stop(self, *, final: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if final:
            self.poll()

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aios.ui.progress",
        description="Показывать ход прогона OPM по его журналу.",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="runtime",
        help="каталог прогона; по умолчанию runtime/, туда пишет validation-demo",
    )
    parser.add_argument("--interval", type=float, default=3.0, help="секунд между проверками")
    args = parser.parse_args(argv)

    directory = Path(args.directory)
    print(f"Слежу за журналами OPM в {directory}. Остановить: Ctrl+C", flush=True)
    # Две минуты назад -- чтобы подхватить прогон, запущенный чуть раньше
    # следилки, но не показывать давно закончившийся.
    watcher = FlowProgress(
        directory,
        lambda line: print(line, flush=True),
        since=time.time() - 120,
        interval=args.interval,
        every_percent=5,
        label_simulations=True,
    )
    try:
        while True:
            watcher.poll()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
