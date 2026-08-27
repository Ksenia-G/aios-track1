"""Live cognitive provider: configuration, transport, fallback and authority.

A live model is an untrusted external component. These tests pin two things:
no failure of that component may take the closed loop down, and nothing it
returns may weaken the specialist's numerical-authority gates. No test makes a
network call — the transport is injected.
"""

from __future__ import annotations

import json

import pytest

from aios.mas.common import ProposalSource
from aios.mas.providers import (
    InjectionLiveProvider,
    LiveTextCompletionProvider,
    ProductionLiveProvider,
    ProviderConfig,
    ProviderConfigError,
    ProviderErrorKind,
    ProviderKind,
    ProviderTransportError,
    load_provider_config,
)
from aios.mas.providers.contracts import MAX_PROMPT_CHARS, MAX_RESPONSE_CHARS


def _config(**overrides: object) -> ProviderConfig:
    fields: dict[str, object] = {
        "kind": ProviderKind.ANTHROPIC,
        "model_id": "test-model",
        "base_url": "https://api.example.com",
        "_credential": "secret-value-never-printed",
    }
    fields.update(overrides)
    return ProviderConfig(**fields)  # type: ignore[arg-type]


class _ScriptedTransport:
    """Replays prepared HTTP outcomes; records what it was asked to send."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def post_json(self, url, *, headers, payload, timeout):
        self.calls.append(
            {"url": url, "headers": dict(headers), "payload": payload, "timeout": timeout}
        )
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _anthropic_body(text: str) -> bytes:
    return json.dumps(
        {"id": "req-1", "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 10}}
    ).encode()


# --- configuration ------------------------------------------------------------


def test_supported_provider_is_accepted() -> None:
    assert _config().provider_id == "anthropic"


def test_unsupported_provider_is_refused() -> None:
    with pytest.raises(ProviderConfigError, match="unsupported provider"):
        load_provider_config({"MAS_LLM_PROVIDER": "not-a-provider", "MAS_LLM_MODEL": "m"})


def test_empty_model_is_refused() -> None:
    with pytest.raises(ProviderConfigError, match="model_id"):
        _config(model_id="  ")


def test_unset_provider_yields_no_configuration() -> None:
    """Absence is absence, never a default that quietly calls something."""
    assert load_provider_config({}) is None
    assert load_provider_config({"MAS_LLM_PROVIDER": "anthropic"}) is None


def test_missing_credential_is_refused_at_use() -> None:
    config = _config(_credential="")
    with pytest.raises(ProviderConfigError, match="requires a credential"):
        config.credential()


def test_plain_http_endpoint_is_refused_unless_local() -> None:
    with pytest.raises(ProviderConfigError, match="https"):
        _config(base_url="http://api.example.com")
    assert _config(base_url="http://127.0.0.1:11434", _credential="").requires_credential is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 0.1),
        ("timeout_seconds", 10_000.0),
        ("max_tokens", 0),
        ("max_attempts", 9),
    ],
)
def test_out_of_range_configuration_is_refused(field: str, value: object) -> None:
    with pytest.raises(ProviderConfigError):
        _config(**{field: value})


# --- secret handling ----------------------------------------------------------


def test_configuration_never_serialises_the_credential() -> None:
    config = _config()
    body = json.dumps(config.to_json())
    assert "secret-value-never-printed" not in body
    assert "credential" not in body
    assert set(config.to_json()) == {
        "provider_id",
        "model_id",
        "base_url",
        "timeout_seconds",
        "temperature",
        "max_tokens",
        "max_attempts",
    }


def test_credential_is_absent_from_repr_and_str() -> None:
    config = _config()
    assert "secret-value-never-printed" not in repr(config)
    assert "secret-value-never-printed" not in str(config)


def test_transport_error_text_carries_no_credential() -> None:
    transport = _ScriptedTransport((401, b"{}"))
    provider = LiveTextCompletionProvider(config=_config(), transport=transport)
    with pytest.raises(ProviderTransportError) as excinfo:
        provider.complete("prompt")
    assert "secret-value-never-printed" not in str(excinfo.value)


def test_completion_provenance_carries_no_raw_text() -> None:
    transport = _ScriptedTransport((200, _anthropic_body('{"intents": []}')))
    completion = LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")
    provenance = completion.provenance()
    assert "intents" not in json.dumps(provenance)
    assert provenance["response_digest"]
    assert provenance["model_id"] == "test-model"


# --- transport ----------------------------------------------------------------


def test_successful_call_returns_normalised_completion() -> None:
    transport = _ScriptedTransport((200, _anthropic_body("hello")))
    completion = LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")
    assert completion.text == "hello"
    assert completion.provider_id == "anthropic"
    assert completion.attempts == 1
    assert completion.request_id == "req-1"
    assert completion.usage.input_tokens == 10


def test_credential_is_sent_as_a_header_not_in_the_payload() -> None:
    transport = _ScriptedTransport((200, _anthropic_body("hi")))
    LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")
    call = transport.calls[0]
    assert call["headers"]["x-api-key"] == "secret-value-never-printed"
    assert "secret-value-never-printed" not in json.dumps(call["payload"])


@pytest.mark.parametrize("status", [400, 401, 403])
def test_client_errors_are_not_retried(status: int) -> None:
    """Repeating a rejected request only spends budget."""
    transport = _ScriptedTransport((status, b"{}"), (200, _anthropic_body("late")))
    with pytest.raises(ProviderTransportError):
        LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_errors_are_retried_within_a_bound(status: int) -> None:
    transport = _ScriptedTransport((status, b"{}"), (200, _anthropic_body("recovered")))
    completion = LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")
    assert completion.text == "recovered"
    assert completion.attempts == 2


def test_retries_are_bounded_and_do_not_loop() -> None:
    transport = _ScriptedTransport((503, b"{}"), (503, b"{}"), (503, b"{}"))
    with pytest.raises(ProviderTransportError):
        LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")
    assert len(transport.calls) == 2  # max_attempts default


def test_timeout_is_classified_and_retryable() -> None:
    error = ProviderTransportError(ProviderErrorKind.TIMEOUT, "timed out")
    assert error.retryable
    transport = _ScriptedTransport(error, (200, _anthropic_body("ok")))
    completion = LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")
    assert completion.attempts == 2


def test_malformed_json_body_fails_closed() -> None:
    transport = _ScriptedTransport((200, b"not json at all"))
    with pytest.raises(ProviderTransportError, match="not valid JSON"):
        LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")


def test_missing_text_fails_closed() -> None:
    transport = _ScriptedTransport((200, json.dumps({"content": []}).encode()))
    with pytest.raises(ProviderTransportError, match="no content blocks"):
        LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")


def test_empty_text_fails_closed() -> None:
    transport = _ScriptedTransport((200, _anthropic_body("   ")))
    with pytest.raises(ProviderTransportError, match="no text"):
        LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")


def test_oversized_prompt_is_refused_before_the_network_call() -> None:
    transport = _ScriptedTransport((200, _anthropic_body("never reached")))
    with pytest.raises(ProviderTransportError, match="exceeds"):
        LiveTextCompletionProvider(config=_config(), transport=transport).complete(
            "x" * (MAX_PROMPT_CHARS + 1)
        )
    assert transport.calls == []


def test_oversized_response_is_refused_rather_than_truncated() -> None:
    """A truncated JSON body would look like a broken model, not a broken read."""
    transport = _ScriptedTransport((200, _anthropic_body("y" * (MAX_RESPONSE_CHARS + 10))))
    with pytest.raises(ProviderTransportError, match="exceeds|read limit"):
        LiveTextCompletionProvider(config=_config(), transport=transport).complete("p")


def test_openai_compatible_shape_is_supported() -> None:
    body = json.dumps(
        {"id": "c-1", "choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 3}}
    ).encode()
    transport = _ScriptedTransport((200, body))
    config = _config(kind=ProviderKind.OPENAI_COMPATIBLE, _credential="k")
    completion = LiveTextCompletionProvider(config=config, transport=transport).complete("p")
    assert completion.text == "ok"
    assert transport.calls[0]["headers"]["Authorization"].startswith("Bearer ")


# --- specialist adapters ------------------------------------------------------


def test_production_adapter_returns_a_provider_response() -> None:
    transport = _ScriptedTransport((200, _anthropic_body('{"intents": []}')))
    live = LiveTextCompletionProvider(config=_config(), transport=transport)
    response = ProductionLiveProvider(provider=live).complete("p")
    assert response.text == '{"intents": []}'
    assert response.model_id == "test-model"
    assert response.source is ProposalSource.LLM_LIVE
    assert response.latency_ms is not None


def test_injection_adapter_returns_plain_text_and_keeps_provenance() -> None:
    transport = _ScriptedTransport((200, _anthropic_body('{"intents": []}')))
    live = LiveTextCompletionProvider(config=_config(), transport=transport)
    adapter = InjectionLiveProvider(provider=live, model_id="test-model")
    assert adapter.complete("p") == '{"intents": []}'
    assert adapter.last_completion.model_id == "test-model"


# --- deterministic floor under a hostile provider ------------------------------


def _production_envelope():
    from tests.support.mas_team_fixtures import synthetic_production_envelope

    return synthetic_production_envelope()


@pytest.mark.parametrize(
    "outcome",
    [
        ProviderTransportError(ProviderErrorKind.TIMEOUT, "timed out"),
        ProviderTransportError(ProviderErrorKind.CONNECTION, "connection failed"),
        ProviderTransportError(ProviderErrorKind.AUTH, "HTTP 401"),
        ProviderTransportError(ProviderErrorKind.SERVER_ERROR, "HTTP 500"),
        RuntimeError("unexpected transport explosion"),
    ],
)
def test_provider_failure_reaches_the_deterministic_floor(outcome: Exception) -> None:
    """No provider failure may take the closed loop down."""
    from aios.mas.specialists.production import ProductionController

    class FailingProvider:
        def complete(self, prompt: str):
            raise outcome

    output = ProductionController(
        llm_enabled=True, provider=ProductionLiveProvider(provider=FailingProvider())
    ).propose(_production_envelope())
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE


def test_malformed_model_output_reaches_the_deterministic_floor() -> None:
    from aios.mas.specialists.production import ProductionController

    transport = _ScriptedTransport(
        (200, _anthropic_body("I think you should increase production.")),
        (200, _anthropic_body("still not JSON")),
    )
    live = LiveTextCompletionProvider(config=_config(), transport=transport)
    output = ProductionController(
        llm_enabled=True, provider=ProductionLiveProvider(provider=live)
    ).propose(_production_envelope())
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK


# --- numerical authority is not weakened by a live provider --------------------


def _intent(**overrides: object) -> dict[str, object]:
    intent: dict[str, object] = {
        "action": "INCREASE_OFFTAKE",
        "direction": "UP",
        "intensity": "MODERATE",
        "target_wells": ["SYN-P1"],
        "target_regions": [],
        "evidence_ids": ["SYN-pressure-1"],
        "uncertainty": "MEDIUM",
        "rationale_short": "Pressure evidence supports a structural test.",
    }
    intent.update(overrides)
    return intent


def _controller_with(text: str):
    from aios.mas.specialists.production import ProductionController

    transport = _ScriptedTransport((200, _anthropic_body(text)), (200, _anthropic_body(text)))
    live = LiveTextCompletionProvider(config=_config(), transport=transport)
    return ProductionController(
        llm_enabled=True, provider=ProductionLiveProvider(provider=live)
    ).propose(_production_envelope())


def test_a_live_model_cannot_smuggle_a_setpoint_through_rationale() -> None:
    payload = json.dumps({"intents": [_intent(rationale_short="Set injection to 240 m3/day.")]})
    output = _controller_with(payload)
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK


def test_a_live_model_cannot_add_a_numeric_control_field() -> None:
    intent = _intent()
    intent["rate"] = 240
    output = _controller_with(json.dumps({"intents": [intent]}))
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK


def test_a_live_model_producing_valid_qualitative_output_is_accepted() -> None:
    """The gate refuses setpoints, not cognition."""
    output = _controller_with(json.dumps({"intents": [_intent()]}))
    assert output.primary_proposal.source is ProposalSource.LLM_LIVE
    intent = output.primary_proposal.intents[0]
    assert str(intent.action) == "INCREASE_OFFTAKE"
    assert "liquid_rate" not in json.dumps(intent.to_json())
