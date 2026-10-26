"""转账与状态查询的接口契约，不提供适配器实现。"""

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from finguard.domain.models import TransactionStatus, TransferParameters
from finguard.domain.values import DomainModel, Reference
from finguard.tools.registry import (
    AccessMode,
    AuthorizationRequirement,
    Idempotency,
    SideEffect,
    TimeoutPolicy,
    ToolContract,
    ToolRegistry,
)


class TransferRequest(DomainModel):
    subject_ref: Reference
    task_ref: Reference
    operation_ref: Reference
    parameters: TransferParameters


class TransferLookup(DomainModel):
    subject_ref: Reference
    operation_ref: Reference


class TransferResult(DomainModel):
    operation_ref: Reference
    transaction_ref: Reference
    status: TransactionStatus


class LookupObservation(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


class TransferLookupResult(DomainModel):
    operation_ref: Reference
    observation: LookupObservation
    transaction_ref: Reference | None = None
    status: TransactionStatus | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if self.observation is LookupObservation.FOUND:
            if self.transaction_ref is None or self.status is None:
                raise ValueError("找到交易时必须提供业务编号和状态。")
        elif self.transaction_ref is not None or self.status is not None:
            raise ValueError("未找到或无法确认交易时不能附带已确认交易结果。")
        return self


def transfer_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolContract(
            action="transfer.get",
            request_model=TransferLookup,
            response_model=TransferLookupResult,
            data_scopes=("transactions.read",),
            access_mode=AccessMode.READ,
            side_effect=SideEffect.NONE,
            authorization=AuthorizationRequirement.DATA_ACCESS,
            idempotency=Idempotency.NONE,
            timeout_policy=TimeoutPolicy.SAFE_TO_RETRY,
        )
    )
    registry.register(
        ToolContract(
            action="transfer.create",
            request_model=TransferRequest,
            response_model=TransferResult,
            data_scopes=("accounts.read", "transfers.write"),
            access_mode=AccessMode.WRITE,
            side_effect=SideEffect.FUNDS_MOVEMENT,
            authorization=AuthorizationRequirement.SINGLE_OPERATION,
            idempotency=Idempotency.STABLE_OPERATION_REF,
            timeout_policy=TimeoutPolicy.RECONCILE,
            status_query_action="transfer.get",
        )
    )
    registry.validate_links()
    return registry
