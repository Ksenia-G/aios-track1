"""Тесты content-identity результата симуляции.

result_digest -- не подсказка от вызывающей стороны, а вычисляемая
идентичность результата. Если ему можно подсунуть произвольное значение,
воспроизводимость превращается в декорацию.
"""

import pytest

from aios.evaluation.artifacts import TrajectoryArtifactRef
from aios.evaluation.simulation import (
    ResultDigestMismatchError,
    SimulationFailure,
    SimulationResult,
    SimulationStatus,
    compute_result_digest,
)

PAYLOAD_DIGEST = "ab" * 32


def _ref(**overrides: object) -> TrajectoryArtifactRef:
    fields: dict[str, object] = {
        "relative_path": "trajectory.json",
        "content_digest": PAYLOAD_DIGEST,
        "media_type": "application/json",
        "byte_size": 128,
        "producer_id": "test-simulator",
    }
    fields.update(overrides)
    return TrajectoryArtifactRef(**fields)  # type: ignore[arg-type]


def _result(**overrides: object) -> SimulationResult:
    fields: dict[str, object] = {
        "status": SimulationStatus.SUCCESS,
        "trajectory_ref": _ref(),
        "metadata": {"step_count": 8},
    }
    fields.update(overrides)
    return SimulationResult.create(**fields)  # type: ignore[arg-type]


def test_relative_path_is_not_part_of_identity() -> None:
    """Перенос артефакта из workspace в хранилище не меняет идентичность."""
    in_workspace = _result(trajectory_ref=_ref(relative_path="trajectory.json"))
    in_store = _result(trajectory_ref=_ref(relative_path="blobs/ab/cdef"))
    assert in_workspace.result_digest == in_store.result_digest


def test_changed_artifact_content_changes_digest() -> None:
    assert (
        _result().result_digest
        != _result(trajectory_ref=_ref(content_digest="cd" * 32)).result_digest
    )


@pytest.mark.parametrize(
    "ref_overrides",
    [
        {"byte_size": 129},
        {"media_type": "application/octet-stream"},
        {"producer_id": "other-simulator"},
    ],
)
def test_changed_artifact_properties_change_digest(ref_overrides: dict) -> None:
    assert _result().result_digest != _result(trajectory_ref=_ref(**ref_overrides)).result_digest


def test_changed_status_changes_digest() -> None:
    assert _result().result_digest != _result(status=SimulationStatus.PARTIAL).result_digest


def test_changed_metadata_changes_digest() -> None:
    assert _result().result_digest != _result(metadata={"step_count": 9}).result_digest


def test_absent_artifact_differs_from_present_artifact() -> None:
    assert _result().result_digest != _result(trajectory_ref=None).result_digest


def test_failure_information_is_part_of_identity() -> None:
    crashed = SimulationResult.create(
        status=SimulationStatus.CRASHED,
        failure=SimulationFailure(message="boom"),
    )
    other = SimulationResult.create(
        status=SimulationStatus.CRASHED,
        failure=SimulationFailure(message="different"),
    )
    assert crashed.result_digest != other.result_digest


def test_diagnostics_are_not_part_of_identity() -> None:
    """Диагностика различается между машинами при одном физическом результате."""
    quick = SimulationResult.create(
        status=SimulationStatus.SUCCESS,
        trajectory_ref=_ref(),
        metadata={"step_count": 8},
        diagnostics={"elapsed_seconds": 1.0},
    )
    slow = SimulationResult.create(
        status=SimulationStatus.SUCCESS,
        trajectory_ref=_ref(),
        metadata={"step_count": 8},
        diagnostics={"elapsed_seconds": 99.0},
    )
    assert quick.result_digest == slow.result_digest


def test_arbitrary_digest_is_rejected_by_constructor() -> None:
    with pytest.raises(ResultDigestMismatchError):
        SimulationResult(
            status=SimulationStatus.SUCCESS,
            result_digest="0" * 64,
            trajectory_ref=_ref(),
            metadata={"step_count": 8},
        )


def test_tampered_digest_is_rejected_by_from_json() -> None:
    payload = _result().to_json()
    payload["result_digest"] = "0" * 64
    with pytest.raises(ResultDigestMismatchError):
        SimulationResult.from_json(payload)


def test_tampered_metadata_is_rejected_by_from_json() -> None:
    payload = _result().to_json()
    payload["metadata"] = {"step_count": 1000}
    with pytest.raises(ResultDigestMismatchError):
        SimulationResult.from_json(payload)


def test_valid_json_roundtrip() -> None:
    original = _result()
    assert SimulationResult.from_json(original.to_json()) == original


def test_compute_result_digest_matches_created_result() -> None:
    result = _result()
    assert result.result_digest == compute_result_digest(
        status=result.status,
        trajectory_ref=result.trajectory_ref,
        metadata=result.metadata,
        failure=result.failure,
    )
