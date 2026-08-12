"""Тесты конвейера оценки."""

import json
import sqlite3
from collections.abc import Callable

from conftest import (
    CountingSimulator,
    CrashingSimulator,
    EngineHarness,
    FailingNpvEvaluator,
    FakeNpvEvaluator,
    LyingSimulator,
    PartialSimulator,
    artifact_reading_post_constraint,
    blocking_pre_constraint,
    post_violation_constraint,
)

from aios.evaluation.constraints import ConstraintRegistry, ConstraintStatus
from aios.evaluation.npv import EvaluatorProvenance, NpvStatus
from aios.evaluation.records import RunStatus
from aios.evaluation.simulation import SimulationRequest, SimulationStatus

HarnessFactory = Callable[..., EngineHarness]


def test_happy_path_completes_and_caches(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness(npv_evaluator=FakeNpvEvaluator())
    outcome = harness.engine.evaluate(sample_request)

    record = outcome.run_record
    assert record.status is RunStatus.COMPLETED
    assert record.simulation_status is SimulationStatus.SUCCESS
    assert record.cache_hit is False
    assert record.cached_from_run_id is None
    assert record.constraint_status is ConstraintStatus.SATISFIED
    assert record.is_feasible is True
    assert record.npv_status is NpvStatus.OK
    assert record.npv_value is not None
    assert record.npv_provenance is EvaluatorProvenance.NOT_OFFICIAL
    assert harness.db.count_cached_simulations() == 1
    assert harness.db.get_run(record.run_id) == record


def test_artifact_is_ingested_into_store(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness(npv_evaluator=FakeNpvEvaluator())
    outcome = harness.engine.evaluate(sample_request)

    result = outcome.simulation_result
    assert result is not None
    assert result.trajectory_ref is not None
    # Дескриптор указывает уже в хранилище, а не в рабочий каталог симулятора.
    assert result.trajectory_ref.relative_path.startswith("blobs/")
    assert harness.artifacts.read(result.trajectory_ref)
    assert outcome.run_record.trajectory_digest == result.trajectory_ref.content_digest


def test_cache_hit_does_not_invoke_simulator(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    simulator = CountingSimulator()
    harness = make_harness(simulator=simulator, npv_evaluator=FakeNpvEvaluator())

    first = harness.engine.evaluate(sample_request)
    second = harness.engine.evaluate(sample_request)

    assert simulator.call_count == 1
    assert second.run_record.cache_hit is True
    assert second.run_record.cached_from_run_id == first.run_record.run_id
    assert second.run_record.run_id != first.run_record.run_id
    assert second.run_record.simulation_key == first.run_record.simulation_key
    assert second.simulation_result is not None
    assert second.simulation_result.result_digest == first.run_record.metadata["result_digest"]


def test_cache_bypass_invokes_simulator_again(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    simulator = CountingSimulator()
    harness = make_harness(simulator=simulator, npv_evaluator=FakeNpvEvaluator())

    first = harness.engine.evaluate(sample_request)
    bypassed = harness.engine.evaluate(sample_request, use_cache=False)

    assert simulator.call_count == 2
    assert bypassed.run_record.cache_hit is False
    assert bypassed.run_record.cached_from_run_id is None
    # Политика кеширования не влияет на физический смысл эксперимента.
    assert bypassed.run_record.simulation_key == first.run_record.simulation_key
    assert harness.db.count_cached_simulations() == 1


def test_repeated_runs_are_kept_in_history(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness(npv_evaluator=FakeNpvEvaluator())
    first = harness.engine.evaluate(sample_request)
    harness.engine.evaluate(sample_request, use_cache=False)
    harness.engine.evaluate(sample_request)

    assert harness.db.count_runs_for_simulation_key(first.run_record.simulation_key) == 3
    assert len(harness.db.list_recent_runs(limit=10)) == 3


def test_simulator_crash_is_recorded_and_not_cached(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness(simulator=CrashingSimulator(), npv_evaluator=FakeNpvEvaluator())
    outcome = harness.engine.evaluate(sample_request)

    record = outcome.run_record
    assert record.status is RunStatus.FAILED
    assert record.simulation_status is SimulationStatus.CRASHED
    assert record.npv_status is NpvStatus.NOT_EVALUATED
    assert record.npv_value is None
    assert record.constraint_status is ConstraintStatus.NOT_CHECKED
    assert record.is_feasible is None
    assert harness.db.count_cached_simulations() == 0
    assert harness.db.get_run(record.run_id) is not None
    assert "simulation_failure" in record.metadata


def test_artifact_digest_mismatch_is_treated_as_failure(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness(simulator=LyingSimulator(), npv_evaluator=FakeNpvEvaluator())
    outcome = harness.engine.evaluate(sample_request)

    assert outcome.run_record.status is RunStatus.FAILED
    assert outcome.run_record.simulation_status is SimulationStatus.CRASHED
    assert outcome.run_record.trajectory_digest is None
    assert harness.db.count_cached_simulations() == 0


def test_evaluator_failure_is_recorded_separately(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness(npv_evaluator=FailingNpvEvaluator())
    outcome = harness.engine.evaluate(sample_request)

    record = outcome.run_record
    # Симуляция удалась, экономическая оценка -- нет. Это разные исходы.
    assert record.status is RunStatus.COMPLETED
    assert record.simulation_status is SimulationStatus.SUCCESS
    assert record.npv_status is NpvStatus.FAILED
    assert record.npv_value is None
    assert record.npv_provenance is None
    assert "npv_error" in record.metadata
    assert harness.db.count_cached_simulations() == 1


def test_missing_evaluator_leaves_npv_absent(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness()
    record = harness.engine.evaluate(sample_request).run_record

    assert record.npv_status is NpvStatus.NOT_EVALUATED
    # Ни нуля, ни -inf, ни штрафа: политика штрафов принадлежит оптимизатору.
    assert record.npv_value is None


def test_blocking_pre_constraint_prevents_simulation(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    simulator = CountingSimulator()
    registry = ConstraintRegistry()
    registry.register_pre("test.pre.blocking", blocking_pre_constraint)
    harness = make_harness(
        simulator=simulator, constraints=registry, npv_evaluator=FakeNpvEvaluator()
    )

    outcome = harness.engine.evaluate(sample_request)

    assert simulator.call_count == 0
    assert outcome.simulation_result is None
    record = outcome.run_record
    assert record.status is RunStatus.BLOCKED
    assert record.constraint_status is ConstraintStatus.BLOCKED
    assert record.is_feasible is False
    assert record.simulation_status is None
    assert record.npv_status is NpvStatus.NOT_EVALUATED
    assert record.npv_value is None
    assert harness.db.count_cached_simulations() == 0
    assert harness.db.get_run(record.run_id) is not None


def test_post_violation_does_not_suppress_npv(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    registry = ConstraintRegistry()
    registry.register_post("test.post.violated", post_violation_constraint)
    harness = make_harness(constraints=registry, npv_evaluator=FakeNpvEvaluator())

    outcome = harness.engine.evaluate(sample_request)

    record = outcome.run_record
    assert record.status is RunStatus.COMPLETED
    assert record.constraint_status is ConstraintStatus.VIOLATED
    assert record.is_feasible is False
    # Осуществимость и экономика независимы: NPV посчитан несмотря на нарушение.
    assert record.npv_status is NpvStatus.OK
    assert record.npv_value is not None
    assert outcome.post_report.violations[0].constraint_id == "test.post.violated"


def test_corrupted_artifact_never_returns_silent_cache_hit(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    simulator = CountingSimulator()
    harness = make_harness(simulator=simulator, npv_evaluator=FakeNpvEvaluator())

    first = harness.engine.evaluate(sample_request)
    assert first.simulation_result is not None
    assert first.simulation_result.trajectory_ref is not None
    harness.artifacts.resolve(first.simulation_result.trajectory_ref).write_bytes(b"tampered")

    second = harness.engine.evaluate(sample_request)

    assert second.run_record.cache_hit is False
    assert simulator.call_count == 2
    assert second.run_record.npv_status is NpvStatus.OK
    assert second.simulation_result is not None
    assert second.simulation_result.trajectory_ref is not None
    assert harness.artifacts.verify(second.simulation_result.trajectory_ref) is True


def test_post_constraint_receives_engine_artifact_reader(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    """POST-ограничение читает траекторию через reader, выданный движком."""
    registry = ConstraintRegistry()
    registry.register_post("test.post.artifact", artifact_reading_post_constraint)
    harness = make_harness(constraints=registry, npv_evaluator=FakeNpvEvaluator())

    outcome = harness.engine.evaluate(sample_request)

    violation = outcome.post_report.violations[0]
    assert violation.constraint_id == "test.post.artifact"
    assert violation.metadata == {"observed_steps": 8}


def test_partial_result_is_not_reported_as_completed(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    evaluator = FakeNpvEvaluator()
    registry = ConstraintRegistry()
    registry.register_post("test.post.artifact", artifact_reading_post_constraint)
    harness = make_harness(
        simulator=PartialSimulator(), constraints=registry, npv_evaluator=evaluator
    )

    outcome = harness.engine.evaluate(sample_request)

    record = outcome.run_record
    assert record.simulation_status is SimulationStatus.PARTIAL
    # Оборванный прогон не должен выглядеть как полноценный.
    assert record.status is RunStatus.PARTIAL
    # POST-ограничения выполняются: частичная траектория диагностически полезна.
    assert outcome.post_report.violations[0].metadata == {"observed_steps": 1}
    # А целевая функция по ней не считается.
    assert evaluator.call_count == 0
    assert record.npv_status is NpvStatus.NOT_EVALUATED
    assert record.npv_value is None
    assert record.npv_provenance is None
    assert outcome.npv_estimate is None
    assert harness.db.count_cached_simulations() == 0


def test_partial_result_is_never_served_from_cache(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    simulator = PartialSimulator()
    harness = make_harness(simulator=simulator, npv_evaluator=FakeNpvEvaluator())

    harness.engine.evaluate(sample_request)
    second = harness.engine.evaluate(sample_request)

    assert simulator.call_count == 2
    assert second.run_record.cache_hit is False


def test_tampered_cached_result_digest_is_not_a_valid_hit(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    simulator = CountingSimulator()
    harness = make_harness(simulator=simulator, npv_evaluator=FakeNpvEvaluator())
    first = harness.engine.evaluate(sample_request)

    connection = sqlite3.connect(harness.db.path)
    try:
        row = connection.execute("SELECT simulation_key, result_json FROM simulation_cache")
        simulation_key, result_json = row.fetchone()
        payload = json.loads(result_json)
        payload["result_digest"] = "0" * 64
        connection.execute(
            "UPDATE simulation_cache SET result_json = ? WHERE simulation_key = ?",
            (json.dumps(payload), simulation_key),
        )
        connection.commit()
    finally:
        connection.close()

    second = harness.engine.evaluate(sample_request)

    assert simulation_key == first.run_record.simulation_key
    assert second.run_record.cache_hit is False
    assert simulator.call_count == 2


def test_each_run_uses_its_own_workspace(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness()
    first = harness.engine.evaluate(sample_request)
    second = harness.engine.evaluate(sample_request, use_cache=False)

    workspaces = sorted(path.name for path in harness.workspace_root.iterdir())
    assert workspaces == sorted([first.run_record.run_id, second.run_record.run_id])


def test_runtime_and_timestamp_are_recorded(
    make_harness: HarnessFactory, sample_request: SimulationRequest
) -> None:
    harness = make_harness()
    record = harness.engine.evaluate(sample_request).run_record
    assert record.runtime_seconds >= 0.0
    assert record.created_at.tzinfo is not None
