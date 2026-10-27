"""首个转账切片的业务对象；校验结构不等于签发授权或验证业务结果。"""

from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Literal, Self

from finguard_bank_contracts.transfer import TransferParameters as TransferParameters
from pydantic import AwareDatetime, Field, model_validator

from finguard.domain.errors import BindingConflict
from finguard.domain.states import OperationStatus, TaskStatus
from finguard.domain.values import (
    Currency,
    DomainModel,
    Minor,
    Reference,
    RequestText,
)


class Task(DomainModel):
    task_ref: Reference
    subject_ref: Reference
    user_request: RequestText
    goal: TransferParameters
    status: TaskStatus = TaskStatus.UNDERSTANDING


class PlanStep(DomainModel):
    step_ref: Reference
    operation_ref: Reference
    action: Literal["transfer.create"] = "transfer.create"
    arguments: TransferParameters
    depends_on: tuple[Reference, ...] = ()
    success_condition: Literal["TRANSACTION_SUCCEEDED"] = "TRANSACTION_SUCCEEDED"
    failure_policy: Literal["STOP_DEPENDENTS"] = "STOP_DEPENDENTS"


class Plan(DomainModel):
    plan_ref: Reference
    task_ref: Reference
    subject_ref: Reference
    version: Annotated[int, Field(strict=True, ge=1)]
    steps: Annotated[tuple[PlanStep, ...], Field(min_length=1, max_length=1000)]
    stop_on_user_request: bool = True

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        if not self.stop_on_user_request:
            raise ValueError("计划必须允许用户停止后续调度。")
        step_ids = {step.step_ref for step in self.steps}
        if len(step_ids) != len(self.steps):
            raise ValueError("计划节点编号重复。")
        if len({step.operation_ref for step in self.steps}) != len(self.steps):
            raise ValueError("转账计划不能重复引用同一操作。")
        graph = {}
        for step in self.steps:
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("节点依赖重复。")
            if not set(step.depends_on) <= step_ids:
                raise ValueError("节点引用了不存在的依赖。")
            graph[step.step_ref] = step.depends_on
        try:
            TopologicalSorter(graph).prepare()
        except CycleError as exc:
            raise ValueError("计划依赖存在环。") from exc
        return self


class ApprovalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    CONSUMED = "CONSUMED"


class Approval(DomainModel):
    approval_ref: Reference
    subject_ref: Reference
    operation_ref: Reference
    parameters: TransferParameters
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    status: ApprovalStatus = ApprovalStatus.ACTIVE
    kind: Literal["SINGLE_OPERATION"] = "SINGLE_OPERATION"

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("授权有效期必须晚于签发时间。")
        return self


class Operation(DomainModel):
    operation_ref: Reference
    task_ref: Reference
    subject_ref: Reference
    action: Literal["transfer.create"] = "transfer.create"
    parameters: TransferParameters
    status: OperationStatus = OperationStatus.PREPARING


class EvidenceKind(StrEnum):
    TRANSACTION_STATUS = "transaction_status"
    LEDGER_ENTRY = "ledger_entry"
    BALANCE_AFTER_TRANSFER = "balance_after_transfer"


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Evidence(DomainModel):
    evidence_ref: Reference
    subject_ref: Reference
    operation_ref: Reference
    source_ref: Reference
    record_ref: Reference
    collected_at: AwareDatetime
    kind: EvidenceKind
    transaction_status: TransactionStatus | None = None
    balance: Minor | None = None
    currency: Currency = "CNY"

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.kind is EvidenceKind.TRANSACTION_STATUS:
            if self.transaction_status is None or self.balance is not None:
                raise ValueError("交易状态证据必须仅包含交易状态。")
        elif self.kind is EvidenceKind.BALANCE_AFTER_TRANSFER:
            if self.balance is None or self.transaction_status is not None:
                raise ValueError("余额证据必须仅包含余额。")
        elif self.transaction_status is not None or self.balance is not None:
            raise ValueError("流水证据通过业务记录编号关联，不混入其他证据字段。")
        return self


def require_operation_binding(task: Task, operation: Operation) -> None:
    if (operation.subject_ref, operation.task_ref) != (task.subject_ref, task.task_ref):
        raise BindingConflict("操作与任务的主体或任务编号不一致。")
    if operation.parameters != task.goal:
        raise BindingConflict("操作参数偏离已明确的转账任务。")


def require_plan_binding(task: Task, plan: Plan, operations: tuple[Operation, ...]) -> None:
    """检查首个单次转账计划；多操作任务将在编排阶段扩展。"""
    if (plan.subject_ref, plan.task_ref) != (task.subject_ref, task.task_ref):
        raise BindingConflict("计划与任务归属不一致。")
    if len(plan.steps) != 1 or len(operations) != 1:
        raise BindingConflict("当前转账任务必须对应一个转账操作。")
    operation = operations[0]
    require_operation_binding(task, operation)
    step = plan.steps[0]
    if (step.operation_ref, step.action, step.arguments) != (
        operation.operation_ref,
        operation.action,
        operation.parameters,
    ):
        raise BindingConflict("计划节点与操作内容不一致。")
