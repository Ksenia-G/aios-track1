"""Shared NO-SETPOINTS-CROSS boundary for Production and Injection specialists.

Единственный источник правды по вопросу "является ли это prescriptive numerical
control authority". До этого модуля Production и Injection несли две разные,
расходящиеся реализации одной и той же границы: исправление в одной не
исправляло другую, а их строгость отличалась на том самом классе payload,
который и требовалось закрыть.

Граница проводится семантически, а не по списку разрешённых имён:

* ФАКТ разрешён. Наблюдательное число -- это факт: measured BHP, WLPR, WWIR,
  water cut, margin, confidence, тренд, current_target как снимок текущего
  состояния скважины. Открытый мир: незнакомый ключ диагностики -- это ещё не
  команда, иначе любая эволюция схемы diagnostics ломала бы специалиста.
* PRESCRIPTIVE CONTROL AUTHORITY запрещён. Запрещено имя поля, которое само по
  себе называет управление (producer_control, surface_rate, setpoint), имя с
  предписывающим квалификатором (recommended_*, target_rate, new_bhp),
  simulator-команда (WCONINJE/WCONPROD) и текст, несущий директиву вместе с
  величиной ("Set injection to 240 m3/day").

Numerical control appears first at MagnitudeResolver -- не здесь.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

__all__ = [
    "PrescriptiveAuthorityError",
    "contains_numeric_magnitude",
    "contains_prescriptive_text",
    "find_prescriptive_control",
    "is_prescriptive_key",
    "reject_prescriptive_payload",
    "reject_prescriptive_text",
]


class PrescriptiveAuthorityError(ValueError):
    """Значение несёт prescriptive numerical control до MagnitudeResolver."""


# Standalone numerical magnitude. Отрицательный lookbehind на [\w-] -- это и
# есть различие между "240" и цифрой внутри идентификатора: SYN-I1, I10, F3,
# BLOCK-12 не содержат standalone-величины, "to 240" -- содержит.
_NUMERIC_MAGNITUDE = re.compile(r"(?<![\w-])[-+]?(?:\d+(?:[.,]\d+)?|\.\d+)(?:[eE][-+]?\d+)?")

# Токены, называющие управление напрямую (Eclipse/schedule control authority).
_CONTROL_COMMAND_TOKEN = re.compile(
    r"(?i)(?<![\w-])(?:wconinje|wconprod|wconhist|welopen|wtarget|setpoints?)(?![\w-])"
)

# Имена полей, которые являются numerical control authority безусловно.
_CONTROL_AUTHORITY_KEYS = frozenset(
    {
        "bhp_limit",
        "bhp_setpoint",
        "bhp_target",
        "injector_control",
        "producer_control",
        "setpoint",
        "setpoints",
        "wconhist",
        "wconinje",
        "wconprod",
        "welopen",
    }
)

# Имена, двусмысленные по своей природе: `liquid_rate` -- это поле
# ProducerControl (setpoint) и одновременно измеренный дебит в
# FieldStateSnapshot.production (факт). Различает не имя, а раздел: под
# наблюдательной секцией это измерение, вне её -- управление.
_AMBIGUOUS_CONTROL_METRIC_KEYS = frozenset(
    {
        "grat",
        "injection_rate",
        "injector_rate",
        "liquid_rate",
        "lrat",
        "orat",
        "producer_rate",
        "rate",
        "surface_rate",
        "wrat",
    }
)

# Разделы authoritative StateCard/diagnostics, содержащие измерения.
_OBSERVATIONAL_SECTIONS = frozenset(
    {
        "compensation",
        "cumulative",
        "derived",
        "field",
        "injection",
        "measured",
        "observed",
        "pressure",
        "production",
        "realized",
        "state",
        "totals",
        "voidage",
        "water",
    }
)

# Квалификаторы, превращающие наблюдение в предписание, где бы ни стояли.
_PRESCRIPTIVE_QUALIFIER_TOKENS = frozenset(
    {
        "command",
        "commanded",
        "desired",
        "prescribed",
        "recommendation",
        "recommended",
        "setpoint",
        "setpoints",
    }
)

# Префикс + метрика: target_rate, new_bhp, set_wlpr, proposed_injection.
_PRESCRIPTIVE_PREFIXES = frozenset(
    {
        "apply",
        "assign",
        "command",
        "new",
        "prescribed",
        "proposed",
        "recommended",
        "set",
        "target",
    }
)
_CONTROL_METRIC_TOKENS = frozenset(
    {
        "bhp",
        "control",
        "injection",
        "lrat",
        "offtake",
        "orat",
        "rate",
        "setpoint",
        "surface",
        "thp",
        "wlpr",
        "wrat",
    }
)

# Предписывающие глаголы. "use"/"used" намеренно НЕ включены: "Operator used 12
# hours of downtime" -- наблюдение, а не команда, и раньше отклонялось ложно.
_PRESCRIPTIVE_VERB = (
    r"(?:appl(?:y|ied|ies|ying)|assign(?:ed|ing|s)?|command(?:ed|ing|s)?|"
    r"prescrib(?:e|ed|es|ing)|recommend(?:ed|ing|s)?|set(?:s|ting)?|target(?:ed|ing|s)?|"
    r"inject(?:ed|ing|s)?|increas(?:e|ed|es|ing)|decreas(?:e|ed|es|ing)|"
    r"reduc(?:e|ed|es|ing)|rais(?:e|ed|es|ing)|lower(?:ed|ing|s)?|"
    r"scal(?:e|ed|es|ing)|allocat(?:e|ed|es|ing)|reallocat(?:e|ed|es|ing)|"
    r"назнач\w*|примен\w*|рекоменд\w*|установ\w*)"
)

# Директива и величина рядом: не более трёх слов между ними. Это отделяет
# "Set production to 420" от "was set based on prior schedule, currently 0.8".
_PRESCRIPTIVE_PHRASE = re.compile(
    rf"(?i)(?<![\w-]){_PRESCRIPTIVE_VERB}(?![\w-])"
    rf"(?:\s+[\w%/-]+){{0,3}}?\s*(?:to|by|at|=|:|до|на)?\s*"
    rf"(?<![\w-])[-+]?(?:\d+(?:[.,]\d+)?|\.\d+)"
)

# Императив с величиной: "Use 420 m3/day for this well." Отдельное правило,
# потому что различает не глагол, а позиция: повелительное наклонение в начале
# предложения -- это команда, а прошедшее время в середине ("Operator used 12
# hours of downtime") -- наблюдение, которое обязано пройти.
_IMPERATIVE_MAGNITUDE = re.compile(
    r"(?im)(?:^|[.!?;]\s*)"
    r"(?:use|utilise|utilize|keep|hold|maintain|allocate|apply|set|target|inject)(?![\w-])"
    r"(?:\s+[\w%/-]+){0,3}?\s*(?:to|by|at|=|:)?\s*"
    r"(?<![\w-])[-+]?(?:\d+(?:[.,]\d+)?|\.\d+)"
)

# metric=240 / bhp: 300 -- присваивание величины управляющему имени.
_CONTROL_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w-])(?:bhp|lrat|orat|wrat|grat|rate|thp|wlpr|setpoint|"
    r"surface[_ -]?rate|liquid[_ -]?rate|injection[_ -]?rate)"
    r"\s*(?:=|:|\bto\b|\bдо\b)\s*[-+]?\d"
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-zа-я0-9])(?=[A-ZА-Я])")
_KEY_TOKEN_SPLIT = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)


def contains_numeric_magnitude(text: str) -> bool:
    """Есть ли в тексте standalone числовая величина.

    Цифры внутри идентификатора (SYN-I1, I10, F3, BLOCK-12) величиной не
    считаются: NO SETPOINTS CROSS, а не NO DIGITS CROSS.
    """
    return _NUMERIC_MAGNITUDE.search(text) is not None


def contains_prescriptive_text(text: str) -> bool:
    """Несёт ли свободный текст численную команду управления.

    Наблюдение с числом ("Average pressure is 211.5 bar") разрешено: запрещена
    именно директива с величиной или simulator-команда.
    """
    if _CONTROL_COMMAND_TOKEN.search(text) and contains_numeric_magnitude(text):
        return True
    if _CONTROL_ASSIGNMENT.search(text):
        return True
    if _IMPERATIVE_MAGNITUDE.search(text):
        return True
    return _PRESCRIPTIVE_PHRASE.search(text) is not None


def _normalize_key(key: object) -> str:
    return _CAMEL_BOUNDARY.sub("_", str(key).strip()).lower()


def is_prescriptive_key(key: object, *, section: str | None = None) -> bool:
    """Называет ли имя поля управление, а не наблюдение.

    `section` -- нормализованное имя родительского раздела. Двусмысленные
    метрики (`liquid_rate`) под наблюдательным разделом остаются фактом.
    """
    normalized = _normalize_key(key)
    if normalized in _CONTROL_AUTHORITY_KEYS:
        return True
    if normalized in _AMBIGUOUS_CONTROL_METRIC_KEYS and (
        section is None or section not in _OBSERVATIONAL_SECTIONS
    ):
        return True
    tokens = tuple(token for token in _KEY_TOKEN_SPLIT.split(normalized) if token)
    if not tokens:
        return False
    token_set = set(tokens)
    if token_set & _PRESCRIPTIVE_QUALIFIER_TOKENS:
        return True
    return tokens[0] in _PRESCRIPTIVE_PREFIXES and bool(token_set & _CONTROL_METRIC_TOKENS)


def _is_prescriptive_pairing(key: object, value: object) -> bool:
    """Предписывающее слово, которому присвоена величина.

    Различает не имя, а пару имя+значение. `target` в реальной диагностике --
    это ссылка на скважину или регион, и такой факт обязан проходить;
    `target` с величиной -- это setpoint, и проходить не должен:

        {"target": "SYN-I1"}       ссылка на скважину        -- факт
        {"target": ["SYN-I1"]}     ссылка на скважины        -- факт
        {"target": 240}            присвоенная величина      -- setpoint
        {"target": "240 m3/day"}   та же величина строкой    -- setpoint

    Величина, записанная строкой, ничем не безопаснее числа: канал прежде
    закрывался только для скаляров, и `"240 m3/day"` проходил насквозь.
    Различает не тип значения, а наличие в нём standalone-величины, поэтому
    идентификатор с цифрами (SYN-I1, SYN-B2, I10) остаётся фактом.
    """
    if _normalize_key(key) not in _PRESCRIPTIVE_PREFIXES:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if isinstance(value, str):
        return contains_numeric_magnitude(value)
    return False


def find_prescriptive_control(
    value: object, path: str = "payload", *, section: str | None = None
) -> str | None:
    """Первое место в payload, несущее prescriptive control, или None.

    Открытый мир: незнакомый ключ сам по себе нарушением не является -- иначе
    любое расширение схемы diagnostics ломало бы специалиста.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if is_prescriptive_key(key, section=section) or _is_prescriptive_pairing(key, item):
                return f"{path}.{key}"
            found = find_prescriptive_control(item, f"{path}.{key}", section=_normalize_key(key))
            if found is not None:
                return found
        return None
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, item in enumerate(value):
            found = find_prescriptive_control(item, f"{path}[{index}]", section=section)
            if found is not None:
                return found
        return None
    if isinstance(value, str) and contains_prescriptive_text(value):
        return path
    return None


def reject_prescriptive_payload(value: object, where: str, path: str = "payload") -> None:
    """Отклонить payload, несущий prescriptive numerical control authority."""
    found = find_prescriptive_control(value, path)
    if found is not None:
        raise PrescriptiveAuthorityError(
            f"{where}.{found}: prescriptive numerical control is forbidden before MagnitudeResolver"
        )


def reject_prescriptive_text(text: str, *, where: str = "rationale_short") -> None:
    """Граница для rationale специалиста.

    Rationale обязан ссылаться на числовые наблюдения через evidence_id, а не
    повторять величину: любая standalone-величина здесь запрещена. Идентификаторы
    с цифрами (SYN-I1, BLOCK-12) остаются разрешёнными.
    """
    if not text:
        return
    if contains_numeric_magnitude(text):
        raise PrescriptiveAuthorityError(f"{where} carries a standalone numerical magnitude")
    if _CONTROL_COMMAND_TOKEN.search(text):
        raise PrescriptiveAuthorityError(f"{where} carries a prescriptive control token")
