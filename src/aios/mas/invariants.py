"""Frozen system invariants T1, T3-T7 for Hybrid MAS v1.1 (T2 audit included).

Это deterministic guard layer, а не реализация pipeline: ни один Orchestrator,
PredictiveSafetyScreen или MagnitudeResolver здесь не появляется. Каждая
функция ниже -- reusable assertion, которую ОБЯЗАН вызвать соответствующий
будущий компонент; до тех пор, пока такого компонента нет, guard проверен
только напрямую тестами, а не end-to-end внутри реального pipeline.

Ни один guard не вводит NPV-теорему и не утверждает NPV(MAS) >= NPV(BASELINE)
-- такого утверждения не существует. T3, например, гарантирует только, что
HOLD и DETERMINISTIC_BASELINE физически присутствуют в кандидатском наборе.
"""

from __future__ import annotations

import ast
import dataclasses
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from aios.evaluation.canonical import JsonValue, canonical_json, digest
from aios.mas.arbiter import ArbiterDecision, DecisionBundle
from aios.mas.common import CandidateKind
from aios.mas.envelopes import ArbiterEnvelope, InjectionEnvelope, ProductionEnvelope
from aios.mas.intents import InjectionIntent, ProductionIntent
from aios.mas.magnitude import (
    MagnitudeCandidate,
    injector_control_to_json,
    producer_control_to_json,
)
from aios.mas.proposals import (
    InjectionAgentOutput,
    InjectionProposal,
    ProductionAgentOutput,
    ProductionProposal,
)
from aios.schedule import InjectorControl, ProducerControl, natural_key


class MasInvariantError(ValueError):
    """Системный инвариант Hybrid MAS v1.1 нарушен."""


# ---------------------------------------------------------------------------
# T1 -- ANTI-ANCHORING
# ---------------------------------------------------------------------------


def require_no_baseline_field(envelope_type: type | object) -> None:
    """Structural guard для границы будущего PromptBuilder.

    Специалист-envelope уже структурно не несёт baseline-поля (проверено
    контрактами MAS-CONTRACTS-001). Этот guard -- дополнительная защита на
    случай, если будущий код передаст в PromptBuilder расширенный/изменённый
    envelope-подобный тип, который снова протащил бы baseline. Сигнатура
    SpecialistPromptBuilder.build(envelope) сама по себе не принимает второй
    аргумент -- это первый рубеж; этот guard -- второй, на случай мутации
    самого типа envelope.
    """
    target = envelope_type if isinstance(envelope_type, type) else type(envelope_type)
    if not dataclasses.is_dataclass(target):
        raise MasInvariantError(f"anti_anchoring: {target.__name__} is not a dataclass")
    for f in dataclasses.fields(target):
        if "baseline" in f.name.lower():
            raise MasInvariantError(
                f"anti_anchoring: {target.__name__}.{f.name} carries a baseline-like field"
            )


# ---------------------------------------------------------------------------
# T2 -- NO SETPOINTS CROSS (package-level authority audit)
# ---------------------------------------------------------------------------

# Единственные модули aios.mas, которым разрешено видеть authoritative
# numerical control types. magnitude.py их производит (ArbiterDecision ->
# MagnitudeCandidate); invariants.py их только сравнивает/дайджестирует
# постфактум (HOLD carry-forward, canonical identity) и не принимает решений
# о том, какие controls выбрать.
NUMERICAL_CONTROL_BOUNDARY_MODULES = frozenset({"magnitude.py", "invariants.py"})

DECISION_LAYER_TYPES: tuple[type, ...] = (
    ProductionIntent,
    InjectionIntent,
    ProductionProposal,
    InjectionProposal,
    ProductionAgentOutput,
    InjectionAgentOutput,
    ProductionEnvelope,
    InjectionEnvelope,
    ArbiterEnvelope,
    DecisionBundle,
    ArbiterDecision,
)

_MAS_PACKAGE_DIR = Path(__file__).resolve().parent


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def audit_numerical_control_imports(package_dir: Path | None = None) -> tuple[str, ...]:
    """Вернуть имена файлов вне boundary, импортирующих numerical controls.

    Пустой tuple -- пакет соответствует T2. AST-инспекция, а не текстовый
    grep: находит реальные import-узлы, а не совпадения подстроки в
    комментариях/докстрингах.
    """
    directory = package_dir if package_dir is not None else _MAS_PACKAGE_DIR
    offenders: list[str] = []
    for path in sorted(directory.glob("*.py")):
        if path.name in NUMERICAL_CONTROL_BOUNDARY_MODULES or path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_names(tree)
        if "ProducerControl" in imported or "InjectorControl" in imported:
            offenders.append(path.name)
    return tuple(offenders)


def require_numerical_control_import_boundary(package_dir: Path | None = None) -> None:
    offenders = audit_numerical_control_imports(package_dir)
    if offenders:
        raise MasInvariantError(f"no_setpoints_cross: forbidden control import in {offenders}")


def require_no_numerical_controls_before_magnitude(
    types: Sequence[type] = DECISION_LAYER_TYPES,
) -> None:
    """Structural guard: decision-layer dataclasses carry no control fields."""
    for cls in types:
        for f in dataclasses.fields(cls):
            type_str = str(f.type)
            if "ProducerControl" in type_str or "InjectorControl" in type_str:
                raise MasInvariantError(
                    f"no_setpoints_cross: {cls.__name__}.{f.name} carries a numerical control type"
                )


# ---------------------------------------------------------------------------
# T3 -- SIMULATION FLOOR
# ---------------------------------------------------------------------------


def require_simulation_floor(candidates: Sequence[MagnitudeCandidate]) -> None:
    """HOLD и DETERMINISTIC_BASELINE обязаны присутствовать среди resolved candidates.

    Не теорема о NPV: только гарантия, что "ничего не делать" и "продолжать
    как раньше" физически дошли до simulation candidate set.
    """
    kinds = {candidate.candidate_kind for candidate in candidates}
    if CandidateKind.HOLD not in kinds:
        raise MasInvariantError("simulation_floor: candidate set is missing CandidateKind.HOLD")
    if CandidateKind.DETERMINISTIC_BASELINE not in kinds:
        raise MasInvariantError(
            "simulation_floor: candidate set is missing CandidateKind.DETERMINISTIC_BASELINE"
        )


# ---------------------------------------------------------------------------
# T4 -- SAFETY SCREEN IS NOT A FILTER
# ---------------------------------------------------------------------------


def require_candidate_preservation(
    before_ids: Sequence[str], after_ids: Sequence[str]
) -> None:
    """Annotation-стадия обязана сохранять identity set candidates ровно.

    Сравнение по multiset (Counter), а не по len(): порядок неважен, но
    dropped/added/duplicated identity -- запрещены равно.
    """
    before_counts = Counter(before_ids)
    after_counts = Counter(after_ids)
    if before_counts != after_counts:
        dropped = before_counts - after_counts
        added = after_counts - before_counts
        raise MasInvariantError(
            f"candidate_preservation: identity set changed "
            f"(dropped={dict(dropped)}, added={dict(added)})"
        )


# ---------------------------------------------------------------------------
# T5 -- FAIR LLM ON/OFF ABLATION
# ---------------------------------------------------------------------------

_ABLATION_IGNORED_KEY = "llm_enabled"


def require_ablation_equivalence(
    config_a: Mapping[str, JsonValue], config_b: Mapping[str, JsonValue]
) -> None:
    """Variant B и Variant D обязаны совпадать во всём, кроме llm_enabled.

    Единственная разрешённая разница -- значение ключа "llm_enabled". Любая
    другая разница (simulation_budget, resolver_version, candidate budget,
    guardian/CHDD/MPC config и т.п.) -- запрещена. Ключ "llm_enabled" обязан
    присутствовать в обоих конфигах.
    """
    keys_a, keys_b = set(config_a), set(config_b)
    if keys_a != keys_b:
        raise MasInvariantError(
            f"ablation_equivalence: config key sets differ "
            f"(only_a={sorted(keys_a - keys_b)}, only_b={sorted(keys_b - keys_a)})"
        )
    if _ABLATION_IGNORED_KEY not in keys_a:
        raise MasInvariantError(
            f"ablation_equivalence: both configs must carry {_ABLATION_IGNORED_KEY!r}"
        )
    stripped_a = {key: value for key, value in config_a.items() if key != _ABLATION_IGNORED_KEY}
    stripped_b = {key: value for key, value in config_b.items() if key != _ABLATION_IGNORED_KEY}
    if canonical_json(stripped_a) != canonical_json(stripped_b):
        raise MasInvariantError(
            f"ablation_equivalence: configs differ beyond {_ABLATION_IGNORED_KEY!r}"
        )


# ---------------------------------------------------------------------------
# T6 / T7 -- HOLD CARRY-FORWARD and CANONICAL IDENTITY
# ---------------------------------------------------------------------------


def _require_no_ambiguous_wells(
    controls: Sequence[ProducerControl] | Sequence[InjectorControl], label: str
) -> None:
    """Один и тот же well с разными controls -- запрещённая двусмысленность.

    Точные буквальные повторы (тот же well, тот же control) допустимы и
    канонизируются в одну запись -- так же, как ScheduleStep.canonical()
    убирает дословные повторы, не пытаясь разрешить противоречие политики.
    """
    seen: dict[str, ProducerControl | InjectorControl] = {}
    for control in controls:
        existing = seen.get(control.well)
        if existing is not None and existing != control:
            raise MasInvariantError(
                f"canonical_control_snapshot.{label}: well {control.well!r} has "
                "conflicting control values"
            )
        seen[control.well] = control


def canonical_control_snapshot(
    producers: Sequence[ProducerControl],
    injectors: Sequence[InjectorControl],
) -> dict[str, JsonValue]:
    """Order-independent JSON-совместимый снимок resolved numerical controls.

    Reuse существующей сериализации из aios.mas.magnitude (не второй
    canonicalizer) и существующего natural_key для порядка.
    """
    _require_no_ambiguous_wells(producers, "producers")
    _require_no_ambiguous_wells(injectors, "injectors")

    producer_wells = {control.well for control in producers}
    injector_wells = {control.well for control in injectors}
    overlap = producer_wells & injector_wells
    if overlap:
        raise MasInvariantError(
            f"canonical_control_snapshot: well(s) {sorted(overlap)} appear as both "
            "producer and injector"
        )

    canonical_producers = tuple(
        sorted(dict.fromkeys(producers), key=lambda c: natural_key(c.well))
    )
    canonical_injectors = tuple(
        sorted(dict.fromkeys(injectors), key=lambda c: natural_key(c.well))
    )
    return {
        "producers": [producer_control_to_json(control) for control in canonical_producers],
        "injectors": [injector_control_to_json(control) for control in canonical_injectors],
    }


def canonical_control_digest(
    producers: Sequence[ProducerControl],
    injectors: Sequence[InjectorControl],
) -> str:
    """SHA-256 канонического снимка. Reuse aios.evaluation.canonical.digest."""
    return digest(canonical_control_snapshot(producers, injectors))


def require_hold_carry_forward(
    *,
    previous_producers: Sequence[ProducerControl],
    previous_injectors: Sequence[InjectorControl],
    held_producers: Sequence[ProducerControl],
    held_injectors: Sequence[InjectorControl],
) -> None:
    """HOLD обязан быть точным carry-forward, без пересчёта magnitude.

    Никакого tolerance: canonical snapshot предыдущих controls обязан
    совпасть с canonical snapshot held controls побайтово (через canonical
    JSON). Порядок входных tuples не важен, значения -- важны полностью.
    """
    previous_snapshot = canonical_control_snapshot(previous_producers, previous_injectors)
    held_snapshot = canonical_control_snapshot(held_producers, held_injectors)
    if previous_snapshot != held_snapshot:
        raise MasInvariantError(
            "hold_carry_forward: held controls are not an exact carry-forward of previous controls"
        )
