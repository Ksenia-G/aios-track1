"""RUNTIME-EXECUTION-001: адаптер OpmFlowSimulator над протоколом Simulator."""

from __future__ import annotations

from pathlib import Path

import pytest

import aios.execution.simulator_adapter as adapter_module
from aios.evaluation.simulation import SimulationRequest, SimulationStatus
from aios.evaluation.simulator import Simulator
from aios.execution.execution import FullOpmRun, serialize_trajectory_document
from aios.execution.simulator_adapter import (
    OPM_SIMULATOR_ID,
    OpmFlowSimulator,
    decode_trajectory_artifact,
)
from aios.opm.runner import OpmRunResult
from aios.schedule.generator import schedule_sha256
from tests.support.runtime_fixtures import synthetic_summary_data


def _request(schedule_text: str) -> SimulationRequest:
    return SimulationRequest(
        payload={"schedule_text": schedule_text},
        schedule_digest=schedule_sha256(schedule_text),
        model_digest="SYN-model",
        scenario_digest="b" * 64,
    )


def _ok_run(*args, **kwargs):
    summary = kwargs.get("summary", synthetic_summary_data())
    return FullOpmRun(workspace=None, run_result=_stub_result(), summary=summary)


def _stub_result(*, exit_code: int = 0) -> OpmRunResult:
    return OpmRunResult(
        exit_code=exit_code,
        wall_seconds=1.5,
        command=("docker", "run", "example/flow"),
        log_path=Path("flow.log"),
        output_files=("MINI.SMPSPEC", "MINI.UNSMRY"),
        report_steps=3,
        final_date="2020-04-01",
        warnings=(),
        warning_count=0,
        errors=(),
    )


@pytest.fixture
def simulator(mini_case) -> OpmFlowSimulator:
    return OpmFlowSimulator(case=mini_case, history_text="-- synthetic history\n")


class TestProtocolConformance:
    def test_is_instance_of_simulator_protocol(self, simulator: OpmFlowSimulator) -> None:
        assert isinstance(simulator, Simulator)

    def test_identity_is_deterministic(self, mini_case, simulator) -> None:
        identity = simulator.identity
        assert identity.simulator_id == OPM_SIMULATOR_ID
        assert identity.simulator_version == f"docker:{mini_case.simulator.docker_image}"
        again = OpmFlowSimulator(case=mini_case, history_text="x").identity
        assert identity.to_json() == again.to_json()


class TestInputValidation:
    def test_missing_schedule_text_is_invalid_input(self, simulator, tmp_path) -> None:
        request = SimulationRequest(payload={})
        result = simulator.run(request, tmp_path / "ws")
        assert result.status is SimulationStatus.INVALID_INPUT
        assert "schedule_text" in result.failure.message

    def test_digest_mismatch_is_invalid_input(self, simulator, tmp_path) -> None:
        request = _request("DATES\n 01 'FEB' 2020 /\n/\n")
        tampered = SimulationRequest(
            payload=request.payload,
            schedule_digest="f" * 64,
            model_digest="SYN-model",
        )
        result = simulator.run(tampered, tmp_path / "ws")
        assert result.status is SimulationStatus.INVALID_INPUT
        assert result.failure.details["expected"] == "f" * 64


class TestRunOutcomes:
    def test_success_produces_physical_artifact(self, monkeypatch, simulator, tmp_path) -> None:
        monkeypatch.setattr(adapter_module, "run_full_opm", _ok_run)
        workspace = tmp_path / "ws"
        request = _request("DATES\n 01 'FEB' 2020 /\n/\n")
        result = simulator.run(request, workspace)
        assert result.status is SimulationStatus.SUCCESS
        assert result.trajectory_ref.byte_size == (workspace / "trajectory.json").stat().st_size
        document = decode_trajectory_artifact((workspace / "trajectory.json").read_bytes())
        assert document["physical_only"] is True
        assert set(document["well_vectors"]["WBHP"]) == {"P1", "P2", "I1"}
        assert result.metadata["report_steps"] == 3
        assert result.metadata["final_date"] == "2020-04-01"
        # Wall time и команда -- диагностика, не метаданные результата.
        assert "wall_seconds" in result.diagnostics["opm_run"]

    def test_failed_flow_becomes_crashed_not_success(
        self, monkeypatch, simulator, tmp_path
    ) -> None:
        monkeypatch.setattr(
            adapter_module,
            "run_full_opm",
            lambda *a, **kw: FullOpmRun(workspace=None, run_result=_stub_result(), summary=None),
        )
        request = _request("DATES\n 01 'FEB' 2020 /\n/\n")
        result = simulator.run(request, tmp_path / "ws")
        assert result.status is SimulationStatus.CRASHED
        assert result.failure is not None
        assert result.trajectory_ref is None

    def test_timeout_exit_code_maps_to_timeout(self, monkeypatch, simulator, tmp_path) -> None:
        monkeypatch.setattr(
            adapter_module,
            "run_full_opm",
            lambda *a, **kw: FullOpmRun(
                workspace=None, run_result=_stub_result(exit_code=124), summary=None
            ),
        )
        request = _request("DATES\n 01 'FEB' 2020 /\n/\n")
        result = simulator.run(request, tmp_path / "ws")
        assert result.status is SimulationStatus.TIMEOUT

    def test_same_input_same_result_digest(self, monkeypatch, simulator, tmp_path) -> None:
        monkeypatch.setattr(adapter_module, "run_full_opm", _ok_run)
        request = _request("DATES\n 01 'FEB' 2020 /\n/\n")
        first = simulator.run(request, tmp_path / "ws1")
        second = simulator.run(request, tmp_path / "ws2")
        assert first.result_digest == second.result_digest


def test_decode_rejects_foreign_format() -> None:
    foreign = serialize_trajectory_document({"format": "something-else"})
    with pytest.raises(ValueError, match="unexpected trajectory format"):
        decode_trajectory_artifact(foreign)
