"""Test support for runtime/execution zone.

TEST SUPPORT ONLY -- not production API (TF14/TF15: no aios.* module may
import this). Builds synthetic SummaryData instances and scripted Simulator
doubles over existing contracts; defines no new production types (TF16).
"""

from __future__ import annotations

import datetime
from pathlib import Path

from aios.evaluation.artifacts import TrajectoryArtifactRef
from aios.evaluation.canonical import digest_bytes
from aios.evaluation.simulation import (
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorIdentity,
)
from aios.opm.summary import SummaryData

SYNTHETIC_START = datetime.datetime(2020, 1, 1)  # noqa: DTZ001
SYNTHETIC_DATES = (
    datetime.datetime(2020, 2, 1),  # noqa: DTZ001
    datetime.datetime(2020, 3, 1),  # noqa: DTZ001
    datetime.datetime(2020, 4, 1),  # noqa: DTZ001
)

_COLUMN_LAYOUT: tuple[tuple[str, str], ...] = (
    *[(vector, "") for vector in ("FOPR", "FWPR", "FWIR", "FOPT", "FWPT", "FWIT")],
    *[(vector, well) for vector in ("WBHP", "WLPR", "WOPR", "WWPR") for well in ("P1", "P2")],
    ("WBHP", "I1"),
    ("WWIR", "I1"),
)


def synthetic_summary_data(
    *, perturbation: dict[tuple[str, str, int], float] | None = None
) -> SummaryData:
    """Детерминированная сводка трёх шагов по структуре Model Y.

    ``perturbation`` -- {(vector, well_or_empty, step_index): value}, где
    пустая строка означает полевой вектор; значение добавляется к базе.
    """
    edits = perturbation or {}
    columns: dict[tuple[str, str], int] = {
        (vector, well): index for index, (vector, well) in enumerate(_COLUMN_LAYOUT)
    }
    base_rows = [
        (
            120.0 + 10 * step,
            35.0 + step,
            150.0 + 5 * step,
            1000.0 + 100 * step,
            400.0 + 20 * step,
            1400.0 + 50 * step,
            55.0 + step,
            56.0 + step,
            250.0 + step,
            155.0 + step,
            154.0 + step,
            120.0 + 8 * step,
            119.0 + 8 * step,
            35.0 + step,
            34.0 + step,
            150.0 + 5 * step,
        )
        for step in range(len(SYNTHETIC_DATES))
    ]
    for (vector, well, step_index), delta in edits.items():
        column = columns[(vector, well)]
        row = list(base_rows[step_index])
        row[column] += delta
        base_rows[step_index] = tuple(row)
    well_index = {
        (vector, well): index for index, (vector, well) in enumerate(_COLUMN_LAYOUT) if well
    }
    field_index = {vector: index for index, (vector, well) in enumerate(_COLUMN_LAYOUT) if not well}
    units = {keyword: "SM3/DAY" for keyword, _ in _COLUMN_LAYOUT}
    return SummaryData(
        start=SYNTHETIC_START,
        dates=SYNTHETIC_DATES,
        wells=("P1", "P2", "I1"),
        units=units,
        _well_index=well_index,
        _field_index=field_index,
        _params=tuple(tuple(row) for row in base_rows),
    )


class ScriptedSimulator:
    """Simulator double: успешный результат с физическим артефактом."""

    def __init__(self) -> None:
        self.calls: list[SimulationRequest] = []

    @property
    def identity(self) -> SimulatorIdentity:
        return SimulatorIdentity(
            simulator_id="scripted-simulator/NOT_OFFICIAL",
            simulator_version="1.0.0",
            configuration={"mode": "synthetic"},
        )

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        self.calls.append(request)
        data = b'{"format":"aios.execution.opm_summary","physical_only":true,"steps":3}'
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "trajectory.json").write_bytes(data)
        return SimulationResult.create(
            status=SimulationStatus.SUCCESS,
            trajectory_ref=TrajectoryArtifactRef(
                relative_path="trajectory.json",
                content_digest=digest_bytes(data),
                media_type="application/json",
                byte_size=len(data),
                producer_id="scripted-simulator@1.0.0",
            ),
            metadata={"report_steps": 3},
        )


class ExplodingSimulator:
    """Simulator double, падающий исключением."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> SimulatorIdentity:
        return SimulatorIdentity(
            simulator_id="exploding-simulator/NOT_OFFICIAL",
            simulator_version="1.0.0",
        )

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        self.calls += 1
        raise RuntimeError("synthetic simulator explosion")


__all__ = [
    "SYNTHETIC_DATES",
    "SYNTHETIC_START",
    "ExplodingSimulator",
    "ScriptedSimulator",
    "synthetic_summary_data",
]
