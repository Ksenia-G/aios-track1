"""Тесты кеша симуляций."""

from datetime import UTC, datetime

from aios.evaluation.artifacts import ArtifactStore
from aios.evaluation.cache import SimulationCache
from aios.evaluation.canonical import canonical_json
from aios.evaluation.records import RunRecord, RunStatus
from aios.evaluation.simulation import (
    SimulationFailure,
    SimulationResult,
    SimulationStatus,
)
from aios.evaluation.store import ExperimentDB

SIMULATION_KEY = "a" * 64


def _record_run(db: ExperimentDB, run_id: str) -> str:
    db.record_run(
        RunRecord(
            run_id=run_id,
            created_at=datetime.now(UTC),
            simulation_key=SIMULATION_KEY,
            simulation_request_digest="b" * 64,
            simulator_id="test-simulator",
            simulator_version="0.1.0",
            status=RunStatus.COMPLETED,
        )
    )
    return run_id


def _successful_result(artifact_store: ArtifactStore) -> SimulationResult:
    ref = artifact_store.put_bytes(b"payload", media_type="application/json", producer_id="test")
    return SimulationResult.create(status=SimulationStatus.SUCCESS, trajectory_ref=ref)


def test_store_and_lookup_roundtrip(
    experiment_db: ExperimentDB, artifact_store: ArtifactStore, simulation_cache: SimulationCache
) -> None:
    run_id = _record_run(experiment_db, "run-1")
    result = _successful_result(artifact_store)
    assert simulation_cache.store(SIMULATION_KEY, result, run_id) is True

    cached = simulation_cache.lookup(SIMULATION_KEY)
    assert cached is not None
    assert cached.result.result_digest == result.result_digest
    assert cached.result.trajectory_ref == result.trajectory_ref
    assert cached.source_run_id == run_id


def test_lookup_of_unknown_key_is_a_miss(simulation_cache: SimulationCache) -> None:
    assert simulation_cache.lookup("f" * 64) is None


def test_failed_simulation_is_not_cached(
    experiment_db: ExperimentDB, simulation_cache: SimulationCache
) -> None:
    run_id = _record_run(experiment_db, "run-failed")
    failed = SimulationResult.create(
        status=SimulationStatus.NON_CONVERGED,
        failure=SimulationFailure(message="did not converge"),
    )
    assert simulation_cache.store(SIMULATION_KEY, failed, run_id) is False
    assert simulation_cache.lookup(SIMULATION_KEY) is None
    assert experiment_db.count_cached_simulations() == 0


def test_corrupted_artifact_turns_hit_into_miss(
    experiment_db: ExperimentDB, artifact_store: ArtifactStore, simulation_cache: SimulationCache
) -> None:
    run_id = _record_run(experiment_db, "run-corrupt")
    result = _successful_result(artifact_store)
    simulation_cache.store(SIMULATION_KEY, result, run_id)
    assert result.trajectory_ref is not None
    artifact_store.resolve(result.trajectory_ref).write_bytes(b"tampered")

    assert simulation_cache.lookup(SIMULATION_KEY) is None


def test_missing_artifact_turns_hit_into_miss(
    experiment_db: ExperimentDB, artifact_store: ArtifactStore, simulation_cache: SimulationCache
) -> None:
    run_id = _record_run(experiment_db, "run-missing")
    result = _successful_result(artifact_store)
    simulation_cache.store(SIMULATION_KEY, result, run_id)
    assert result.trajectory_ref is not None
    artifact_store.resolve(result.trajectory_ref).unlink()

    assert simulation_cache.lookup(SIMULATION_KEY) is None


def test_tampered_result_digest_is_a_miss(
    experiment_db: ExperimentDB, artifact_store: ArtifactStore, simulation_cache: SimulationCache
) -> None:
    """Подделанный result_digest не может считаться валидным результатом."""
    run_id = _record_run(experiment_db, "run-tampered")
    payload = _successful_result(artifact_store).to_json()
    payload["result_digest"] = "0" * 64
    experiment_db.put_cached_simulation(SIMULATION_KEY, canonical_json(payload), run_id)

    assert simulation_cache.lookup(SIMULATION_KEY) is None


def test_malformed_cache_payload_is_a_miss(
    experiment_db: ExperimentDB, simulation_cache: SimulationCache
) -> None:
    run_id = _record_run(experiment_db, "run-malformed")
    experiment_db.put_cached_simulation(SIMULATION_KEY, "{not json", run_id)

    assert simulation_cache.lookup(SIMULATION_KEY) is None


def test_partial_result_is_not_cached(
    experiment_db: ExperimentDB, artifact_store: ArtifactStore, simulation_cache: SimulationCache
) -> None:
    run_id = _record_run(experiment_db, "run-partial")
    ref = artifact_store.put_bytes(b"partial", media_type="application/json", producer_id="test")
    partial = SimulationResult.create(status=SimulationStatus.PARTIAL, trajectory_ref=ref)

    assert simulation_cache.store(SIMULATION_KEY, partial, run_id) is False
    assert experiment_db.count_cached_simulations() == 0


def test_first_write_wins(
    experiment_db: ExperimentDB, artifact_store: ArtifactStore, simulation_cache: SimulationCache
) -> None:
    first_run = _record_run(experiment_db, "run-first")
    second_run = _record_run(experiment_db, "run-second")
    first_result = _successful_result(artifact_store)
    second_ref = artifact_store.put_bytes(
        b"other payload", media_type="application/json", producer_id="test"
    )
    second_result = SimulationResult.create(
        status=SimulationStatus.SUCCESS, trajectory_ref=second_ref
    )

    assert simulation_cache.store(SIMULATION_KEY, first_result, first_run) is True
    assert simulation_cache.store(SIMULATION_KEY, second_result, second_run) is False

    cached = simulation_cache.lookup(SIMULATION_KEY)
    assert cached is not None
    assert cached.source_run_id == first_run
    assert experiment_db.count_cached_simulations() == 1
