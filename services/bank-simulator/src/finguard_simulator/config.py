"""只读取模拟银行目录的配置，不加载项目本体的模型密钥。"""

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    database_url: SecretStr
    database_schema: str = Field(default="simbank", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    api_key: SecretStr = Field(min_length=32)

    @classmethod
    def from_env(cls, path=Path(".env")):
        values = {}
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.strip().partition("=")
                if sep and not key.startswith("#"):
                    values[key] = value.strip().strip('"').strip("'")
        return cls.model_validate(
            {
                field: os.environ.get(name, values.get(name))
                for field in cls.model_fields
                if (name := f"SIMBANK_{field.upper()}") in os.environ or name in values
            }
        )
