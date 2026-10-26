"""显式加载本机配置；凭据不进入默认配置或错误消息。"""

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database_url: SecretStr
    database_schema: str = Field(default="finguard", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    worker_enabled: bool = True
    worker_interval_seconds: float = Field(default=1, ge=0.1, le=60)
    callback_secret: SecretStr | None = None
    model_url: str | None = None
    model_api_key: SecretStr | None = None
    model_name: str | None = None
    model_allow_local_http: bool = False
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
        return cls.model_validate(values)
