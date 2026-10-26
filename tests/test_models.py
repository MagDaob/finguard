from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from finguard.domain.errors import BindingConflict
from finguard.domain.models import (
    Approval,
    Evidence,
    EvidenceKind,
    Operation,
    Plan,
    PlanStep,
    TransactionStatus,
    TransferParameters,
    require_operation_binding,
    require_plan_binding,
)


def test_five_objects_can_serialize_without_mutating_operation_identity(scenario):
    task, plan, operation = scenario.to_domain()
    require_plan_binding(task, plan, (operation,))
    restored = Operation.model_validate_json(operation.model_dump_json())
    assert restored == operation
    assert scenario.to_domain()[2].operation_ref == operation.operation_ref
    with pytest.raises(ValidationError):
        operation.parameters.amount_minor = 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_minor", 0),
        ("amount_minor", True),
        ("fee_minor", -1),
        ("minimum_remaining_balance_minor", 0.1),
        ("currency", "USD"),
    ],
)
def test_transfer_parameters_reject_invalid_business_values(scenario, field, value):
    data = scenario.operation_draft.parameters().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        TransferParameters.model_validate(data)


def test_total_debit_must_fit_storage_range(scenario):
    data = scenario.operation_draft.parameters().model_dump()
    data.update(amount_minor=2**63 - 1, fee_minor=1)
    with pytest.raises(ValidationError):
        TransferParameters.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"subject_ref": "another-user"},
        {"task_ref": "another-task"},
    ],
)
def test_cross_subject_and_task_bindings_are_rejected(scenario, change):
    task, _, operation = scenario.to_domain()
    changed = Operation.model_validate({**operation.model_dump(), **change})
    with pytest.raises(BindingConflict):
        require_operation_binding(task, changed)


@pytest.mark.parametrize("field", ["fee_minor", "minimum_remaining_balance_minor"])
def test_financial_constraints_cannot_default_to_zero_when_omitted(scenario, field):
    data = scenario.operation_draft.parameters().model_dump()
    del data[field]
    with pytest.raises(ValidationError):
        TransferParameters.model_validate(data)


def test_altered_amount_cannot_silently_replace_task_goal(scenario):
    task, plan, operation = scenario.to_domain()
    data = operation.model_dump()
    data["parameters"]["amount_minor"] += 1
    with pytest.raises(BindingConflict):
        require_plan_binding(task, plan, (Operation.model_validate(data),))


def test_duplicate_operations_cannot_double_single_transfer(scenario):
    task, plan, operation = scenario.to_domain()
    with pytest.raises(BindingConflict):
        require_plan_binding(task, plan, (operation, operation))


@pytest.mark.parametrize(
    "dependencies",
    [
        (("step-b",), ("step-a",)),
        (("step-a",), ()),
        (("missing",), ()),
        (("step-b", "step-b"), ()),
    ],
)
def test_invalid_plan_graphs_are_rejected(scenario, dependencies):
    arguments = scenario.operation_draft.parameters()
    steps = tuple(
        PlanStep(
            step_ref=ref, operation_ref=f"operation-{ref}", arguments=arguments, depends_on=deps
        )
        for ref, deps in zip(("step-a", "step-b"), dependencies, strict=True)
    )
    with pytest.raises(ValidationError):
        Plan(plan_ref="plan-1", task_ref="task-1", subject_ref="user-1", version=1, steps=steps)


def test_valid_dependency_graph_and_duplicate_step_detection(scenario):
    arguments = scenario.operation_draft.parameters()
    first = PlanStep(step_ref="step-a", operation_ref="operation-a", arguments=arguments)
    second = PlanStep(
        step_ref="step-b", operation_ref="operation-b", arguments=arguments, depends_on=("step-a",)
    )
    Plan(
        plan_ref="plan-1", task_ref="task-1", subject_ref="user-1", version=1, steps=(first, second)
    )
    with pytest.raises(ValidationError):
        Plan(
            plan_ref="plan-1",
            task_ref="task-1",
            subject_ref="user-1",
            version=1,
            steps=(first, first),
        )


def test_approval_records_require_timezone_and_forward_expiry(scenario):
    _, _, operation = scenario.to_domain()
    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    base = dict(
        approval_ref="approval-1",
        subject_ref=operation.subject_ref,
        operation_ref=operation.operation_ref,
        parameters=operation.parameters,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    record = Approval(**base)
    assert Approval.model_validate_json(record.model_dump_json()) == record
    for changes in ({"expires_at": now}, {"issued_at": now.replace(tzinfo=None)}):
        with pytest.raises(ValidationError):
            Approval(**{**base, **changes})


def test_evidence_must_be_traceable_and_have_matching_payload():
    base = dict(
        evidence_ref="evidence-1",
        subject_ref="user-1",
        operation_ref="operation-1",
        source_ref="mock-bank",
        record_ref="transaction-1",
        collected_at=datetime(2026, 10, 26, 12, tzinfo=UTC),
    )
    Evidence(
        **base, kind=EvidenceKind.TRANSACTION_STATUS, transaction_status=TransactionStatus.SUCCEEDED
    )
    with pytest.raises(ValidationError):
        Evidence(**base, kind=EvidenceKind.TRANSACTION_STATUS)
    with pytest.raises(ValidationError):
        Evidence(**base, kind=EvidenceKind.BALANCE_AFTER_TRANSFER, balance=-1)
    with pytest.raises(ValidationError):
        Evidence(**base, kind=EvidenceKind.LEDGER_ENTRY, verified=True)
