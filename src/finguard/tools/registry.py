"""注册可信代码定义的工具契约；外部文本不能修改注册表。"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from finguard.domain.errors import BindingConflict, DuplicateTool, UnknownTool
from finguard.domain.values import DomainModel, Reference


class AccessMode(StrEnum):
    READ = "READ"
    WRITE = "WRITE"


class SideEffect(StrEnum):
    NONE = "NONE"
    FUNDS_MOVEMENT = "FUNDS_MOVEMENT"
    STATE_CHANGE = "STATE_CHANGE"


class AuthorizationRequirement(StrEnum):
    DATA_ACCESS = "DATA_ACCESS"
    SINGLE_OPERATION = "SINGLE_OPERATION"


class Idempotency(StrEnum):
    NONE = "NONE"
    STABLE_OPERATION_REF = "STABLE_OPERATION_REF"
    DOWNSTREAM_KEY = "DOWNSTREAM_KEY"


class TimeoutPolicy(StrEnum):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    RECONCILE = "RECONCILE"
    DO_NOT_RETRY = "DO_NOT_RETRY"


class Compensation(StrEnum):
    NONE = "NONE"
    SEPARATE_AUTHORIZED_OPERATION = "SEPARATE_AUTHORIZED_OPERATION"


class ToolContract(DomainModel):
    action: Reference
    version: Annotated[int, Field(strict=True, ge=1)] = 1
    request_model: type[DomainModel]
    response_model: type[DomainModel]
    data_scopes: Annotated[tuple[Reference, ...], Field(min_length=1)]
    access_mode: AccessMode
    side_effect: SideEffect
    authorization: AuthorizationRequirement
    idempotency: Idempotency
    timeout_policy: TimeoutPolicy
    status_query_action: Reference | None = None
    compensation: Compensation = Compensation.NONE

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if len(set(self.data_scopes)) != len(self.data_scopes):
            raise ValueError("数据访问范围不能重复。")
        if self.access_mode is AccessMode.READ:
            if self.side_effect is not SideEffect.NONE:
                raise ValueError("读工具不能声明业务副作用。")
            if self.authorization is not AuthorizationRequirement.DATA_ACCESS:
                raise ValueError("读工具必须声明数据访问校验。")
        else:
            if self.side_effect is SideEffect.NONE:
                raise ValueError("写工具必须声明副作用。")
            if self.authorization is not AuthorizationRequirement.SINGLE_OPERATION:
                raise ValueError("当前写工具必须要求单次操作授权。")
            if (
                self.timeout_policy is TimeoutPolicy.SAFE_TO_RETRY
                and self.idempotency is Idempotency.NONE
            ):
                raise ValueError("无幂等能力的写工具不能声明超时后可安全重试。")
        if self.timeout_policy is TimeoutPolicy.RECONCILE and not self.status_query_action:
            raise ValueError("核实型工具必须声明状态查询操作。")
        return self


class ToolRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> None:
        contract = ToolContract.model_validate(contract)
        if contract.action in self._contracts:
            raise DuplicateTool("工具已注册，不能静默替换契约。")
        self._contracts[contract.action] = contract

    def get(self, action: str) -> ToolContract:
        try:
            return self._contracts[action]
        except KeyError as exc:
            raise UnknownTool("工具未注册。") from exc

    def validate_request(self, action: str, payload: dict[str, object]) -> DomainModel:
        """仅校验参数结构；不授予权限，也不调用工具。"""
        return self.get(action).request_model.model_validate(payload)

    def validate_response(self, action: str, payload: dict[str, object]) -> DomainModel:
        return self.get(action).response_model.model_validate(payload)

    def validate_request_json(self, action: str, payload: str) -> DomainModel:
        return self.get(action).request_model.model_validate_json(payload)

    def validate_response_json(self, action: str, payload: str) -> DomainModel:
        return self.get(action).response_model.model_validate_json(payload)

    def validate_links(self) -> None:
        for contract in self._contracts.values():
            if contract.status_query_action is not None:
                query = self.get(contract.status_query_action)
                if query.access_mode is not AccessMode.READ:
                    raise BindingConflict("状态查询工具必须是读工具。")
