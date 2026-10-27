from types import SimpleNamespace

import httpx
import pytest
from finguard_bank_contracts.connections import (
    BankCapabilities,
    BankConnection,
    BankOperationResult,
)
from finguard_simulator.bank import SimulatedBank
from pydantic import ValidationError

from finguard.backend.bank import HttpBank, ResultUnknown
from finguard.backend.config import Settings
from finguard.backend.errors import ServiceError
from finguard.integrations.banks import BankProvider, BankRegistry, configured_bank


def connection(**changes):
    return BankConnection(
        **{
            "connection_ref": "bank-1",
            "owner_ref": "owner",
            "provider": "custom-bank",
            "environment": "external",
            **changes,
        }
    )


def test_missing_provider_does_not_fall_back_to_simulator():
    settings = Settings(database_url="postgresql://unused", bank_provider="not-installed")
    with pytest.raises(ServiceError) as error:
        configured_bank(settings)
    assert error.value.code == "BANK_PROVIDER_NOT_REGISTERED"
    with pytest.raises(ServiceError) as error:
        configured_bank(
            settings.model_copy(
                update={"bank_provider": "simulated-bank", "bank_environment": "external"}
            )
        )
    assert error.value.code == "BANK_ENVIRONMENT_UNSUPPORTED"


def test_host_registration_conflicts_and_capability_mismatch_close_client():
    registry = BankRegistry()
    closed = []
    fake = SimpleNamespace(
        get_capabilities=lambda: BankCapabilities(provider="custom-bank", environment="external"),
        close=lambda: closed.append(True),
    )
    provider = BankProvider("custom-bank", ("external",), lambda connection, credential: fake)
    registry.register(provider)
    with pytest.raises(ServiceError):
        registry.register(provider)
    with pytest.raises(ServiceError) as error:
        registry.build(connection(), required_actions=("transfer.create",))
    assert error.value.code == "BANK_CAPABILITY_UNSUPPORTED" and closed == [True]
    fake.get_capabilities = lambda: BankCapabilities(provider="other-bank", environment="external")
    with pytest.raises(ServiceError) as error:
        registry.build(connection(), required_actions=("transfer.create",))
    assert error.value.code == "BANK_CAPABILITIES_MISMATCH"


def test_simulator_declares_fence_and_minimum_balance_as_its_own_capabilities():
    capabilities = SimulatedBank(None).get_capabilities()
    assert capabilities.environment == "simulation" and capabilities.fence_absent_operation
    capabilities.require_action("transfer.create")
    assert not BankCapabilities(
        provider="future-bank", environment="external"
    ).atomic_minimum_balance


def test_http_capabilities_use_the_authenticated_connection():
    capabilities = SimulatedBank(None).get_capabilities()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=capabilities.model_dump(mode="json"))

    with httpx.Client(
        base_url="https://bank.example",
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(respond),
    ) as client:
        result = HttpBank("https://bank.example", "unused", client=client).get_capabilities()
        assert result == capabilities
        assert (
            requests[0].url.path == "/v1/capabilities"
            and requests[0].headers["Authorization"] == "Bearer test-key"
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "SUCCEEDED"},
        {"status": "SUCCEEDED", "transaction_ref": "bank-record", "error_code": "FAILED"},
        {"status": "REQUIRES_USER_ACTION"},
        {"status": "PENDING", "user_action_ref": "confirm"},
    ],
)
def test_bank_result_cannot_claim_inconsistent_success(payload):
    with pytest.raises(ValidationError):
        BankOperationResult(operation_ref="operation-1", **payload)


def test_external_numeric_records_are_kept_separate_from_internal_receipt_identity():
    result = BankOperationResult(
        operation_ref="operation-1",
        transaction_ref="operation-1",
        bank_record_ref="202610270001",
        status="SUCCEEDED",
    )
    receipt = result.to_backend_receipt()
    assert (
        receipt["transaction_ref"] == "operation-1" and receipt["bank_record_ref"] == "202610270001"
    )
    with pytest.raises(ResultUnknown):
        BankOperationResult(operation_ref="operation-1", status="UNKNOWN").to_backend_receipt()
    with pytest.raises(ServiceError) as error:
        BankOperationResult(
            operation_ref="operation-1", status="REQUIRES_USER_ACTION", user_action_ref="confirm"
        ).to_backend_receipt()
    assert error.value.code == "BANK_USER_ACTION_UNSUPPORTED"
    with pytest.raises(ValidationError):
        BankOperationResult(
            operation_ref="operation-1", status="PENDING", data={"status": "SUCCEEDED"}
        )
