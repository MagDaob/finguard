"""统一选择确定性规划、自定义网关或 DeepSeek 官方接口。"""

from finguard.backend.config import Settings
from finguard.backend.deepseek import AdaptiveDeepSeekPlanner, DeepSeekClient
from finguard.backend.errors import require
from finguard.backend.planning import GatewayPlanner


def configured_planner(settings: Settings):
    if settings.model_provider == "deepseek":
        return AdaptiveDeepSeekPlanner(DeepSeekClient(settings))
    if settings.model_url:
        return GatewayPlanner(
            settings.model_url,
            settings.model_api_key.get_secret_value() if settings.model_api_key else None,
            settings.model_allow_local_http,
        )
    require(settings.model_provider != "gateway", "MODEL_NOT_CONFIGURED", "尚未配置模型网关。", 503)
    return None
