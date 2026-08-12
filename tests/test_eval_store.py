"""Тесты базы экспериментов."""

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aios.evaluation.constraints import ConstraintStatus
from aios.evaluation.npv import EvaluatorProvenance, NpvStatus
from aios.evaluation.records import RunRecord, RunStatus
from aios.evaluation.simulation import SimulationStatus
from aios.evaluation.store import (
    SCHEMA_VERSION,
    CorruptedRecordError,
    ExperimentDB,
    SchemaVersionMismatchError,
)

SIMULATION_KEY = "c" * 64
_PROVENANCE = EvaluatorProvenance.NOT_OFFICIAL


def _record(run_id: str, **overrides: object) -> RunRecord:
    fields: dict[str, object] = {
        "run_id": run_id,
        "created_at": datetime.now(UTC),
        "simulation_key": SIMULATION_KEY,
        "simulation_request_digest": "d" * 64,
        "simulator_id": "test-simulator",
        "simulator_version": "0.1.0",
        "status": RunStatus.COMPLETED,
    }
    fields.update(overrides)
    return RunRecord(**fields)  # type: ignore[arg-type]


def test_run_record_roundtrip(experiment_db: ExperimentDB) -> None:
    record = _record(
        "run-1",
        model_digest="m",
        scenario_digest="s",
        schedule_digest="w",
        simulation_status=SimulationStatus.SUCCESS,
        trajectory_digest="t" * 64,
        npv_status=NpvStatus.OK,
        npv_value=123.5,
        npv_provenance=EvaluatorProvenance.NOT_OFFICIAL,
        constraint_status=ConstraintStatus.SATISFIED,
        is_feasible=True,
        cache_hit=False,
        runtime_seconds=1.25,
        metadata={"note": "synthetic"},
    )
    experiment_db.record_run(record)
    restored = experiment_db.get_run("run-1")
    assert restored == record


def test_run_record_roundtrip_without_npv(experiment_db: ExperimentDB) -> None:
    record = _record(
        "run-no-npv",
        simulation_status=SimulationStatus.SUCCESS,
        npv_status=NpvStatus.NOT_EVALUATED,
        is_feasible=None,
    )
    experiment_db.record_run(record)
    restored = experiment_db.get_run("run-no-npv")
    assert restored is not None
    # Отсутствующий NPV остаётся отсутствующим, а не превращается в 0.0.
    assert restored.npv_value is None
    assert restored.npv_provenance is None
    assert restored.is_feasible is None


def test_unknown_run_returns_none(experiment_db: ExperimentDB) -> None:
    assert experiment_db.get_run("absent") is None


def test_multiple_runs_share_one_simulation_key(experiment_db: ExperimentDB) -> None:
    """Повторный прогон одного эксперимента разрешён (численный шум)."""
    experiment_db.record_run(_record("run-a"))
    experiment_db.record_run(_record("run-b"))
    assert experiment_db.count_runs_for_simulation_key(SIMULATION_KEY) == 2


def test_duplicate_run_id_is_rejected(experiment_db: ExperimentDB) -> None:
    experiment_db.record_run(_record("run-dup"))
    with pytest.raises(sqlite3.IntegrityError):
        experiment_db.record_run(_record("run-dup"))


def test_simulation_cache_is_unique_by_key(experiment_db: ExperimentDB) -> None:
    experiment_db.record_run(_record("run-1"))
    experiment_db.record_run(_record("run-2"))
    assert experiment_db.put_cached_simulation(SIMULATION_KEY, "{}", "run-1") is True
    assert experiment_db.put_cached_simulation(SIMULATION_KEY, "{}", "run-2") is False
    assert experiment_db.count_cached_simulations() == 1
    cached = experiment_db.get_cached_simulation(SIMULATION_KEY)
    assert cached is not None
    assert cached.source_run_id == "run-1"


def test_cache_requires_existing_run(experiment_db: ExperimentDB) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        experiment_db.put_cached_simulation(SIMULATION_KEY, "{}", "absent-run")


def test_list_recent_runs_respects_limit(experiment_db: ExperimentDB) -> None:
    for index in range(5):
        experiment_db.record_run(_record(f"run-{index}"))
    assert len(experiment_db.list_recent_runs(limit=3)) == 3
    assert len(experiment_db.list_recent_runs(limit=50)) == 5


def test_records_survive_close_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "experiments.sqlite3"
    with ExperimentDB(path) as db:
        db.record_run(_record("run-persisted"))
        db.put_cached_simulation(SIMULATION_KEY, '{"status":"SUCCESS"}', "run-persisted")
    with ExperimentDB(path) as reopened:
        assert reopened.get_run("run-persisted") is not None
        assert reopened.get_cached_simulation(SIMULATION_KEY) is not None


def test_schema_version_is_persisted(tmp_path: Path) -> None:
    path = tmp_path / "experiments.sqlite3"
    with ExperimentDB(path):
        pass
    connection = sqlite3.connect(path)
    try:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
    finally:
        connection.close()
    assert version == SCHEMA_VERSION


def test_schema_version_mismatch_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "experiments.sqlite3"
    with ExperimentDB(path):
        pass
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(SchemaVersionMismatchError):
        ExperimentDB(path)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError):
        _record("run-naive", created_at=datetime.now())  # noqa: DTZ005


@pytest.mark.parametrize(
    "overrides",
    [
        # npv_status=OK обязан иметь конечное значение и происхождение.
        {"npv_status": NpvStatus.OK, "npv_value": None, "npv_provenance": _PROVENANCE},
        {"npv_status": NpvStatus.OK, "npv_value": 1.0, "npv_provenance": None},
        {"npv_status": NpvStatus.OK, "npv_value": float("inf"), "npv_provenance": _PROVENANCE},
        {"npv_status": NpvStatus.OK, "npv_value": float("-inf"), "npv_provenance": _PROVENANCE},
        {"npv_status": NpvStatus.OK, "npv_value": float("nan"), "npv_provenance": _PROVENANCE},
        # Любой другой статус запрещает и значение, и происхождение.
        {"npv_status": NpvStatus.FAILED, "npv_value": 1.0, "npv_provenance": _PROVENANCE},
        {"npv_status": NpvStatus.FAILED, "npv_value": None, "npv_provenance": _PROVENANCE},
        {"npv_status": NpvStatus.NOT_EVALUATED, "npv_value": 1.0, "npv_provenance": _PROVENANCE},
        {"npv_status": NpvStatus.NOT_EVALUATED, "npv_value": 0.0, "npv_provenance": None},
    ],
)
def test_inconsistent_npv_state_is_rejected(overrides: dict) -> None:
    with pytest.raises(ValueError):
        _record("run-bad-npv", **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"npv_status": NpvStatus.OK, "npv_value": 42.5, "npv_provenance": _PROVENANCE},
        {"npv_status": NpvStatus.FAILED},
        {"npv_status": NpvStatus.NOT_EVALUATED},
    ],
)
def test_consistent_npv_state_roundtrips(experiment_db: ExperimentDB, overrides: dict) -> None:
    record = _record("run-npv-ok", **overrides)
    experiment_db.record_run(record)
    assert experiment_db.get_run("run-npv-ok") == record


def _insert_raw_row(path: Path, row: dict[str, object], *, ignore_checks: bool) -> None:
    """Записать строку в обход доменной валидации.

    Так имитируется испорченная запись, которую могла оставить запись мимо
    доменного слоя или повреждение файла.
    """
    connection = sqlite3.connect(path)
    try:
        if ignore_checks:
            connection.execute("PRAGMA ignore_check_constraints = ON")
        columns = ",".join(row)
        placeholders = ",".join(f":{column}" for column in row)
        connection.execute(f"INSERT INTO runs ({columns}) VALUES ({placeholders})", row)
        connection.commit()
    finally:
        connection.close()


def _raw_ok_row(run_id: str, npv_value: object) -> dict[str, object]:
    """Строка со статусом OK и произвольным значением целевой функции."""
    row = _record(
        run_id,
        npv_status=NpvStatus.OK,
        npv_value=1.0,
        npv_provenance=_PROVENANCE,
    ).to_row()
    row["npv_value"] = npv_value
    return row


def test_database_rejects_inconsistent_npv_row(experiment_db: ExperimentDB) -> None:
    """CHECK-ограничение страхует от записи в обход доменного слоя."""
    row = _record("run-raw", npv_status=NpvStatus.FAILED).to_row()
    row["npv_value"] = 1.0
    with pytest.raises(sqlite3.IntegrityError):
        _insert_raw_row(experiment_db.path, row, ignore_checks=False)


@pytest.mark.parametrize("npv_value", [float("inf"), float("-inf")])
def test_database_rejects_non_finite_npv_value(
    experiment_db: ExperimentDB, npv_value: float
) -> None:
    """Бесконечность нельзя сравнивать и сортировать -- в целевой функции её нет."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_raw_row(experiment_db.path, _raw_ok_row("run-inf", npv_value), ignore_checks=False)


def test_database_rejects_nan_npv_value(experiment_db: ExperimentDB) -> None:
    """NaN драйвер sqlite3 сохраняет как NULL, и его отсекает IS NOT NULL."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_raw_row(
            experiment_db.path, _raw_ok_row("run-nan", float("nan")), ignore_checks=False
        )


@pytest.mark.parametrize(
    "npv_value",
    [42.5, -42.5, 0.0, sys.float_info.max, -sys.float_info.max],
)
def test_database_accepts_finite_npv_value(experiment_db: ExperimentDB, npv_value: float) -> None:
    """Границы CHECK -- предельные конечные float, а не экономический диапазон."""
    _insert_raw_row(experiment_db.path, _raw_ok_row("run-finite", npv_value), ignore_checks=False)

    restored = experiment_db.get_run("run-finite")
    assert restored is not None
    assert restored.npv_value == npv_value


def test_corrupted_npv_row_surfaces_instead_of_silent_coercion(tmp_path: Path) -> None:
    path = tmp_path / "experiments.sqlite3"
    with ExperimentDB(path):
        pass
    row = _record("run-corrupt", npv_status=NpvStatus.NOT_EVALUATED).to_row()
    row["npv_value"] = 7.0
    _insert_raw_row(path, row, ignore_checks=True)

    with ExperimentDB(path) as reopened, pytest.raises(CorruptedRecordError):
        reopened.get_run("run-corrupt")


def test_corrupted_enum_row_surfaces_on_listing(tmp_path: Path) -> None:
    path = tmp_path / "experiments.sqlite3"
    with ExperimentDB(path):
        pass
    row = _record("run-weird").to_row()
    row["status"] = "NOT_A_REAL_STATUS"
    _insert_raw_row(path, row, ignore_checks=True)

    with ExperimentDB(path) as reopened, pytest.raises(CorruptedRecordError):
        reopened.list_recent_runs()
