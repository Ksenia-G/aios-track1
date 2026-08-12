"""Тесты реестра ограничений."""

import json

import pytest
from conftest import (
    artifact_reading_post_constraint,
    blocking_pre_constraint,
    post_violation_constraint,
)

from aios.evaluation.artifacts import ArtifactStore
from aios.evaluation.constraints import (
    ConstraintRegistry,
    ConstraintReport,
    ConstraintViolation,
)
from aios.evaluation.simulation import (
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)


def _dummy_result() -> SimulationResult:
    return SimulationResult.create(status=SimulationStatus.SUCCESS)


def test_empty_registry_reports_feasible(sample_request: SimulationRequest) -> None:
    registry = ConstraintRegistry()
    report = registry.check_pre(sample_request)
    assert report.is_feasible is True
    assert report.has_blocking is False
    assert report.violations == ()


def test_pre_violation_is_reported(sample_request: SimulationRequest) -> None:
    registry = ConstraintRegistry()
    registry.register_pre("test.pre.blocking", blocking_pre_constraint)
    report = registry.check_pre(sample_request)
    assert report.is_feasible is False
    assert report.has_blocking is True
    assert report.violations[0].constraint_id == "test.pre.blocking"


def test_post_violation_is_reported(artifact_store: ArtifactStore) -> None:
    registry = ConstraintRegistry()
    registry.register_post("test.post.violated", post_violation_constraint)
    report = registry.check_post(_dummy_result(), artifact_store)
    assert report.is_feasible is False
    # Не блокирующее нарушение: результат уже получен, запрещать нечего.
    assert report.has_blocking is False


def test_post_predicate_reads_artifact_through_reader(artifact_store: ArtifactStore) -> None:
    """POST-предикат добирается до байтов только через ArtifactReader."""
    data = json.dumps({"steps": [{"synthetic_channel_a": 1.0}] * 4}).encode("utf-8")
    ref = artifact_store.put_bytes(data, media_type="application/json", producer_id="test")
    result = SimulationResult.create(status=SimulationStatus.SUCCESS, trajectory_ref=ref)

    registry = ConstraintRegistry()
    registry.register_post("test.post.artifact", artifact_reading_post_constraint)
    report = registry.check_post(result, artifact_store)

    assert report.violations[0].metadata == {"observed_steps": 4}


def test_non_blocking_violation_still_makes_candidate_infeasible() -> None:
    report = ConstraintReport(
        (ConstraintViolation(constraint_id="c", message="m", is_blocking=False),)
    )
    assert report.is_feasible is False
    assert report.has_blocking is False


def test_duplicate_pre_constraint_id_is_rejected() -> None:
    registry = ConstraintRegistry()
    registry.register_pre("test.pre", blocking_pre_constraint)
    with pytest.raises(ValueError):
        registry.register_pre("test.pre", blocking_pre_constraint)


def test_duplicate_post_constraint_id_is_rejected() -> None:
    registry = ConstraintRegistry()
    registry.register_post("test.post", post_violation_constraint)
    with pytest.raises(ValueError):
        registry.register_post("test.post", post_violation_constraint)


def test_registered_ids_are_exposed() -> None:
    registry = ConstraintRegistry()
    registry.register_pre("test.pre", blocking_pre_constraint)
    registry.register_post("test.post", post_violation_constraint)
    assert registry.pre_constraint_ids == ("test.pre",)
    assert registry.post_constraint_ids == ("test.post",)


def test_registry_passes_reader_to_every_post_predicate(artifact_store: ArtifactStore) -> None:
    seen: list[object] = []

    def spy(result: SimulationResult, artifacts: object) -> list[ConstraintViolation]:
        seen.append(artifacts)
        return []

    registry = ConstraintRegistry()
    registry.register_post("test.post.spy", spy)
    registry.check_post(_dummy_result(), artifact_store)
    assert seen == [artifact_store]


def test_violations_from_several_predicates_are_merged(sample_request: SimulationRequest) -> None:
    registry = ConstraintRegistry()
    registry.register_pre("test.pre.a", blocking_pre_constraint)
    registry.register_pre(
        "test.pre.b",
        lambda request: [ConstraintViolation(constraint_id="test.pre.b", message="second")],
    )
    report = registry.check_pre(sample_request)
    assert len(report.violations) == 2
