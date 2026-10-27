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
    exported = client.get(f"/v1/export/{reference}").json()
    assert exported["contains_credentials"] is None
    assert exported["redaction"]["applied"]


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


def test_followup_requeries_balance_and_is_bound_to_owned_task(client):
    initial = client.post(
        "/v1/tasks", json={"client_request_ref": "dialog-balance", "user_request": "查一下余额"}
    ).json()
    assert client.post(f"/v1/tasks/{initial['task_ref']}/run").json()["verified_success"]
    transfer = client.post(
        "/v1/tasks",
        json={
            "client_request_ref": "dialog-transfer",
            "user_request": "转给小王 100 块",
            "context_task_ref": initial["task_ref"],
        },
    ).json()
    operation = transfer["operations"][0]
    assert (
        client.post(f"/v1/tasks/{transfer['task_ref']}/run").json()["status"] == "WAITING_APPROVAL"
    )
    assert (
        client.post(
            f"/v1/operations/{operation['operation_ref']}/approvals",
            json={"expected_digest": operation["digest"]},
        ).status_code
        == 200
    )
    assert client.post(f"/v1/tasks/{transfer['task_ref']}/run").json()["verified_success"]
    followup = client.post(
        "/v1/tasks",
        json={
            "client_request_ref": "dialog-followup",
            "user_request": "还剩多少",
            "context_task_ref": transfer["task_ref"],
        },
    ).json()
    assert followup["context_task_ref"] == transfer["task_ref"]
    result = client.post(f"/v1/tasks/{followup['task_ref']}/run").json()
    assert result["verified_success"]
    assert result["operations"][0]["result"]["balance_minor"] == 140000
    backend = client.app.state.backend
    other = backend.security.issue_token("user-demo-002")
    assert (
        client.post(
            "/v1/tasks",
            headers={"Authorization": "Bearer " + other},
            json={
                "client_request_ref": "cross-context",
                "user_request": "还剩多少",
                "context_task_ref": transfer["task_ref"],
            },
        ).status_code
        == 404
    )


def test_partial_intent_and_ambiguous_followup_do_not_create_operations(client):
    for i, text in enumerate(("给小王转100元，再修改密码", "还剩多少", "再给他转一样的金额")):
        task = client.post(
            "/v1/tasks", json={"client_request_ref": f"clarify-{i}", "user_request": text}
        ).json()
        assert task["status"] == "WAITING_CLARIFICATION" and not task["operations"]
    assert client.get("/v1/accounts").json()[0]["balance_minor"] == 150000


def test_task_storage_and_export_apply_redaction(client):
    body = {
        "client_request_ref": "private-input",
        "user_request": "卡号6222020200111234 api_key=private-demo-value",
        "steps": [
            {
                "step_ref": "balance",
                "action": "accounts.balance",
                "arguments": {"account_ref": "account-demo-001"},
            }
        ],
    }
    task = client.post("/v1/tasks", json=body).json()
    with client.app.state.backend.db.transaction() as c:
        stored = c.execute(
            "SELECT user_request FROM tasks WHERE task_ref=%s", (task["task_ref"],)
        ).fetchone()["user_request"]
    assert "6222020200111234" not in stored and "private-demo-value" not in stored
    exported = client.get(f"/v1/export/{task['task_ref']}")
    assert "private-demo-value" not in exported.text and "6222020200111234" not in exported.text
    assert exported.json()["contains_credentials"] is None
    assert not exported.json()["redaction"]["complete_detection_guaranteed"]


def test_repeated_transfers_are_individually_approved(client):
    task = client.post(
        "/v1/tasks",
        json={
            "client_request_ref": "two-payments",
            "user_request": "给小王转100元，再给小王转200元",
        },
    ).json()
    assert len(task["operations"]) == 2
    for op in task["operations"]:
        assert (
            client.post(
                f"/v1/operations/{op['operation_ref']}/approvals",
                json={"expected_digest": op["digest"]},
            ).status_code
            == 200
        )
    result = client.post(f"/v1/tasks/{task['task_ref']}/run").json()
    assert result["verified_success"] and len(result["evidence"]) >= 2
    assert client.get("/v1/accounts").json()[0]["balance_minor"] == 120000
    assert client.post(f"/v1/tasks/{task['task_ref']}/run").json()["verified_success"]
    assert client.get("/v1/accounts").json()[0]["balance_minor"] == 120000


def test_high_value_operation_is_blocked_until_step_up_exists(client):
    task = client.post(
        "/v1/tasks", json={"client_request_ref": "high-value", "user_request": "给小王转1001元"}
    ).json()
    op = task["operations"][0]
    preview = client.get(f"/v1/operations/{op['operation_ref']}/preview").json()
    assert preview["authentication_policy"]["step_up_required"]
    assert not preview["authentication_policy"]["step_up_available"]
    response = client.post(
        f"/v1/operations/{op['operation_ref']}/approvals", json={"expected_digest": op["digest"]}
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "STEP_UP_REQUIRED"
    result = client.post(f"/v1/tasks/{task['task_ref']}/run").json()
    assert result["status"] == "WAITING_APPROVAL" and not result["verified_success"]
    assert result["operations"][0]["error_code"] == "STEP_UP_REQUIRED"
    assert client.get("/v1/accounts").json()[0]["balance_minor"] == 150000


def test_new_policy_cannot_be_bypassed_by_an_existing_approval(client):
    task = client.post(
        "/v1/tasks", json={"client_request_ref": "policy-update", "user_request": "给小王转100元"}
    ).json()
    op = task["operations"][0]
    assert (
        client.post(
            f"/v1/operations/{op['operation_ref']}/approvals",
            json={"expected_digest": op["digest"]},
        ).status_code
        == 200
    )
    client.app.state.backend.step_up_threshold_minor = 5000
    result = client.post(f"/v1/tasks/{task['task_ref']}/run").json()
    assert result["status"] == "WAITING_APPROVAL"
    assert result["operations"][0]["error_code"] == "STEP_UP_REQUIRED"
    assert client.get("/v1/accounts").json()[0]["balance_minor"] == 150000


def test_step_up_cannot_be_bypassed_by_delegation(client):
    task = client.post(
        "/v1/tasks",
        json={"client_request_ref": "high-delegation", "user_request": "给小王转1001元"},
    ).json()
    op = task["operations"][0]
    grant = client.post(
        "/v1/delegations",
        json={
            "source_account_ref": "account-demo-001",
            "recipient_refs": ["recipient-demo-wang"],
            "per_operation_limit_minor": 110000,
            "total_limit_minor": 110000,
            "maximum_operations": 2,
            "period_seconds": 60,
            "ttl_seconds": 300,
        },
    ).json()
    assert (
        client.post(
            f"/v1/operations/{op['operation_ref']}/delegation",
            json={"delegation_ref": grant["delegation_ref"]},
        ).status_code
        == 200
    )
    result = client.post(f"/v1/tasks/{task['task_ref']}/run").json()
    assert result["status"] == "WAITING_APPROVAL"
    assert result["operations"][0]["error_code"] == "STEP_UP_REQUIRED"
    assert client.get("/v1/delegations").json()[0]["used_minor"] == 0
    assert client.get("/v1/accounts").json()[0]["balance_minor"] == 150000
