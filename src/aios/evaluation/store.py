"""Лёгкая база экспериментов на stdlib sqlite3.

ExperimentDB отвечает ТОЛЬКО за хранение. Она не вычисляет хеши и не решает,
что кешировать: иначе появится вторая канонизация и вторая политика кеша.
Миграционного фреймворка нет -- при несовпадении версии схемы падаем громко.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from aios.evaluation.records import RunRecord

SCHEMA_VERSION = 1

_RUN_COLUMNS = (
    "run_id",
    "created_at",
    "simulation_key",
    "simulation_request_digest",
    "simulator_id",
    "simulator_version",
    "status",
    "model_digest",
    "scenario_digest",
    "schedule_digest",
    "simulation_status",
    "trajectory_digest",
    "npv_status",
    "npv_value",
    "npv_provenance",
    "constraint_status",
    "is_feasible",
    "cache_hit",
    "cached_from_run_id",
    "runtime_seconds",
    "metadata",
)

_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        simulation_key TEXT NOT NULL,
        simulation_request_digest TEXT NOT NULL,
        simulator_id TEXT NOT NULL,
        simulator_version TEXT NOT NULL,
        status TEXT NOT NULL,
        model_digest TEXT,
        scenario_digest TEXT,
        schedule_digest TEXT,
        simulation_status TEXT,
        trajectory_digest TEXT,
        npv_status TEXT NOT NULL,
        npv_value REAL,
        npv_provenance TEXT,
        constraint_status TEXT NOT NULL,
        is_feasible INTEGER,
        cache_hit INTEGER NOT NULL,
        cached_from_run_id TEXT,
        runtime_seconds REAL NOT NULL,
        metadata TEXT NOT NULL,
        -- Тот же инвариант, что и в RunRecord: состояние целевой функции
        -- согласовано, а сохранённое значение конечно. Проверка в Python
        -- (math.isfinite) остаётся основной, эта -- страховка от записи
        -- в обход доменного слоя.
        --
        -- Границы -- это в точности предельные конечные значения IEEE-754
        -- binary64, а не экономический диапазон: ±Inf выпадает из BETWEEN,
        -- а NaN Python-драйвер сохраняет как NULL и его отсекает IS NOT NULL.
        CHECK (
            (
                npv_status = 'OK'
                AND npv_value IS NOT NULL
                AND npv_value BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308
                AND npv_provenance IS NOT NULL
            )
            OR (npv_status <> 'OK' AND npv_value IS NULL AND npv_provenance IS NULL)
        )
    )
    """,
    # Индекс, а не UNIQUE: повторные прогоны одного и того же физического
    # эксперимента разрешены и нужны для оценки численного шума.
    "CREATE INDEX IF NOT EXISTS idx_runs_simulation_key ON runs (simulation_key)",
    """
    CREATE TABLE IF NOT EXISTS simulation_cache (
        simulation_key TEXT PRIMARY KEY,
        result_json TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_run_id) REFERENCES runs (run_id)
    )
    """,
)


class ExperimentDBError(RuntimeError):
    """Базовая ошибка базы экспериментов."""


class SchemaVersionMismatchError(ExperimentDBError):
    """Версия схемы в файле не совпадает с версией кода."""


class CorruptedRecordError(ExperimentDBError):
    """Строка в БД не собирается в валидный RunRecord.

    Такую строку нельзя молча «починить»: испорченная запись обязана быть
    видимой, а не превращаться в правдоподобный объект.
    """


@dataclass(frozen=True, slots=True)
class CachedSimulationRow:
    """Сырая строка кеша симуляций."""

    simulation_key: str
    result_json: str
    source_run_id: str
    created_at: datetime


class ExperimentDB:
    """Хранилище истории прогонов и кеша симуляций."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    @property
    def path(self) -> Path:
        return self._path

    def _initialize_schema(self) -> None:
        with self._connection:
            for statement in _SCHEMA_STATEMENTS:
                self._connection.execute(statement)
            row = self._connection.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif int(row["version"]) != SCHEMA_VERSION:
                raise SchemaVersionMismatchError(
                    f"database schema version {row['version']} != expected {SCHEMA_VERSION}"
                )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def record_run(self, record: RunRecord) -> None:
        row = record.to_row()
        placeholders = ",".join(f":{column}" for column in _RUN_COLUMNS)
        columns = ",".join(_RUN_COLUMNS)
        with self._connection:
            self._connection.execute(f"INSERT INTO runs ({columns}) VALUES ({placeholders})", row)

    @staticmethod
    def _restore_run(row: sqlite3.Row) -> RunRecord:
        try:
            return RunRecord.from_row(dict(row))
        except (ValueError, TypeError, KeyError) as error:
            raise CorruptedRecordError(
                f"stored run {row['run_id']!r} is not a valid RunRecord: {error}"
            ) from error

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else self._restore_run(row)

    def list_recent_runs(self, limit: int = 20) -> Sequence[RunRecord]:
        rows = self._connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._restore_run(row) for row in rows]

    def count_runs_for_simulation_key(self, simulation_key: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM runs WHERE simulation_key = ?", (simulation_key,)
        ).fetchone()
        return int(row["total"])

    def get_cached_simulation(self, simulation_key: str) -> CachedSimulationRow | None:
        row = self._connection.execute(
            "SELECT * FROM simulation_cache WHERE simulation_key = ?", (simulation_key,)
        ).fetchone()
        if row is None:
            return None
        return CachedSimulationRow(
            simulation_key=str(row["simulation_key"]),
            result_json=str(row["result_json"]),
            source_run_id=str(row["source_run_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def put_cached_simulation(
        self, simulation_key: str, result_json: str, source_run_id: str
    ) -> bool:
        """Идемпотентная запись в кеш. Возвращает True, если запись создана.

        Первая запись побеждает: перезапись кеша скрыла бы расхождение между
        двумя прогонами одного ключа, а именно оно нас и интересует.
        """
        created_at = datetime.now(UTC).isoformat()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO simulation_cache (simulation_key, result_json, source_run_id, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (simulation_key) DO NOTHING
                """,
                (simulation_key, result_json, source_run_id, created_at),
            )
        return cursor.rowcount > 0

    def count_cached_simulations(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS total FROM simulation_cache").fetchone()
        return int(row["total"])
