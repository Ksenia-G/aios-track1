"""Контракты запроса и результата симуляции.

Контракты намеренно schema-neutral: официальный симулятор, колода, расписание
скважин, управления и единицы измерения на этом этапе неизвестны. Любое
предположение о них, зафиксированное здесь, протечёт во все записи БД и кеша.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from aios.evaluation.artifacts import TrajectoryArtifactRef
from aios.evaluation.canonical import KEY_SCHEMA_VERSION, JsonValue, digest, normalize_json


class ResultDigestMismatchError(ValueError):
    """Объявленный result_digest не соответствует содержимому результата."""


class SimulationStatus(StrEnum):
    """Исход запуска симулятора."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NON_CONVERGED = "NON_CONVERGED"
    INVALID_INPUT = "INVALID_INPUT"
    TIMEOUT = "TIMEOUT"
    CRASHED = "CRASHED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SimulatorIdentity:
    """Идентичность адаптера симулятора, влияющая на воспроизводимость."""

    simulator_id: str
    simulator_version: str
    configuration: Mapping[str, JsonValue] = field(default_factory=dict)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "simulator_id": self.simulator_id,
            "simulator_version": self.simulator_version,
            "configuration": normalize_json(dict(self.configuration)),
        }


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Запрос на симуляцию.

    `payload` -- произвольная JSON-совместимая структура, смысл которой задаёт
    конкретный адаптер. Дайджесты входных данных хранятся отдельно, потому что
    входы идентифицируются содержимым, а не путём в файловой системе.
    """

    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    seed: int | None = None
    model_digest: str | None = None
    scenario_digest: str | None = None
    schedule_digest: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "payload": normalize_json(dict(self.payload)),
            "seed": self.seed,
            "model_digest": self.model_digest,
            "scenario_digest": self.scenario_digest,
            "schedule_digest": self.schedule_digest,
        }


@dataclass(frozen=True, slots=True)
class SimulationFailure:
    """Описание отказа симулятора. Не подменяет собой успешный результат."""

    message: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def to_json(self) -> dict[str, JsonValue]:
        return {"message": self.message, "details": normalize_json(dict(self.details))}

    @classmethod
    def from_json(cls, payload: dict[str, JsonValue]) -> "SimulationFailure":
        details = payload.get("details") or {}
        return cls(message=str(payload["message"]), details=dict(details))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Лёгкая метаданная запись о результате.

    Тяжёлые байты живут в хранилище артефактов, здесь -- только ссылка:
    в SQLite попадает запись фиксированного размера.

    `result_digest` не принимается на веру: он обязан совпадать с
    `compute_result_digest()` от самого объекта. Произвольное значение,
    пришедшее из кеша или извне, делает воспроизводимость фикцией.
    """

    status: SimulationStatus
    result_digest: str
    trajectory_ref: TrajectoryArtifactRef | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    diagnostics: Mapping[str, JsonValue] = field(default_factory=dict)
    failure: SimulationFailure | None = None

    def __post_init__(self) -> None:
        expected = compute_result_digest(
            status=self.status,
            trajectory_ref=self.trajectory_ref,
            metadata=self.metadata,
            failure=self.failure,
        )
        if self.result_digest != expected:
            raise ResultDigestMismatchError(
                f"result_digest does not match result content: "
                f"declared {self.result_digest}, expected {expected}"
            )

    @property
    def supports_constraint_checks(self) -> bool:
        """Можно ли проверять POST-ограничения.

        Частичный результат тоже проверяем: обрыв счёта сам по себе бывает
        диагностически полезен.
        """
        return self.status in (SimulationStatus.SUCCESS, SimulationStatus.PARTIAL)

    @property
    def supports_objective_evaluation(self) -> bool:
        """Можно ли считать целевую функцию.

        Только полный успех. Экономика по оборванной траектории -- это число,
        которое выглядит сравнимым с полными прогонами, но им не является.
        """
        return self.status is SimulationStatus.SUCCESS

    @classmethod
    def create(
        cls,
        *,
        status: SimulationStatus,
        trajectory_ref: TrajectoryArtifactRef | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
        diagnostics: Mapping[str, JsonValue] | None = None,
        failure: SimulationFailure | None = None,
    ) -> "SimulationResult":
        resolved_metadata = dict(metadata or {})
        return cls(
            status=status,
            result_digest=compute_result_digest(
                status=status,
                trajectory_ref=trajectory_ref,
                metadata=resolved_metadata,
                failure=failure,
            ),
            trajectory_ref=trajectory_ref,
            metadata=resolved_metadata,
            diagnostics=dict(diagnostics or {}),
            failure=failure,
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "status": str(self.status),
            "result_digest": self.result_digest,
            "trajectory_ref": self.trajectory_ref.to_json() if self.trajectory_ref else None,
            "metadata": normalize_json(dict(self.metadata)),
            "diagnostics": normalize_json(dict(self.diagnostics)),
            "failure": self.failure.to_json() if self.failure else None,
        }

    @classmethod
    def from_json(cls, payload: dict[str, JsonValue]) -> "SimulationResult":
        raw_ref = payload.get("trajectory_ref")
        raw_failure = payload.get("failure")
        return cls(
            status=SimulationStatus(str(payload["status"])),
            result_digest=str(payload["result_digest"]),
            trajectory_ref=TrajectoryArtifactRef.from_json(raw_ref) if raw_ref else None,  # type: ignore[arg-type]
            metadata=dict(payload.get("metadata") or {}),  # type: ignore[arg-type]
            diagnostics=dict(payload.get("diagnostics") or {}),  # type: ignore[arg-type]
            failure=SimulationFailure.from_json(raw_failure) if raw_failure else None,  # type: ignore[arg-type]
        )


def compute_result_digest(
    *,
    status: SimulationStatus,
    trajectory_ref: TrajectoryArtifactRef | None,
    metadata: Mapping[str, JsonValue],
    failure: SimulationFailure | None = None,
) -> str:
    """Content-identity результата: единственная каноническая семантика.

    Входит всё, что определяет физический смысл результата, включая свойства
    артефакта, кроме его местоположения. `relative_path` исключён намеренно:
    перенос артефакта из workspace симулятора в хранилище не меняет байтов,
    и менять из-за этого идентичность результата нельзя.

    Диагностика (время счёта, число итераций) не входит: она различается между
    машинами при физически одинаковом результате, и её включение сломало бы
    и кеш, и проверку детерминизма.
    """
    artifact_identity: JsonValue = None
    if trajectory_ref is not None:
        artifact_identity = {
            "content_digest": trajectory_ref.content_digest,
            "byte_size": trajectory_ref.byte_size,
            "media_type": trajectory_ref.media_type,
            "producer_id": trajectory_ref.producer_id,
        }
    return digest(
        {
            "key_schema_version": KEY_SCHEMA_VERSION,
            "status": str(status),
            "artifact": artifact_identity,
            "metadata": normalize_json(dict(metadata)),
            "failure": failure.to_json() if failure is not None else None,
        }
    )


def compute_request_digest(request: SimulationRequest) -> str:
    """Дайджест самого запроса, без идентичности симулятора."""
    return digest({"key_schema_version": KEY_SCHEMA_VERSION, "request": request.to_json()})


def compute_simulation_key(request: SimulationRequest, identity: SimulatorIdentity) -> str:
    """Ключ физически идентичной симуляции.

    В ключ входит всё, что меняет физический смысл прогона, и ничего больше.
    Сознательно исключены run_id, время, hostname, абсолютные пути, каталог
    вывода и политика кеширования: один и тот же физический эксперимент можно
    выполнить повторно (например, для оценки численного шума), и он обязан
    получить тот же ключ.
    """
    return digest(
        {
            "key_schema_version": KEY_SCHEMA_VERSION,
            "simulator": identity.to_json(),
            "request": request.to_json(),
        }
    )
