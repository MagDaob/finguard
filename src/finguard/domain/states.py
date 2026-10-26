"""任务和操作使用独立状态图；结果未知的操作只能进入核实终态。"""

from enum import StrEnum
from types import MappingProxyType

from finguard.domain.errors import InvalidTransition


class TaskStatus(StrEnum):
    UNDERSTANDING = "UNDERSTANDING"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"


class OperationStatus(StrEnum):
    PREPARING = "PREPARING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    SUBMITTING = "SUBMITTING"
    UNKNOWN = "UNKNOWN"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED_PRE_SUBMIT = "CANCELLED_PRE_SUBMIT"


TASK_TRANSITIONS = MappingProxyType(
    {
        TaskStatus.UNDERSTANDING: frozenset(
            {
                TaskStatus.WAITING_CLARIFICATION,
                TaskStatus.PLANNING,
                TaskStatus.TERMINATED,
            }
        ),
        TaskStatus.WAITING_CLARIFICATION: frozenset({TaskStatus.PLANNING, TaskStatus.TERMINATED}),
        TaskStatus.PLANNING: frozenset(
            {
                TaskStatus.WAITING_CLARIFICATION,
                TaskStatus.WAITING_APPROVAL,
                TaskStatus.EXECUTING,
                TaskStatus.TERMINATED,
            }
        ),
        TaskStatus.EXECUTING: frozenset(
            {
                TaskStatus.PLANNING,
                TaskStatus.WAITING_APPROVAL,
                TaskStatus.PARTIALLY_COMPLETED,
                TaskStatus.COMPLETED,
                TaskStatus.TERMINATED,
            }
        ),
        TaskStatus.WAITING_APPROVAL: frozenset(
            {
                TaskStatus.PLANNING,
                TaskStatus.EXECUTING,
                TaskStatus.PARTIALLY_COMPLETED,
                TaskStatus.TERMINATED,
            }
        ),
        TaskStatus.PARTIALLY_COMPLETED: frozenset(
            {
                TaskStatus.PLANNING,
                TaskStatus.WAITING_APPROVAL,
                TaskStatus.EXECUTING,
                TaskStatus.COMPLETED,
                TaskStatus.TERMINATED,
            }
        ),
        TaskStatus.COMPLETED: frozenset(),
        TaskStatus.TERMINATED: frozenset(),
    }
)

OPERATION_TRANSITIONS = MappingProxyType(
    {
        OperationStatus.PREPARING: frozenset(
            {
                OperationStatus.WAITING_APPROVAL,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED_PRE_SUBMIT,
            }
        ),
        OperationStatus.WAITING_APPROVAL: frozenset(
            {
                OperationStatus.AUTHORIZED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED_PRE_SUBMIT,
            }
        ),
        OperationStatus.AUTHORIZED: frozenset(
            {
                OperationStatus.WAITING_APPROVAL,
                OperationStatus.SUBMITTING,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED_PRE_SUBMIT,
            }
        ),
        OperationStatus.SUBMITTING: frozenset(
            {
                OperationStatus.UNKNOWN,
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
            }
        ),
        OperationStatus.UNKNOWN: frozenset({OperationStatus.SUCCEEDED, OperationStatus.FAILED}),
        OperationStatus.SUCCEEDED: frozenset(),
        OperationStatus.FAILED: frozenset(),
        OperationStatus.CANCELLED_PRE_SUBMIT: frozenset(),
    }
)


def require_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if type(current) is not TaskStatus or type(target) is not TaskStatus:
        raise InvalidTransition("任务状态类型无效。")
    if target not in TASK_TRANSITIONS[current]:
        raise InvalidTransition(f"任务不允许从 {current} 迁移到 {target}。")


def require_operation_transition(current: OperationStatus, target: OperationStatus) -> None:
    if type(current) is not OperationStatus or type(target) is not OperationStatus:
        raise InvalidTransition("操作状态类型无效。")
    if target not in OPERATION_TRANSITIONS[current]:
        raise InvalidTransition(f"操作不允许从 {current} 迁移到 {target}。")
