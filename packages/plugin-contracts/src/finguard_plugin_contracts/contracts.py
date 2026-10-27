"""插件清单和调用格式；权限申请不构成有效授权。"""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .schemas import check_schema, matches

Reference = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.:-]{0,127}$")]
PluginId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")]
ToolName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
Version = Annotated[str, Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]
KNOWN_PERMISSIONS = frozenset(
    {
        "accounts.read",
        "transactions.read",
        "services.read",
        "products.read",
        "reports.write",
        "models.invoke",
        "transfers.write",
        "services.write",
        "investments.write",
    }
)


class Contract(BaseModel):
    # HTTP 解码后的 JSON 数组可转换为元组；权限与枚举仍显式校验。
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class PluginTool(Contract):
    name: ToolName
    description: Annotated[str, Field(max_length=2000)] = ""
    effect: Literal["read", "write"]
    permissions: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]

    @field_validator("input_schema", "output_schema")
    @classmethod
    def valid_schema(cls, value):
        check_schema(value)
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not set(self.permissions) <= KNOWN_PERMISSIONS or len(set(self.permissions)) != len(
            self.permissions
        ):
            raise ValueError("工具权限声明无效。")
        if self.effect == "read" and any(p.endswith(".write") for p in self.permissions):
            raise ValueError("只读工具不能申请写权限。")
        if self.effect == "write" and not any(p.endswith(".write") for p in self.permissions):
            raise ValueError("写工具必须申请明确的写权限。")
        return self


class PluginManifest(Contract):
    interface_version: Literal["1.0"] = "1.0"
    plugin_id: PluginId
    version: Version
    kind: Literal["tool", "model_provider", "bank_connector", "report", "workflow"]
    entrypoint: Literal["contract_only"] = "contract_only"
    requested_permissions: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    config_schema: dict[str, JsonValue] = Field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )
    tools: Annotated[tuple[PluginTool, ...], Field(max_length=64)] = ()

    @field_validator("config_schema")
    @classmethod
    def valid_schema(cls, value):
        check_schema(value)
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.plugin_id in {"core", "finguard", "system", "bank", "model"}:
            raise ValueError("插件命名空间被宿主保留。")
        if not set(self.requested_permissions) <= KNOWN_PERMISSIONS or len(
            set(self.requested_permissions)
        ) != len(self.requested_permissions):
            raise ValueError("插件权限声明无效。")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("插件工具名称重复。")
        if any(not set(tool.permissions) <= set(self.requested_permissions) for tool in self.tools):
            raise ValueError("工具权限超出插件申请范围。")
        return self


class PluginInvocation(Contract):
    interface_version: Literal["1.0"] = "1.0"
    invocation_ref: Reference
    plugin_id: PluginId
    plugin_version: Version
    tool_name: ToolName
    subject_ref: Reference
    task_ref: Reference | None = None
    operation_ref: Reference | None = None
    context_ref: Reference | None = None
    permission_grant_ref: Reference | None = None
    deadline: AwareDatetime
    arguments: dict[str, JsonValue]


class PluginResult(Contract):
    invocation_ref: Reference
    status: Literal["completed", "rejected", "failed", "pending"]
    data: dict[str, JsonValue] = Field(default_factory=dict)
    source_refs: tuple[Reference, ...] = ()
    evidence_refs: tuple[Reference, ...] = ()
    error_code: Reference | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status in {"rejected", "failed"}) != bool(self.error_code):
            raise ValueError("插件状态与错误码不一致。")
        return self


def validate_invocation(manifest: PluginManifest, invocation: PluginInvocation) -> None:
    """仅校验契约，不核发权限，也不执行工具。"""
    if invocation.plugin_id != manifest.plugin_id or invocation.plugin_version != manifest.version:
        raise ValueError("调用与插件身份或版本不匹配。")
    if invocation.deadline <= datetime.now(UTC):
        raise ValueError("插件调用已经过期。")
    tool = next((tool for tool in manifest.tools if tool.name == invocation.tool_name), None)
    if tool is None or not matches(tool.input_schema, invocation.arguments):
        raise ValueError("插件工具不存在或参数不符合模式。")


def validate_result(
    manifest: PluginManifest, invocation: PluginInvocation, result: PluginResult
) -> None:
    if result.invocation_ref != invocation.invocation_ref:
        raise ValueError("插件结果与调用不匹配。")
    if invocation.plugin_id != manifest.plugin_id or invocation.plugin_version != manifest.version:
        raise ValueError("插件结果的版本上下文不匹配。")
    tool = next((tool for tool in manifest.tools if tool.name == invocation.tool_name), None)
    if tool is None or (
        result.status == "completed" and not matches(tool.output_schema, result.data)
    ):
        raise ValueError("插件结果不符合模式。")


class PluginHost(Protocol):
    """未来运行宿主必须额外核实权限与隔离，本期不提供执行实现。"""

    def invoke(self, invocation: PluginInvocation) -> PluginResult: ...
    def enable(self, plugin_id: str) -> None: ...
    def disable(self, plugin_id: str) -> None: ...
    def unregister(self, plugin_id: str) -> None: ...
