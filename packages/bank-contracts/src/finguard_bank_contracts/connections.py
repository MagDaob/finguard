"""银行扩展契约；配置和能力声明都不代表业务授权。"""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .errors import ResultUnknown, require
from .values import DomainModel, Reference

CredentialReference = Annotated[
    str,
    Field(pattern=r"^(env:[A-Z][A-Z0-9_]{0,127}|secret:[a-zA-Z][a-zA-Z0-9_.:-]{0,127})$"),
]


class BankConnectionDraft(DomainModel):
    connection_ref: Reference
    provider: Reference
    environment: Literal["simulation", "external"]
    interface_version: Literal["1.0"] = "1.0"
    configuration_version: Annotated[int, Field(strict=True, ge=1)] = 1
    credential_ref: CredentialReference | None = None


class BankConnection(BankConnectionDraft):
    owner_ref: Reference


class BankCapabilities(DomainModel):
    provider: Reference
    environment: Literal["simulation", "external"]
    interface_version: Literal["1.0"] = "1.0"
    supported_actions: Annotated[tuple[Reference, ...], Field(max_length=128)] = ()
    stable_operation_identity: bool = False
    lookup_by_operation: bool = False
    evidence: bool = False
    atomic_minimum_balance: bool = False
    fence_absent_operation: bool = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len(set(self.supported_actions)) != len(self.supported_actions):
            raise ValueError("银行能力存在重复动作。")
        if self.fence_absent_operation and not self.lookup_by_operation:
            raise ValueError("查询栅栏需要操作查询能力。")
        return self

    def require_action(self, action: str) -> None:
        require(
            action in self.supported_actions,
            "BANK_CAPABILITY_UNSUPPORTED",
            "银行未声明该能力。",
            422,
        )


class BankOperationResult(DomainModel):
    operation_ref: Reference
    status: Literal["PENDING", "SUCCEEDED", "FAILED", "UNKNOWN", "REQUIRES_USER_ACTION"]
    transaction_ref: Reference | None = None
    bank_record_ref: (
        Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")] | None
    ) = None
    error_code: Reference | None = None
    user_action_ref: Reference | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == "SUCCEEDED" and (not self.transaction_ref or self.error_code):
            raise ValueError("成功结果需要业务记录引用，且不能包含错误。")
        if self.status == "REQUIRES_USER_ACTION" and not self.user_action_ref:
            raise ValueError("等待用户处理需要动作引用。")
        if self.status != "REQUIRES_USER_ACTION" and self.user_action_ref:
            raise ValueError("当前状态不能携带待处理动作。")
        if set(self.data) & {
            "operation_ref",
            "transaction_ref",
            "status",
            "error_code",
            "bank_record_ref",
        }:
            raise ValueError("业务数据不能覆盖结果信封。")
        return self

    def to_backend_receipt(self) -> dict:
        """转换为现有执行内核的回执格式；不推断业务成功。"""
        if self.status == "UNKNOWN":
            raise ResultUnknown()
        require(
            self.status != "REQUIRES_USER_ACTION",
            "BANK_USER_ACTION_UNSUPPORTED",
            "现有执行内核尚未实现该银行用户动作。",
            501,
        )
        require(
            self.transaction_ref in {None, self.operation_ref},
            "BANK_RECEIPT_BINDING",
            "内部回执编号必须绑定项目操作；外部流水另存。",
            422,
        )
        result = {
            **self.data,
            "operation_ref": self.operation_ref,
            "transaction_ref": self.operation_ref,
            "status": self.status,
        }
        if self.error_code:
            result["error_code"] = self.error_code
        if self.bank_record_ref:
            result["bank_record_ref"] = self.bank_record_ref
        return result


class BankEvidence(DomainModel):
    subject_ref: Reference
    operation_ref: Reference
    source_ref: Reference
    kind: Reference
    record_ref: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]
    verified_at: AwareDatetime
    data: dict[str, JsonValue]
