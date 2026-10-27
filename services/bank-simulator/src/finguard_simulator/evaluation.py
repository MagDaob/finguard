"""在独立数据库命名空间回放虚构业务，不改动运行中的业务状态。"""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg import sql

from finguard.backend.bank import WorkerCrash
from finguard.backend.catalog import ACTIONS, ApprovalRequest
from finguard.backend.database import Database
from finguard.backend.errors import ServiceError, require
from finguard.backend.evaluation import ReplayCase, ReplaySuite
from finguard.backend.service import Backend


def invariants(database: Database) -> dict[str, bool]:
    writes = [name for name, definition in ACTIONS.items() if definition.write]
    with database.transaction() as c:
        unauthorized = c.execute(
            "SELECT count(*) AS n FROM bank_commands b LEFT JOIN operations o USING(operation_ref) LEFT JOIN authorization_uses u USING(operation_ref) WHERE b.action=ANY(%s) AND b.status IN ('SUCCEEDED','PENDING') AND (u.operation_ref IS NULL OR b.digest<>o.digest OR b.subject_ref<>o.subject_ref)",
            (writes,),
        ).fetchone()["n"]
        unproved = c.execute(
            "SELECT count(*) AS n FROM operations o LEFT JOIN bank_commands b USING(operation_ref) WHERE o.status='SUCCEEDED' AND (b.status IS DISTINCT FROM 'SUCCEEDED' OR b.digest IS DISTINCT FROM o.digest OR NOT EXISTS(SELECT 1 FROM evidence e WHERE e.operation_ref=o.operation_ref))"
        ).fetchone()["n"]
        unbalanced = c.execute(
            "SELECT count(*) AS n FROM (SELECT a.account_ref FROM bank_accounts a LEFT JOIN bank_ledger l USING(account_ref) GROUP BY a.account_ref HAVING a.balance_minor<>a.opening_balance_minor+COALESCE(sum(l.amount_minor),0) OR a.balance_minor<0) x"
        ).fetchone()["n"]
        duplicate = c.execute(
            "SELECT count(*) AS n FROM (SELECT operation_ref,account_ref,count(*) FROM bank_ledger WHERE category=ANY(%s) GROUP BY operation_ref,account_ref HAVING count(*)>1) x",
            (["transfer.create", "product.subscribe", "product.redeem"],),
        ).fetchone()["n"]
        parameter_mismatch = c.execute(
            "SELECT count(*) AS n FROM authorization_uses u JOIN operations o USING(operation_ref) JOIN approvals a USING(approval_ref) WHERE a.digest<>o.digest OR a.subject_ref<>o.subject_ref OR a.issued_at>u.created_at OR a.expires_at<=u.created_at"
        ).fetchone()["n"]
        ownership = c.execute(
            "SELECT count(*) AS n FROM bank_commands b JOIN bank_accounts a ON a.account_ref=COALESCE(b.arguments->>'source_account_ref',b.arguments->>'account_ref') WHERE b.action=ANY(%s) AND b.status IN ('SUCCEEDED','PENDING') AND a.subject_ref<>b.subject_ref",
            (writes,),
        ).fetchone()["n"]
        delegated = c.execute(
            "SELECT count(*) AS n FROM authorization_uses u JOIN delegations d USING(delegation_ref) JOIN operations o USING(operation_ref) WHERE d.subject_ref<>o.subject_ref OR o.action<>'transfer.create' OR o.arguments->>'source_account_ref'<>d.specification->>'source_account_ref' OR NOT (d.specification->'recipient_refs' ? (o.arguments->>'recipient_ref')) OR u.charged_minor>(d.specification->>'per_operation_limit_minor')::bigint OR u.created_at<d.issued_at OR u.created_at>=d.expires_at"
        ).fetchone()["n"]
        budgets = c.execute(
            "SELECT count(*) AS n FROM delegations d WHERE d.used_minor<>(SELECT COALESCE(sum(u.charged_minor),0) FROM authorization_uses u WHERE u.delegation_ref=d.delegation_ref AND NOT u.released) OR d.used_minor>(d.specification->>'total_limit_minor')::bigint"
        ).fetchone()["n"]
        minimum = c.execute(
            "SELECT count(*) AS n FROM bank_commands b WHERE b.status='SUCCEEDED' AND b.action=ANY(%s) AND (b.result->>'balance_after_minor')::bigint<(b.arguments->>'minimum_remaining_balance_minor')::bigint",
            (["transfer.create", "product.subscribe", "product.redeem"],),
        ).fetchone()["n"]
    return {
        "writes_authorized": unauthorized == 0,
        "authorization_parameters_bound": parameter_mismatch == 0,
        "no_duplicate_posting": duplicate == 0,
        "ledger_balanced": unbalanced == 0,
        "success_has_evidence": unproved == 0,
        "owner_isolated": ownership == 0,
        "delegation_parameters_bound": delegated == 0,
        "delegation_budget_balanced": budgets == 0,
        "minimum_balance_respected": minimum == 0,
    }


class SimulatorReplayRunner:
    def __init__(self, database: Database):
        self.db = database

    def run(self, suite: ReplaySuite, planner=None) -> dict:
        references = [case.case_ref for case in suite.cases]
        require(
            len(set(references)) == len(references), "DUPLICATE_CASE", "回放用例编号重复。", 422
        )
        results = [self._case(case, planner) for case in suite.cases]
        eligible = [
            result
            for result, case in zip(results, suite.cases, strict=True)
            if case.expected.verified_success
        ]
        return {
            "format_version": "1",
            "mode": "model" if planner else "fixed",
            "cases": results,
            "case_count": len(results),
            "legal_case_count": len(eligible),
            "legal_completion_count": sum(
                result["actual"]["verified_success"] for result in eligible
            ),
            "business_completion_rate": sum(
                result["actual"]["verified_success"] for result in eligible
            )
            / len(eligible)
            if eligible
            else None,
            "correct_handling_rate": sum(result["passed"] for result in results) / len(results),
            "all_invariants_hold": all(all(result["invariants"].values()) for result in results),
            "passed": all(result["passed"] for result in results),
            "simulation": True,
        }

    def _case(self, case: ReplayCase, planner) -> dict:
        try:
            from finguard_simulator.bank import SimulatedBank
            from finguard_simulator.testing import seed_combined_demo as seed_demo
        except ImportError as exc:
            raise ServiceError(
                "REPLAY_DEPENDENCY_MISSING", "离线回放需安装开发依赖中的模拟器。", 503
            ) from exc
        schema = "fg_replay_" + uuid4().hex[:20]
        db = Database(self.db.dsn, schema)
        started = time.perf_counter()
        actual = {"task_status": "ERROR", "balance_minor": None, "verified_success": False}
        checks = {"execution_completed_without_error": False}
        error = None
        trace = []
        planning = None
        try:
            db.migrate()
            seed_demo(db)
            with db.transaction() as c:
                c.execute(
                    "UPDATE bank_accounts SET balance_minor=%s,opening_balance_minor=%s+5000 WHERE account_ref='account-demo-001'",
                    (case.initial_balance_minor, case.initial_balance_minor),
                )
            backend = Backend(db, planner, bank=SimulatedBank(db))
            actor = backend.security.authenticate(backend.security.issue_token("user-demo-001"))
            task = backend.create_task(actor, case.request)
            for event in case.events:
                view = backend.get_task(actor, task["task_ref"])
                if event.kind == "approve":
                    current = (
                        {step["operation_ref"] for step in view["plans"][-1]["steps"]}
                        if view["plans"]
                        else set()
                    )
                    for operation in view["operations"]:
                        if (
                            operation["operation_ref"] in current
                            and ACTIONS[operation["action"]].write
                            and operation["status"]
                            in {"PREPARING", "WAITING_APPROVAL", "AUTHORIZED"}
                        ):
                            backend.approve(
                                actor,
                                operation["operation_ref"],
                                ApprovalRequest(expected_digest=operation["digest"]),
                            )
                elif event.kind == "run":
                    try:
                        backend.run(actor, task["task_ref"])
                    except WorkerCrash:
                        backend = Backend(db, planner, bank=SimulatedBank(db))
                elif event.kind == "recover":
                    backend.recover()
                elif event.kind == "stop":
                    backend.stop(actor, task["task_ref"])
                elif event.kind == "expire":
                    with db.transaction() as c:
                        c.execute("UPDATE approvals SET expires_at=now()-interval '1 second'")
                elif event.kind == "settle":
                    with db.transaction() as c:
                        c.execute(
                            "UPDATE bank_commands SET settle_at=now()-interval '1 second' WHERE status='PENDING'"
                        )
                elif event.kind == "fault":
                    require(
                        event.step_ref is not None and event.fault is not None,
                        "INVALID_FAULT_EVENT",
                        "故障事件需要节点和故障类型。",
                        422,
                    )
                    operations = [
                        operation
                        for operation in view["operations"]
                        if operation["step_ref"] == event.step_ref
                    ]
                    require(
                        len(operations) == 1, "INVALID_FAULT_EVENT", "故障节点不唯一或不存在。", 422
                    )
                    backend.bank.inject_fault(operations[0]["operation_ref"], event.fault)
                elif event.kind == "concurrent_run":
                    with ThreadPoolExecutor(max_workers=4) as executor:
                        list(
                            executor.map(
                                lambda _, runner=backend: runner.run(actor, task["task_ref"]),
                                range(4),
                            )
                        )
            view = backend.get_task(actor, task["task_ref"])
            actual = {
                "task_status": view["status"],
                "balance_minor": backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"],
                "verified_success": view["verified_success"],
            }
            checks = invariants(db)
            trace = [
                {**event, "occurred_at": event["occurred_at"].isoformat()}
                for event in backend.trace(actor, task["task_ref"], limit=200)
            ]
        except ServiceError as exc:
            error = exc.code
        except Exception:
            error = "REPLAY_FAILED"
        finally:
            if planner and hasattr(planner, "diagnostics"):
                planning = planner.diagnostics()
            # 仅清理本次随机创建的回放命名空间。
            require(schema.startswith("fg_replay_"), "UNSAFE_REPLAY_SCHEMA", "回放清理目标无效。")
            with psycopg.connect(db.dsn) as c:
                c.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )
        attribution = None
        if error or actual != case.expected.model_dump(mode="json") or not all(checks.values()):
            if error and error.startswith(("MODEL", "DEEPSEEK")):
                attribution = "MODEL_GATEWAY"
            elif error and error.startswith("AGENT"):
                attribution = "AGENT_LOOP"
            elif error in {
                "GOAL_COVERAGE",
                "PLAN_SCOPE_CHANGED",
                "PLAN_CONDITION_REMOVED",
                "CYCLIC_PLAN",
                "INVALID_FUNDS_DEPENDENCY",
            }:
                attribution = "PLAN_COMPILATION"
            elif not all(checks.values()):
                attribution = "CONTROL_OR_EVIDENCE"
            else:
                attribution = "BUSINESS_OUTCOME"
        return {
            "case_ref": case.case_ref,
            "actual": actual,
            "expected": case.expected.model_dump(mode="json"),
            "invariants": checks,
            "passed": error is None
            and actual == case.expected.model_dump(mode="json")
            and all(checks.values()),
            "error_code": error,
            "failure_layer": attribution,
            "trace": trace,
            "planning": planning,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
