import pytest
from fastapi.testclient import TestClient

from finguard.backend.api import create_app
from finguard.backend.config import Settings

pytestmark = pytest.mark.postgres


@pytest.fixture
def client(database, bank):
    settings = Settings(
        database_url=database.dsn,
        database_schema=database.schema,
        worker_enabled=False,
        callback_secret="test-only-secret",
    )
    with TestClient(create_app(settings, bank=bank)) as client:
        backend = client.app.state.backend
        token = backend.security.issue_token("user-demo-001")
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


def test_health_identity_and_documented_api(client):
    assert client.get("/health/ready").status_code == 200
    assert client.get("/v1/me").json()["subject_ref"] == "user-demo-001"
    assert "/v1/tasks" in client.get("/openapi.json").json()["paths"]
    assert client.get("/v1/accounts").json()[0]["balance_minor"] == 150000


def test_bank_unavailable_fails_readiness_but_keeps_liveness(client, monkeypatch):
    from finguard.backend.errors import ServiceError

    def unavailable():
        raise ServiceError("BANK_UNAVAILABLE", "银行服务暂时不可用。", 503)

    monkeypatch.setattr(client.app.state.backend.bank, "ready", unavailable, raising=False)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_http_transfer_full_lifecycle(client):
    response = client.post(
        "/v1/tasks",
        json={
            "client_request_ref": "http-transfer",
            "user_request": "给小王转300元，至少保留1000元",
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    reference = task["task_ref"]
    assert client.post(f"/v1/tasks/{reference}/run").json()["status"] == "WAITING_APPROVAL"
    operation = task["operations"][0]
    preview = client.get(f"/v1/operations/{operation['operation_ref']}/preview").json()
    approval = client.post(
        f"/v1/operations/{operation['operation_ref']}/approvals",
        json={"expected_digest": preview["digest"]},
    )
    assert approval.status_code == 200, approval.text
    result = client.post(f"/v1/tasks/{reference}/run").json()
    assert result["verified_success"] and result["status"] == "COMPLETED"
    assert client.get(f"/v1/tasks/{reference}/trace").json()
    assert (
        client.get(f"/v1/operations/{operation['operation_ref']}/approvals").json()[0]["status"]
        == "CONSUMED"
    )
    assert client.get("/v1/accounts/account-demo-001/ledger").status_code == 200
    assert client.get("/v1/accounts/account-demo-002/ledger").status_code == 404
    assert client.get("/v1/delegations").json() == []
    assert client.get(f"/v1/export/{reference}").json()["contains_credentials"] is False


def test_unauthenticated_and_revoked_tokens_are_rejected(client):
    assert (
        client.get("/v1/accounts", headers={"Authorization": "Bearer invalid"}).status_code == 401
    )
    assert client.post("/v1/tokens/revoke-current").status_code == 200
    assert client.get("/v1/accounts").status_code == 401


def test_strict_amount_and_extra_authorization_fields_cannot_be_injected(client, transfer_args):
    body = {
        "client_request_ref": "bad-input",
        "user_request": "transfer",
        "steps": [
            {
                "step_ref": "transfer",
                "action": "transfer.create",
                "arguments": {**transfer_args, "amount_minor": True},
            }
        ],
    }
    assert client.post("/v1/tasks", json=body).status_code == 422
    body["steps"][0]["arguments"] = transfer_args
    body["authorized"] = True
    assert client.post("/v1/tasks", json=body).status_code == 422


def test_oversized_body_is_rejected_before_parsing(client):
    response = client.post(
        "/v1/tasks", content=b"x" * 1048577, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_evaluation_requires_operator_and_explicit_enablement(client):
    assert client.post("/v1/evaluation/candidates", json={"prompt": "candidate"}).status_code == 403
    from finguard.backend.security import ADMIN_SCOPES

    token = client.app.state.backend.security.issue_token("operator-demo", ADMIN_SCOPES)
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.post("/v1/evaluation/candidates", json={"prompt": "candidate"}).status_code == 503
