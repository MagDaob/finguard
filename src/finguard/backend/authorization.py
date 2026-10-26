"""授权消费与提交意图在同一本地事务中保存。"""

from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from finguard.backend.catalog import DelegationRequest, charged_amount
from finguard.backend.errors import require
from finguard.domain.values import new_ref


def create_delegation(c, subject: str, request: DelegationRequest) -> dict:
    require(
        request.per_operation_limit_minor <= request.total_limit_minor,
        "INVALID_LIMITS",
        "单笔上限不能高于累计限额。",
        422,
    )
    require(
        len(set(request.recipient_refs)) == len(request.recipient_refs),
        "DUPLICATE_RECIPIENT",
        "收款人白名单不能重复。",
        422,
    )
    reference = new_ref("delegation")
    expiry = datetime.now(UTC) + timedelta(seconds=request.ttl_seconds)
    c.execute(
        "INSERT INTO delegations(delegation_ref,subject_ref,specification,expires_at) VALUES (%s,%s,%s,%s)",
        (reference, subject, Jsonb(request.model_dump(mode="json")), expiry),
    )
    return {
        "delegation_ref": reference,
        "expires_at": expiry,
        "specification": request.model_dump(mode="json"),
        "used_minor": 0,
        "revoked": False,
    }


def reserve(c, operation: dict) -> bool:
    ref, subject = operation["operation_ref"], operation["subject_ref"]
    require(
        c.execute("SELECT 1 FROM authorization_uses WHERE operation_ref=%s", (ref,)).fetchone()
        is None,
        "AUTHORIZATION_ALREADY_USED",
        "操作授权已消费，必须核实原提交。",
        409,
    )
    approval = c.execute(
        "SELECT * FROM approvals WHERE operation_ref=%s AND subject_ref=%s AND digest=%s AND status='ACTIVE' AND issued_at<=now() AND expires_at>now() FOR UPDATE",
        (ref, subject, operation["digest"]),
    ).fetchone()
    amount = charged_amount(operation["action"], operation["arguments"])
    if approval and not (approval["issued_at"] <= datetime.now(UTC) < approval["expires_at"]):
        approval = None
    if approval:
        c.execute(
            "UPDATE approvals SET status='CONSUMED' WHERE approval_ref=%s",
            (approval["approval_ref"],),
        )
        c.execute(
            "INSERT INTO authorization_uses(operation_ref,approval_ref,charged_minor) VALUES (%s,%s,%s)",
            (ref, approval["approval_ref"], amount),
        )
        return True
    delegation_ref = operation.get("delegation_ref")
    if not delegation_ref or operation["action"] != "transfer.create":
        return False
    grant = c.execute(
        "SELECT * FROM delegations WHERE delegation_ref=%s AND subject_ref=%s AND NOT revoked AND expires_at>now() FOR UPDATE",
        (delegation_ref, subject),
    ).fetchone()
    if not grant or not (grant["issued_at"] <= datetime.now(UTC) < grant["expires_at"]):
        return False
    spec, args = grant["specification"], operation["arguments"]
    if (
        args["source_account_ref"] != spec["source_account_ref"]
        or args["recipient_ref"] not in spec["recipient_refs"]
        or amount > spec["per_operation_limit_minor"]
        or grant["used_minor"] + amount > spec["total_limit_minor"]
    ):
        return False
    count = c.execute(
        "SELECT count(*) AS n FROM authorization_uses WHERE delegation_ref=%s AND created_at>=clock_timestamp()-make_interval(secs => %s)",
        (delegation_ref, spec["period_seconds"]),
    ).fetchone()["n"]
    if count >= spec["maximum_operations"]:
        return False
    c.execute(
        "UPDATE delegations SET used_minor=used_minor+%s WHERE delegation_ref=%s",
        (amount, delegation_ref),
    )
    c.execute(
        "INSERT INTO authorization_uses(operation_ref,delegation_ref,charged_minor) VALUES (%s,%s,%s)",
        (ref, delegation_ref, amount),
    )
    return True


def release_failed(c, operation_ref: str) -> None:
    use = c.execute(
        "SELECT * FROM authorization_uses WHERE operation_ref=%s FOR UPDATE", (operation_ref,)
    ).fetchone()
    if use and not use["released"]:
        if use["delegation_ref"]:
            c.execute(
                "UPDATE delegations SET used_minor=used_minor-%s WHERE delegation_ref=%s",
                (use["charged_minor"], use["delegation_ref"]),
            )
        c.execute(
            "UPDATE authorization_uses SET released=true WHERE operation_ref=%s", (operation_ref,)
        )
