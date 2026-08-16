"""Адаптер OPM Flow: рабочая копия кейса, запуск, чтение сводки."""

from aios.opm.runner import (
    OpmRunResult,
    SimulationError,
    build_command,
    run_simulation,
)
from aios.opm.summary import SummaryData, SummaryReadError, read_summary
from aios.opm.workspace import (
    HISTORY_INCLUDE,
    SCHEDULE_INCLUDE,
    Workspace,
    WorkspaceError,
    prepare_workspace,
)

__all__ = [
    "HISTORY_INCLUDE",
    "SCHEDULE_INCLUDE",
    "OpmRunResult",
    "SimulationError",
    "SummaryData",
    "SummaryReadError",
    "Workspace",
    "WorkspaceError",
    "build_command",
    "prepare_workspace",
    "read_summary",
    "run_simulation",
]
