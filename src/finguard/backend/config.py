"""显式加载本机配置；凭据不进入默认配置或错误消息。"""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database_url: SecretStr
    database_schema: str = Field(default="finguard", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    bank_url: str = "http://127.0.0.1:8001"
    bank_provider: str = "simulated-bank"
    bank_environment: Literal["simulation", "external"] = "simulation"
    bank_api_key: SecretStr | None = None
    bank_timeout_seconds: float = Field(default=10, ge=1, le=60)
    step_up_threshold_minor: int = Field(default=100000, ge=1, le=2**63 - 1)
    worker_enabled: bool = True
    worker_interval_seconds: float = Field(default=1, ge=0.1, le=60)
    callback_secret: SecretStr | None = None
    model_url: str | None = None
    model_api_key: SecretStr | None = None
    model_name: str | None = None
    model_allow_local_http: bool = False
    model_provider: Literal["deterministic", "gateway", "deepseek"] = "deterministic"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"
    deepseek_planning_mode: Literal["auto", "loop"] = "auto"
    deepseek_thinking: bool = False
    deepseek_reasoning_effort: Literal["low", "high", "max"] = "low"
    deepseek_max_tokens: int = Field(default=2048, ge=128, le=8192)
    deepseek_max_rounds: int = Field(default=4, ge=2, le=8)
    deepseek_concurrency: int = Field(default=4, ge=1, le=16)
    deepseek_timeout_seconds: float = Field(default=45, ge=5, le=180)
    deepseek_loop_seconds: float = Field(default=120, ge=10, le=600)
    evaluation_enabled: bool = False
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")) -> "Settings":
        local = {}
        if env_file and env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    key, separator, value = line.partition("=")
                    if separator:
                        local[key.strip()] = value.strip().strip('"').strip("'")
        values = {}
        for field in cls.model_fields:
            name = f"FINGUARD_{field.upper()}"
            if name in os.environ or name in local:
                values[field] = os.environ.get(name, local.get(name))
                if field == "cors_origins":
                    values[field] = json.loads(values[field])
        if not values.get("deepseek_api_key"):
            values["deepseek_api_key"] = (
                os.environ.get("DEEPSEEK_API_KEY") or local.get("DEEPSEEK_API_KEY") or None
            )
        return cls.model_validate(values)
