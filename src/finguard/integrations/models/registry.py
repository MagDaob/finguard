"""模型提供方由受信代码注册；不根据用户配置导入模块。"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import TypeAdapter

from finguard.backend.errors import require
from finguard.domain.values import Reference
from finguard.integrations.models.contracts import (
    ModelCapabilities,
    ModelClient,
    ModelConnection,
    ModelCredential,
)


@dataclass(frozen=True)
class ModelProvider:
    name: str
    protocols: tuple[str, ...]
    capabilities: ModelCapabilities
    factory: Callable[[ModelConnection, ModelCredential], ModelClient]


class ModelRegistry:
    def __init__(self):
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        TypeAdapter(Reference).validate_python(provider.name, strict=True)
        require(
            provider.name not in self._providers,
            "MODEL_PROVIDER_CONFLICT",
            "模型提供方已注册。",
            409,
        )
        require(
            bool(provider.protocols)
            and set(provider.protocols)
            <= {"chat_completions", "responses", "messages", "planning_gateway", "deepseek_legacy"}
            and callable(provider.factory),
            "INVALID_MODEL_PROVIDER",
            "模型提供方声明无效。",
            422,
        )
        self._providers[provider.name] = provider

    def describe(self) -> list[dict]:
        return [
            {
                "provider": p.name,
                "protocols": list(p.protocols),
                "capabilities": p.capabilities.model_dump(mode="json"),
            }
            for p in self._providers.values()
        ]

    def build(
        self,
        connection: ModelConnection,
        credential: ModelCredential,
        *,
        requirements: tuple[str, ...] = (),
    ) -> ModelClient:
        provider = self._providers.get(connection.provider)
        require(provider is not None, "MODEL_PROVIDER_NOT_REGISTERED", "模型提供方尚未注册。", 503)
        require(
            connection.protocol in provider.protocols,
            "MODEL_PROTOCOL_UNSUPPORTED",
            "模型提供方尚未实现该协议。",
            422,
        )
        require(
            connection.owner_ref == credential.owner_ref
            and connection.credential_ref == credential.credential_ref
            and connection.base_url == credential.base_url
            and connection.configuration_version == credential.configuration_version,
            "MODEL_CREDENTIAL_BINDING",
            "密钥引用与连接身份、地址或版本不匹配。",
            403,
        )
        require(
            bool(credential.api_key.get_secret_value().strip()),
            "MODEL_CREDENTIAL_MISSING",
            "模型密钥未配置。",
            503,
        )
        for capability in requirements:
            require(
                capability in {"tools", "structured_output", "streaming", "reasoning"}
                and getattr(provider.capabilities, capability, False),
                "MODEL_CAPABILITY_UNSUPPORTED",
                "模型提供方未声明所需能力。",
                422,
            )
        return provider.factory(connection, credential)
