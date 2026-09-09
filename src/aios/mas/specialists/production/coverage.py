"""Полнота структурного покрытия ОТВЕТА специалиста.

Проверка живёт в зоне специалиста, потому что проверяется его собственный
вывод. Детерминированный слой НИЧЕГО здесь не синтезирует: он умеет только
сказать, что требуемое направление осталось и не рассмотренным, и не
отклонённым. Придумать за модель действие, направление, качественную силу или
охват -- значит отдать детерминизму ту власть, которую контракт оставляет
специалисту.

Неполный ответ -- это не «почти валидный» ответ: пока допустимое направление
не рассмотрено и не отклонено с причиной, вывод не является полным
структурным заявлением о состоянии.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from aios.evaluation.canonical import JsonValue
from aios.mas.coverage import (
    COVERED_FAMILIES,
    CoverageCell,
    CoverageDeclaration,
    CoverageDisposition,
    CoverageGap,
    CoverageStatus,
    ScopeClass,
    coverage_gaps,
    declaration_set_errors,
    decline_explanation,
    declined_cells,
    occupied_cells,
    parse_decline_reason,
    required_cells,
    scope_class,
)
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.intents import ProductionAction
from aios.mas.magnitude_resolution.capability import (
    production_capabilities,
    supported_production_actions,
)
from aios.mas.magnitude_resolution.profile import ModelYEmpiricalMagnitudeProfile
from aios.state import WellRole

COVERAGE_VALIDATOR_VERSION = "aios.mas.specialists.production.coverage/1.1.0"

#: Действия, чья официальная семантика на сегодня действительно не разрешена.
#: Единственный источник правды для причины SEMANTICS_UNRESOLVED: заявить её
#: про действие, у которого семантика ясна, нельзя.
SEMANTICS_UNRESOLVED_ACTIONS: frozenset[ProductionAction] = frozenset({ProductionAction.SHUT_IN})


class StructuralCoverageIncomplete(ValueError):
    """Требуемое направление не рассмотрено и не отклонено с причиной."""


def authorized_producers(envelope: ProductionEnvelope) -> tuple[str, ...]:
    """Фонд, которым специалисту вообще разрешено оперировать на этом снимке."""
    return tuple(
        well.well_id for well in envelope.state_card.wells if well.role is WellRole.PRODUCER
    )


def executable_actions(envelope: ProductionEnvelope) -> tuple[ProductionAction, ...]:
    """Пересечение разрешённого envelope и исполнимого резолвером.

    Оба источника уже существуют; второго списка здесь не заводится, иначе он
    разошёлся бы с профилем при первом же изменении якорей.
    """
    allowed = {ProductionAction(action) for action in envelope.allowed_actions}
    return tuple(action for action in supported_production_actions() if action in allowed)


def _executable_flags() -> dict[str, bool]:
    return {
        item.action: item.executable
        for item in production_capabilities(ModelYEmpiricalMagnitudeProfile())
    }


def validate_decline(
    cell: CoverageCell, reason: str, *, envelope: ProductionEnvelope
) -> str | None:
    """Проверить причину отказа против того, что система знает наверняка.

    Правильный токен -- ещё не основание. Три причины проверяемы
    детерминированно и отвергаются, если противоречат текущей исполнимости или
    состоянию. Четвёртая -- NOT_GROUNDABLE -- НЕ проверяема здесь: заземляемо
    ли направление на этих данных, судит специалист, и подменять это суждение
    детерминированным правилом значило бы вернуть себе ту власть, которую
    контракт ему отдаёт. Она принимается с сохранением пояснения.
    """
    action = ProductionAction(cell.action)
    if reason == str(CoverageStatus.NOT_MATERIALIZABLE):
        if _executable_flags().get(str(action), False):
            return (
                f"{cell.key()}: NOT_MATERIALIZABLE contradicts the resolver, which "
                "reports this action executable"
            )
        return None
    if reason == str(CoverageStatus.SEMANTICS_UNRESOLVED):
        if action not in SEMANTICS_UNRESOLVED_ACTIONS:
            return f"{cell.key()}: SEMANTICS_UNRESOLVED is not declared for this action"
        return None
    if reason == str(CoverageStatus.INADMISSIBLE_NOW):
        if authorized_producers(envelope):
            return (
                f"{cell.key()}: INADMISSIBLE_NOW contradicts the state, which still "
                "holds targetable authorized producers"
            )
        return None
    return None


def validate_declarations(
    envelope: ProductionEnvelope,
    *,
    parsed_declarations,
    preferred_intents: Sequence[object],
    considered_intents: Sequence[object],
) -> tuple[tuple[CoverageDeclaration, ...], tuple[str, ...]]:
    """Сверить заявления о покрытии со структурой, которую специалист написал.

    Заявление не является вторым источником структуры: PREFERRED и
    CONSIDERED_NOT_PREFERRED обязаны соответствовать реально существующему
    предложению того же действия и охвата. Иначе заявление утверждало бы
    покрытие, за которым ничего не стоит.
    """
    fund = authorized_producers(envelope)
    required = required_cells(authorized=fund, executable=executable_actions(envelope))
    declarations: list[CoverageDeclaration] = []
    errors: list[str] = []

    def _cells(intents) -> set[str]:
        found: set[str] = set()
        for intent in intents:
            action = ProductionAction(intent.action)
            if action not in COVERED_FAMILIES:
                continue
            scope = scope_class(intent.target_wells, fund)
            if scope is not None:
                found.add(CoverageCell(action=action, scope=scope).key())
        return found

    preferred_cells = _cells(preferred_intents)
    considered_cells = _cells(considered_intents)

    for item in parsed_declarations:
        try:
            cell = CoverageCell(
                action=ProductionAction(item.action), scope=ScopeClass(item.scope_class)
            )
            disposition = CoverageDisposition(item.disposition)
        except (ValueError, KeyError) as exc:
            errors.append(f"unparsable_declaration:{exc}")
            continue
        reason = None
        if disposition is CoverageDisposition.DECLINED:
            reason = parse_decline_reason(item.decline_reason)
            if reason is None:
                errors.append(f"{cell.key()}: DECLINED without a valid structural reason")
                continue
            conflict = validate_decline(cell, reason, envelope=envelope)
            if conflict is not None:
                errors.append(conflict)
                continue
        elif disposition is CoverageDisposition.PREFERRED:
            if cell.key() not in preferred_cells:
                errors.append(f"{cell.key()}: PREFERRED without a matching ranked hypothesis")
                continue
        elif cell.key() not in considered_cells:
            errors.append(f"{cell.key()}: CONSIDERED_NOT_PREFERRED without a matching alternative")
            continue
        declarations.append(
            CoverageDeclaration(
                cell=cell,
                disposition=disposition,
                decline_reason=reason,
                explanation=decline_explanation(item.decline_reason) or item.explanation,
            )
        )

    errors.extend(declaration_set_errors(declarations, required))
    return tuple(declarations), tuple(errors)


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Что специалист покрыл, чем именно и что осталось непокрытым."""

    authorized_producer_count: int
    required: tuple[str, ...]
    filled: Mapping[str, str]
    gaps: tuple[CoverageGap, ...]

    @property
    def complete(self) -> bool:
        return not self.gaps

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "validator_version": COVERAGE_VALIDATOR_VERSION,
            "authorized_producer_count": self.authorized_producer_count,
            "required_cells": list(self.required),
            "filled_cells": dict(self.filled),
            "gaps": [gap.to_json() for gap in self.gaps],
            "complete": self.complete,
        }


def evaluate_coverage(
    envelope: ProductionEnvelope,
    *,
    preferred_intents: Sequence[object],
    considered_intents: Sequence[object],
    declared_reasons: Mapping[str, str],
) -> CoverageReport:
    """Свести покрытие ответа. Ничего не создаёт и ничего не отвергает."""
    fund = authorized_producers(envelope)
    executable = executable_actions(envelope)
    pairs: list[tuple[object, CoverageStatus]] = [
        (intent, CoverageStatus.PREFERRED) for intent in preferred_intents
    ]
    pairs += [(intent, CoverageStatus.CONSIDERED_NOT_PREFERRED) for intent in considered_intents]
    occupied = occupied_cells(pairs, authorized=fund)
    required = required_cells(authorized=fund, executable=executable)
    declined = declined_cells(declared_reasons, required=required)
    gaps = coverage_gaps(
        authorized=fund, executable=executable, occupied=occupied, declined=declined
    )
    filled = {cell.key(): str(status) for cell, status in {**occupied, **declined}.items()}
    return CoverageReport(
        authorized_producer_count=len(fund),
        required=tuple(cell.key() for cell in required),
        filled=filled,
        gaps=gaps,
    )


def declared_reasons_from(considered_actions: Iterable[object]) -> dict[str, str]:
    """Причины отказа, объявленные специалистом в `considered_actions`.

    Причина принимается только в виде одного из структурных статусов; свободный
    текст остаётся аудитом и ячейку не закрывает.
    """
    reasons: dict[str, str] = {}
    for item in considered_actions:
        if getattr(item, "supported", True):
            continue
        # Строка сохраняется целиком: токен из неё извлекает единственный
        # разборщик причин, а не второе правило здесь. Прежний вариант
        # приводил ВСЮ строку к верхнему регистру и сравнивал её с токеном,
        # поэтому естественное "NOT_MATERIALIZABLE: <пояснение>" не совпадало
        # ни с чем и не закрывало ячейку.
        reasons[str(getattr(item, "action", ""))] = str(getattr(item, "reason", ""))
    return reasons


__all__ = [
    "COVERAGE_VALIDATOR_VERSION",
    "SEMANTICS_UNRESOLVED_ACTIONS",
    "CoverageReport",
    "StructuralCoverageIncomplete",
    "authorized_producers",
    "declared_reasons_from",
    "evaluate_coverage",
    "executable_actions",
    "validate_declarations",
    "validate_decline",
]
