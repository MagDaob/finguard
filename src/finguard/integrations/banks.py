"""银行连接器的显式注册；未知提供方不回退到模拟银行。"""

from collections.abc import Callable
from dataclasses import dataclass

from finguard_bank_contracts.connections import BankConnection
from pydantic import SecretStr, TypeAdapter

from finguard.backend.bank import Bank, HttpBank
from finguard.backend.errors import require
from finguard.domain.values import Reference

BankFactory = Callable[[BankConnection, SecretStr | None], Bank]


@dataclass(frozen=True)
class BankProvider:
    name: str
    environments: tuple[str, ...]
    factory: BankFactory


class BankRegistry:
    def __init__(self):
        self._providers: dict[str, BankProvider] = {}

    def register(self, provider: BankProvider) -> None:
        TypeAdapter(Reference).validate_python(provider.name, strict=True)
        require(
            provider.name not in self._providers,
            "BANK_PROVIDER_CONFLICT",
            "银行提供方已注册。",
            409,
        )
        require(
            bool(provider.environments)
            and set(provider.environments) <= {"simulation", "external"},
            "INVALID_BANK_PROVIDER",
            "银行环境声明无效。",
            422,
        )
        require(callable(provider.factory), "INVALID_BANK_PROVIDER", "银行工厂必须可调用。", 422)
        self._providers[provider.name] = provider

    def describe(self) -> list[dict]:
        return [
            {"provider": p.name, "environments": list(p.environments), "interface_version": "1.0"}
            for p in self._providers.values()
        ]

    def build(
        self,
        connection: BankConnection,
        credential: SecretStr | None = None,
        *,
        required_actions: tuple[str, ...] = (),
    ) -> Bank:
        provider = self._providers.get(connection.provider)
        require(provider is not None, "BANK_PROVIDER_NOT_REGISTERED", "银行提供方尚未注册。", 503)
        require(
            connection.environment in provider.environments,
            "BANK_ENVIRONMENT_UNSUPPORTED",
            "银行提供方不支持该环境。",
            422,
        )
        bank = provider.factory(connection, credential)
        if required_actions:
            try:
                capabilities = bank.get_capabilities()
                require(
                    capabilities.provider == connection.provider
                    and capabilities.environment == connection.environment,
                    "BANK_CAPABILITIES_MISMATCH",
                    "能力声明与连接不匹配。",
                    503,
                )
                for action in required_actions:
                    capabilities.require_action(action)
            except Exception:
                if hasattr(bank, "close"):
                    bank.close()
                raise
        return bank


def default_bank_registry(settings) -> BankRegistry:
    registry = BankRegistry()

    def simulator(connection, credential):
        require(
            credential is not None and bool(credential.get_secret_value()),
            "BANK_NOT_CONFIGURED",
            "请配置独立银行服务的访问密钥。",
            503,
        )
        return HttpBank(
            settings.bank_url, credential.get_secret_value(), settings.bank_timeout_seconds
        )

    registry.register(BankProvider("simulated-bank", ("simulation",), simulator))
    return registry


def configured_bank(settings, registry: BankRegistry | None = None) -> Bank:
    connection = BankConnection(
        connection_ref="bank-default",
        owner_ref="system",
        provider=settings.bank_provider,
        environment=settings.bank_environment,
        credential_ref="env:FINGUARD_BANK_API_KEY",
    )
    return (registry or default_bank_registry(settings)).build(connection, settings.bank_api_key)
