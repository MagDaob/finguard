from dataclasses import replace

import pytest
from pydantic import ValidationError

from finguard.backend.config import Settings
from finguard.backend.errors import ServiceError
from finguard.integrations.models.contracts import (
    ModelCapabilities,
    ModelConnection,
    ModelCredential,
    ModelEvent,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    ToolCall,
)
from finguard.integrations.models.legacy import legacy_model_connection
from finguard.integrations.models.registry import ModelProvider, ModelRegistry


def connection(**changes):
    return ModelConnection(
        **{
            "connection_ref": "model-1",
            "owner_ref": "owner",
            "provider": "custom-model",
            "protocol": "chat_completions",
            "base_url": "https://model.example/v1",
            "model_id": "my-model",
            "credential_ref": "secret:model-key",
            **changes,
        }
    )


def credential(**changes):
    return ModelCredential(
        **{
            "owner_ref": "owner",
            "credential_ref": "secret:model-key",
            "base_url": "https://model.example/v1",
            "configuration_version": 1,
            "api_key": "private-test-key",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///private/key",
        "https://user:password@model.example/v1",
        "https://model.example/v1?key=private",
        "https://model.example/v1#private",
        "http://remote.example/v1",
        "http://127.0.0.1/v1",
        "https://model.example:99999/v1",
        "https://model.example/\nkey",
    ],
)
def test_model_configuration_rejects_credential_urls_and_unapproved_http(url):
    with pytest.raises(ValidationError):
        connection(base_url=url)


def test_local_http_is_opt_in_and_keys_are_references_only():
    assert connection(
        base_url="http://127.0.0.1:9000/v1/", allow_local_http=True
    ).base_url.endswith("/v1")
    with pytest.raises(ValidationError):
        connection(api_key="raw-secret")
    with pytest.raises(ValidationError):
        connection(credential_ref="sk-not-a-reference")
    assert "private-test-key" not in credential().model_dump_json()
    assert "private-test-key" not in repr(credential())


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_ref": "someone-else"},
        {"credential_ref": "secret:another"},
        {"base_url": "https://other.example/v1"},
        {"configuration_version": 2},
    ],
)
def test_credentials_cannot_move_between_owners_endpoints_or_revisions(changes):
    calls = []
    registry = ModelRegistry()
    registry.register(
        ModelProvider(
            "custom-model",
            ("chat_completions",),
            ModelCapabilities(),
            lambda connection, key: calls.append(key),
        )
    )
    with pytest.raises(ServiceError) as error:
        registry.build(connection(), credential(**changes))
    assert error.value.code == "MODEL_CREDENTIAL_BINDING" and not calls


def test_self_declared_capabilities_never_select_unregistered_or_unsupported_provider():
    registry = ModelRegistry()
    config = connection(capabilities=ModelCapabilities(tools=True, verification="live_tested"))
    with pytest.raises(ServiceError) as error:
        registry.build(config, credential())
    assert error.value.code == "MODEL_PROVIDER_NOT_REGISTERED"
    provider = ModelProvider(
        "custom-model", ("chat_completions",), ModelCapabilities(), lambda connection, key: object()
    )
    registry.register(provider)
    with pytest.raises(ServiceError) as error:
        registry.build(config, credential(), requirements=("tools",))
    assert error.value.code == "MODEL_CAPABILITY_UNSUPPORTED"
    with pytest.raises(ServiceError):
        registry.register(replace(provider))
    assert registry.build(connection(), credential()) is not None


def test_usage_unknown_is_not_zero_and_cache_is_bounded():
    assert ModelUsage().input_tokens is None
    assert ModelUsage(input_tokens=0).input_tokens == 0
    with pytest.raises(ValidationError):
        ModelUsage(input_tokens=1, cache_read_tokens=2)


def test_tool_messages_and_events_preserve_call_identity():
    with pytest.raises(ValidationError):
        ModelMessage(role="tool", content="result")
    call = ToolCall(call_ref="call-1", name="inspect", arguments={})
    message = ModelMessage(role="assistant", tool_calls=(call,))
    result = ModelResponse(
        request_ref="request-1",
        connection_ref="model-1",
        model_id="my-model",
        message=message,
        finish_reason="tool_calls",
    )
    assert ModelEvent(request_ref="request-1", sequence=1, kind="completed", response=result)
    with pytest.raises(ValidationError):
        ModelEvent(request_ref="other-request", sequence=1, kind="completed", response=result)
    with pytest.raises(ValidationError):
        ModelResponse(
            request_ref="request-1",
            connection_ref="model-1",
            model_id="my-model",
            message=message,
            finish_reason="stop",
        )


def test_legacy_settings_are_described_without_constructing_clients():
    settings = Settings(database_url="postgresql://unused", model_provider="deepseek")
    profile = legacy_model_connection(settings)
    assert profile.protocol == "deepseek_legacy" and profile.credential_ref.startswith("env:")
    gateway = legacy_model_connection(
        settings.model_copy(
            update={"model_provider": "gateway", "model_url": "https://gateway.example/plan"}
        )
    )
    assert gateway.protocol == "planning_gateway"
    assert (
        legacy_model_connection(settings.model_copy(update={"model_provider": "deterministic"}))
        is None
    )
