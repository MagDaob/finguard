import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from finguard.backend.bank import WorkerCrash
from finguard.backend.catalog import (
    ApprovalRequest,
    DelegationRequest,
    ReplanRequest,
    Step,
    TaskCreate,
)
from finguard.backend.errors import ServiceError
from finguard.backend.service import Backend

pytestmark = pytest.mark.postgres


def make_task(backend, actor, arguments, key="request-1", steps=None):
    return backend.create_task(
        actor,
        TaskCreate(
            client_request_ref=key,
            user_request="给小王转 300 元，至少保留 1000 元。",
            steps=steps
            or (Step(step_ref="transfer", action="transfer.create", arguments=arguments),),
        ),
    )


def approve_all(backend, actor, task):
    for operation in task["operations"]:
        if operation["action"] not in {"accounts.balance", "bills.analyze"} and operation[
            "status"
        ] in {"PREPARING", "WAITING_APPROVAL"}:
            backend.approve(
                actor,
                operation["operation_ref"],
                ApprovalRequest(expected_digest=operation["digest"]),
            )


def test_task_request_idempotency_and_conflicting_content(backend, actor, transfer_args):
    first = make_task(backend, actor, transfer_args)
    assert make_task(backend, actor, transfer_args)["task_ref"] == first["task_ref"]
    with pytest.raises(ServiceError, match="其他任务"):
        make_task(backend, actor, {**transfer_args, "amount_minor": 1})


def test_no_authorization_no_write_then_exact_approval_completes(backend, actor, transfer_args):
    task = make_task(backend, actor, transfer_args)
    result = backend.run(actor, task["task_ref"])
    assert result["status"] == "WAITING_APPROVAL" and not result["verified_success"]
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 150000
    approve_all(backend, actor, result)
    result = backend.run(actor, task["task_ref"])
    assert (
        result["status"] == "COMPLETED"
        and result["verified_success"]
        and len(result["evidence"]) == 3
    )
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 120000


@pytest.mark.parametrize("mode", ["response_lost", "malformed_response", "lookup_unknown"])
def test_uncertain_response_is_not_reported_as_success_until_reconciled(
    backend, database, actor, transfer_args, mode
):
    task = make_task(backend, actor, transfer_args)
    approve_all(backend, actor, task)
    backend.bank.inject_fault(task["operations"][0]["operation_ref"], mode)
    result = backend.run(actor, task["task_ref"])
    assert not result["verified_success"] and result["operations"][0]["status"] == "UNKNOWN"
    Backend(database, bank=backend.bank).recover()
    result = backend.get_task(actor, task["task_ref"])
    assert result["verified_success"]
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 120000


@pytest.mark.parametrize(
    "mode,expected_balance,expected_status",
    [("crash_after_commit", 120000, "SUCCEEDED"), ("crash_before_bank", 150000, "FAILED")],
)
def test_process_loss_recovers_using_persisted_operation_identity(
    backend, database, actor, transfer_args, mode, expected_balance, expected_status
):
    task = make_task(backend, actor, transfer_args)
    approve_all(backend, actor, task)
    backend.bank.inject_fault(task["operations"][0]["operation_ref"], mode)
    with pytest.raises(WorkerCrash):
        backend.run(actor, task["task_ref"])
    Backend(database, bank=backend.bank).recover()
    assert backend.get_task(actor, task["task_ref"])["operations"][0]["status"] == expected_status
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == expected_balance


def test_known_unsent_request_is_fenced_and_fails_without_debit(backend, actor, transfer_args):
    task = make_task(backend, actor, transfer_args)
    approve_all(backend, actor, task)
    backend.bank.inject_fault(task["operations"][0]["operation_ref"], "before_send")
    result = backend.run(actor, task["task_ref"])
    assert result["operations"][0]["status"] == "FAILED" and not result["verified_success"]
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 150000


def test_balance_is_rechecked_after_confirmation(backend, database, actor, transfer_args):
    task = make_task(backend, actor, transfer_args)
    approve_all(backend, actor, task)
    with database.transaction() as c:
        c.execute(
            "UPDATE bank_accounts SET balance_minor=110000 WHERE account_ref='account-demo-001'"
        )
    result = backend.run(actor, task["task_ref"])
    assert result["operations"][0]["error_code"] == "INSUFFICIENT_FUNDS"
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 110000


def test_expired_and_revoked_authorization_cannot_commit(backend, database, actor, transfer_args):
    task = make_task(backend, actor, transfer_args)
    approval = backend.approve(
        actor,
        task["operations"][0]["operation_ref"],
        ApprovalRequest(expected_digest=task["operations"][0]["digest"]),
    )
    with database.transaction() as c:
        c.execute(
            "UPDATE approvals SET expires_at=now()-interval '1 second' WHERE approval_ref=%s",
            (approval["approval_ref"],),
        )
    assert backend.run(actor, task["task_ref"])["status"] == "WAITING_APPROVAL"
    approval = backend.approve(
        actor,
        task["operations"][0]["operation_ref"],
        ApprovalRequest(expected_digest=task["operations"][0]["digest"]),
    )
    backend.revoke_approval(actor, approval["approval_ref"])
    assert backend.run(actor, task["task_ref"])["status"] == "WAITING_APPROVAL"
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 150000


def test_stale_preview_and_cross_user_queries_are_rejected(backend, actor, transfer_args):
    task = make_task(backend, actor, transfer_args)
    with pytest.raises(ServiceError):
        backend.approve(
            actor, task["operations"][0]["operation_ref"], ApprovalRequest(expected_digest="a" * 64)
        )
    other = backend.security.authenticate(backend.security.issue_token("user-demo-002"))
    with pytest.raises(ServiceError) as error:
        backend.get_task(other, task["task_ref"])
    assert error.value.status == 404


def test_same_task_concurrent_run_never_double_submits(backend, database, actor, transfer_args):
    task = make_task(backend, actor, transfer_args)
    approve_all(backend, actor, task)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: backend.run(actor, task["task_ref"]), range(8)))
    assert backend.get_task(actor, task["task_ref"])["verified_success"]
    with database.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM authorization_uses").fetchone()["n"] == 1


def test_partial_task_keeps_successful_facts_when_replanned(backend, actor, transfer_args):
    read = Step(
        step_ref="bills",
        action="bills.analyze",
        arguments={"account_ref": "account-demo-001", "days": 30},
    )
    write = Step(step_ref="transfer", action="transfer.create", arguments=transfer_args)
    task = make_task(backend, actor, transfer_args, steps=(read, write))
    partial = backend.run(actor, task["task_ref"])
    assert partial["status"] == "PARTIALLY_COMPLETED"
    old_read = next(
        operation for operation in partial["operations"] if operation["step_ref"] == "bills"
    )
    changed = Step(
        step_ref="transfer",
        action="transfer.create",
        arguments={**transfer_args, "amount_minor": 20000},
    )
    replanned = backend.replan(
        actor, task["task_ref"], ReplanRequest(expected_version=1, steps=(read, changed))
    )
    assert replanned["current_version"] == 2
    assert (
        next(
            operation
            for operation in replanned["operations"]
            if operation["operation_ref"] == old_read["operation_ref"]
        )["status"]
        == "SUCCEEDED"
    )
    current_ids = {step["operation_ref"] for step in replanned["plans"][-1]["steps"]}
    approve_all(
        backend,
        actor,
        {
            "operations": [
                operation
                for operation in replanned["operations"]
                if operation["operation_ref"] in current_ids
            ]
        },
    )
    assert backend.run(actor, task["task_ref"])["verified_success"]
    with pytest.raises(ServiceError):
        backend.replan(actor, task["task_ref"], ReplanRequest(expected_version=1, steps=(changed,)))


def test_stop_does_not_cancel_already_submitted_operation_but_stops_future_steps(
    backend, database, actor, transfer_args
):
    first = Step(step_ref="transfer", action="transfer.create", arguments=transfer_args)
    second = Step(
        step_ref="card",
        action="card.set_lock",
        arguments={"card_ref": "card-demo", "locked": True},
        depends_on=("transfer",),
    )
    task = make_task(backend, actor, transfer_args, steps=(first, second))
    approve_all(backend, actor, task)
    started, release = Event(), Event()
    original = backend.bank.execute

    def paused(*args):
        started.set()
        assert release.wait(10)
        return original(*args)

    backend.bank.execute = paused
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(backend.run, actor, task["task_ref"])
        assert started.wait(10)
        backend.stop(actor, task["task_ref"])
        release.set()
        result = future.result(timeout=20)
    assert {operation["step_ref"]: operation["status"] for operation in result["operations"]} == {
        "transfer": "SUCCEEDED",
        "card": "CANCELLED_PRE_SUBMIT",
    }
    assert backend.bank.list_services(actor.subject_ref, "card")[0]["state"] == "ACTIVE"


def test_delegation_budget_is_consumed_atomically_across_tasks(
    backend, database, actor, transfer_args
):
    specification = DelegationRequest(
        source_account_ref="account-demo-001",
        recipient_refs=("recipient-demo-wang",),
        per_operation_limit_minor=30000,
        total_limit_minor=30000,
        maximum_operations=10,
        period_seconds=60,
        ttl_seconds=300,
    )
    grant = backend.add_delegation(actor, specification)
    tasks = [
        make_task(
            backend,
            actor,
            {**transfer_args, "minimum_remaining_balance_minor": 0},
            key=f"request-{i}",
        )
        for i in range(2)
    ]
    for task in tasks:
        backend.bind_delegation(
            actor, task["operations"][0]["operation_ref"], grant["delegation_ref"]
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda task: backend.run(actor, task["task_ref"]), tasks))
    assert sorted(result["status"] for result in results) == ["COMPLETED", "WAITING_APPROVAL"]
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 120000
    backend.revoke_delegation(actor, grant["delegation_ref"])


def test_redemption_dependency_waits_for_actual_cash(backend, database, actor, transfer_args):
    redeem = Step(
        step_ref="redeem",
        action="product.redeem",
        arguments={
            "account_ref": "account-demo-001",
            "product_ref": "fund-demo",
            "amount_minor": 10000,
            "fee_minor": 0,
            "minimum_remaining_balance_minor": 0,
        },
    )
    transfer = Step(
        step_ref="transfer",
        action="transfer.create",
        arguments=transfer_args,
        depends_on=("redeem",),
        funds_available_after=("redeem",),
    )
    task = make_task(backend, actor, transfer_args, steps=(redeem, transfer))
    approve_all(backend, actor, task)
    pending = backend.run(actor, task["task_ref"])
    assert (
        next(
            operation for operation in pending["operations"] if operation["step_ref"] == "transfer"
        )["status"]
        == "AUTHORIZED"
    )
    with database.transaction() as c:
        c.execute(
            "UPDATE bank_commands SET settle_at=now()-interval '1 second' WHERE action='product.redeem'"
        )
    backend.recover()
    assert backend.get_task(actor, task["task_ref"])["verified_success"]
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 130000


def test_callbacks_are_signed_idempotent_and_do_not_trust_reported_status(
    backend, actor, transfer_args
):
    task = make_task(backend, actor, transfer_args)
    approve_all(backend, actor, task)
    operation_ref = task["operations"][0]["operation_ref"]
    backend.bank.inject_fault(operation_ref, "response_lost")
    backend.run(actor, task["task_ref"])
    body = json.dumps(
        {"event_ref": "callback-1", "operation_ref": operation_ref, "reported_status": "FAILED"}
    ).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        b"test-secret", str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    with pytest.raises(ServiceError):
        backend.callback("callback-1", operation_ref, timestamp, body, "bad", "test-secret")
    assert not backend.callback(
        "callback-1", operation_ref, timestamp, body, signature, "test-secret"
    )["duplicate"]
    assert backend.callback("callback-1", operation_ref, timestamp, body, signature, "test-secret")[
        "duplicate"
    ]
    assert backend.get_task(actor, task["task_ref"])["verified_success"]


def test_natural_language_clarifies_ambiguous_recipients(backend, database, actor):
    with database.transaction() as c:
        c.execute(
            "INSERT INTO bank_recipients VALUES ('another-wang','user-demo-001','小王','account-demo-002',true)"
        )
    task = backend.create_task(
        actor,
        TaskCreate(client_request_ref="natural-1", user_request="给小王转300元，至少保留1000元"),
    )
    assert task["status"] == "WAITING_CLARIFICATION" and not task["operations"]
    assert any(question["field"] == "recipient_ref" for question in task["questions"])


def test_missing_ledger_evidence_never_reports_success(backend, database, actor, transfer_args):
    task = make_task(backend, actor, transfer_args)
    approve_all(backend, actor, task)
    operation = task["operations"][0]
    backend.bank.inject_fault(operation["operation_ref"], "response_lost")
    backend.run(actor, task["task_ref"])
    with database.transaction() as c:
        c.execute("DELETE FROM bank_ledger WHERE operation_ref=%s", (operation["operation_ref"],))
    backend.recover()
    result = backend.get_task(actor, task["task_ref"])
    assert not result["verified_success"] and result["operations"][0]["status"] == "UNKNOWN"


def test_delegation_expiring_during_lock_wait_is_not_consumed(
    backend, database, actor, transfer_args
):
    grant = backend.add_delegation(
        actor,
        DelegationRequest(
            source_account_ref="account-demo-001",
            recipient_refs=("recipient-demo-wang",),
            per_operation_limit_minor=30000,
            total_limit_minor=90000,
            maximum_operations=3,
            period_seconds=60,
            ttl_seconds=60,
        ),
    )
    task = make_task(backend, actor, transfer_args)
    backend.bind_delegation(actor, task["operations"][0]["operation_ref"], grant["delegation_ref"])
    with database.transaction() as c:
        c.execute(
            "UPDATE delegations SET expires_at=clock_timestamp()+interval '1 second' WHERE delegation_ref=%s",
            (grant["delegation_ref"],),
        )
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.transaction() as c:
            c.execute(
                "SELECT 1 FROM delegations WHERE delegation_ref=%s FOR UPDATE",
                (grant["delegation_ref"],),
            )
            future = executor.submit(backend.run, actor, task["task_ref"])
            Event().wait(1.5)
        result = future.result(timeout=20)
    assert result["status"] == "WAITING_APPROVAL"
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 150000


def test_card_compensation_is_new_operation_requiring_authorization(backend, actor, transfer_args):
    task = make_task(
        backend,
        actor,
        transfer_args,
        steps=(
            Step(
                step_ref="card",
                action="card.set_lock",
                arguments={"card_ref": "card-demo", "locked": True},
            ),
        ),
    )
    approve_all(backend, actor, task)
    backend.run(actor, task["task_ref"])
    preview = backend.compensation_preview(actor, task["operations"][0]["operation_ref"])
    assert preview["available"] and preview["requires_new_approval"]
    restore = backend.create_task(
        actor,
        TaskCreate(
            client_request_ref="restore-card",
            user_request="恢复临时锁卡前状态",
            steps=tuple(Step.model_validate(step) for step in preview["steps"]),
        ),
    )
    assert backend.run(actor, restore["task_ref"])["status"] == "WAITING_APPROVAL"
    assert backend.bank.list_services(actor.subject_ref, "card")[0]["state"] == "LOCKED"
    approve_all(backend, actor, restore)
    assert backend.run(actor, restore["task_ref"])["verified_success"]
    assert backend.bank.list_services(actor.subject_ref, "card")[0]["state"] == "ACTIVE"
