"""业务工具和计划输入；所有写参数进入统一的规范化与摘要流程。"""

from graphlib import CycleError, TopologicalSorter
from typing import Annotated

from finguard_bank_contracts.actions import (
    ACTIONS as ACTIONS,
)
from finguard_bank_contracts.actions import ProductArguments as ProductArguments
from finguard_bank_contracts.actions import (
    canonical as canonical,
)
from finguard_bank_contracts.actions import (
    digest as digest,
)
from finguard_bank_contracts.actions import (
    operation_digest as operation_digest,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from finguard.backend.errors import ServiceError, require
from finguard.domain.values import (
    Minor,
    PositiveMinor,
    Reference,
    RequestText,
)

USER_SCOPES = (
    "accounts.read",
    "transactions.read",
    "services.read",
    "products.read",
    "tasks.read",
    "tasks.write",
    "approvals.write",
    "transfers.write",
    "services.write",
    "investments.write",
)


def required_scopes(action: str) -> tuple[str, ...]:
    primary = ACTIONS[action].scope
    if action.startswith("product."):
        return (primary, "accounts.read", "products.read")
    if action in {"transfer.create", "bills.analyze"}:
        return (primary, "accounts.read")
    if action in {"card.set_lock", "subscription.cancel"}:
        return (primary, "services.read")
    return (primary,)


class RequestModel(BaseModel):
    # HTTP JSON 数组可转换为元组，金额与次数仍通过字段执行严格整数校验。
    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(RequestModel):
    step_ref: Reference
    action: Reference
    arguments: dict[str, JsonValue]
    depends_on: tuple[Reference, ...] = ()
    funds_available_after: tuple[Reference, ...] = ()


class Constraints(RequestModel):
    minimum_remaining_balance_minor: Minor = 0
    maximum_fee_minor: Minor = 0


class TaskCreate(RequestModel):
    client_request_ref: Reference
    user_request: RequestText
    steps: Annotated[tuple[Step, ...], Field(max_length=32)] = ()
    constraints: Constraints = Field(default_factory=Constraints)


class ReplanRequest(RequestModel):
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    steps: Annotated[tuple[Step, ...], Field(min_length=1, max_length=32)]


class ApprovalRequest(RequestModel):
    expected_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    ttl_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)] = 300


class DelegationRequest(RequestModel):
    source_account_ref: Reference
    recipient_refs: Annotated[tuple[Reference, ...], Field(min_length=1, max_length=100)]
    per_operation_limit_minor: PositiveMinor
    total_limit_minor: PositiveMinor
    maximum_operations: Annotated[int, Field(strict=True, ge=1, le=1000)]
    period_seconds: Annotated[int, Field(strict=True, ge=1, le=86400)]
    ttl_seconds: Annotated[int, Field(strict=True, ge=1, le=2592000)]


def normalize_steps(steps: tuple[Step, ...], constraints: Constraints) -> list[dict]:
    require(bool(steps), "EMPTY_PLAN", "计划必须包含目标。", 422)
    require(len(steps) <= 32, "PLAN_TOO_LARGE", "计划最多包含 32 个目标。", 422)
    identifiers = {step.step_ref for step in steps}
    require(len(identifiers) == len(steps), "DUPLICATE_STEP", "计划节点编号重复。", 422)
    normalized = []
    graph = {}
    for step in steps:
        require(step.action in ACTIONS, "UNKNOWN_TOOL", "计划包含未注册工具。", 422)
        require(
            len(set(step.depends_on)) == len(step.depends_on),
            "DUPLICATE_DEPENDENCY",
            "计划依赖重复。",
            422,
        )
        require(set(step.depends_on) <= identifiers, "MISSING_DEPENDENCY", "计划依赖不存在。", 422)
        require(
            set(step.funds_available_after) <= set(step.depends_on),
            "INVALID_CONDITION",
            "资金条件必须对应显式依赖。",
            422,
        )
        arguments = (
            ACTIONS[step.action]
            .arguments.model_validate_json(canonical(step.arguments))
            .model_dump(mode="json")
        )
        if ACTIONS[step.action].monetary:
            require(
                arguments["fee_minor"] <= constraints.maximum_fee_minor,
                "FEE_LIMIT",
                "费用超出用户约束。",
                422,
            )
            arguments["minimum_remaining_balance_minor"] = max(
                arguments["minimum_remaining_balance_minor"],
                constraints.minimum_remaining_balance_minor,
            )
        normalized.append({**step.model_dump(mode="json"), "arguments": arguments})
        graph[step.step_ref] = step.depends_on
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ServiceError("CYCLIC_PLAN", "计划依赖存在环。", 422) from exc
    return normalized


def charged_amount(action: str, arguments: dict) -> int:
    if action in {"transfer.create", "product.subscribe"}:
        return arguments["amount_minor"] + arguments["fee_minor"]
    return 0
