"""MAS-INJECTION-INTENT-CONTRACT-001: схема и домен обязаны совпадать.

Провайдер, соблюдающий объявленную структурную схему, не имеет права
получить в ответ `intents[0] is not a valid InjectionIntent`. Такой отказ --
это не защита, а потерянный вызов: retry, а затем DETERMINISTIC_FALLBACK,
хотя модель сделала ровно то, о чём её попросили.

Контракт здесь односторонний и намеренно такой:

    schema_accepts(payload)  =>  InjectionIntent собирается

Обратное неверно и не проверяется: домен вправе принимать больше, чем
предлагает схема. Ослабление домена ради схемы -- не исправление.

Остаток, который статическая схема выразить не может, перечислен явно в
`RESIDUAL_DOMAIN_ONLY_CASES`: пока он перечислен, он не расползается.
"""

from __future__ import annotations

import json

import pytest

from aios.mas.common import Direction, Intensity
from aios.mas.intents import InjectionAction
from aios.mas.providers import injection_response_schema
from aios.mas.specialists.injection.parser import ParseError, StrictOutputParser
from tests.support.json_schema import (
    SUPPORTED_SCHEMA_KEYWORDS,
    schema_accepts,
    schema_keywords,
)

_LIST_FIELDS = (
    "donor_wells",
    "recipient_wells",
    "source_regions",
    "target_regions",
    "evidence_ids",
)


def _intent(**overrides: object) -> dict[str, object]:
    """Минимальный HOLD-intent; overrides делают из него любой другой."""
    item: dict[str, object] = {
        "action": "HOLD",
        "direction": "NONE",
        "intensity": "NONE",
        "donor_wells": [],
        "recipient_wells": [],
        "source_regions": [],
        "target_regions": [],
        "evidence_ids": [],
        "uncertainty": "LOW",
        "rationale_short": "",
    }
    item.update(overrides)
    return item


def _beam(*intents: dict[str, object]) -> dict[str, object]:
    return {"hypotheses": [{"intents": list(intents)}], "considered_actions": []}


def _domain_accepts(payload: dict[str, object]) -> bool:
    try:
        StrictOutputParser().parse_beam(json.dumps(payload))
    except ParseError:
        return False
    return True


def _is_residual_domain_error(payload: dict[str, object]) -> bool:
    """Отказ, который может остаться только доменным."""
    message = _domain_error(payload)
    return any(reason in message for reason in RESIDUAL_DOMAIN_REASONS)


def _domain_error(payload: dict[str, object]) -> str:
    with pytest.raises(ParseError) as caught:
        StrictOutputParser().parse_beam(json.dumps(payload))
    cause = caught.value.__cause__
    return f"{caught.value}: {cause}"


# --- канонические валидные payload, по одному на каждое действие ------------

CANONICAL_PAYLOADS: dict[InjectionAction, dict[str, object]] = {
    InjectionAction.HOLD: _intent(action="HOLD"),
    InjectionAction.SCALE_TOTAL_INJECTION: _intent(
        action="SCALE_TOTAL_INJECTION",
        direction="UP",
        intensity="MODERATE",
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Voidage replacement is short across the pattern.",
    ),
    InjectionAction.SUPPORT_REGION: _intent(
        action="SUPPORT_REGION",
        direction="UP",
        intensity="SMALL",
        recipient_wells=["SYN-I1"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Pressure keeps drifting down around SYN-I1.",
    ),
    InjectionAction.REDUCE_SUPPORT_PATH: _intent(
        action="REDUCE_SUPPORT_PATH",
        direction="DOWN",
        intensity="SMALL",
        donor_wells=["SYN-I2"],
        source_regions=["BLOCK-B"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="The SYN-I2 path is cycling water without sweep.",
    ),
    InjectionAction.REALLOCATE_INJECTION: _intent(
        action="REALLOCATE_INJECTION",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-A"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support from the cycling path to the drained flank.",
    ),
    InjectionAction.REBALANCE_ACROSS_BLOCKS: _intent(
        action="REBALANCE_ACROSS_BLOCKS",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-B"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Block B is over-supported while block A keeps falling.",
    ),
}


# --- combinations that the domain refuses and the schema must refuse too ----

STATICALLY_INVALID_CASES: dict[str, dict[str, object]] = {
    # I1: the payload that produced the reported fallback.
    "reallocate_without_donors": _intent(
        action="REALLOCATE_INJECTION",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=[],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-A"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support to the drained flank.",
    ),
    "reallocate_without_recipients": _intent(
        action="REALLOCATE_INJECTION",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=[],
        source_regions=["BLOCK-A"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support off the cycling path.",
    ),
    "reallocate_without_regions": _intent(
        action="REALLOCATE_INJECTION",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=[],
        target_regions=[],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support to the drained flank.",
    ),
    "reallocate_with_a_direction": _intent(
        action="REALLOCATE_INJECTION",
        direction="UP",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-A"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support to the drained flank.",
    ),
    "reallocate_without_intensity": _intent(
        action="REALLOCATE_INJECTION",
        direction="NONE",
        intensity="NONE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-A"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support to the drained flank.",
    ),
    "rebalance_without_donors": _intent(
        action="REBALANCE_ACROSS_BLOCKS",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=[],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-B"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Block A keeps falling.",
    ),
    "rebalance_with_a_direction": _intent(
        action="REBALANCE_ACROSS_BLOCKS",
        direction="DOWN",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-B"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Block A keeps falling.",
    ),
    "hold_with_a_direction": _intent(action="HOLD", direction="UP"),
    "hold_with_an_intensity": _intent(action="HOLD", intensity="SMALL"),
    "hold_with_donor_wells": _intent(action="HOLD", donor_wells=["SYN-I2"]),
    "hold_with_recipient_wells": _intent(action="HOLD", recipient_wells=["SYN-I1"]),
    "hold_with_source_regions": _intent(action="HOLD", source_regions=["BLOCK-A"]),
    "hold_with_target_regions": _intent(action="HOLD", target_regions=["BLOCK-A"]),
    "scale_total_with_donor_wells": _intent(
        action="SCALE_TOTAL_INJECTION",
        direction="UP",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Voidage replacement is short.",
    ),
    "scale_total_with_recipient_wells": _intent(
        action="SCALE_TOTAL_INJECTION",
        direction="UP",
        intensity="MODERATE",
        recipient_wells=["SYN-I1"],
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Voidage replacement is short.",
    ),
    "scale_total_without_a_direction": _intent(
        action="SCALE_TOTAL_INJECTION",
        direction="NONE",
        intensity="MODERATE",
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Voidage replacement is short.",
    ),
    "scale_total_without_intensity": _intent(
        action="SCALE_TOTAL_INJECTION",
        direction="UP",
        intensity="NONE",
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Voidage replacement is short.",
    ),
    "support_region_pointing_down": _intent(
        action="SUPPORT_REGION",
        direction="DOWN",
        intensity="SMALL",
        recipient_wells=["SYN-I1"],
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Pressure keeps drifting down.",
    ),
    "support_region_without_a_target": _intent(
        action="SUPPORT_REGION",
        direction="UP",
        intensity="SMALL",
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Pressure keeps drifting down.",
    ),
    "support_region_without_intensity": _intent(
        action="SUPPORT_REGION",
        direction="UP",
        intensity="NONE",
        recipient_wells=["SYN-I1"],
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Pressure keeps drifting down.",
    ),
    "reduce_support_path_pointing_up": _intent(
        action="REDUCE_SUPPORT_PATH",
        direction="UP",
        intensity="SMALL",
        donor_wells=["SYN-I2"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="The path is cycling water.",
    ),
    "reduce_support_path_without_a_source": _intent(
        action="REDUCE_SUPPORT_PATH",
        direction="DOWN",
        intensity="SMALL",
        evidence_ids=["EV-SWEEP"],
        rationale_short="The path is cycling water.",
    ),
    "reduce_support_path_without_intensity": _intent(
        action="REDUCE_SUPPORT_PATH",
        direction="DOWN",
        intensity="NONE",
        donor_wells=["SYN-I2"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="The path is cycling water.",
    ),
    "non_hold_without_evidence": _intent(
        action="SUPPORT_REGION",
        direction="UP",
        intensity="SMALL",
        recipient_wells=["SYN-I1"],
        evidence_ids=[],
        rationale_short="Pressure keeps drifting down.",
    ),
}


#: То, что статическая схема выразить не может, и потому остаётся доменной
#: проверкой. Список исчерпывающий и проверяется тестом: молча пополнить его
#: нельзя.
#:
#: * равенство/различие двух наборов регионов -- сравнение двух полей между
#:   собой, для JSON Schema невыразимое;
#: * длина и число предложений rationale_short -- текстовое качество; strict
#:   structured output не исполняет minLength/maxLength.
#: Отказы домена, которые статическая схема выразить не может. Проверка
#: мутациями сверяется с этим списком: всё, что в него не попало, обязано быть
#: отвергнуто схемой, а не доменом.
RESIDUAL_DOMAIN_REASONS = (
    "must be the same scope",
    "must differ",
    "rationale_short",
)

RESIDUAL_DOMAIN_ONLY_CASES: dict[str, dict[str, object]] = {
    "reallocate_across_two_scopes": _intent(
        action="REALLOCATE_INJECTION",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-B"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support to the drained flank.",
    ),
    "rebalance_within_one_scope": _intent(
        action="REBALANCE_ACROSS_BLOCKS",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=["SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-A"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Block A is unevenly supported.",
    ),
    "non_hold_without_a_rationale": _intent(
        action="SUPPORT_REGION",
        direction="UP",
        intensity="SMALL",
        recipient_wells=["SYN-I1"],
        evidence_ids=["EV-PRESSURE"],
        rationale_short="",
    ),
    "rationale_longer_than_two_sentences": _intent(
        action="SUPPORT_REGION",
        direction="UP",
        intensity="SMALL",
        recipient_wells=["SYN-I1"],
        evidence_ids=["EV-PRESSURE"],
        rationale_short="Pressure falls. Sweep is poor. The flank is drained.",
    ),
}


# --- I2, I3, I10, I11, I12: canonical payloads clear every stage ------------


@pytest.mark.parametrize("action", list(InjectionAction), ids=lambda a: str(a))
def test_every_action_has_a_canonical_payload_the_whole_chain_accepts(
    action: InjectionAction,
) -> None:
    """I12: у каждого действия есть путь schema -> parser -> InjectionIntent."""
    payload = _beam(CANONICAL_PAYLOADS[action])
    assert schema_accepts(payload, injection_response_schema()), (
        f"{action}: the advertised schema rejects its own canonical payload"
    )
    beam = StrictOutputParser().parse_beam(json.dumps(payload))
    assert beam.hypotheses[0][0].action is action


def test_the_canonical_table_covers_every_supported_action() -> None:
    """I12: новое действие обязано принести с собой канонический payload."""
    assert set(CANONICAL_PAYLOADS) == set(InjectionAction)


# --- I1, I4..I9, I14: schema-valid/domain-invalid must not exist ------------


@pytest.mark.parametrize("case", sorted(STATICALLY_INVALID_CASES))
def test_statically_invalid_combinations_are_refused_by_the_schema(case: str) -> None:
    """I1/I4..I9/I14: то, что домен отвергает статически, схема не предлагает."""
    payload = _beam(STATICALLY_INVALID_CASES[case])
    assert not _domain_accepts(payload), f"{case}: expected the domain to refuse this payload"
    assert not schema_accepts(payload, injection_response_schema()), (
        f"{case}: the schema accepts a payload the domain refuses -- "
        f"this is an avoidable fallback ({_domain_error(payload)})"
    )


# --- I13: the contract invariant itself -------------------------------------


def test_schema_acceptance_implies_a_constructible_intent() -> None:
    """I13: единственный настоящий инвариант этого milestone.

    Перебор ведётся по всем таблицам сразу: канонические, статически
    невалидные и остаточные. Ни один payload, принятый схемой, не имеет права
    упасть на построении InjectionIntent, кроме явно перечисленного остатка.
    """
    schema = injection_response_schema()
    residual = set(RESIDUAL_DOMAIN_ONLY_CASES)
    offenders: list[str] = []
    for name, intent in (
        {f"canonical:{k}": v for k, v in CANONICAL_PAYLOADS.items()}
        | {f"invalid:{k}": v for k, v in STATICALLY_INVALID_CASES.items()}
        | {f"residual:{k}": v for k, v in RESIDUAL_DOMAIN_ONLY_CASES.items()}
    ).items():
        payload = _beam(intent)
        if name.removeprefix("residual:") in residual:
            continue
        if schema_accepts(payload, schema) and not _domain_accepts(payload):
            offenders.append(f"{name}: {_domain_error(payload)}")
    assert not offenders, "schema accepts payloads the domain refuses:\n" + "\n".join(offenders)


def test_the_residual_domain_only_cases_are_exactly_the_declared_ones() -> None:
    """Остаток задокументирован целиком: молча вырасти он не может."""
    schema = injection_response_schema()
    for name, intent in RESIDUAL_DOMAIN_ONLY_CASES.items():
        payload = _beam(intent)
        assert schema_accepts(payload, schema), (
            f"{name}: declared as a residual domain check, but the schema already refuses it -- "
            "move it out of RESIDUAL_DOMAIN_ONLY_CASES"
        )
        assert not _domain_accepts(payload), (
            f"{name}: declared as a residual domain check, but the domain accepts it"
        )
        assert _is_residual_domain_error(payload), (
            f"{name}: refused for {_domain_error(payload)!r}, which is not one of the "
            f"declared residual reasons {RESIDUAL_DOMAIN_REASONS}"
        )


# --- I7, I8: direction and intensity are per-action, not global -------------


@pytest.mark.parametrize("action", list(InjectionAction), ids=lambda a: str(a))
def test_every_illegal_direction_is_refused_by_the_schema(action: InjectionAction) -> None:
    """I7: направление, несовместимое с действием, не проходит схему."""
    schema = injection_response_schema()
    canonical = CANONICAL_PAYLOADS[action]
    for direction in Direction:
        candidate = dict(canonical, direction=str(direction))
        payload = _beam(candidate)
        if _domain_accepts(payload) or _is_residual_domain_error(payload):
            continue
        assert not schema_accepts(payload, schema), (
            f"{action} with direction {direction}: schema accepts, domain refuses"
        )


@pytest.mark.parametrize("action", list(InjectionAction), ids=lambda a: str(a))
def test_every_illegal_intensity_is_refused_by_the_schema(action: InjectionAction) -> None:
    """I8: интенсивность, несовместимая с действием, не проходит схему."""
    schema = injection_response_schema()
    canonical = CANONICAL_PAYLOADS[action]
    for intensity in Intensity:
        candidate = dict(canonical, intensity=str(intensity))
        payload = _beam(candidate)
        if _domain_accepts(payload) or _is_residual_domain_error(payload):
            continue
        assert not schema_accepts(payload, schema), (
            f"{action} with intensity {intensity}: schema accepts, domain refuses"
        )


# --- I9: fields an action forbids are refused, field by field ---------------


@pytest.mark.parametrize("action", list(InjectionAction), ids=lambda a: str(a))
def test_a_forbidden_structural_field_is_refused_by_the_schema(action: InjectionAction) -> None:
    """I9: непустой список там, где действие требует пустого."""
    schema = injection_response_schema()
    canonical = CANONICAL_PAYLOADS[action]
    for field in _LIST_FIELDS:
        candidate = dict(canonical, **{field: ["SYN-I9"] if "wells" in field else ["BLOCK-Z"]})
        payload = _beam(candidate)
        if _domain_accepts(payload) or _is_residual_domain_error(payload):
            continue
        assert not schema_accepts(payload, schema), (
            f"{action} with a non-empty {field}: schema accepts, domain refuses"
        )


@pytest.mark.parametrize("action", list(InjectionAction), ids=lambda a: str(a))
def test_an_emptied_required_field_is_refused_by_the_schema(action: InjectionAction) -> None:
    """I4/I5: пустой список там, где действие требует непустого."""
    schema = injection_response_schema()
    canonical = CANONICAL_PAYLOADS[action]
    for field in _LIST_FIELDS:
        candidate = dict(canonical, **{field: []})
        payload = _beam(candidate)
        if _domain_accepts(payload) or _is_residual_domain_error(payload):
            continue
        assert not schema_accepts(payload, schema), (
            f"{action} with an empty {field}: schema accepts, domain refuses"
        )


# --- I6: donor/recipient overlap is NOT a domain invariant today ------------


def test_donor_recipient_overlap_stays_a_downstream_concern() -> None:
    """I6: пересечение донор/получатель домен НЕ запрещает.

    `magnitude_resolution.reallocation` снимает пересечение при разрешении
    величины. Запрещать его в схеме значило бы делать схему СТРОЖЕ домена --
    это другая ошибка того же рода: провайдер получил бы отказ на комбинации,
    которую система на самом деле принимает.
    """
    overlapping = _intent(
        action="REALLOCATE_INJECTION",
        direction="NONE",
        intensity="MODERATE",
        donor_wells=["SYN-I1", "SYN-I2"],
        recipient_wells=["SYN-I1"],
        source_regions=["BLOCK-A"],
        target_regions=["BLOCK-A"],
        evidence_ids=["EV-SWEEP"],
        rationale_short="Move support toward the drained flank.",
    )
    payload = _beam(overlapping)
    assert _domain_accepts(payload)
    assert schema_accepts(payload, injection_response_schema())


# --- the schema must stay inside what the transport actually enforces -------


def test_the_schema_stays_inside_the_supported_strict_subset() -> None:
    """Схема, которую strict transport не исполняет, строга только у нас."""
    used = schema_keywords(injection_response_schema())
    assert used <= SUPPORTED_SCHEMA_KEYWORDS, sorted(used - SUPPORTED_SCHEMA_KEYWORDS)


# --- I13 (exhaustive): the invariant over the whole static combination space -


def test_no_static_combination_is_schema_valid_and_domain_invalid() -> None:
    """Полный перебор статического пространства, а не подобранных случаев.

    Ручной список случаев доказывает только то, что в него положили. Здесь
    перебираются все сочетания action x direction x intensity x «какие
    списки непусты»: если схема хоть одно из них принимает, а домен
    отвергает, это ровно тот отказ, из-за которого специалист уходил в
    DETERMINISTIC_FALLBACK на честном ответе провайдера.
    """
    import itertools

    schema = injection_response_schema()
    sample = {
        "donor_wells": ["SYN-I2"],
        "recipient_wells": ["SYN-I1"],
        "source_regions": ["BLOCK-A"],
        "target_regions": ["BLOCK-A"],
        "evidence_ids": ["EV-SWEEP"],
    }
    offenders: list[str] = []
    accepted = 0
    for action, direction, intensity, mask in itertools.product(
        InjectionAction,
        Direction,
        Intensity,
        itertools.product((0, 1), repeat=len(_LIST_FIELDS)),
    ):
        candidate = _intent(
            action=str(action),
            direction=str(direction),
            intensity=str(intensity),
            uncertainty="MEDIUM",
            rationale_short="Support is uneven across the pattern.",
            **{
                field: list(sample[field]) if flag else []
                for field, flag in zip(_LIST_FIELDS, mask, strict=True)
            },
        )
        payload = _beam(candidate)
        if not schema_accepts(payload, schema):
            continue
        accepted += 1
        if _domain_accepts(payload) or _is_residual_domain_error(payload):
            continue
        offenders.append(f"{action}/{direction}/{intensity}/{mask}: {_domain_error(payload)}")

    assert accepted, "the schema accepts nothing at all -- it cannot be the advertised contract"
    assert not offenders, "schema accepts payloads the domain refuses:\n" + "\n".join(
        offenders[:20]
    )


# --- Phase 4: the schema constrains generation, the parser keeps authority ---


def test_a_payload_the_schema_refuses_is_still_refused_by_the_parser() -> None:
    """Схема -- не разрешение доверять ответу.

    Согласованная схема убирает ИЗБЕЖНЫЙ fallback, а не проверку. Payload,
    дошедший до парсера в обход схемы, обязан быть отвергнут ровно так же.
    """
    for name, intent in STATICALLY_INVALID_CASES.items():
        with pytest.raises(ParseError):
            StrictOutputParser().parse_beam(json.dumps(_beam(intent)))
        assert not schema_accepts(_beam(intent), injection_response_schema()), name


def test_the_parser_never_coerces_an_invalid_intent_into_a_valid_one() -> None:
    """Ни подмены действия, ни выдуманных доноров, ни численных setpoints."""
    payload = _beam(STATICALLY_INVALID_CASES["reallocate_without_donors"])
    with pytest.raises(ParseError) as caught:
        StrictOutputParser().parse_beam(json.dumps(payload))
    assert "intents[0] is not a valid InjectionIntent" in str(caught.value)


def test_the_discriminated_schema_opens_no_numerical_channel() -> None:
    """NO SETPOINTS CROSS: новые ветви не приносят ни одного поля величины."""
    from aios.mas.providers import FORBIDDEN_SCHEMA_FIELDS, schema_field_names

    assert not (schema_field_names(injection_response_schema()) & FORBIDDEN_SCHEMA_FIELDS)


# --- the same contract, all the way down the live structured-output path -----


class _CapturingTransport:
    """Записывает исходящий payload и отдаёт заранее подготовленный ответ."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.calls: list[dict[str, object]] = []

    def post_json(self, url, *, headers, payload, timeout):
        self.calls.append(payload)
        document = {
            "id": "c-1",
            "choices": [{"message": {"content": self.body}}],
            "usage": {"prompt_tokens": 7},
        }
        return 200, json.dumps(document).encode()


def test_a_schema_conforming_answer_reaches_llm_live_without_a_retry() -> None:
    """Контракт целиком: объявленная схема -> ответ -> InjectionIntent.

    Именно этот путь и ломался: провайдер отвечал по схеме, домен отказывал,
    специалист повторял вызов и уходил в DETERMINISTIC_FALLBACK. Здесь
    проверяется, что ровно один вызов даёт LLM_LIVE и fallback_used=False.
    """
    from aios.mas.common import ProposalSource
    from aios.mas.providers.adapters import InjectionLiveProvider
    from aios.mas.providers.config import ProviderConfig, ProviderKind
    from aios.mas.providers.transport import LiveTextCompletionProvider
    from aios.mas.specialists.injection.controller import InjectionController
    from tests.support.mas_team_fixtures import synthetic_injection_envelope

    answer = {
        "hypotheses": [
            {
                "intents": [
                    _intent(
                        action="SUPPORT_REGION",
                        direction="UP",
                        intensity="SMALL",
                        recipient_wells=["SYN-I1"],
                        target_regions=["SYN-B1"],
                        evidence_ids=["SYN-pressure-1"],
                        uncertainty="MEDIUM",
                        rationale_short="Pressure support around SYN-I1 is thin.",
                    )
                ]
            },
            {"intents": [_intent(action="HOLD")]},
        ],
        "considered_actions": [
            {
                "action": "REALLOCATE_INJECTION",
                "supported": False,
                "reason": "only one injector is on the current StateCard",
            }
        ],
    }
    assert schema_accepts(answer, injection_response_schema())

    transport = _CapturingTransport(json.dumps(answer))
    config = ProviderConfig(
        kind=ProviderKind.OPENAI_COMPATIBLE,
        model_id="structured-model",
        base_url="https://api.openai.example.com",
        structured_output=True,
        reasoning_effort="low",
        _credential="secret-value-never-printed",
    )
    provider = InjectionLiveProvider(
        provider=LiveTextCompletionProvider(config=config, transport=transport),
        model_id="structured-model",
        structured=True,
    )
    output = InjectionController(
        llm_enabled=True, llm_provider=provider, llm_source=ProposalSource.LLM_LIVE
    ).propose(
        synthetic_injection_envelope(
            allowed_actions=(InjectionAction.HOLD, InjectionAction.SUPPORT_REGION)
        )
    )

    sent = transport.calls[0]["response_format"]["json_schema"]
    assert sent["strict"] is True
    assert sent["schema"] == injection_response_schema()
    assert len(transport.calls) == 1, "a conforming answer must not cost a retry"
    assert output.primary_proposal.source is ProposalSource.LLM_LIVE
    assert str(output.primary_proposal.intents[0].action) == "SUPPORT_REGION"
    assert output.primary_proposal.provenance.fallback_used is False
    assert output.primary_proposal.provenance.fallback_reason is None
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
