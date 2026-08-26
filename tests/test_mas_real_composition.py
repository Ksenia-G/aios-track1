"""The real OPM closed-loop composition: construction and wiring.

These tests do not run OPM. They prove that the production composition
assembles the repository's own real components and refuses to stand in for
any of them.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from aios.agents.constraint_guardian import ConstraintGuardian
from aios.execution import ExecutionMode, SimulatorRuntime
from aios.mas.orchestration import (
    ClosedLoopOrchestrator,
    CompositionError,
    OpmChdd,
    OpmRealizedStateExtractor,
    OpmRunLocator,
    RealizedStateAdvancer,
    build_real_closed_loop,
)
from aios.mas.orchestration.boundaries import BoundaryError, SimulatorRuntimeExecutor


def _field_state() -> object:
    """Minimal real FieldState -- the composition only holds it, never invents it."""
    from aios.schedule.state import FieldState

    return FieldState(as_of=datetime.date(2020, 1, 1), known_wells=(), wells={})


# --- C3: restart mode must carry its date ------------------------------------


class _RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, candidate: object, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return object()


def test_restart_mode_requires_an_explicit_restart_date() -> None:
    with pytest.raises(BoundaryError, match="restart_date"):
        SimulatorRuntimeExecutor(
            runtime=_RecordingRuntime(),  # type: ignore[arg-type]
            workspace_root=Path("/tmp/ws"),
            mode=ExecutionMode.RESTART,
        )


def test_restart_date_reaches_the_runtime() -> None:
    """The date used to be held in a field that was never passed on."""
    runtime = _RecordingRuntime()
    restart_date = datetime.date(2020, 6, 1)
    executor = SimulatorRuntimeExecutor(
        runtime=runtime,  # type: ignore[arg-type]
        workspace_root=Path("/tmp/ws"),
        mode=ExecutionMode.RESTART,
        restart_date=restart_date,
    )
    executor.execute(object(), run_label="run-1")  # type: ignore[arg-type]

    assert runtime.calls[0]["mode"] is ExecutionMode.RESTART
    assert runtime.calls[0]["restart_date"] == restart_date


def test_full_mode_passes_no_restart_date() -> None:
    runtime = _RecordingRuntime()
    executor = SimulatorRuntimeExecutor(
        runtime=runtime,  # type: ignore[arg-type]
        workspace_root=Path("/tmp/ws"),
    )
    executor.execute(object(), run_label="run-1")  # type: ignore[arg-type]

    assert runtime.calls[0]["mode"] is ExecutionMode.FULL
    assert runtime.calls[0]["restart_date"] is None


# --- C2/C4/C5/C6: the composition assembles the real components --------------


def test_composition_requires_a_restart_date_in_restart_mode(model_y_case, tmp_path) -> None:
    from aios.schedule.events import Schedule

    with pytest.raises(CompositionError, match="restart_date"):
        build_real_closed_loop(
            case=model_y_case,
            history_text="-- history",
            history_start_date=datetime.date(2020, 1, 1),
            initial_state=_field_state(),
            baseline=Schedule(steps=()),
            workspace_root=tmp_path,
            mode=ExecutionMode.RESTART,
        )


def test_composition_binds_the_real_components(model_y_case, tmp_path) -> None:
    from aios.schedule.events import Schedule

    composition = build_real_closed_loop(
        case=model_y_case,
        history_text="-- history",
        history_start_date=datetime.date(2020, 1, 1),
        initial_state=_field_state(),
        baseline=Schedule(steps=()),
        workspace_root=tmp_path,
    )

    # Real runtime zone, not a parallel one.
    assert isinstance(composition.runtime, SimulatorRuntime)
    assert isinstance(composition.executor, SimulatorRuntimeExecutor)
    # Real legality, real economics, realized-state bridge and advancement.
    assert isinstance(composition.guardian, ConstraintGuardian)
    assert isinstance(composition.chdd, OpmChdd)
    assert isinstance(composition.realized_state_extractor, OpmRealizedStateExtractor)
    assert isinstance(composition.state_advancer, RealizedStateAdvancer)
    # Next state comes from the committed run, not from the same card re-dated.
    assert (
        composition.state_advancer.realized_state_extractor is composition.realized_state_extractor
    )


def test_composition_boundaries_satisfy_the_orchestrator(model_y_case, tmp_path) -> None:
    """The composition is directly usable as the orchestrator's boundaries."""
    from aios.schedule.events import Schedule
    from tests.test_mas_orchestration import (
        StubArbiter,
        StubInjectionAgent,
        StubMagnitudeResolver,
        StubProductionAgent,
        _config,
    )

    composition = build_real_closed_loop(
        case=model_y_case,
        history_text="-- history",
        history_start_date=datetime.date(2020, 1, 1),
        initial_state=_field_state(),
        baseline=Schedule(steps=()),
        workspace_root=tmp_path,
    )

    orchestrator = ClosedLoopOrchestrator(
        config=_config(),
        production_agent=StubProductionAgent(),
        injection_agent=StubInjectionAgent(),
        arbiter=StubArbiter(),
        magnitude_resolver=StubMagnitudeResolver(),
        **composition.boundaries(),
    )
    assert orchestrator.config.simulation_budget > 0


def test_run_locator_mirrors_the_runtime_workspace_layout(tmp_path) -> None:
    locator = OpmRunLocator(workspace_root=tmp_path)
    out = locator.output_dir("run-abc")
    assert out == tmp_path / "run-abc" / "opm" / "OUT"
    smspec, unsmry = locator.summary_paths("run-abc", "MODEL_Y")
    assert smspec.name == "MODEL_Y.SMSPEC"
    assert unsmry.name == "MODEL_Y.UNSMRY"


def test_summary_is_addressed_by_the_deck_case_name(model_y_case, tmp_path) -> None:
    """The OPM case name comes from the deck path, not from the raw setting.

    `HistorySpec.data_file` is the configured file name as text; only
    `data_path` is the resolved deck path a stem can be taken from. Getting
    this wrong is invisible until a real run has already succeeded.
    """
    from aios.schedule.events import Schedule

    composition = build_real_closed_loop(
        case=model_y_case,
        history_text="-- history",
        history_start_date=datetime.date(2020, 1, 1),
        initial_state=_field_state(),
        baseline=Schedule(steps=()),
        workspace_root=tmp_path,
    )
    expected_stem = model_y_case.history.data_path.stem
    smspec, unsmry = composition.locator.summary_paths("run-1", expected_stem)
    assert smspec.name == f"{expected_stem}.SMSPEC"
    assert unsmry.name == f"{expected_stem}.UNSMRY"


def test_missing_opm_summary_is_a_closed_failure(tmp_path) -> None:
    """No summary means no realized state -- never a fabricated one."""
    locator = OpmRunLocator(workspace_root=tmp_path)
    with pytest.raises(CompositionError, match="summary for run"):
        locator.read_summary("run-missing", "MODEL_Y")
