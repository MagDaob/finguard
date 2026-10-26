"""加载和检查虚构场景；确认事件不会转换为有效授权。"""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from finguard.domain.models import (
    EvidenceKind,
    Operation,
    Plan,
    PlanStep,
    Task,
    TransferParameters,
    require_plan_binding,
)
from finguard.domain.values import Currency, DomainModel, Minor, Reference, RequestText


class InitialState(DomainModel):
    subject_ref: Reference
    source_account_ref: Reference
    recipient_ref: Reference
    recipient_confirmed: bool
    currency: Currency
    source_balance_minor: Minor
    ledger_entries: tuple[Reference, ...]


class OperationDraft(TransferParameters):
    operation_ref: Reference
    action: Literal["transfer.create"]

    def parameters(self) -> TransferParameters:
        return TransferParameters.model_validate(
            self.model_dump(exclude={"operation_ref", "action"})
        )


class ConfirmationEvent(DomainModel):
    type: Literal["USER_CONFIRMS_OPERATION"]
    subject_ref: Reference
    operation_ref: Reference
    note: str


class AdapterEvent(DomainModel):
    type: Literal["ADAPTER_CONFIRMS_TRANSFER"]
    operation_ref: Reference
    transaction_ref: Reference
    transaction_status: Literal["SUCCEEDED"]


ScenarioEvent = Annotated[ConfirmationEvent | AdapterEvent, Field(discriminator="type")]


class ExpectedResult(DomainModel):
    task_status: Literal["COMPLETED"]
    operation_status: Literal["SUCCEEDED"]
    source_balance_minor: Minor
    source_debit_count: Annotated[int, Field(strict=True, ge=1, le=1)]
    effective_authorization_required: bool
    minimum_remaining_balance_satisfied: bool
    evidence_required: tuple[EvidenceKind, ...]
    success_report_requires_verified_evidence: bool


class TransferScenario(DomainModel):
    fixture_format_version: Literal["0.1"]
    scenario_id: Reference
    fixture_only: bool
    description: str
    user_request: RequestText
    initial_state: InitialState
    operation_draft: OperationDraft
    simulated_events: tuple[ScenarioEvent, ...]
    expected_result: ExpectedResult

    @property
    def projected_balance_minor(self) -> int:
        return (
            self.initial_state.source_balance_minor
            - self.operation_draft.amount_minor
            - self.operation_draft.fee_minor
        )

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        initial, draft, expected = self.initial_state, self.operation_draft, self.expected_result
        if not self.fixture_only or not initial.recipient_confirmed:
            raise ValueError("首个场景必须使用虚构数据和已确认收款人。")
        if (initial.source_account_ref, initial.recipient_ref, initial.currency) != (
            draft.source_account_ref,
            draft.recipient_ref,
            draft.currency,
        ):
            raise ValueError("初始资源或币种与操作草案不一致。")
        if self.projected_balance_minor != expected.source_balance_minor:
            raise ValueError("预期余额与金额、费用不一致。")
        if self.projected_balance_minor < draft.minimum_remaining_balance_minor:
            raise ValueError("成功场景不满足最低保留余额。")
        if not all(
            (
                expected.effective_authorization_required,
                expected.minimum_remaining_balance_satisfied,
                expected.success_report_requires_verified_evidence,
            )
        ):
            raise ValueError("成功场景必须声明授权、余额和证据约束。")
        if set(expected.evidence_required) != set(EvidenceKind) or len(
            expected.evidence_required
        ) != len(EvidenceKind):
            raise ValueError("成功场景必须包含全部三类证据，且不能重复。")
        confirmations = 0
        adapter_events = 0
        for event in self.simulated_events:
            if event.operation_ref != draft.operation_ref:
                raise ValueError("场景事件引用了其他操作。")
            if isinstance(event, ConfirmationEvent):
                confirmations += 1
                if event.subject_ref != initial.subject_ref:
                    raise ValueError("确认事件主体与任务主体不一致。")
            else:
                adapter_events += 1
        if confirmations != 1 or adapter_events != 1:
            raise ValueError("首个场景需要一次模拟确认和一次模拟业务结果。")
        # 同时验证生成的业务对象之间的关联，事件本身不证明结果已发生。
        self.to_domain()
        return self

    def to_domain(self) -> tuple[Task, Plan, Operation]:
        parameters = self.operation_draft.parameters()
        task = Task(
            task_ref=f"task-{self.scenario_id}",
            subject_ref=self.initial_state.subject_ref,
            user_request=self.user_request,
            goal=parameters,
        )
        operation = Operation(
            operation_ref=self.operation_draft.operation_ref,
            task_ref=task.task_ref,
            subject_ref=task.subject_ref,
            parameters=parameters,
        )
        plan = Plan(
            plan_ref=f"plan-{self.scenario_id}",
            task_ref=task.task_ref,
            subject_ref=task.subject_ref,
            version=1,
            steps=(
                PlanStep(
                    step_ref="transfer-01",
                    operation_ref=operation.operation_ref,
                    arguments=parameters,
                ),
            ),
        )
        require_plan_binding(task, plan, (operation,))
        return task, plan, operation


def load_transfer_scenario(path: Path) -> TransferScenario:
    return TransferScenario.model_validate_json(path.read_text(encoding="utf-8"))
