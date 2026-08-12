"""Тесты детерминированности фейкового симулятора."""

import json
from pathlib import Path

from aios.evaluation.canonical import digest_bytes
from aios.evaluation.fake_simulator import FAKE_ARTIFACT_FILENAME, FakeSimulator
from aios.evaluation.simulation import SimulationRequest, SimulationStatus
from aios.evaluation.simulator import Simulator


def _run(
    simulator: FakeSimulator, request: SimulationRequest, workspace: Path
) -> tuple[str, bytes]:
    result = simulator.run(request, workspace)
    data = (workspace / FAKE_ARTIFACT_FILENAME).read_bytes()
    return result.result_digest, data


def test_fake_simulator_satisfies_protocol() -> None:
    assert isinstance(FakeSimulator(), Simulator)


def test_run_is_deterministic(sample_request: SimulationRequest, tmp_path: Path) -> None:
    simulator = FakeSimulator()
    first_digest, first_bytes = _run(simulator, sample_request, tmp_path / "a")
    second_digest, second_bytes = _run(simulator, sample_request, tmp_path / "b")
    assert first_digest == second_digest
    assert first_bytes == second_bytes


def test_declared_digest_matches_written_bytes(
    sample_request: SimulationRequest, tmp_path: Path
) -> None:
    result = FakeSimulator().run(sample_request, tmp_path)
    assert result.status is SimulationStatus.SUCCESS
    assert result.trajectory_ref is not None
    data = (tmp_path / FAKE_ARTIFACT_FILENAME).read_bytes()
    assert result.trajectory_ref.content_digest == digest_bytes(data)
    assert result.trajectory_ref.byte_size == len(data)


def test_changed_request_changes_result(sample_request: SimulationRequest, tmp_path: Path) -> None:
    simulator = FakeSimulator()
    baseline_digest, baseline_bytes = _run(simulator, sample_request, tmp_path / "a")
    modified = SimulationRequest(
        payload={"variant": "modified", "levels": [1, 2, 3]},
        seed=sample_request.seed,
        model_digest=sample_request.model_digest,
    )
    changed_digest, changed_bytes = _run(simulator, modified, tmp_path / "b")
    assert changed_digest != baseline_digest
    assert changed_bytes != baseline_bytes


def test_changed_seed_changes_result(sample_request: SimulationRequest, tmp_path: Path) -> None:
    simulator = FakeSimulator()
    baseline_digest, _ = _run(simulator, sample_request, tmp_path / "a")
    reseeded = SimulationRequest(
        payload=sample_request.payload,
        seed=(sample_request.seed or 0) + 1,
        model_digest=sample_request.model_digest,
    )
    changed_digest, _ = _run(simulator, reseeded, tmp_path / "b")
    assert changed_digest != baseline_digest


def test_changed_configuration_changes_result(
    sample_request: SimulationRequest, tmp_path: Path
) -> None:
    baseline_digest, _ = _run(FakeSimulator(step_count=8), sample_request, tmp_path / "a")
    changed_digest, _ = _run(FakeSimulator(step_count=9), sample_request, tmp_path / "b")
    assert changed_digest != baseline_digest


def test_artifact_is_marked_as_non_official_schema(
    sample_request: SimulationRequest, tmp_path: Path
) -> None:
    """Синтетические каналы не должны выглядеть как формат трека."""
    FakeSimulator(step_count=3).run(sample_request, tmp_path)
    document = json.loads((tmp_path / FAKE_ARTIFACT_FILENAME).read_text(encoding="utf-8"))
    assert document["not_official_schema"] is True
    assert len(document["steps"]) == 3
    assert set(document["steps"][0]) == {"step_index", "synthetic_channel_a", "synthetic_channel_b"}
