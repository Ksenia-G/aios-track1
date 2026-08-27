"""Контракты воспроизводимого бенчмарка MAS v1.1.

Бенчмарк отвечает на один вопрос: даёт ли MAS экономическое преимущество при
ОДИНАКОВЫХ физических и экономических условиях. Поэтому здесь фиксируется не
только NPV, но и вся рамка прогона -- колода, симулятор, горизонт, стартовое
состояние, версия ЧДД, режим компенсации. Без этой рамки два числа сравнивать
нельзя, а таблица из несопоставимых чисел выглядит убедительнее, чем есть.

Секреты сюда не попадают: провайдер описывается идентификатором модели, а не
ключом, и в манифесте нет ни путей рабочих каталогов, ни тяжёлых артефактов.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from aios.evaluation.canonical import JsonValue, digest

BENCHMARK_SCHEMA_VERSION = "aios.benchmark/1.0.0"


class BenchmarkError(ValueError):
    """Запись бенчмарка нарушает контракт."""


class VariantFamily(StrEnum):
    """Происхождение варианта. Определяет, что именно мы измеряем."""

    BASELINE = "BASELINE"
    EXP003 = "EXP003"
    EXP004 = "EXP004"
    MAS_OFF = "MAS_OFF"
    MAS_ON = "MAS_ON"


class RunKind(StrEnum):
    """Как вариант получает свои управления.

    Это не косметика: фиксированное расписание оценивается один раз на весь
    горизонт, а замкнутый контур принимает решения по шагам и порождает
    расписание сам. Сравнивать их NPV можно, но только зная, что это разные
    механизмы, а не два прогона одного и того же.
    """

    HISTORICAL_REFERENCE = "HISTORICAL_REFERENCE"
    FIXED_SCHEDULE_REPLAY = "FIXED_SCHEDULE_REPLAY"
    CLOSED_LOOP_POLICY = "CLOSED_LOOP_POLICY"


class ComparisonClass(StrEnum):
    """Насколько честно сравнение двух вариантов."""

    DIRECT_COMPARABLE = "DIRECT_COMPARABLE"
    NORMALIZED_COMPARABLE = "NORMALIZED_COMPARABLE"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    NOT_COMPARABLE = "NOT_COMPARABLE"


#: Классы, в которых заявлять победу нельзя.
NON_WINNING_CLASSES = frozenset({ComparisonClass.REFERENCE_ONLY, ComparisonClass.NOT_COMPARABLE})


class CompensationPolicy(StrEnum):
    """Режим компенсации.

    Официальная формула организаторами не подтверждена, поэтому исследовательские
    допущения нельзя молча выдавать за правило: вариант, посчитанный под
    исследовательским режимом, обязан говорить об этом сам.
    """

    OFFICIAL_UNKNOWN_PORT = "OFFICIAL_UNKNOWN_PORT"
    RESEARCH_SCREENING_VRR = "RESEARCH_SCREENING_VRR"
    RESEARCH_SCREENING_WATER_BALANCE = "RESEARCH_SCREENING_WATER_BALANCE"


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{where}: expected non-empty text")
    return value


def _require_finite(value: float, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BenchmarkError(f"{where}: expected a real number")
    if math.isnan(value) or math.isinf(value):
        raise BenchmarkError(f"{where}: must be finite, got {value!r}")
    return float(value)


@dataclass(frozen=True, slots=True)
class PhysicalFrame:
    """Физическая и экономическая рамка прогона.

    Два варианта сравнимы напрямую только когда их рамки совпадают. Всё, что
    может подменить смысл числа -- колода, симулятор, горизонт, стартовое
    состояние, версия ЧДД, режим компенсации -- лежит здесь и участвует в
    дайджесте.
    """

    case_id: str
    case_digest: str
    deck_reference: str
    deck_digest: str | None
    simulator_identity: str
    runtime_mode: str
    initial_state_digest: str
    effective_start: str
    effective_end: str
    chdd_calculator_version: str
    chdd_month_count: int | None
    guardian_version: str
    compensation_policy: CompensationPolicy

    def __post_init__(self) -> None:
        for name in (
            "case_id",
            "case_digest",
            "deck_reference",
            "simulator_identity",
            "runtime_mode",
            "initial_state_digest",
            "effective_start",
            "effective_end",
            "chdd_calculator_version",
            "guardian_version",
        ):
            _require_text(getattr(self, name), f"physical_frame.{name}")
        CompensationPolicy(self.compensation_policy)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "case_id": self.case_id,
            "case_digest": self.case_digest,
            "deck_reference": self.deck_reference,
            "deck_digest": self.deck_digest,
            "simulator_identity": self.simulator_identity,
            "runtime_mode": self.runtime_mode,
            "initial_state_digest": self.initial_state_digest,
            "effective_start": self.effective_start,
            "effective_end": self.effective_end,
            "chdd_calculator_version": self.chdd_calculator_version,
            "chdd_month_count": self.chdd_month_count,
            "guardian_version": self.guardian_version,
            "compensation_policy": str(self.compensation_policy),
        }

    def digest(self) -> str:
        return digest(self.to_json())

    #: Поля, расхождение в которых делает прямое сравнение невозможным.
    COMPARABLE_KEYS = (
        "case_id",
        "case_digest",
        "deck_reference",
        "simulator_identity",
        "initial_state_digest",
        "effective_start",
        "effective_end",
        "chdd_calculator_version",
        "guardian_version",
        "compensation_policy",
    )


@dataclass(frozen=True, slots=True)
class CognitiveConfig:
    """Состояние когнитивной ветки. Ключей и секретов здесь нет намеренно."""

    llm_enabled: bool
    provider_id: str | None = None
    model_id: str | None = None
    run_id: str | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.llm_enabled, bool):
            raise BenchmarkError("cognitive_config.llm_enabled: expected a boolean")
        for name in ("provider_id", "model_id", "run_id"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, f"cognitive_config.{name}")
        if self.llm_enabled and not self.model_id:
            raise BenchmarkError(
                "cognitive_config: llm_enabled requires a model_id; a run with the "
                "cognitive branch on but no model named is not reproducible"
            )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "llm_enabled": self.llm_enabled,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "run_id": self.run_id,
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class EvaluationLineage:
    """Происхождение одной физической оценки, как его отдаёт runtime-зона."""

    schedule_digest: str
    simulation_key: str | None = None
    request_digest: str | None = None
    result_digest: str | None = None
    realized_state_digest: str | None = None
    chdd_input_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.schedule_digest, "evaluation_lineage.schedule_digest")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schedule_digest": self.schedule_digest,
            "simulation_key": self.simulation_key,
            "request_digest": self.request_digest,
            "result_digest": self.result_digest,
            "realized_state_digest": self.realized_state_digest,
            "chdd_input_digest": self.chdd_input_digest,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRunManifest:
    """Полная воспроизводимая запись одного прогона варианта."""

    benchmark_schema_version: str
    variant_id: str
    variant_family: VariantFamily
    run_kind: RunKind
    code_sha: str
    frame: PhysicalFrame
    cognitive: CognitiveConfig
    lineage: tuple[EvaluationLineage, ...] = ()
    source_reference: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.variant_id, "run_manifest.variant_id")
        _require_text(self.code_sha, "run_manifest.code_sha")
        VariantFamily(self.variant_family)
        RunKind(self.run_kind)
        if self.benchmark_schema_version != BENCHMARK_SCHEMA_VERSION:
            raise BenchmarkError(
                f"run_manifest: unsupported schema {self.benchmark_schema_version!r}"
            )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "benchmark_schema_version": self.benchmark_schema_version,
            "variant_id": self.variant_id,
            "variant_family": str(self.variant_family),
            "run_kind": str(self.run_kind),
            "code_sha": self.code_sha,
            "frame": self.frame.to_json(),
            "cognitive": self.cognitive.to_json(),
            "lineage": [item.to_json() for item in self.lineage],
            "source_reference": self.source_reference,
            "notes": list(self.notes),
        }

    def digest(self) -> str:
        return digest(self.to_json())


@dataclass(frozen=True, slots=True)
class BenchmarkOutcome:
    """Измеренный результат одного прогона варианта."""

    manifest: BenchmarkRunManifest
    npv_mln_rub: float
    guardian_pass: bool
    guardian_check_count: int = 0
    guardian_failed_count: int = 0
    simulation_count: int = 0
    control_change_count: int = 0
    decision_steps: int = 0
    fallback_count: int = 0
    round2_count: int = 0
    policy_failure_count: int = 0
    yearly_profile_digest: str | None = None
    chdd_result_digest: str | None = None
    wall_duration_seconds: float | None = None
    extra: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_finite(self.npv_mln_rub, "benchmark_outcome.npv_mln_rub")
        if self.guardian_failed_count < 0 or self.guardian_check_count < 0:
            raise BenchmarkError("benchmark_outcome: guardian counters must be >= 0")
        if self.guardian_pass and self.guardian_failed_count:
            raise BenchmarkError(
                "benchmark_outcome: a passing Guardian verdict cannot carry failed checks"
            )

    @property
    def variant_id(self) -> str:
        return self.manifest.variant_id

    @property
    def family(self) -> VariantFamily:
        return self.manifest.variant_family

    @property
    def rankable(self) -> bool:
        """Может ли результат вообще участвовать в ранжировании.

        Нарушивший официальные ограничения кандидат не становится победителем
        из-за большего NPV: он просто нелегален.
        """
        return self.guardian_pass and math.isfinite(self.npv_mln_rub)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "manifest": self.manifest.to_json(),
            "manifest_digest": self.manifest.digest(),
            "npv_mln_rub": self.npv_mln_rub,
            "guardian_pass": self.guardian_pass,
            "guardian_check_count": self.guardian_check_count,
            "guardian_failed_count": self.guardian_failed_count,
            "simulation_count": self.simulation_count,
            "control_change_count": self.control_change_count,
            "decision_steps": self.decision_steps,
            "fallback_count": self.fallback_count,
            "round2_count": self.round2_count,
            "policy_failure_count": self.policy_failure_count,
            "yearly_profile_digest": self.yearly_profile_digest,
            "chdd_result_digest": self.chdd_result_digest,
            "wall_duration_seconds": self.wall_duration_seconds,
            "rankable": self.rankable,
            "extra": dict(self.extra),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    """Сопоставление двух вариантов с явным классом честности."""

    left_variant_id: str
    right_variant_id: str
    comparison_class: ComparisonClass
    reasons: tuple[str, ...] = ()
    delta_npv_mln_rub: float | None = None
    delta_pct: float | None = None

    def __post_init__(self) -> None:
        ComparisonClass(self.comparison_class)

    @property
    def claimable(self) -> bool:
        """Можно ли на этом сравнении заявлять выигрыш."""
        return self.comparison_class not in NON_WINNING_CLASSES

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "left_variant_id": self.left_variant_id,
            "right_variant_id": self.right_variant_id,
            "comparison_class": str(self.comparison_class),
            "claimable": self.claimable,
            "reasons": list(self.reasons),
            "delta_npv_mln_rub": self.delta_npv_mln_rub,
            "delta_pct": self.delta_pct,
        }


__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "NON_WINNING_CLASSES",
    "BenchmarkComparison",
    "BenchmarkError",
    "BenchmarkOutcome",
    "BenchmarkRunManifest",
    "CognitiveConfig",
    "ComparisonClass",
    "CompensationPolicy",
    "EvaluationLineage",
    "PhysicalFrame",
    "RunKind",
    "VariantFamily",
]
