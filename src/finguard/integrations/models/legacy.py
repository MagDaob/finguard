"""只描述已有配置，不构建客户端或读取密钥明文。"""

from finguard.integrations.models.contracts import ModelCapabilities, ModelConnection


def legacy_model_connection(settings) -> ModelConnection | None:
    if settings.model_provider == "deepseek":
        return ModelConnection(
            connection_ref="legacy-deepseek",
            owner_ref="system",
            provider="deepseek",
            protocol="deepseek_legacy",
            base_url="https://api.deepseek.com",
            model_id=settings.deepseek_model,
            credential_ref="env:FINGUARD_DEEPSEEK_API_KEY",
            capabilities=ModelCapabilities(tools=True, structured_output=True, reasoning=True),
        )
    if settings.model_url:
        return ModelConnection(
            connection_ref="legacy-gateway",
            owner_ref="system",
            provider="custom-gateway",
            protocol="planning_gateway",
            base_url=settings.model_url,
            model_id=settings.model_name or "gateway-defined",
            credential_ref="env:FINGUARD_MODEL_API_KEY" if settings.model_api_key else None,
            allow_local_http=settings.model_allow_local_http,
        )
    return None
