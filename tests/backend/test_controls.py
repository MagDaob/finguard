from concurrent.futures import ThreadPoolExecutor

import pytest

from finguard.backend.catalog import (
    ApprovalRequest,
    DelegationRequest,
    ReplanRequest,
    Step,
    TaskCreate,
)
from finguard.backend.errors import ServiceError

pytestmark = pytest.mark.postgres


def transfer(backend, actor, args, key):
    return backend.create_task(
        actor,
        TaskCreate(
            client_request_ref=key,
            user_request="虚构转账",
            steps=(Step(step_ref="transfer", action="transfer.create", arguments=args),),
        ),
    )


def grant(backend, actor, **overrides):
    values = dict(
        source_account_ref="account-demo-001",
        recipient_refs=("recipient-demo-wang",),
        per_operation_limit_minor=30000,
        total_limit_minor=90000,
        maximum_operations=1,
        period_seconds=60,
        ttl_seconds=300,
    )
    return backend.add_delegation(actor, DelegationRequest(**{**values, **overrides}))


def test_delegation_frequency_is_atomic_across_distinct_tasks(backend, actor, transfer_args):
    delegation = grant(backend, actor)
    tasks = [transfer(backend, actor, transfer_args, f"frequency-{i}") for i in range(2)]
    for task in tasks:
        backend.bind_delegation(
            actor, task["operations"][0]["operation_ref"], delegation["delegation_ref"]
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda t: backend.run(actor, t["task_ref"]), tasks))
    assert sum(result["verified_success"] for result in results) == 1
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 120000


@pytest.mark.parametrize("condition", ["single_limit", "revoked", "expired"])
def test_inapplicable_delegation_cannot_replace_approval(
    backend, database, actor, transfer_args, condition
):
    delegation = grant(
        backend, actor, per_operation_limit_minor=10000 if condition == "single_limit" else 30000
    )
    task = transfer(backend, actor, transfer_args, "inapplicable")
    backend.bind_delegation(
        actor, task["operations"][0]["operation_ref"], delegation["delegation_ref"]
    )
    if condition == "revoked":
        backend.revoke_delegation(actor, delegation["delegation_ref"])
    elif condition == "expired":
        with database.transaction() as c:
            c.execute("UPDATE delegations SET expires_at=now()-interval '1 second'")
    assert backend.run(actor, task["task_ref"])["status"] == "WAITING_APPROVAL"
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 150000


def test_definitive_failure_releases_budget_but_retains_frequency_record(
    backend, database, actor, transfer_args
):
    delegation = grant(backend, actor, total_limit_minor=30000, maximum_operations=2)
    first = transfer(backend, actor, transfer_args, "fails-unsent")
    op = first["operations"][0]
    backend.bind_delegation(actor, op["operation_ref"], delegation["delegation_ref"])
    backend.bank.inject_fault(op["operation_ref"], "before_send")
    assert backend.run(actor, first["task_ref"])["status"] == "TERMINATED"
    with database.transaction() as c:
        assert c.execute("SELECT used_minor FROM delegations").fetchone()["used_minor"] == 0
        assert c.execute("SELECT count(*) AS n FROM authorization_uses").fetchone()["n"] == 1
    second = transfer(backend, actor, transfer_args, "next-attempt")
    backend.bind_delegation(
        actor, second["operations"][0]["operation_ref"], delegation["delegation_ref"]
    )
    assert backend.run(actor, second["task_ref"])["verified_success"]


def test_parameter_change_revokes_old_approval(backend, actor, transfer_args):
    task = transfer(backend, actor, transfer_args, "replace-goal")
    operation = task["operations"][0]
    backend.approve(
        actor, operation["operation_ref"], ApprovalRequest(expected_digest=operation["digest"])
    )
    changed = Step(
        step_ref="transfer",
        action="transfer.create",
        arguments={**transfer_args, "amount_minor": 20000},
    )
    revised = backend.replan(
        actor, task["task_ref"], ReplanRequest(expected_version=1, steps=(changed,))
    )
    assert backend.run(actor, task["task_ref"])["status"] == "WAITING_APPROVAL"
    old = next(
        op for op in revised["operations"] if op["operation_ref"] == operation["operation_ref"]
    )
    assert old["status"] == "CANCELLED_PRE_SUBMIT"
    with pytest.raises(ServiceError) as exc:
        backend.replan(
            actor,
            task["task_ref"],
            ReplanRequest(
                expected_version=2,
                steps=(
                    Step(step_ref="transfer", action="transfer.create", arguments=transfer_args),
                ),
            ),
        )
    assert exc.value.code == "TERMINAL_OPERATION"
