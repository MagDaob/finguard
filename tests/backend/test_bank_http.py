"""接口边界、服务认证和未知结果的回归检查。"""

from importlib.metadata import requires
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from finguard_bank_contracts.errors import ResultUnknown
from finguard_simulator.api import create_app as simulator_app
from finguard_simulator.config import Settings as SimulatorSettings

from finguard.backend.bank import HttpBank
from finguard.backend.errors import ServiceError


def test_submission_timeout_is_unknown_and_never_retried():
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("private transport details", request=request)

    with httpx.Client(
        base_url="https://bank.example", transport=httpx.MockTransport(timeout)
    ) as client:
        bank = HttpBank("https://bank.example", "unused", client=client)
        with pytest.raises(ResultUnknown):
            bank.execute("user", "operation", "accounts.balance", {})
        assert len(requests) == 1
        with pytest.raises(ServiceError) as error:
            bank.list_accounts("user")
        assert error.value.code == "BANK_UNAVAILABLE"
        assert "private" not in str(error.value)


@pytest.mark.parametrize("body", [{}, [], {"error": {}}, {"result": [None]}])
def test_invalid_bank_evidence_never_becomes_success(body):
    with httpx.Client(
        base_url="https://bank.example",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    ) as client:
        with pytest.raises(ServiceError):
            HttpBank("https://bank.example", "unused", client=client).verify("user", "op", "f" * 64)


def test_simulator_authenticates_and_has_no_fault_or_admin_endpoint(monkeypatch, transfer_args):
    calls = []
    fake = SimpleNamespace(list_accounts=lambda subject: calls.append(subject) or [])
    monkeypatch.setattr("finguard_simulator.api.SimulatedBank", lambda database: fake)
    key = "private-test-service-key-" + "a" * 32
    settings = SimulatorSettings(database_url="postgresql://unused", api_key=key)
    # 不启动数据库生命周期，只检查 HTTP 请求边界。
    client = TestClient(simulator_app(settings))
    try:
        payload = {"subject": "user-demo-001"}
        assert client.post("/v1/bank/list_accounts", json=payload).status_code == 401
        assert not calls
        client.headers["Authorization"] = f"Bearer {key}"
        assert client.post("/v1/bank/list_accounts", json=payload).json() == {"result": []}
        assert calls == ["user-demo-001"]
        assert client.post("/v1/bank/inject_fault", json=payload).status_code == 422
        assert (
            client.post("/v1/bank/list_accounts", json={**payload, "api_key": key}).status_code
            == 422
        )
        response = client.post(
            "/v1/bank/execute",
            json={
                **payload,
                "operation_ref": "operation-1",
                "action": "transfer.create",
                "arguments": {**transfer_args, "amount_minor": -1},
            },
        )
        assert response.status_code == 422 and key not in response.text
    finally:
        client.close()


def test_production_dependencies_do_not_install_the_other_service():
    assert all(
        not requirement.startswith("finguard-bank-simulator")
        for requirement in requires("finguard")
    )
    assert all(
        not requirement.startswith("finguard==")
        for requirement in requires("finguard-bank-simulator")
    )


def test_malformed_submission_response_is_unknown():
    with httpx.Client(
        base_url="https://bank.example",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"result": []})),
    ) as client:
        with pytest.raises(ResultUnknown):
            HttpBank("https://bank.example", "unused", client=client).execute(
                "user", "operation", "accounts.balance", {}
            )
