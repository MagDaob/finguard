"""完整响应与流式事件的统一数据结构。"""

from collections.abc import Iterator
from ipaddress import ip_address
from typing import Annotated, Literal, Protocol, Self
from urllib.parse import urlsplit

from finguard_bank_contracts.connections import CredentialReference
from pydantic import Field, JsonValue, SecretStr, field_validator, model_validator

from finguard.domain.values import DomainModel, Minor, Reference


class ModelCapabilities(DomainModel):
    tools: bool = False
    structured_output: bool = False
    streaming: bool = False
    reasoning: bool = False
    verification: Literal["unverified", "contract_tested", "live_tested"] = "unverified"


class ModelConnectionDraft(DomainModel):
    interface_version: Literal["1.0"] = "1.0"
    connection_ref: Reference
    provider: Reference
    protocol: Literal[
        "chat_completions", "responses", "messages", "planning_gateway", "deepseek_legacy"
    ]
    base_url: Annotated[str, Field(min_length=8, max_length=2048)]
    model_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
    credential_ref: CredentialReference | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    configuration_version: Annotated[int, Field(strict=True, ge=1)] = 1
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=600)] = 45
    concurrency: Annotated[int, Field(strict=True, ge=1, le=64)] = 4
    request_budget: Annotated[int, Field(strict=True, ge=1, le=10000)] = 32
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=131072)] = 2048
    allow_local_http: bool = False

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            valid = (
                url.scheme in {"https", "http"}
                and url.hostname
                and not (url.username or url.password or url.query or url.fragment)
            )
            if url.port is not None and not 1 <= url.port <= 65535:
                valid = False
        except ValueError:
            valid = False
        if not valid or any(ord(c) <= 32 for c in value) or "\\" in value:
            raise ValueError("模型地址须为不含凭据、查询参数或片段的 HTTP(S) 基础地址。")
        return value.rstrip("/")

    @model_validator(mode="after")
    def local_http(self) -> Self:
        url = urlsplit(self.base_url)
        if url.scheme == "http":
            try:
                local = url.hostname == "localhost" or ip_address(url.hostname).is_loopback
            except ValueError:
                local = False
            if not self.allow_local_http or not local:
                raise ValueError("HTTP 仅允许显式启用的本机开发地址。")
        return self


class ModelConnection(ModelConnectionDraft):
    owner_ref: Reference


class ModelCredential(DomainModel):
    owner_ref: Reference
    credential_ref: CredentialReference
    base_url: str
    configuration_version: Annotated[int, Field(strict=True, ge=1)]
    api_key: SecretStr = Field(exclude=True, repr=False)


class ToolDefinition(DomainModel):
    name: Reference
    description: Annotated[str, Field(max_length=2000)] = ""
    input_schema: dict[str, JsonValue]


class ToolCall(DomainModel):
    call_ref: Reference
    name: Reference
    arguments: dict[str, JsonValue]


class ModelMessage(DomainModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Annotated[str, Field(max_length=65536)] | None = None
    tool_calls: Annotated[tuple[ToolCall, ...], Field(max_length=32)] = ()
    tool_call_ref: Reference | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.role == "tool" and not self.tool_call_ref:
            raise ValueError("工具结果缺少调用编号。")
        if self.role != "tool" and self.tool_call_ref:
            raise ValueError("该消息角色不能携带工具结果编号。")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("只有助手消息可提出工具调用。")
        if not self.content and not self.tool_calls:
            raise ValueError("消息需要内容或工具调用。")
        if len({call.call_ref for call in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("工具调用编号重复。")
        return self


class ModelRequest(DomainModel):
    interface_version: Literal["1.0"] = "1.0"
    request_ref: Reference
    connection_ref: Reference
    messages: Annotated[tuple[ModelMessage, ...], Field(min_length=1, max_length=256)]
    tools: Annotated[tuple[ToolDefinition, ...], Field(max_length=128)] = ()
    output_mode: Literal["text", "json"] = "text"
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=131072)] = 2048

    @model_validator(mode="after")
    def unique_tools(self) -> Self:
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("模型工具名称重复。")
        return self


class ModelUsage(DomainModel):
    input_tokens: Minor | None = None
    output_tokens: Minor | None = None
    cache_read_tokens: Minor | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            self.input_tokens is not None
            and self.cache_read_tokens is not None
            and self.cache_read_tokens > self.input_tokens
        ):
            raise ValueError("缓存读取量不能超过输入用量。")
        return self


class ModelResponse(DomainModel):
    request_ref: Reference
    connection_ref: Reference
    model_id: str
    message: ModelMessage
    finish_reason: Literal["stop", "tool_calls", "length", "filtered", "cancelled", "unknown"]
    usage: ModelUsage = Field(default_factory=ModelUsage)

    @model_validator(mode="after")
    def assistant_response(self) -> Self:
        if self.message.role != "assistant":
            raise ValueError("模型响应必须是助手消息。")
        if (self.finish_reason == "tool_calls") != bool(self.message.tool_calls):
            raise ValueError("结束原因与工具调用不一致。")
        return self


class ModelEvent(DomainModel):
    request_ref: Reference
    sequence: Minor
    kind: Literal["text_delta", "tool_delta", "completed", "error"]
    text: str | None = None
    response: ModelResponse | None = None
    error_code: Reference | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.kind == "completed":
            if (
                not self.response
                or self.response.request_ref != self.request_ref
                or self.text is not None
                or self.error_code
            ):
                raise ValueError("完成事件需要同一请求的完整响应。")
        elif self.kind == "error":
            if not self.error_code or self.response or self.text is not None:
                raise ValueError("错误事件需要错误码。")
        elif self.text is None or self.response or self.error_code:
            raise ValueError("增量事件需要文本片段。")
        return self


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]: ...
    def close(self) -> None: ...


class ModelConnectionRepository(Protocol):
    """持久化管理扩展点；本期不提供存储实现。"""

    def create(self, connection: ModelConnection) -> ModelConnection: ...
    def get(self, owner_ref: str, connection_ref: str) -> ModelConnection: ...
    def update(self, connection: ModelConnection, expected_version: int) -> ModelConnection: ...
    def disable(self, owner_ref: str, connection_ref: str, expected_version: int) -> None: ...
