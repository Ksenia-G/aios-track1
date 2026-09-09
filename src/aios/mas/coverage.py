"""Структурное покрытие Production: словарь, ячейки и пробелы.

Здесь нет ни одной численной величины и ни одного идентификатора колоды.
Модуль отвечает на один вопрос: КАКИЕ структурные направления специалист
обязан был рассмотреть на этом снимке, и какие из них он действительно
рассмотрел. Он ничего не предлагает и ничего не выбирает.

Правило покрытия существует потому, что предпочтение специалиста и
физическая проверка -- разные вещи. "Модель не предпочла действие" не
означает "действие не существует для физической проверки": пока
допустимое направление не проверено физически, утверждение о превосходстве
противоположного не опровергнуто, а просто не проверялось.

Покрытие НИКОГДА не изобретает действие. Оно только называет пробел; закрыть
его может исключительно сам специалист -- гипотезой, рассмотренной
альтернативой или явной структурной причиной отказа.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from aios.evaluation.canonical import JsonValue
from aios.mas.intents import ProductionAction

#: Доля авторизованного фонда, начиная с которой охват считается широким.
#: Порог зафиксирован до измерений и с тех пор не менялся; это классификация
#: заявленной широты, а не численная величина управления.
BROAD_SHARE = 0.75

#: Пара противоположных масштабирующих семейств. Покрытие определено только на
#: ней: это единственные два действия, которые (а) прямо противоположны,
#: (б) оба исполнимы, (в) оба масштабируемы. Непроверенное из них -- это
#: неопровергнутая контрфактическая гипотеза, а не отсутствующая степень свободы.
COVERED_FAMILIES: tuple[ProductionAction, ...] = (
    ProductionAction.INCREASE_OFFTAKE,
    ProductionAction.REDUCE_OFFTAKE,
)


class CoverageError(ValueError):
    """Контракт покрытия нарушен."""


class ScopeClass(StrEnum):
    """Заявленная широта охвата. Выводится, а не приходит от модели."""

    SELECTIVE = "SELECTIVE"
    BROAD = "BROAD"


class CoverageDisposition(StrEnum):
    """Что специалист заявил про требуемую ячейку покрытия."""

    PREFERRED = "PREFERRED"
    CONSIDERED_NOT_PREFERRED = "CONSIDERED_NOT_PREFERRED"
    DECLINED = "DECLINED"


class CoverageStatus(StrEnum):
    """Чем именно специалист закрыл ячейку покрытия."""

    PREFERRED = "PREFERRED"
    CONSIDERED_NOT_PREFERRED = "CONSIDERED_NOT_PREFERRED"
    NOT_GROUNDABLE = "NOT_GROUNDABLE"
    INADMISSIBLE_NOW = "INADMISSIBLE_NOW"
    NOT_MATERIALIZABLE = "NOT_MATERIALIZABLE"
    SEMANTICS_UNRESOLVED = "SEMANTICS_UNRESOLVED"


#: Причины, которыми специалист имеет право закрыть ячейку БЕЗ гипотезы.
DECLINE_REASONS: frozenset[str] = frozenset(
    {
        str(CoverageStatus.NOT_GROUNDABLE),
        str(CoverageStatus.INADMISSIBLE_NOW),
        str(CoverageStatus.NOT_MATERIALIZABLE),
        str(CoverageStatus.SEMANTICS_UNRESOLVED),
    }
)


def parse_decline_reason(text: str) -> str | None:
    """Извлечь ВЕДУЩИЙ структурный токен причины; иначе None.

    Токен -- это первое слово строки, ограниченное концом строки, двоеточием
    или пробелом. Подстрочный поиск здесь недопустим: `NOT_MATERIALIZABLE`,
    встреченное в середине фразы, -- это проза о причине, а не заявленная
    причина, и `NOT_MATERIALIZABLE_BUT_MAYBE` -- вообще другое слово.
    Пояснение после токена разрешено и живёт отдельно.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head = stripped.split(":", 1)[0].split()[0] if stripped.split(":", 1)[0].split() else ""
    token = head.strip().upper()
    return token if token in DECLINE_REASONS else None


def decline_explanation(text: str) -> str:
    """Пояснение после токена причины. Аудит, а не решение."""
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if ":" in stripped:
        return stripped.split(":", 1)[1].strip()
    parts = stripped.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


@dataclass(frozen=True, slots=True)
class CoverageCell:
    """Одна ячейка матрицы покрытия: семейство действия и класс охвата."""

    action: ProductionAction
    scope: ScopeClass

    def __post_init__(self) -> None:
        ProductionAction(self.action)
        ScopeClass(self.scope)

    def key(self) -> str:
        return f"{self.action!s}|{self.scope!s}"

    def to_json(self) -> dict[str, JsonValue]:
        return {"action": str(self.action), "scope": str(self.scope)}


@dataclass(frozen=True, slots=True)
class CoverageDeclaration:
    """Заявление специалиста ровно про одну требуемую ячейку.

    Слой прослеживаемости, а не второй источник структуры: заявление
    PREFERRED/CONSIDERED_NOT_PREFERRED обязано соответствовать реально
    существующему предложению того же действия и охвата, иначе оно
    утверждало бы покрытие, за которым ничего не стоит.
    """

    cell: CoverageCell
    disposition: CoverageDisposition
    decline_reason: str | None = None
    explanation: str = ""

    def __post_init__(self) -> None:
        CoverageDisposition(self.disposition)
        if self.disposition is CoverageDisposition.DECLINED:
            if self.decline_reason not in DECLINE_REASONS:
                raise CoverageError(
                    f"coverage_declaration[{self.cell.key()}]: DECLINED requires one of "
                    f"{sorted(DECLINE_REASONS)}"
                )
        elif self.decline_reason is not None:
            raise CoverageError(
                f"coverage_declaration[{self.cell.key()}]: decline_reason is only for DECLINED"
            )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            **self.cell.to_json(),
            "disposition": str(self.disposition),
            "decline_reason": self.decline_reason,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """Требуемая, но не закрытая ячейка."""

    cell: CoverageCell
    family_absent: bool

    def to_json(self) -> dict[str, JsonValue]:
        return {**self.cell.to_json(), "family_absent": self.family_absent}


def scope_class(targets: Sequence[str], authorized: Iterable[str]) -> ScopeClass | None:
    """Класс охвата относительно ТЕКУЩЕГО авторизованного фонда.

    Никакой константы размера фонда: и порог, и знаменатель приходят из
    состояния. Пустая цель охвата не имеет (это HOLD), и это не ошибка.
    """
    fund = frozenset(authorized)
    named = frozenset(targets)
    if not named or not fund:
        return None
    return ScopeClass.BROAD if len(named) / len(fund) >= BROAD_SHARE else ScopeClass.SELECTIVE


def is_all_authorized(targets: Sequence[str], authorized: Iterable[str]) -> bool:
    """Цель -- ВЕСЬ авторизованный фонд, проверенная равенством множеств.

    Это и есть единственный способ выразить «все авторизованные скважины»
    без нового поля контракта и без числа: специалист перечисляет фонд,
    а система сверяет его с собственным снимком.
    """
    fund = frozenset(authorized)
    return bool(fund) and frozenset(targets) == fund


def required_cells(
    *, authorized: Iterable[str], executable: Iterable[ProductionAction]
) -> tuple[CoverageCell, ...]:
    """Ячейки, которые вообще имеет смысл требовать на этом снимке.

    Требуются только исполнимые масштабирующие семейства. Класс BROAD
    исчезает, если фонд слишком мал, чтобы отличаться от SELECTIVE, --
    матрица не добивается до полноты ради вида.
    """
    fund = frozenset(authorized)
    if not fund:
        return ()
    executable_set = {ProductionAction(action) for action in executable}
    scopes: tuple[ScopeClass, ...] = (
        (ScopeClass.BROAD, ScopeClass.SELECTIVE) if len(fund) >= 2 else (ScopeClass.BROAD,)
    )
    return tuple(
        CoverageCell(action=action, scope=scope)
        for action in COVERED_FAMILIES
        if action in executable_set
        for scope in scopes
    )


def occupied_cells(
    intents: Iterable[object], *, authorized: Iterable[str]
) -> dict[CoverageCell, CoverageStatus]:
    """Ячейки, закрытые самим специалистом, и чем именно.

    `intents` -- пары (intent, status): предпочтённая гипотеза и рассмотренная
    альтернатива закрывают ячейку одинаково хорошо, различается только статус.
    """
    filled: dict[CoverageCell, CoverageStatus] = {}
    for intent, status in intents:  # type: ignore[misc]
        action = ProductionAction(intent.action)
        if action not in COVERED_FAMILIES:
            continue
        scope = scope_class(intent.target_wells, authorized)
        if scope is None:
            continue
        cell = CoverageCell(action=action, scope=scope)
        # PREFERRED сильнее: ячейка, закрытая ранжированной гипотезой, не
        # понижается до рассмотренной альтернативы другим намерением.
        if filled.get(cell) is CoverageStatus.PREFERRED:
            continue
        filled[cell] = CoverageStatus(status)
    return filled


def declined_cells(
    declarations: Mapping[str, str], *, required: Iterable[CoverageCell]
) -> dict[CoverageCell, CoverageStatus]:
    """Ячейки, закрытые явной структурной причиной отказа.

    Ключ объявления -- имя действия; причина распространяется на все его
    требуемые ячейки: если семейство структурно неприменимо на этом снимке,
    оно неприменимо в любом охвате.
    """
    resolved: dict[CoverageCell, CoverageStatus] = {}
    for cell in required:
        reason = parse_decline_reason(declarations.get(str(cell.action), ""))
        if reason is not None:
            resolved[cell] = CoverageStatus(reason)
    return resolved


def coverage_gaps(
    *,
    authorized: Iterable[str],
    executable: Iterable[ProductionAction],
    occupied: Mapping[CoverageCell, CoverageStatus],
    declined: Mapping[CoverageCell, CoverageStatus] | None = None,
) -> tuple[CoverageGap, ...]:
    """Требуемые и никак не закрытые ячейки, в детерминированном порядке.

    Порядок: сначала полностью отсутствующее семейство, затем BROAD раньше
    SELECTIVE, затем имя действия как последний стабильный ключ. Имя действия
    НЕ даёт преимущества: слоты раздаются полными раундами, поэтому глубина
    ячейки от позиции внутри раунда не зависит.
    """
    cells = required_cells(authorized=authorized, executable=executable)
    closed = dict(declined or {})
    present_families = {cell.action for cell in occupied}
    gaps = [
        CoverageGap(cell=cell, family_absent=cell.action not in present_families)
        for cell in cells
        if cell not in occupied and cell not in closed
    ]
    gaps.sort(
        key=lambda gap: (
            not gap.family_absent,
            gap.cell.scope is not ScopeClass.BROAD,
            str(gap.cell.action),
        )
    )
    return tuple(gaps)


def declaration_set_errors(
    declarations: Sequence[CoverageDeclaration], required: Sequence[CoverageCell]
) -> tuple[str, ...]:
    """Ровно одно заявление на каждую требуемую ячейку. Иначе -- отказ.

    Пропуск, повтор и посторонняя ячейка закрываются одинаково жёстко: любая
    из трёх ситуаций означает, что множество отвеченных вопросов не совпадает
    с множеством заданных, и полнота ответа недоказуема.
    """
    required_keys = [cell.key() for cell in required]
    seen: dict[str, int] = {}
    for declaration in declarations:
        seen[declaration.cell.key()] = seen.get(declaration.cell.key(), 0) + 1
    errors: list[str] = []
    for key in required_keys:
        count = seen.get(key, 0)
        if count == 0:
            errors.append(f"missing:{key}")
        elif count > 1:
            errors.append(f"duplicate:{key}")
    for key in sorted(seen):
        if key not in required_keys:
            errors.append(f"unexpected:{key}")
    return tuple(errors)


__all__ = [
    "BROAD_SHARE",
    "COVERED_FAMILIES",
    "DECLINE_REASONS",
    "CoverageCell",
    "CoverageDeclaration",
    "CoverageDisposition",
    "CoverageError",
    "CoverageGap",
    "CoverageStatus",
    "ScopeClass",
    "coverage_gaps",
    "declaration_set_errors",
    "decline_explanation",
    "declined_cells",
    "is_all_authorized",
    "occupied_cells",
    "parse_decline_reason",
    "required_cells",
    "scope_class",
]
