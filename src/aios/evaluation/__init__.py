"""Simulator-agnostic фундамент оценки вариантов управления.

Публичный API пакета. Фейковый эвалюатор NPV здесь отсутствует намеренно:
он живёт только в тестах, чтобы синтетическое число не могло попасть
в production-путь и притвориться официальной метрикой.
"""

from aios.evaluation.artifacts import (
    ArtifactCorruptedError,
    ArtifactMissingError,
    ArtifactPathEscapeError,
    ArtifactReader,
    ArtifactStore,
    ArtifactStoreError,
    TrajectoryArtifactRef,
)
from aios.evaluation.cache import CachedSimulation, SimulationCache
from aios.evaluation.canonical import (
    KEY_SCHEMA_VERSION,
    CanonicalizationError,
    JsonValue,
    canonical_json,
    digest,
    digest_bytes,
    normalize_json,
)
from aios.evaluation.constraints import (
    ConstraintRegistry,
    ConstraintReport,
    ConstraintStatus,
    ConstraintViolation,
)
from aios.evaluation.engine import EvaluationEngine, EvaluationOutcome
from aios.evaluation.fake_simulator import FakeSimulator
from aios.evaluation.npv import (
    EvaluatorProvenance,
    NpvEstimate,
    NpvEvaluationError,
    NpvEvaluator,
    NpvStatus,
)
from aios.evaluation.records import RunRecord, RunStatus
from aios.evaluation.simulation import (
    ResultDigestMismatchError,
    SimulationFailure,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorIdentity,
    compute_request_digest,
    compute_result_digest,
    compute_simulation_key,
)
from aios.evaluation.simulator import Simulator, SimulatorError
from aios.evaluation.store import (
    SCHEMA_VERSION,
    CachedSimulationRow,
    CorruptedRecordError,
    ExperimentDB,
    ExperimentDBError,
    SchemaVersionMismatchError,
)

__all__ = [
    "KEY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ArtifactCorruptedError",
    "ArtifactMissingError",
    "ArtifactPathEscapeError",
    "ArtifactReader",
    "ArtifactStore",
    "ArtifactStoreError",
    "CachedSimulation",
    "CachedSimulationRow",
    "CanonicalizationError",
    "ConstraintRegistry",
    "ConstraintReport",
    "ConstraintStatus",
    "ConstraintViolation",
    "CorruptedRecordError",
    "EvaluationEngine",
    "EvaluationOutcome",
    "EvaluatorProvenance",
    "ExperimentDB",
    "ExperimentDBError",
    "FakeSimulator",
    "JsonValue",
    "NpvEstimate",
    "NpvEvaluationError",
    "NpvEvaluator",
    "NpvStatus",
    "ResultDigestMismatchError",
    "RunRecord",
    "RunStatus",
    "SchemaVersionMismatchError",
    "SimulationCache",
    "SimulationFailure",
    "SimulationRequest",
    "SimulationResult",
    "SimulationStatus",
    "Simulator",
    "SimulatorError",
    "SimulatorIdentity",
    "TrajectoryArtifactRef",
    "canonical_json",
    "compute_request_digest",
    "compute_result_digest",
    "compute_simulation_key",
    "digest",
    "digest_bytes",
    "normalize_json",
]
