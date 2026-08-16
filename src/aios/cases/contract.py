"""Контракт кейса управления: что именно оптимизируется и в каких рамках.

Кейс -- единственный вход пайплайна. Значения конкретного кейса живут в JSON,
а не в коде: захардкожены только те инварианты Track 1, которые уже доказаны
лабораторно и нарушение которых обрывает расчёт у официального калькулятора.

Доказанные инварианты (OFFICIAL-003B, правило S):
* `WLPR > 500` -- `ValueError` внутри CHDD, весь расчёт падает целиком;
* перевод добывающей скважины в нагнетательные для Track 1 запрещён.
"""

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Any

# Жёсткий предел официального калькулятора: 500 принимается, 500.0001 -- нет.
TRACK1_WLPR_HARD_MAX = 500.0
TRACK1_TRACK_ID = 1


class CaseError(ValueError):
    """Кейс не читается или нарушает инвариант трека. Значения по умолчанию
    здесь не подставляются: молчаливая починка кейса -- это подмена задачи."""


@dataclass(frozen=True, slots=True)
class HistorySpec:
    """Источник истории: физическая колода и её файл расписания."""

    deck_root: Path
    data_file: str
    schedule_include: str

    @property
    def data_path(self) -> Path:
        return self.deck_root / self.data_file

    @property
    def schedule_path(self) -> Path:
        return self.deck_root / self.schedule_include


@dataclass(frozen=True, slots=True)
class ConstraintSpec:
    """Технологические ограничения кейса.

    `producer_wlpr_max` не может превышать `TRACK1_WLPR_HARD_MAX`: более мягкий
    кейс просто не досчитается до конца.
    """

    producer_wlpr_max: float = TRACK1_WLPR_HARD_MAX
    allowed_producer_controls: tuple[str, ...] = ("LRAT",)
    allowed_injector_controls: tuple[str, ...] = ("RATE",)
    allow_producer_to_injector: bool = False
    producer_bhp_min: float | None = None
    injector_bhp_max: float | None = None
    injector_rate_max: float | None = None
    field_injection_rate_max: float | None = None


@dataclass(frozen=True, slots=True)
class EconomicSpec:
    """Конфигурация экономики.

    Формулы не дублируются: считает официальный пакет по пути
    `calculator_root`. Здесь только то, что нужно, чтобы его накормить.
    """

    calculator_root: Path
    norms_workbook: Path
    economic_start_date: date
    oil_density_t_per_sm3: float
    water_density_t_per_sm3: float
    provenance: str


@dataclass(frozen=True, slots=True)
class AcceptanceSpec:
    """Опорное значение для сверки пайплайна с уже полученным результатом.

    Это ссылка для сравнения, а не источник ответа: в сабмишен попадает то
    число, которое посчитал калькулятор в этом прогоне.
    """

    baseline_npv_mln_rub: float
    npv_tolerance_mln_rub: float
    reference: str


@dataclass(frozen=True, slots=True)
class SimulatorSpec:
    """Как запускается OPM Flow."""

    docker_image: str
    flow_command: str = "flow"
    extra_args: tuple[str, ...] = ()
    timeout_seconds: int = 3600


@dataclass(frozen=True, slots=True)
class Case:
    """Полный кейс управления."""

    case_id: str
    track: int
    management_start_date: date
    management_end_date: date
    history: HistorySpec
    constraints: ConstraintSpec
    economics: EconomicSpec
    simulator: SimulatorSpec
    acceptance: AcceptanceSpec | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "track": self.track,
            "management_start_date": self.management_start_date.isoformat(),
            "management_end_date": self.management_end_date.isoformat(),
            "history": {
                "deck_root": str(self.history.deck_root),
                "data_file": self.history.data_file,
                "schedule_include": self.history.schedule_include,
            },
            "constraints": {
                "producer_wlpr_max": self.constraints.producer_wlpr_max,
                "allowed_producer_controls": list(self.constraints.allowed_producer_controls),
                "allowed_injector_controls": list(self.constraints.allowed_injector_controls),
                "allow_producer_to_injector": self.constraints.allow_producer_to_injector,
                "producer_bhp_min": self.constraints.producer_bhp_min,
                "injector_bhp_max": self.constraints.injector_bhp_max,
                "injector_rate_max": self.constraints.injector_rate_max,
                "field_injection_rate_max": self.constraints.field_injection_rate_max,
            },
            "economics": {
                "calculator_root": str(self.economics.calculator_root),
                "norms_workbook": str(self.economics.norms_workbook),
                "economic_start_date": self.economics.economic_start_date.isoformat(),
                "oil_density_t_per_sm3": self.economics.oil_density_t_per_sm3,
                "water_density_t_per_sm3": self.economics.water_density_t_per_sm3,
                "provenance": self.economics.provenance,
            },
            "simulator": {
                "docker_image": self.simulator.docker_image,
                "flow_command": self.simulator.flow_command,
                "extra_args": list(self.simulator.extra_args),
                "timeout_seconds": self.simulator.timeout_seconds,
            },
            "acceptance": None
            if self.acceptance is None
            else {
                "baseline_npv_mln_rub": self.acceptance.baseline_npv_mln_rub,
                "npv_tolerance_mln_rub": self.acceptance.npv_tolerance_mln_rub,
                "reference": self.acceptance.reference,
            },
        }


def _require(payload: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in payload:
        raise CaseError(f"{where}: missing required field {key!r}")
    return payload[key]


def _section(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = _require(payload, key, "case")
    if not isinstance(value, Mapping):
        raise CaseError(f"case: section {key!r} must be an object")
    return value


def _as_date(value: Any, where: str) -> date:
    if not isinstance(value, str):
        raise CaseError(f"{where}: date must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CaseError(f"{where}: bad date {value!r}") from exc


def _as_float(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CaseError(f"{where}: expected a number, got {value!r}")
    number = float(value)
    if not isfinite(number):
        raise CaseError(f"{where}: number must be finite")
    return number


def _as_optional_float(value: Any, where: str) -> float | None:
    return None if value is None else _as_float(value, where)


def _as_upper_tuple(value: Any, where: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise CaseError(f"{where}: expected a list of control keywords")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CaseError(f"{where}: control keyword must be a non-empty string")
        items.append(item.strip().upper())
    if len(set(items)) != len(items):
        raise CaseError(f"{where}: duplicate control keywords")
    return tuple(items)


_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand_environment(value: str, where: str) -> str:
    """Подставить переменные окружения в путь.

    Model Y и официальный пакет ЧДД в репозиторий не кладутся, поэтому кейс
    ссылается на них через переменные окружения. Незаданная переменная --
    это ошибка кейса, а не пустая строка: молча собранный путь вида
    `/input/Нормативы_ЧДД.xlsx` привёл бы к отказу где-то глубже, где уже
    непонятно, что именно не настроено.
    """

    missing = [
        name
        for match in _ENV_REFERENCE.finditer(value)
        if (name := match.group(1) or match.group(2)) not in os.environ
    ]
    if missing:
        raise CaseError(
            f"{where}: environment variable(s) not set: {', '.join(sorted(set(missing)))}. "
            f"Point them at the external Model Y deck and CHDD package."
        )
    return os.path.expandvars(value)


def _as_path(value: Any, where: str, base_dir: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"{where}: expected a non-empty path string")
    path = Path(_expand_environment(value, where)).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def parse_case(payload: Mapping[str, Any], *, base_dir: Path) -> Case:
    """Собрать кейс из разобранного JSON. Относительные пути -- от `base_dir`."""

    case_id = _require(payload, "case_id", "case")
    if not isinstance(case_id, str) or not case_id.strip():
        raise CaseError("case: case_id must be a non-empty string")

    track = _require(payload, "track", "case")
    if track != TRACK1_TRACK_ID:
        raise CaseError(f"case: this pipeline serves track {TRACK1_TRACK_ID}, got {track!r}")

    start = _as_date(_require(payload, "management_start_date", "case"), "management_start_date")
    end = _as_date(_require(payload, "management_end_date", "case"), "management_end_date")
    if end <= start:
        raise CaseError("case: management_end_date must be after management_start_date")

    history_raw = _section(payload, "history")
    history = HistorySpec(
        deck_root=_as_path(
            _require(history_raw, "deck_root", "history"), "history.deck_root", base_dir
        ),
        data_file=str(_require(history_raw, "data_file", "history")),
        schedule_include=str(_require(history_raw, "schedule_include", "history")),
    )

    constraints_raw = _section(payload, "constraints")
    wlpr_max = _as_float(
        _require(constraints_raw, "producer_wlpr_max", "constraints"),
        "constraints.producer_wlpr_max",
    )
    if wlpr_max > TRACK1_WLPR_HARD_MAX:
        raise CaseError(
            f"constraints.producer_wlpr_max={wlpr_max} exceeds the hard CHDD limit "
            f"{TRACK1_WLPR_HARD_MAX}"
        )
    allow_conversion = bool(constraints_raw.get("allow_producer_to_injector", False))
    if allow_conversion:
        raise CaseError("constraints: producer -> injector conversion is forbidden on track 1")
    constraints = ConstraintSpec(
        producer_wlpr_max=wlpr_max,
        allowed_producer_controls=_as_upper_tuple(
            _require(constraints_raw, "allowed_producer_controls", "constraints"),
            "constraints.allowed_producer_controls",
        ),
        allowed_injector_controls=_as_upper_tuple(
            _require(constraints_raw, "allowed_injector_controls", "constraints"),
            "constraints.allowed_injector_controls",
        ),
        allow_producer_to_injector=False,
        producer_bhp_min=_as_optional_float(
            constraints_raw.get("producer_bhp_min"), "constraints.producer_bhp_min"
        ),
        injector_bhp_max=_as_optional_float(
            constraints_raw.get("injector_bhp_max"), "constraints.injector_bhp_max"
        ),
        injector_rate_max=_as_optional_float(
            constraints_raw.get("injector_rate_max"), "constraints.injector_rate_max"
        ),
        field_injection_rate_max=_as_optional_float(
            constraints_raw.get("field_injection_rate_max"), "constraints.field_injection_rate_max"
        ),
    )

    economics_raw = _section(payload, "economics")
    provenance = _require(economics_raw, "provenance", "economics")
    if not isinstance(provenance, str) or not provenance.strip():
        raise CaseError("economics: provenance must be a non-empty string")
    economics = EconomicSpec(
        calculator_root=_as_path(
            _require(economics_raw, "calculator_root", "economics"),
            "economics.calculator_root",
            base_dir,
        ),
        norms_workbook=_as_path(
            _require(economics_raw, "norms_workbook", "economics"),
            "economics.norms_workbook",
            base_dir,
        ),
        economic_start_date=_as_date(
            _require(economics_raw, "economic_start_date", "economics"),
            "economics.economic_start_date",
        ),
        oil_density_t_per_sm3=_as_float(
            _require(economics_raw, "oil_density_t_per_sm3", "economics"),
            "economics.oil_density_t_per_sm3",
        ),
        water_density_t_per_sm3=_as_float(
            _require(economics_raw, "water_density_t_per_sm3", "economics"),
            "economics.water_density_t_per_sm3",
        ),
        provenance=provenance.strip(),
    )

    simulator_raw = _section(payload, "simulator")
    simulator = SimulatorSpec(
        docker_image=str(_require(simulator_raw, "docker_image", "simulator")),
        flow_command=str(simulator_raw.get("flow_command", "flow")),
        extra_args=tuple(str(arg) for arg in simulator_raw.get("extra_args", ())),
        timeout_seconds=int(simulator_raw.get("timeout_seconds", 3600)),
    )

    acceptance_raw = payload.get("acceptance")
    acceptance: AcceptanceSpec | None = None
    if acceptance_raw is not None:
        if not isinstance(acceptance_raw, Mapping):
            raise CaseError("case: section 'acceptance' must be an object")
        tolerance = _as_float(
            _require(acceptance_raw, "npv_tolerance_mln_rub", "acceptance"),
            "acceptance.npv_tolerance_mln_rub",
        )
        if tolerance < 0:
            raise CaseError("acceptance.npv_tolerance_mln_rub must not be negative")
        acceptance = AcceptanceSpec(
            baseline_npv_mln_rub=_as_float(
                _require(acceptance_raw, "baseline_npv_mln_rub", "acceptance"),
                "acceptance.baseline_npv_mln_rub",
            ),
            npv_tolerance_mln_rub=tolerance,
            reference=str(_require(acceptance_raw, "reference", "acceptance")),
        )

    return Case(
        case_id=case_id.strip(),
        track=TRACK1_TRACK_ID,
        management_start_date=start,
        management_end_date=end,
        history=history,
        constraints=constraints,
        economics=economics,
        simulator=simulator,
        acceptance=acceptance,
    )


def load_case(path: str | Path) -> Case:
    """Прочитать кейс из JSON-файла."""

    case_path = Path(path).expanduser().resolve()
    try:
        raw = case_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CaseError(f"case file is not readable: {case_path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaseError(f"case file is not valid JSON: {case_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise CaseError(f"case file must contain a JSON object: {case_path}")
    return parse_case(payload, base_dir=case_path.parent)
