"""Кеш успешных симуляций.

Кеш и история -- разные вещи. История (`runs`) хранит каждую попытку оценки,
кеш хранит один успешный результат на физически идентичный запрос.
В PRET0-001 кешируются ТОЛЬКО успешные прогоны: отказ может быть вызван
инфраструктурой, и закреплять его как «ответ» нельзя.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from aios.evaluation.artifacts import ArtifactReader
from aios.evaluation.canonical import canonical_json
from aios.evaluation.simulation import SimulationResult, SimulationStatus
from aios.evaluation.store import ExperimentDB


@dataclass(frozen=True, slots=True)
class CachedSimulation:
    """Попадание в кеш вместе со ссылкой на исходный прогон."""

    simulation_key: str
    result: SimulationResult
    source_run_id: str
    created_at: datetime


class SimulationCache:
    """Кеш результатов поверх ExperimentDB и хранилища артефактов."""

    def __init__(self, db: ExperimentDB, artifact_store: ArtifactReader) -> None:
        self._db = db
        self._artifacts = artifact_store

    def lookup(self, simulation_key: str) -> CachedSimulation | None:
        row = self._db.get_cached_simulation(simulation_key)
        if row is None:
            return None
        try:
            result = SimulationResult.from_json(json.loads(row.result_json))
        except (ValueError, TypeError, KeyError):
            # Нечитаемая или подделанная запись (в том числе несовпадающий
            # result_digest) -- это промах. Считать её валидным результатом
            # значит принять непроверенные данные за результат симуляции.
            return None
        if result.trajectory_ref is not None and not self._artifacts.verify(result.trajectory_ref):
            # Битый или пропавший блоб -- это промах, а не попадание.
            # Молча вернуть такую запись значит подсунуть в оценку чужие байты.
            return None
        return CachedSimulation(
            simulation_key=row.simulation_key,
            result=result,
            source_run_id=row.source_run_id,
            created_at=row.created_at,
        )

    def store(self, simulation_key: str, result: SimulationResult, source_run_id: str) -> bool:
        """Положить успешный результат в кеш. Возвращает True, если записан."""
        if result.status is not SimulationStatus.SUCCESS:
            return False
        return self._db.put_cached_simulation(
            simulation_key=simulation_key,
            result_json=canonical_json(result.to_json()),
            source_run_id=source_run_id,
        )
