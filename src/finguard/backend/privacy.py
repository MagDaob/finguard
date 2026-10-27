"""任务文字和诊断导出的有限脱敏，不宣称识别任意秘密。"""

import re

POLICY = "known-sensitive-patterns-v1"
SENSITIVE_KEYS = {
    "api_key",
    "password",
    "secret",
    "authorization",
    "access_token",
    "refresh_token",
    "token",
}


def redact_text(text: str) -> str:
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    text = re.sub(
        r"""(?i)((?:api[_ -]?key|password|secret|token|密码|密钥)["']?\s*[:：=]\s*)(["'])(.*?)\2""",
        lambda m: m[1] + m[2] + "[REDACTED]" + m[2],
        text,
    )
    text = re.sub(
        r"(?i)((?:api[_ -]?key|password|secret|token|密码|密钥)\s*[:：=]\s*)[^\s,，;；\"']+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])([0-9]{13,19})(?![A-Za-z0-9])", lambda m: "****" + m[1][-4:], text
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])(1[3-9][0-9]{9})(?![A-Za-z0-9])",
        lambda m: m[1][:3] + "****" + m[1][-4:],
        text,
    )
    return text


def redact(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value
