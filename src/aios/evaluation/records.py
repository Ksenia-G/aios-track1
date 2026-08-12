"""Запись об одной попытке оценки.

RunRecord -- журнал истории, а не кеш. Доменных полей (скважины, управления,
единицы) здесь нет и не должно появиться до разбора официальных материалов.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from aios.evaluation.canonical import JsonValue, canonical_json
from aios.evaluation.constraints import ConstraintStatus
from aios.evaluation.npv import EvaluatorProvenance, NpvStatus
from aios.evaluation.simulation import SimulationStatus


class RunStatus(StrEnum):
    """Общий исход попытки оценки.

    PARTIAL выделен отдельно и никогда не сливается с COMPLETED: потребитель
    не должен иметь возможности принять оборванный прогон за полноценный.
    """

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Одна попытка evaluate().

    run_id уникален для каждой попытки, simulation_key выводится из содержания
    запроса. Один и тот же simulation_key может встречаться в нескольких
    записях: повторные прогоны нужны для оценки численного шума и
    воспроизводимости.
    """

    run_id: str
    created_at: datetime
    simulation_key: str
    simulation_request_digest: str
    simulator_id: str
    simulator_version: str
    status: RunStatus
    model_digest: str | None = None
    scenario_digest: str | None = None
    schedule_digest: str | None = None
    simulation_status: SimulationStatus | None = None
    trajectory_digest: str | None = None
    npv_status: NpvStatus = NpvStatus.NOT_EVALUATED
    npv_value: float | None = None
    npv_provenance: EvaluatorProvenance | None = None
    constraint_status: ConstraintStatus = ConstraintStatus.NOT_CHECKED
    is_feasible: bool | None = None
    cache_hit: bool = False
    cached_from_run_id: str | None = None
    runtime_seconds: float = 0.0
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware UTC")
        # Состояние целевой функции хранится согласованно во всех трёх полях.
        # Иначе в БД появляются записи, где npv_status говорит одно, а
        # npv_value -- другое, и отчёт получает число без происхождения.
        if self.npv_status is NpvStatus.OK:
            if self.npv_value is None:
                raise ValueError("npv_status=OK requires npv_value")
            if not math.isfinite(self.npv_value):
                # NaN/Inf в сохранённой целевой функции запрещены: такое
                # значение бессмысленно сравнивать и сортировать.
                raise ValueError("npv_value must be finite")
            if self.npv_provenance is None:
                raise ValueError("npv_status=OK requires npv_provenance")
        else:
            if self.npv_value is not None:
                raise ValueError(f"npv_status={self.npv_status} forbids npv_value")
            if self.npv_provenance is not None:
                raise ValueError(f"npv_status={self.npv_status} forbids npv_provenance")

    def to_row(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "simulation_key": self.simulation_key,
            "simulation_request_digest": self.simulation_request_digest,
            "simulator_id": self.simulator_id,
            "simulator_version": self.simulator_version,
            "status": str(self.status),
            "model_digest": self.model_digest,
            "scenario_digest": self.scenario_digest,
            "schedule_digest": self.schedule_digest,
            "simulation_status": (
                str(self.simulation_status) if self.simulation_status is not None else None
            ),
            "trajectory_digest": self.trajectory_digest,
            "npv_status": str(self.npv_status),
            "npv_value": self.npv_value,
            "npv_provenance": (
                str(self.npv_provenance) if self.npv_provenance is not None else None
            ),
            "constraint_status": str(self.constraint_status),
            "is_feasible": None if self.is_feasible is None else int(self.is_feasible),
            "cache_hit": int(self.cache_hit),
            "cached_from_run_id": self.cached_from_run_id,
            "runtime_seconds": self.runtime_seconds,
            "metadata": canonical_json(dict(self.metadata)),
        }

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "RunRecord":
        simulation_status = row["simulation_status"]
        npv_provenance = row["npv_provenance"]
        is_feasible = row["is_feasible"]
        return cls(
            run_id=str(row["run_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            simulation_key=str(row["simulation_key"]),
            simulation_request_digest=str(row["simulation_request_digest"]),
            simulator_id=str(row["simulator_id"]),
            simulator_version=str(row["simulator_version"]),
            status=RunStatus(str(row["status"])),
            model_digest=_optional_str(row["model_digest"]),
            scenario_digest=_optional_str(row["scenario_digest"]),
            schedule_digest=_optional_str(row["schedule_digest"]),
            simulation_status=(
                SimulationStatus(str(simulation_status)) if simulation_status is not None else None
            ),
            trajectory_digest=_optional_str(row["trajectory_digest"]),
            npv_status=NpvStatus(str(row["npv_status"])),
            npv_value=None if row["npv_value"] is None else float(row["npv_value"]),  # type: ignore[arg-type]
            npv_provenance=(
                EvaluatorProvenance(str(npv_provenance)) if npv_provenance is not None else None
            ),
            constraint_status=ConstraintStatus(str(row["constraint_status"])),
            is_feasible=None if is_feasible is None else bool(is_feasible),
            cache_hit=bool(row["cache_hit"]),
            cached_from_run_id=_optional_str(row["cached_from_run_id"]),
            runtime_seconds=float(row["runtime_seconds"]),  # type: ignore[arg-type]
            metadata=json.loads(str(row["metadata"])),
        )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
