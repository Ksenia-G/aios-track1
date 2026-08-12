"""Общие фикстуры и тестовые дублёры.

Фейковый эвалюатор NPV существует ТОЛЬКО здесь. В src/ его нет и не должно
появиться: синтетическое число не имеет права попасть в production-путь и
притвориться официальной метрикой трека.
"""

import itertools
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aios.evaluation.artifacts import ArtifactReader, ArtifactStore, TrajectoryArtifactRef
from aios.evaluation.cache import SimulationCache
from aios.evaluation.canonical import JsonValue, digest_bytes
from aios.evaluation.constraints import ConstraintRegistry, ConstraintViolation
from aios.evaluation.engine import EvaluationEngine
from aios.evaluation.fake_simulator import FakeSimulator
from aios.evaluation.npv import (
    EvaluatorProvenance,
    NpvEstimate,
    NpvEvaluationError,
    NpvEvaluator,
)
from aios.evaluation.simulation import (
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorIdentity,
)
from aios.evaluation.simulator import Simulator, SimulatorError
from aios.evaluation.store import ExperimentDB

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


class FakeNpvEvaluator:
    """Синтетическая «оценка» для тестов. НЕ формула NPV трека.

    Число выводится из синтетических каналов фейкового артефакта и не имеет
    экономического смысла, поэтому provenance всегда NOT_OFFICIAL,
    а единица измерения не указывается.
    """

    evaluator_id = "fake-npv/NOT_OFFICIAL"
    evaluator_version = "0.1.0"

    def __init__(self) -> None:
        self.call_count = 0

    def evaluate(self, result: SimulationResult, artifacts: ArtifactReader) -> NpvEstimate:
        self.call_count += 1
        if result.trajectory_ref is None:
            raise NpvEvaluationError("no trajectory artifact to evaluate")
        # Байты доступны только через ArtifactReader: ни корня хранилища,
        # ни путей потребитель не видит.
        with artifacts.open_binary(result.trajectory_ref) as stream:
            document = json.loads(stream.read().decode("utf-8"))
        total = sum(float(step["synthetic_channel_a"]) for step in document["steps"])
        return NpvEstimate(
            value=round(total, 6),
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            provenance=EvaluatorProvenance.NOT_OFFICIAL,
            unit=None,
            metadata={"synthetic": True},
        )


class FailingNpvEvaluator:
    """Эвалюатор, который всегда отказывает."""

    evaluator_id = "failing-npv/NOT_OFFICIAL"
    evaluator_version = "0.1.0"

    def __init__(self) -> None:
        self.call_count = 0

    def evaluate(self, result: SimulationResult, artifacts: ArtifactReader) -> NpvEstimate:
        self.call_count += 1
        raise NpvEvaluationError("evaluator is intentionally broken")


class CountingSimulator:
    """Обёртка, считающая вызовы симулятора."""

    def __init__(self, inner: Simulator | None = None) -> None:
        self._inner = inner or FakeSimulator()
        self.call_count = 0

    @property
    def identity(self) -> SimulatorIdentity:
        return self._inner.identity

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        self.call_count += 1
        return self._inner.run(request, workspace)


class CrashingSimulator:
    """Симулятор, падающий исключением."""

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def identity(self) -> SimulatorIdentity:
        return SimulatorIdentity(
            simulator_id="crashing-simulator/NOT_OFFICIAL",
            simulator_version="0.1.0",
        )

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        self.call_count += 1
        raise SimulatorError("simulator exploded")


class LyingSimulator:
    """Симулятор, объявляющий неверный дайджест своего артефакта."""

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def identity(self) -> SimulatorIdentity:
        return SimulatorIdentity(
            simulator_id="lying-simulator/NOT_OFFICIAL",
            simulator_version="0.1.0",
        )

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        self.call_count += 1
        workspace.mkdir(parents=True, exist_ok=True)
        data = b"synthetic-bytes"
        (workspace / "trajectory.bin").write_bytes(data)
        return SimulationResult.create(
            status=SimulationStatus.SUCCESS,
            trajectory_ref=TrajectoryArtifactRef(
                relative_path="trajectory.bin",
                content_digest="0" * 64,
                media_type="application/octet-stream",
                byte_size=len(data),
                producer_id="lying-simulator",
            ),
        )


class PartialSimulator:
    """Симулятор, возвращающий оборванный, но существующий результат."""

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def identity(self) -> SimulatorIdentity:
        return SimulatorIdentity(
            simulator_id="partial-simulator/NOT_OFFICIAL",
            simulator_version="0.1.0",
        )

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        self.call_count += 1
        workspace.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"steps": [{"synthetic_channel_a": 1.0}]}, sort_keys=True).encode()
        (workspace / "trajectory.json").write_bytes(data)
        return SimulationResult.create(
            status=SimulationStatus.PARTIAL,
            trajectory_ref=TrajectoryArtifactRef(
                relative_path="trajectory.json",
                content_digest=digest_bytes(data),
                media_type="application/json",
                byte_size=len(data),
                producer_id="partial-simulator",
            ),
            metadata={"completed_steps": 1},
        )


@dataclass(slots=True)
class EngineHarness:
    """Собранный движок вместе со своими хранилищами."""

    engine: EvaluationEngine
    db: ExperimentDB
    artifacts: ArtifactStore
    cache: SimulationCache
    simulator: Simulator
    workspace_root: Path
    root: Path
    constraints: ConstraintRegistry = field(default_factory=ConstraintRegistry)


def blocking_pre_constraint(request: SimulationRequest) -> list[ConstraintViolation]:
    return [
        ConstraintViolation(
            constraint_id="test.pre.blocking",
            message="request is rejected before simulation",
            is_blocking=True,
        )
    ]


def post_violation_constraint(
    result: SimulationResult, artifacts: ArtifactReader
) -> list[ConstraintViolation]:
    return [
        ConstraintViolation(
            constraint_id="test.post.violated",
            message="result violates a synthetic constraint",
            is_blocking=False,
        )
    ]


def artifact_reading_post_constraint(
    result: SimulationResult, artifacts: ArtifactReader
) -> list[ConstraintViolation]:
    """POST-предикат, которому нужны сами байты траектории.

    Настоящее ограничение по результату смотрит в траекторию, а не только
    в метаданные. Путь к файлу предикату при этом недоступен.
    """
    if result.trajectory_ref is None:
        return [
            ConstraintViolation(
                constraint_id="test.post.artifact",
                message="result has no trajectory artifact",
            )
        ]
    with artifacts.open_binary(result.trajectory_ref) as stream:
        document = json.loads(stream.read().decode("utf-8"))
    return [
        ConstraintViolation(
            constraint_id="test.post.artifact",
            message="observed synthetic trajectory",
            is_blocking=False,
            metadata={"observed_steps": len(document["steps"])},
        )
    ]


@pytest.fixture
def artifact_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def experiment_db(tmp_path: Path) -> Iterator[ExperimentDB]:
    db = ExperimentDB(tmp_path / "db" / "experiments.sqlite3")
    yield db
    db.close()


@pytest.fixture
def simulation_cache(experiment_db: ExperimentDB, artifact_store: ArtifactStore) -> SimulationCache:
    return SimulationCache(experiment_db, artifact_store)


@pytest.fixture
def sample_request() -> SimulationRequest:
    return SimulationRequest(
        payload={"variant": "baseline", "levels": [1, 2, 3]},
        seed=17,
        model_digest="model-digest-placeholder",
    )


@pytest.fixture
def make_harness(tmp_path: Path) -> Iterator[Callable[..., EngineHarness]]:
    """Фабрика изолированных движков. Всё состояние -- только в tmp_path."""
    counter = itertools.count()
    created: list[ExperimentDB] = []

    def factory(
        *,
        simulator: Simulator | None = None,
        constraints: ConstraintRegistry | None = None,
        npv_evaluator: NpvEvaluator | None = None,
    ) -> EngineHarness:
        root = tmp_path / f"harness-{next(counter)}"
        artifacts = ArtifactStore(root / "artifacts")
        db = ExperimentDB(root / "experiments.sqlite3")
        created.append(db)
        cache = SimulationCache(db, artifacts)
        resolved_simulator = simulator or CountingSimulator()
        resolved_constraints = constraints or ConstraintRegistry()
        workspace_root = root / "workspaces"
        engine = EvaluationEngine(
            simulator=resolved_simulator,
            db=db,
            artifact_store=artifacts,
            cache=cache,
            workspace_root=workspace_root,
            constraints=resolved_constraints,
            npv_evaluator=npv_evaluator,
        )
        return EngineHarness(
            engine=engine,
            db=db,
            artifacts=artifacts,
            cache=cache,
            simulator=resolved_simulator,
            workspace_root=workspace_root,
            root=root,
            constraints=resolved_constraints,
        )

    yield factory
    for db in created:
        db.close()


@pytest.fixture
def json_payload() -> dict[str, JsonValue]:
    return {"alpha": 1, "beta": [1, 2.5, None, True], "gamma": {"b": "значение", "a": -0.0}}
