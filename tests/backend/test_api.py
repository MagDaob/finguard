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


def chat_send(client, chat, message, content, sequence):
    return client.post(
        f"/v1/conversations/{chat}/messages",
        json={"message_ref": message, "content": content, "expected_sequence": sequence},
    )


def test_conversation_query_and_persistent_context(client):
    response = chat_send(client, "chat-query", "message-one", "查询当前余额", 0)
    assert response.status_code == 200, response.text
    turn = response.json()["turns"][0]
    task = client.get("/v1/tasks/" + turn["task_ref"]).json()
    assert task["verified_success"] and task["status"] == "COMPLETED"
    assert (
        chat_send(client, "chat-query", "message-one", "查询当前余额", 0).json() == response.json()
    )
    assert chat_send(client, "chat-query", "message-one", "换个目标", 0).status_code == 409
    assert chat_send(client, "chat-query", "message-two", "还剩多少", 0).status_code == 409
    follow = chat_send(client, "chat-query", "message-two", "还剩多少", 1)
    assert follow.status_code == 200, follow.text
    follow_task = client.get("/v1/tasks/" + follow.json()["turns"][-1]["task_ref"]).json()
    assert follow_task["context_task_ref"] == task["task_ref"] and follow_task["verified_success"]
    assert len(client.get("/v1/conversations/chat-query").json()["turns"]) == 2
    assert client.get("/v1/conversations").json()[0]["title"] == "查询当前余额"


def test_conversation_revises_unsubmitted_transfer_without_authorizing(client):
    created = chat_send(client, "chat-transfer", "message-one", "给小王转300元，至少保留1000元", 0)
    assert created.status_code == 200, created.text
    reference = created.json()["turns"][0]["task_ref"]
    task = client.get("/v1/tasks/" + reference).json()
    assert task["status"] == "WAITING_APPROVAL"
    original_op = task["operations"][0]
    original_preview = client.get(f"/v1/operations/{original_op['operation_ref']}/preview").json()
    assert (
        client.post(
            f"/v1/operations/{original_op['operation_ref']}/approvals",
            json={"expected_digest": original_preview["digest"]},
        ).status_code
        == 200
    )
    revised = chat_send(client, "chat-transfer", "message-two", "改成200元", 1)
    assert revised.status_code == 200, revised.text
    assert revised.json()["turns"][-1]["task_ref"] == reference
    task = client.get("/v1/tasks/" + reference).json()
    current = {s["operation_ref"] for s in task["plans"][-1]["steps"]}
    op = next(o for o in task["operations"] if o["operation_ref"] in current)
    assert op["arguments"]["amount_minor"] == 20000
    assert op["arguments"]["minimum_remaining_balance_minor"] == 100000
    assert task["current_version"] == 2 and op["status"] == "WAITING_APPROVAL"
    assert (
        client.get(f"/v1/operations/{original_op['operation_ref']}/approvals").json()[0]["status"]
        == "REVOKED"
    )
    assert client.get(f"/v1/operations/{op['operation_ref']}/approvals").json() == []
    assert chat_send(client, "chat-transfer", "message-three", "好", 2).status_code == 200
    assert client.get(f"/v1/operations/{op['operation_ref']}/approvals").json() == []
    assert chat_send(client, "chat-transfer", "message-two", "改成200元", 1).status_code == 200
    assert client.get("/v1/tasks/" + reference).json()["current_version"] == 2
    preview = client.get(f"/v1/operations/{op['operation_ref']}/preview").json()
    assert (
        client.post(
            f"/v1/operations/{op['operation_ref']}/approvals",
            json={"expected_digest": preview["digest"]},
        ).status_code
        == 200
    )
    assert client.post("/v1/tasks/" + reference + "/run").json()["verified_success"]
    assert chat_send(client, "chat-transfer", "message-four", "改成100元", 3).status_code == 409


def test_conversation_replan_recovers_after_process_error(client, monkeypatch):
    created = chat_send(
        client, "chat-recover", "message-one", "给小王转300元，至少保留1000元", 0
    ).json()
    reference = created["turns"][0]["task_ref"]
    backend = client.app.state.backend
    original = backend.run

    def crash(*args):
        raise RuntimeError("simulated crash after plan installation")

    monkeypatch.setattr(backend, "run", crash)
    with pytest.raises(RuntimeError):
        chat_send(client, "chat-recover", "message-two", "改成200元", 1)
    monkeypatch.setattr(backend, "run", original)
    assert chat_send(client, "chat-recover", "message-three", "查询余额", 2).status_code == 409
    recovered = chat_send(client, "chat-recover", "message-two", "改成200元", 1)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["turns"][-1]["ready"]
    assert client.get("/v1/tasks/" + reference).json()["current_version"] == 2


def test_conversation_ownership_and_message_redaction(client):
    first = chat_send(client, "chat-private", "message-one", "查询余额 token:private-value", 0)
    assert first.status_code == 200, first.text
    assert "private-value" not in first.text
    backend = client.app.state.backend
    backend.security.provision("other-user", "Other")
    token = backend.security.issue_token("other-user")
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/v1/conversations/chat-private", headers=headers).status_code == 404
    assert (
        client.post(
            "/v1/conversations/chat-private/messages",
            headers=headers,
            json={"message_ref": "evil", "content": "查询余额", "expected_sequence": 1},
        ).status_code
        == 404
    )
    assert client.get("/v1/conversations", headers=headers).json() == []


def test_conversation_clarifies_amount_and_preserves_original_task(client):
    created = chat_send(
        client, "chat-clarify", "message-one", "给小王转账，至少保留1000元", 0
    ).json()
    reference = created["turns"][0]["task_ref"]
    assert client.get("/v1/tasks/" + reference).json()["status"] == "WAITING_CLARIFICATION"
    clarified = chat_send(client, "chat-clarify", "message-two", "300元", 1)
    assert clarified.status_code == 200, clarified.text
    assert clarified.json()["turns"][-1]["task_ref"] == reference
    task = client.get("/v1/tasks/" + reference).json()
    assert task["status"] == "WAITING_APPROVAL"
    assert task["operations"][0]["arguments"]["amount_minor"] == 30000
    assert task["operations"][0]["arguments"]["minimum_remaining_balance_minor"] == 100000


def test_conversation_serialization_and_read_scope(client):
    backend = client.app.state.backend
    with backend.db.task_lock("conversation:chat-locked") as acquired:
        assert acquired
        assert chat_send(client, "chat-locked", "message-one", "查询余额", 0).status_code == 409
    assert chat_send(client, "chat-locked", "message-one", "查询余额", 0).status_code == 200
    token = backend.security.issue_token("user-demo-001", ("tasks.write",))
    assert (
        client.post(
            "/v1/conversations/chat-locked/messages",
            headers={"Authorization": "Bearer " + token},
            json={"message_ref": "message-two", "content": "查询余额", "expected_sequence": 1},
        ).status_code
        == 403
    )
    assert client.get("/v1/conversations?before=chat-locked").json() == []


def test_wallet_connect_import_sync_and_disconnect(client):
    banks = client.get("/v1/wallet/banks").json()
    assert [b["bank_ref"] for b in banks] == ["simulated-bank"]
    assert client.get("/v1/wallet").json()["assets"] == []
    request = {"request_ref": "wallet-one", "bank_ref": "simulated-bank"}
    connected = client.post("/v1/wallet/connections", json=request)
    assert connected.status_code == 200, connected.text
    ref = connected.json()["connection_ref"]
    assert client.post("/v1/wallet/connections", json=request).json()["connection_ref"] == ref
    assert (
        client.post(
            "/v1/wallet/connections", json={**request, "request_ref": "wallet-two"}
        ).status_code
        == 409
    )
    available = client.get(f"/v1/wallet/connections/{ref}/assets").json()
    assert {a["kind"] for a in available} == {"card", "account"}
    body = {"asset_refs": [a["asset_ref"] for a in available]}
    imported = client.post(f"/v1/wallet/connections/{ref}/import", json=body)
    assert imported.status_code == 200, imported.text
    assets = imported.json()["assets"]
    assert len(assets) == len(available)
    assert client.post(f"/v1/wallet/connections/{ref}/import", json=body).json()["assets"][0][
        "asset_ref"
    ] in {a["asset_ref"] for a in assets}
    assert len(client.get("/v1/wallet").json()["assets"]) == len(available)
    assert client.post(f"/v1/wallet/connections/{ref}/sync").status_code == 200
    assert (
        client.post(
            f"/v1/wallet/connections/{ref}/import", json={"asset_refs": ["account:not-owned"]}
        ).status_code
        == 422
    )
    assert client.post(f"/v1/wallet/connections/{ref}/disconnect").json()["assets"] == []
    assert client.post(f"/v1/wallet/connections/{ref}/disconnect").status_code == 200
    assert client.get(f"/v1/wallet/connections/{ref}/assets").status_code == 409
    assert client.post("/v1/wallet/connections", json=request).status_code == 409


def test_wallet_rejects_unconfigured_bank_and_cross_owner(client):
    assert (
        client.post(
            "/v1/wallet/connections", json={"request_ref": "bad", "bank_ref": "cmb"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/wallet/connections",
            json={
                "request_ref": "bad",
                "bank_ref": "simulated-bank",
                "url": "https://evil.example",
            },
        ).status_code
        == 422
    )
    ref = client.post(
        "/v1/wallet/connections",
        json={"request_ref": "wallet-private", "bank_ref": "simulated-bank"},
    ).json()["connection_ref"]
    security = client.app.state.backend.security
    security.provision("wallet-other", "Other")
    headers = {"Authorization": "Bearer " + security.issue_token("wallet-other")}
    assert client.get(f"/v1/wallet/connections/{ref}/assets", headers=headers).status_code == 404
    assert client.post(f"/v1/wallet/connections/{ref}/sync", headers=headers).status_code == 404
    assert (
        client.post(f"/v1/wallet/connections/{ref}/disconnect", headers=headers).status_code == 404
    )
    assert client.get("/v1/wallet", headers=headers).json()["connections"] == []
    headers = {
        "Authorization": "Bearer "
        + security.issue_token("user-demo-001", ("accounts.read", "services.read"))
    }
    assert (
        client.post(
            "/v1/wallet/connections",
            json={"request_ref": "no-write", "bank_ref": "simulated-bank"},
            headers=headers,
        ).status_code
        == 403
    )


def test_wallet_bank_failure_does_not_create_connection(client, monkeypatch):
    from finguard.backend.errors import ServiceError

    def unavailable(*args):
        raise ServiceError("BANK_UNAVAILABLE", "Bank unavailable", 503)

    monkeypatch.setattr(client.app.state.backend.bank, "list_accounts", unavailable)
    assert (
        client.post(
            "/v1/wallet/connections",
            json={"request_ref": "wallet-failed", "bank_ref": "simulated-bank"},
        ).status_code
        == 503
    )
    assert client.get("/v1/wallet").json()["connections"] == []
