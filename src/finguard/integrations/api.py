"""接口目录及本地校验，不持久化连接、不解析密钥、不发起网络调用。"""

from fastapi import APIRouter, Depends
from finguard_bank_contracts.connections import (
    BankCapabilities,
    BankConnection,
    BankConnectionDraft,
    BankEvidence,
    BankOperationResult,
)
from finguard_plugin_contracts.contracts import PluginInvocation, PluginManifest, PluginResult
from finguard_plugin_contracts.schemas import matches
from pydantic import Field, JsonValue

from finguard.backend.catalog import ACTIONS, RequestModel
from finguard.backend.errors import require
from finguard.integrations.banks import default_bank_registry
from finguard.integrations.models.contracts import (
    ModelConnection,
    ModelConnectionDraft,
    ModelEvent,
    ModelRequest,
    ModelResponse,
)
from finguard.integrations.models.legacy import legacy_model_connection
from finguard.integrations.models.registry import ModelRegistry
from finguard.plugins.registry import PluginRegistry


class PluginValidation(RequestModel):
    manifest: PluginManifest
    configuration: dict[str, JsonValue] = Field(default_factory=dict)


def register_integration_routes(app, settings, security, current_actor):
    router = APIRouter(prefix="/v1/integrations", tags=["integration-contracts"])
    bank_registry = default_bank_registry(settings)
    model_registry = ModelRegistry()
    plugin_registry = PluginRegistry()
    app.state.bank_registry = bank_registry
    app.state.model_registry = model_registry
    app.state.plugin_registry = plugin_registry

    actor_dependency = Depends(current_actor)

    def authorized(actor=actor_dependency):
        security.require_scope(actor, "tasks.read")
        return actor

    access = Depends(authorized)

    @router.get("/contracts")
    def contracts(actor=access):
        types = (
            BankConnection,
            BankCapabilities,
            BankOperationResult,
            BankEvidence,
            ModelConnection,
            ModelRequest,
            ModelResponse,
            ModelEvent,
            PluginManifest,
            PluginInvocation,
            PluginResult,
        )
        return {
            "interface_version": "1.0",
            "mode": "contracts_only",
            "schemas": {model.__name__: model.model_json_schema() for model in types},
            "connection_management": {"persistent_storage": False, "network_probe": False},
            "plugin_runtime_available": False,
        }

    @router.get("/providers")
    def providers(actor=access):
        legacy = legacy_model_connection(settings)
        return {
            "banks": bank_registry.describe(),
            "models": model_registry.describe(),
            "legacy_model_configuration": legacy.model_dump(mode="json", exclude={"credential_ref"})
            if legacy
            else None,
            "legacy_configuration_is_activated_by_existing_settings": True,
            "connection_validation_activates_provider": False,
        }

    @router.post("/bank-connections/validate")
    def validate_bank(body: BankConnectionDraft, actor=access):
        connection = BankConnection(**body.model_dump(), owner_ref=actor.subject_ref)
        provider = next(
            (p for p in bank_registry.describe() if p["provider"] == connection.provider), None
        )
        return {
            "valid": True,
            "owner_ref": actor.subject_ref,
            "network_request_sent": False,
            "persisted": False,
            "activated": False,
            "provider_registered": provider is not None,
            "environment_supported": bool(
                provider and connection.environment in provider["environments"]
            ),
            "credential_status": "reference_only" if connection.credential_ref else "not_provided",
        }

    @router.post("/model-connections/validate")
    def validate_model(body: ModelConnectionDraft, actor=access):
        connection = ModelConnection(**body.model_dump(), owner_ref=actor.subject_ref)
        provider = next(
            (p for p in model_registry.describe() if p["provider"] == connection.provider), None
        )
        return {
            "valid": True,
            "owner_ref": actor.subject_ref,
            "network_request_sent": False,
            "persisted": False,
            "activated": False,
            "provider_registered": provider is not None,
            "protocol_implemented": bool(provider and connection.protocol in provider["protocols"]),
            "capabilities_verified": False,
            "credential_status": "reference_only" if connection.credential_ref else "not_provided",
        }

    @router.post("/plugins/validate")
    def validate_plugin(body: PluginValidation, actor=access):
        require(
            not any(tool.name in ACTIONS for tool in body.manifest.tools),
            "PLUGIN_TOOL_CONFLICT",
            "插件不能覆盖核心工具。",
            409,
        )
        require(
            matches(body.manifest.config_schema, body.configuration),
            "PLUGIN_CONFIGURATION_INVALID",
            "插件配置不符合模式。",
            422,
        )
        return {
            "valid": True,
            "plugin_id": body.manifest.plugin_id,
            "version": body.manifest.version,
            "registered": False,
            "permissions_granted": False,
            "runtime_available": False,
            "network_request_sent": False,
        }

    app.include_router(router)
