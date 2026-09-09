"""Strict, non-repairing parser for untrusted Production LLM output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aios.mas.specialists.authority import contains_numeric_magnitude

from .errors import StrictOutputError

#: Весь допустимый верхний уровень. Из него же выводится схема
#: структурированного вывода (`aios.mas.providers.schemas`), поэтому вторая
#: рукописная копия формы ответа не заводится.
_TOP_LEVEL_KEYS = frozenset(
    {
        "intents",
        "hypotheses",
        "considered_alternatives",
        "coverage_declarations",
        "considered_actions",
    }
)
#: Единственная форма ответа до пучка. Остаётся принимаемой: провайдер,
#: который вернул одну гипотезу старым способом, не обязан считаться сломанным.
_LEGACY_TOP_LEVEL_KEYS = {"intents"}
#: Форма пучка, симметричная Injection: ранжированные гипотезы одного ответа
#: плюс отчёт о рассмотренных, но отклонённых действиях.
_BEAM_TOP_LEVEL_KEYS = {"hypotheses", "considered_actions"}
#: Форма пучка с явным разделением предпочтения и рассмотрения. Отдельный
#: top-level ключ, а не флаг внутри hypotheses: рассмотренная альтернатива не
#: участвует в ранжировании предпочтений и не должна там оказаться случайно.
_COVERAGE_TOP_LEVEL_KEYS = {"hypotheses", "considered_alternatives", "considered_actions"}
#: Форма с явным слоем полноты: на каждый заданный структурный вопрос -- ровно
#: один ответ. Пропущенная ячейка перестаёт быть молчанием и становится
#: обнаружимым отсутствием.
_DECLARED_TOP_LEVEL_KEYS = {
    "hypotheses",
    "considered_alternatives",
    "coverage_declarations",
    "considered_actions",
}
_DECLARATION_KEYS = {"action", "scope_class", "disposition", "decline_reason", "explanation"}
_CONSIDERED_KEYS = {"action", "supported", "reason"}
_INTENT_KEYS = {
    "action",
    "direction",
    "intensity",
    "target_wells",
    "target_regions",
    "evidence_ids",
    "uncertainty",
    "rationale_short",
}


@dataclass(frozen=True, slots=True)
class ConsideredAction:
    """Действие, которое модель рассмотрела и явно оценила."""

    action: str
    supported: bool
    reason: str

    def label(self) -> str:
        """Короткая auditable метка для ProposalAudit.declined_alternatives."""
        return f"{self.action}: {self.reason}"[:240]


@dataclass(frozen=True, slots=True)
class ParsedCoverageDeclaration:
    """Сырое заявление о покрытии одной ячейки, до сверки со структурой."""

    action: str
    scope_class: str
    disposition: str
    decline_reason: str
    explanation: str


@dataclass(frozen=True, slots=True)
class ProductionBeam:
    """Ранжированные структурные гипотезы ОДНОГО ответа провайдера."""

    hypotheses: tuple[tuple[ParsedProductionIntent, ...], ...]
    considered: tuple[ConsideredAction, ...] = ()
    considered_alternatives: tuple[tuple[ParsedProductionIntent, ...], ...] = ()
    coverage_declarations: tuple[ParsedCoverageDeclaration, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedProductionIntent:
    action: str
    direction: str
    intensity: str
    target_wells: tuple[str, ...]
    target_regions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    uncertainty: str
    rationale_short: str


@dataclass(frozen=True, slots=True)
class StrictOutputParser:
    """Accept exactly one JSON object and never salvage partial intents."""

    def parse(self, response_text: str) -> tuple[ParsedProductionIntent, ...]:
        """Первая (лучшая) гипотеза. Прежние вызывающие не меняются."""
        return self.parse_beam(response_text).hypotheses[0]

    def parse_beam(self, response_text: str) -> ProductionBeam:
        try:
            payload = json.loads(
                response_text,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
        except StrictOutputError:
            raise
        except (TypeError, json.JSONDecodeError) as exc:
            raise StrictOutputError("response must be one valid JSON object") from exc
        if not isinstance(payload, dict):
            raise StrictOutputError("response root must be an object")
        keys = set(payload)
        if keys in (
            _DECLARED_TOP_LEVEL_KEYS,
            _COVERAGE_TOP_LEVEL_KEYS,
            _BEAM_TOP_LEVEL_KEYS,
        ):
            return _parse_beam_payload(payload)
        if keys == _LEGACY_TOP_LEVEL_KEYS:
            return ProductionBeam(hypotheses=(_parse_intents(payload["intents"], "intents"),))
        raise StrictOutputError(
            "response root must contain either 'intents' or 'hypotheses' and 'considered_actions'"
        )


def _parse_beam_payload(payload: dict[str, Any]) -> ProductionBeam:
    """Разобрать пучок. Негодная гипотеза выбывает поодиночке.

    Одна нераспознанная альтернатива не имеет права уносить с собой годные --
    ровно ради этого пучок и вводится. Пустой результат остаётся отказом.
    """
    raw_beam = payload["hypotheses"]
    if not isinstance(raw_beam, list) or not raw_beam:
        raise StrictOutputError("hypotheses must be a non-empty list")
    parsed_beam: list[tuple[ParsedProductionIntent, ...]] = []
    first_error: StrictOutputError | None = None
    for rank, item in enumerate(raw_beam):
        where = f"hypotheses[{rank}]"
        try:
            if not isinstance(item, dict) or set(item) != {"intents"}:
                raise StrictOutputError(f"{where} must be an object with only 'intents'")
            parsed_beam.append(_parse_intents(item["intents"], f"{where}.intents"))
        except StrictOutputError as exc:
            first_error = first_error or exc
    if not parsed_beam:
        raise first_error or StrictOutputError("no hypothesis in the beam is parsable")
    return ProductionBeam(
        hypotheses=tuple(parsed_beam),
        considered=_parse_considered(payload["considered_actions"]),
        considered_alternatives=_parse_hypothesis_list(
            payload.get("considered_alternatives", []), "considered_alternatives"
        ),
        coverage_declarations=_parse_declarations(payload.get("coverage_declarations", [])),
    )


def _parse_declarations(raw: Any) -> tuple[ParsedCoverageDeclaration, ...]:
    """Заявления о покрытии. Здесь только форма; смысл сверяет валидатор."""
    if not isinstance(raw, list):
        raise StrictOutputError("coverage_declarations must be a list")
    parsed: list[ParsedCoverageDeclaration] = []
    for index, item in enumerate(raw):
        where = f"coverage_declarations[{index}]"
        if not isinstance(item, dict) or set(item) != _DECLARATION_KEYS:
            raise StrictOutputError(f"{where} has missing or unknown fields")
        parsed.append(
            ParsedCoverageDeclaration(
                action=_required_string(item["action"], f"{where}.action"),
                scope_class=_required_string(item["scope_class"], f"{where}.scope_class"),
                disposition=_required_string(item["disposition"], f"{where}.disposition"),
                decline_reason=str(item["decline_reason"]),
                explanation=str(item["explanation"]),
            )
        )
    return tuple(parsed)


def _parse_hypothesis_list(raw: Any, prefix: str) -> tuple[tuple[ParsedProductionIntent, ...], ...]:
    """Разобрать список структурных альтернатив; негодная выбывает поодиночке."""
    if not isinstance(raw, list):
        raise StrictOutputError(f"{prefix} must be a list")
    parsed: list[tuple[ParsedProductionIntent, ...]] = []
    for index, item in enumerate(raw):
        where = f"{prefix}[{index}]"
        try:
            if not isinstance(item, dict) or set(item) != {"intents"}:
                raise StrictOutputError(f"{where} must be an object with only 'intents'")
            parsed.append(_parse_intents(item["intents"], f"{where}.intents"))
        except StrictOutputError:
            continue
    return tuple(parsed)


def _parse_considered(raw: Any) -> tuple[ConsideredAction, ...]:
    if not isinstance(raw, list):
        raise StrictOutputError("considered_actions must be a list")
    considered: list[ConsideredAction] = []
    for index, item in enumerate(raw):
        where = f"considered_actions[{index}]"
        if not isinstance(item, dict) or set(item) != _CONSIDERED_KEYS:
            raise StrictOutputError(f"{where} has missing or unknown fields")
        if not isinstance(item["supported"], bool):
            raise StrictOutputError(f"{where}.supported must be a boolean")
        considered.append(
            ConsideredAction(
                action=_required_string(item["action"], f"{where}.action"),
                supported=item["supported"],
                reason=_required_string(item["reason"], f"{where}.reason"),
            )
        )
    return tuple(considered)


def _parse_intents(raw_intents: Any, prefix: str) -> tuple[ParsedProductionIntent, ...]:
    if not isinstance(raw_intents, list):
        raise StrictOutputError(f"{prefix} must be a list")

    parsed: list[ParsedProductionIntent] = []
    for index, raw in enumerate(raw_intents):
        where = f"{prefix}[{index}]"
        if not isinstance(raw, dict):
            raise StrictOutputError(f"{where} must be an object")
        if set(raw) != _INTENT_KEYS:
            raise StrictOutputError(f"{where} has missing or unknown fields")
        rationale = _required_string(raw["rationale_short"], f"{where}.rationale_short")
        if raw["action"] == "HOLD" and rationale != "":
            raise StrictOutputError(f"{where}.HOLD rationale_short must be empty")
        if contains_numeric_magnitude(rationale):
            raise StrictOutputError(
                f"{where}.rationale_short must cite numerical observations by evidence_id"
            )
        parsed.append(
            ParsedProductionIntent(
                action=_required_string(raw["action"], f"{where}.action"),
                direction=_required_string(raw["direction"], f"{where}.direction"),
                intensity=_required_string(raw["intensity"], f"{where}.intensity"),
                target_wells=_string_tuple(raw["target_wells"], f"{where}.target_wells"),
                target_regions=_string_tuple(raw["target_regions"], f"{where}.target_regions"),
                evidence_ids=_string_tuple(raw["evidence_ids"], f"{where}.evidence_ids"),
                uncertainty=_required_string(raw["uncertainty"], f"{where}.uncertainty"),
                rationale_short=rationale,
            )
        )
    return tuple(parsed)


def _required_string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise StrictOutputError(f"{where} must be a string")
    if where.endswith("rationale_short"):
        return value
    if not value.strip():
        raise StrictOutputError(f"{where} must be non-empty")
    return value


def _string_tuple(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise StrictOutputError(f"{where} must be a list of strings")
    result = tuple(_required_string(item, f"{where}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        raise StrictOutputError(f"{where} must not contain duplicates")
    return result


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictOutputError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise StrictOutputError(f"non-finite JSON constant {value!r} is forbidden")
