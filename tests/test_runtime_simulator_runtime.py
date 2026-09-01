"""RUNTIME-EXECUTION-001: фасад SimulatorRuntime -- цепочка кандидат->результат."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

import aios.execution.simulator_runtime as runtime_module
from aios.evaluation.simulation import SimulationStatus
from aios.execution.execution import (
    FullOpmRun,
    RestartChainOutcome,
    RestartExecutionError,
)
from aios.execution.provenance import ExecutionMode
from aios.execution.simulator_runtime import SimulatorRuntime
from aios.mas.common import CandidateKind
from aios.mas.invariants import MasInvariantError
from aios.mas.magnitude import MagnitudeCandidate
from aios.opm.restart import RestartSpec
from aios.opm.runner import OpmRunResult
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule import ProducerControl, WellStatus
from tests.support.runtime_fixtures import (
    ExplodingSimulator,
    ScriptedSimulator,
    synthetic_summary_data,
)


def producer(well: str, liquid_rate: float) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=liquid_rate)


def _ok_run_result() -> OpmRunResult:
    return OpmRunResult(
        exit_code=0,
        wall_seconds=1.0,
        command=("docker",),
        log_path=Path("flow.log"),
        output_files=(),
        report_steps=3,
        final_date="2020-04-01",
        warnings=(),
        warning_count=0,
        errors=(),
    )


def candidate(
    *,
    kind: CandidateKind = CandidateKind.AGENT_BUNDLE,
    effective_date: datetime.date = datetime.date(2020, 2, 1),
) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=kind,
        bundle_digest="b" * 64,
        effective_date=effective_date,
        producers=(producer("P1", 130.0),),
        injectors=(),
        source_intent_digests=("d" * 64,),
        resolver_version="tests.runtime/1.0.0",
    )


@pytest.fixture
def runtime(mini_case):
    state, baseline, history_text, start_date = read_baseline_inputs(mini_case)
    return SimulatorRuntime(
        case=mini_case,
        simulator=ScriptedSimulator(),
        history_text=history_text,
        history_start_date=start_date,
        state=state,
        baseline=baseline,
        model_digest="SYN-model-digest",
    ), mini_case


class TestSimulationFloor:
    def test_batch_without_hold_is_rejected(self, runtime) -> None:
        facade, _ = runtime
        with pytest.raises(MasInvariantError):
            facade.ensure_simulation_floor((candidate(kind=CandidateKind.DETERMINISTIC_BASELINE),))

    def test_floor_accepts_team_fixture_set(self, runtime) -> None:
        from tests.support.mas_team_fixtures import synthetic_candidate_set_with_floor

        facade, _ = runtime
        facade.ensure_simulation_floor(synthetic_candidate_set_with_floor())


class TestFullExecution:
    def test_request_binds_schedule_identity(self, runtime, tmp_path) -> None:
        facade, _ = runtime
        execution = facade.execute(candidate(), workspace_root=tmp_path, run_label="r1")
        compilation = facade.compile(candidate())
        assert execution.request.schedule_digest == compilation.schedule_sha256
        assert execution.request.payload["schedule_text"] == compilation.schedule_text
        assert execution.request.model_digest == "SYN-model-digest"
        assert execution.request.scenario_digest == "b" * 64

    def test_provenance_records_full_mode(self, runtime, tmp_path) -> None:
        facade, _ = runtime
        source = candidate()
        execution = facade.execute(source, workspace_root=tmp_path, run_label="r2")
        provenance = execution.provenance.to_json()
        assert provenance["execution_mode"] == "FULL"
        assert provenance["candidate_kind"] == str(CandidateKind.AGENT_BUNDLE)
        assert provenance["restart_spec"] is None
        assert provenance["reconciliation"] is None
        assert provenance["simulator_identity"]["simulator_id"] == (
            "scripted-simulator/NOT_OFFICIAL"
        )

    def test_same_candidate_same_simulation_key(self, runtime, tmp_path) -> None:
        facade, _ = runtime
        first = facade.execute(candidate(), workspace_root=tmp_path, run_label="a")
        second = facade.execute(candidate(), workspace_root=tmp_path, run_label="b")
        assert first.provenance.simulation_key == second.provenance.simulation_key

    def test_simulator_exception_never_fakes_success(self, mini_case, tmp_path) -> None:
        state, baseline, history_text, start_date = read_baseline_inputs(mini_case)
        facade = SimulatorRuntime(
            case=mini_case,
            simulator=ExplodingSimulator(),
            history_text=history_text,
            history_start_date=start_date,
            state=state,
            baseline=baseline,
        )
        execution = facade.execute(candidate(), workspace_root=tmp_path / "ws", run_label="boom")
        assert execution.result.status is SimulationStatus.CRASHED
        assert "synthetic simulator explosion" in execution.result.failure.message
        # Физического результата не существует: артефакта нет.
        assert execution.result.trajectory_ref is None


class TestRestartExecution:
    def test_restart_requires_explicit_date(self, runtime, tmp_path) -> None:
        facade, _ = runtime
        with pytest.raises(ValueError, match="restart_date"):
            facade.execute(
                candidate(),
                workspace_root=tmp_path,
                run_label="r",
                mode=ExecutionMode.RESTART,
            )

    def test_chain_failure_is_typed_crash_with_restart_provenance(
        self, monkeypatch, runtime, tmp_path
    ) -> None:
        facade, _ = runtime

        def failing_chain(*args, **kwargs):
            raise RestartExecutionError("no restart state at 2020-03-01")

        monkeypatch.setattr(runtime_module, "run_restart_chain", failing_chain)
        execution = facade.execute(
            candidate(),
            workspace_root=tmp_path,
            run_label="r",
            mode=ExecutionMode.RESTART,
            restart_date=datetime.date(2020, 3, 1),
        )
        assert execution.result.status is SimulationStatus.CRASHED
        assert execution.result.failure.details["stage"] == "restart.chain"
        assert execution.provenance.execution_mode is ExecutionMode.RESTART

    def test_successful_restart_carries_spec_and_reconciliation(
        self, monkeypatch, runtime, tmp_path
    ) -> None:
        facade, _ = runtime

        def ok_result():
            return OpmRunResult(
                exit_code=0,
                wall_seconds=1.0,
                command=("docker",),
                log_path=Path("flow.log"),
                output_files=(),
                report_steps=3,
                final_date="2020-04-01",
                warnings=(),
                warning_count=0,
                errors=(),
            )

        spec = RestartSpec(
            source_stem="AIOS_RESTART_SOURCE",
            seqnum=83,
            restart_date=datetime.date(2020, 3, 1),
        )

        def ok_chain(*args, **kwargs):
            return RestartChainOutcome(
                source_run=FullOpmRun(
                    workspace=None,
                    run_result=ok_result(),
                    summary=synthetic_summary_data(),
                ),
                unrst_path=tmp_path / "src.UNRST",
                spec=spec,
                solution_include="INCLUDE/SOLUTION.INC",
                continuation_run=FullOpmRun(
                    workspace=None,
                    run_result=ok_result(),
                    summary=synthetic_summary_data(),
                ),
            )

        monkeypatch.setattr(runtime_module, "run_restart_chain", ok_chain)
        execution = facade.execute(
            candidate(),
            workspace_root=tmp_path,
            run_label="r",
            mode=ExecutionMode.RESTART,
            restart_date=datetime.date(2020, 3, 1),
        )
        assert execution.result.status is SimulationStatus.SUCCESS
        assert execution.result.metadata["execution_mode"] == "RESTART"
        payload = execution.provenance.to_json()
        assert payload["restart_spec"] == spec.to_json()
        reconciliation = payload["reconciliation"]
        assert reconciliation["verdict"] == "PASS"

    def test_failed_reconciliation_is_typed_non_converged_not_success(
        self, monkeypatch, runtime, tmp_path
    ) -> None:
        """Регрессия: расхождение restart с чистым прогоном -- не SUCCESS.

        Downstream (Guardian/CHDD) читает result.status; физически разошедшийся
        ускоренный прогон обязан приходить типизированным отказом.
        """
        facade, _ = runtime
        spec = RestartSpec(
            source_stem="AIOS_RESTART_SOURCE",
            seqnum=83,
            restart_date=datetime.date(2020, 3, 1),
        )

        def diverged_chain(*args, **kwargs):
            return RestartChainOutcome(
                source_run=FullOpmRun(
                    workspace=None,
                    run_result=_ok_run_result(),
                    summary=synthetic_summary_data(),
                ),
                unrst_path=tmp_path / "src.UNRST",
                spec=spec,
                solution_include="INCLUDE/SOLUTION.INC",
                continuation_run=FullOpmRun(
                    workspace=None,
                    run_result=_ok_run_result(),
                    summary=synthetic_summary_data(perturbation={("FOPR", "", 1): 500.0}),
                ),
            )

        monkeypatch.setattr(runtime_module, "run_restart_chain", diverged_chain)
        execution = facade.execute(
            candidate(),
            workspace_root=tmp_path,
            run_label="r",
            mode=ExecutionMode.RESTART,
            restart_date=datetime.date(2020, 3, 1),
        )
        assert execution.result.status is SimulationStatus.NON_CONVERGED
        failure = execution.result.failure
        assert failure is not None
        assert failure.details["stage"] == "restart.reconciliation"
        assert failure.details["verdict"] == "FAIL"
        assert failure.details["tolerance"] == pytest.approx(1e-6)
        assert failure.details["max_abs_deviation"] > 1.0
        assert execution.result.trajectory_ref is None
        provenance = execution.provenance.to_json()
        assert provenance["restart_spec"] == spec.to_json()
        assert provenance["reconciliation"]["verdict"] == "FAIL"

    def test_failed_reconciliation_artifact_is_not_written(
        self, monkeypatch, runtime, tmp_path
    ) -> None:
        """При FAIL reconciliation траектория не сохраняется как успешная."""
        facade, _ = runtime

        def diverged_chain(*args, **kwargs):
            return RestartChainOutcome(
                source_run=FullOpmRun(
                    workspace=None,
                    run_result=_ok_run_result(),
                    summary=synthetic_summary_data(),
                ),
                unrst_path=tmp_path / "src.UNRST",
                spec=RestartSpec(
                    source_stem="AIOS_RESTART_SOURCE",
                    seqnum=83,
                    restart_date=datetime.date(2020, 3, 1),
                ),
                solution_include="INCLUDE/SOLUTION.INC",
                continuation_run=FullOpmRun(
                    workspace=None,
                    run_result=_ok_run_result(),
                    summary=synthetic_summary_data(perturbation={("WBHP", "P1", 2): -90.0}),
                ),
            )

        monkeypatch.setattr(runtime_module, "run_restart_chain", diverged_chain)
        execution = facade.execute(
            candidate(),
            workspace_root=tmp_path,
            run_label="r",
            mode=ExecutionMode.RESTART,
            restart_date=datetime.date(2020, 3, 1),
        )
        assert execution.result.status is SimulationStatus.NON_CONVERGED
        assert list(tmp_path.rglob("trajectory.json")) == []


class TestResultRequestBinding:
    """Every result the runtime returns names the request that produced it."""

    def test_successful_result_is_bound_to_its_request(self, runtime, tmp_path) -> None:
        from aios.evaluation.simulation import compute_request_digest, compute_simulation_key

        facade, _case = runtime
        execution = facade.execute(candidate(), workspace_root=tmp_path / "ws", run_label="bound")
        assert execution.result.is_request_bound
        assert execution.result.request_digest == compute_request_digest(execution.request)
        assert execution.result.simulation_key == compute_simulation_key(
            execution.request, facade._simulator.identity
        )
        assert execution.result.simulation_key == execution.provenance.simulation_key

    def test_failed_result_is_bound_too(self, mini_case, tmp_path) -> None:
        """A crash still belongs to a request, and must say which one."""
        from aios.evaluation.simulation import compute_request_digest

        state, baseline, history_text, start_date = read_baseline_inputs(mini_case)
        facade = SimulatorRuntime(
            case=mini_case,
            simulator=ExplodingSimulator(),
            history_text=history_text,
            history_start_date=start_date,
            state=state,
            baseline=baseline,
        )
        execution = facade.execute(candidate(), workspace_root=tmp_path / "ws", run_label="boom")
        assert execution.result.status is SimulationStatus.CRASHED
        assert execution.result.is_request_bound
        assert execution.result.request_digest == compute_request_digest(execution.request)

    def test_simulator_returning_a_foreign_binding_is_refused(self, mini_case, tmp_path) -> None:
        """A result already bound to another request is a wiring fault."""
        from aios.evaluation.simulation import SimulationResult, SimulationStatus
        from aios.execution import RuntimeExecutionBindingError

        class ForeignBindingSimulator:
            @property
            def identity(self):
                return ScriptedSimulator().identity

            def run(self, request, workspace):
                return SimulationResult.create(
                    status=SimulationStatus.SUCCESS,
                    request_digest="f" * 64,
                    simulation_key="f" * 64,
                )

        state, baseline, history_text, start_date = read_baseline_inputs(mini_case)
        facade = SimulatorRuntime(
            case=mini_case,
            simulator=ForeignBindingSimulator(),
            history_text=history_text,
            history_start_date=start_date,
            state=state,
            baseline=baseline,
        )
        with pytest.raises(RuntimeExecutionBindingError, match="different request"):
            facade.execute(candidate(), workspace_root=tmp_path / "ws", run_label="foreign")
