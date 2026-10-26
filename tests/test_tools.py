import pytest
from pydantic import ValidationError

from finguard.domain.errors import BindingConflict, DuplicateTool, UnknownTool
from finguard.domain.models import TransactionStatus
from finguard.tools.registry import (
    AccessMode,
    AuthorizationRequirement,
    Idempotency,
    SideEffect,
    TimeoutPolicy,
    ToolContract,
    ToolRegistry,
)
from finguard.tools.transfer import (
    LookupObservation,
    TransferLookupResult,
    TransferRequest,
    transfer_registry,
)


def test_registered_transfer_validates_structure_and_declares_control_requirements(scenario):
    registry = transfer_registry()
    task, _, operation = scenario.to_domain()
    payload = dict(
        subject_ref=task.subject_ref,
        task_ref=task.task_ref,
        operation_ref=operation.operation_ref,
        parameters=operation.parameters.model_dump(),
    )
    request = registry.validate_request("transfer.create", payload)
    assert isinstance(request, TransferRequest)
    contract = registry.get("transfer.create")
    assert contract.authorization is AuthorizationRequirement.SINGLE_OPERATION
    assert contract.timeout_policy is TimeoutPolicy.RECONCILE
    assert contract.status_query_action == "transfer.get"
    assert registry.get("transfer.get").access_mode is AccessMode.READ


def test_external_fields_cannot_inject_authorization(scenario):
    task, _, operation = scenario.to_domain()
    payload = dict(
        subject_ref=task.subject_ref,
        task_ref=task.task_ref,
        operation_ref=operation.operation_ref,
        parameters=operation.parameters.model_dump(),
        authorized=True,
    )
    with pytest.raises(ValidationError):
        transfer_registry().validate_request("transfer.create", payload)


def test_unknown_and_duplicate_tools_are_rejected():
    registry = transfer_registry()
    with pytest.raises(UnknownTool):
        registry.get("transfer.force")
    with pytest.raises(DuplicateTool):
        registry.register(registry.get("transfer.create"))


@pytest.mark.parametrize(
    "changes",
    [
        {"authorization": AuthorizationRequirement.DATA_ACCESS},
        {"timeout_policy": TimeoutPolicy.RECONCILE, "status_query_action": None},
        {"timeout_policy": TimeoutPolicy.SAFE_TO_RETRY, "idempotency": Idempotency.NONE},
        {"access_mode": AccessMode.READ, "side_effect": SideEffect.FUNDS_MOVEMENT},
        {"data_scopes": ()},
    ],
)
def test_inconsistent_tool_capabilities_are_rejected(changes):
    data = transfer_registry().get("transfer.create").model_dump()
    with pytest.raises(ValidationError):
        ToolContract.model_validate({**data, **changes})


def test_query_links_must_resolve_to_registered_read_tools():
    registry = ToolRegistry()
    registry.register(transfer_registry().get("transfer.create"))
    with pytest.raises(UnknownTool):
        registry.validate_links()
    invalid = transfer_registry().get("transfer.create").model_dump()
    invalid["status_query_action"] = "transfer.create"
    registry = ToolRegistry()
    registry.register(ToolContract.model_validate(invalid))
    with pytest.raises(BindingConflict):
        registry.validate_links()


def test_json_response_accepts_known_status_and_rejects_unknown_fields_and_states():
    registry = transfer_registry()
    response = registry.validate_response_json(
        "transfer.create",
        ('{"operation_ref":"operation-1","transaction_ref":"transaction-1","status":"SUCCEEDED"}'),
    )
    assert response.status is TransactionStatus.SUCCEEDED
    for payload in (
        '{"operation_ref":"operation-1","transaction_ref":"transaction-1","status":"OK"}',
        '{"operation_ref":"operation-1","transaction_ref":"transaction-1",'
        '"status":"SUCCEEDED","authorized":true}',
    ):
        with pytest.raises(ValidationError):
            registry.validate_response_json("transfer.create", payload)


@pytest.mark.parametrize("observation", [LookupObservation.NOT_FOUND, LookupObservation.UNKNOWN])
def test_query_distinguishes_absent_record_from_uncertain_result(observation):
    result = TransferLookupResult(operation_ref="operation-1", observation=observation)
    restored = transfer_registry().validate_response_json("transfer.get", result.model_dump_json())
    assert restored.status is None
    assert restored.transaction_ref is None


@pytest.mark.parametrize("observation", list(LookupObservation))
def test_query_cannot_mislabel_incomplete_or_contradictory_results(observation):
    payload = dict(operation_ref="operation-1", observation=observation)
    if observation is not LookupObservation.FOUND:
        payload.update(transaction_ref="transaction-1", status=TransactionStatus.SUCCEEDED)
    with pytest.raises(ValidationError):
        TransferLookupResult.model_validate(payload)


def test_response_cannot_claim_success_without_transaction_reference():
    with pytest.raises(ValidationError):
        transfer_registry().validate_response(
            "transfer.create",
            {
                "operation_ref": "operation-1",
                "status": "SUCCEEDED",
            },
        )
