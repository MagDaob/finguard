"""任务编排、可信授权和恢复；应用提交意图与银行提交使用不同事务。"""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from graphlib import TopologicalSorter

import psycopg
from psycopg.types.json import Jsonb

from finguard.backend import authorization
from finguard.backend.bank import NotSubmitted, ResultUnknown, SimulatedBank, WorkerCrash
from finguard.backend.catalog import (
    ACTIONS,
    ApprovalRequest,
    Constraints,
    DelegationRequest,
    ReplanRequest,
    TaskCreate,
    digest,
    normalize_steps,
    operation_digest,
    required_scopes,
)
from finguard.backend.database import Database
from finguard.backend.errors import ServiceError, require
from finguard.backend.planning import BaselinePlanner, compile_candidate
from finguard.backend.security import Actor, Security
from finguard.domain.states import (
    OperationStatus as O,
)
from finguard.domain.states import (
    TaskStatus as T,
)
from finguard.domain.states import (
    require_operation_transition,
    require_task_transition,
)
from finguard.domain.values import new_ref


class Backend:
    def __init__(self, database: Database, planner=None):
        self.db = database
        self.security = Security(database)
        self.bank = SimulatedBank(database)
        self.planner = planner or BaselinePlanner()

    @staticmethod
    def _event(c, task: str, kind: str, data: dict) -> None:
        c.execute(
            "INSERT INTO task_events(task_ref,kind,data) VALUES (%s,%s,%s)",
            (task, kind, Jsonb(data)),
        )

    def _op_state(
        self, c, operation: dict, target: O, result: dict | None = None, error: str | None = None
    ) -> None:
        current = O(operation["status"])
        if current is not target:
            if not (
                not ACTIONS[operation["action"]].write
                and current is O.PREPARING
                and target is O.SUBMITTING
            ):
                require_operation_transition(current, target)
            self._event(
                c,
                operation["task_ref"],
                "operation_state",
                {
                    "operation_ref": operation["operation_ref"],
                    "from": current.value,
                    "to": target.value,
                    "error_code": error,
                },
            )
        c.execute(
            "UPDATE operations SET status=%s,result=COALESCE(%s,result),error_code=%s,updated_at=now() WHERE operation_ref=%s",
            (
                target.value,
                Jsonb(result) if result is not None else None,
                error,
                operation["operation_ref"],
            ),
        )
        operation["status"] = target.value

    def _task_state(self, c, task: dict, target: T) -> None:
        current = T(task["status"])
        if current is target:
            return
        require_task_transition(current, target)
        c.execute(
            "UPDATE tasks SET status=%s,updated_at=now() WHERE task_ref=%s",
            (target.value, task["task_ref"]),
        )
        self._event(c, task["task_ref"], "task_state", {"from": current.value, "to": target.value})
        task["status"] = target.value

    @staticmethod
    def _task(c, subject: str, task_ref: str, lock: bool = False) -> dict:
        query = "SELECT * FROM tasks WHERE task_ref=%s AND subject_ref=%s" + (
            " FOR UPDATE" if lock else ""
        )
        task = c.execute(query, (task_ref, subject)).fetchone()
        require(task is not None, "TASK_NOT_FOUND", "任务不存在或不可访问。", 404)
        return task

    @staticmethod
    def _operation(c, subject: str, operation_ref: str, lock: bool = False) -> dict:
        query = "SELECT * FROM operations WHERE operation_ref=%s AND subject_ref=%s" + (
            " FOR UPDATE" if lock else ""
        )
        row = c.execute(query, (operation_ref, subject)).fetchone()
        require(row is not None, "OPERATION_NOT_FOUND", "操作不存在或不可访问。", 404)
        return row

    def _context(self, actor: Actor, message: str) -> dict:
        self.security.require_scope(actor, "accounts.read")
        context = {
            "accounts": self.bank.list_accounts(actor.subject_ref),
            "recipients": self.bank.list_recipients(actor.subject_ref),
            "cards": [],
            "subscriptions": [],
            "products": [],
            "positions": [],
        }
        if "订阅" in message or "卡" in message:
            self.security.require_scope(actor, "services.read")
            context.update(
                cards=self.bank.list_services(actor.subject_ref, "card"),
                subscriptions=self.bank.list_services(actor.subject_ref, "subscription"),
            )
        if "申购" in message or "赎回" in message:
            self.security.require_scope(actor, "products.read")
            context.update(
                products=self.bank.list_products(),
                positions=self.bank.list_positions(actor.subject_ref),
            )
        return context

    def _validate_goals(self, actor: Actor, goals: list[dict]) -> None:
        for step in goals:
            for scope in required_scopes(step["action"]):
                self.security.require_scope(actor, scope)
            self.bank.preview(actor.subject_ref, step["action"], step["arguments"])

    def _install(self, c, task: dict, steps: list[dict], planner_version: str) -> None:
        version = task["current_version"] + 1
        compiled = []
        for step in steps:
            fingerprint = operation_digest(task["subject_ref"], step["action"], step["arguments"])
            operation = (
                "operation-"
                + hashlib.sha256(
                    f"{task['task_ref']}:{step['step_ref']}:{fingerprint}".encode()
                ).hexdigest()[:40]
            )
            existing = c.execute(
                "SELECT status FROM operations WHERE operation_ref=%s", (operation,)
            ).fetchone()
            require(
                not existing
                or existing["status"] not in {O.FAILED.value, O.CANCELLED_PRE_SUBMIT.value},
                "TERMINAL_OPERATION",
                "已失败或取消的节点不能重新激活；新的业务尝试需要新的节点编号和授权。",
                409,
            )
            c.execute(
                "INSERT INTO operations(operation_ref,subject_ref,task_ref,step_ref,action,arguments,digest,status) VALUES (%s,%s,%s,%s,%s,%s,%s,'PREPARING') ON CONFLICT DO NOTHING",
                (
                    operation,
                    task["subject_ref"],
                    task["task_ref"],
                    step["step_ref"],
                    step["action"],
                    Jsonb(step["arguments"]),
                    fingerprint,
                ),
            )
            compiled.append({**step, "operation_ref": operation})
        c.execute(
            "INSERT INTO plans(task_ref,version,plan_ref,steps,planner_version) VALUES (%s,%s,%s,%s,%s)",
            (task["task_ref"], version, new_ref("plan"), Jsonb(compiled), planner_version),
        )
        c.execute(
            "UPDATE tasks SET current_version=%s,questions='[]',updated_at=now() WHERE task_ref=%s",
            (version, task["task_ref"]),
        )
        self._event(
            c,
            task["task_ref"],
            "plan_installed",
            {
                "version": version,
                "planner_version": planner_version,
                "operations": [step["operation_ref"] for step in compiled],
            },
        )

    def create_task(self, actor: Actor, request: TaskCreate) -> dict:
        self.security.require_scope(actor, "tasks.write")
        request_hash = digest(request.model_dump(mode="json"))
        with self.db.transaction() as c:
            existing = c.execute(
                "SELECT task_ref,request_digest FROM tasks WHERE subject_ref=%s AND client_request_ref=%s",
                (actor.subject_ref, request.client_request_ref),
            ).fetchone()
        if existing:
            require(
                existing["request_digest"] == request_hash,
                "REQUEST_CONFLICT",
                "请求编号已绑定其他任务内容。",
                409,
            )
            return self.get_task(actor, existing["task_ref"])
        constraints, steps, questions = request.constraints, request.steps, []
        if not steps:
            steps, constraints, questions = self.planner.infer(
                request.user_request, self._context(actor, request.user_request), constraints
            )
        goals = normalize_steps(steps, constraints) if steps else []
        compiled, planner_version = [], "clarification-v1"
        if not questions:
            self._validate_goals(actor, goals)
            candidate, planner_version = self.planner.propose(goals, constraints)
            compiled = compile_candidate(goals, candidate, constraints)
        reference = new_ref("task")
        with self.db.transaction() as c:
            self.security.require_scope(actor, "tasks.write", c)
            row = c.execute(
                "INSERT INTO tasks(task_ref,subject_ref,client_request_ref,request_digest,user_request,goals,constraints,status,questions) VALUES (%s,%s,%s,%s,%s,%s,%s,'UNDERSTANDING',%s) ON CONFLICT(subject_ref,client_request_ref) DO NOTHING RETURNING *",
                (
                    reference,
                    actor.subject_ref,
                    request.client_request_ref,
                    request_hash,
                    request.user_request,
                    Jsonb(goals),
                    Jsonb(constraints.model_dump(mode="json")),
                    Jsonb(questions),
                ),
            ).fetchone()
            if row is None:
                row = c.execute(
                    "SELECT * FROM tasks WHERE subject_ref=%s AND client_request_ref=%s",
                    (actor.subject_ref, request.client_request_ref),
                ).fetchone()
                require(
                    row["request_digest"] == request_hash,
                    "REQUEST_CONFLICT",
                    "请求编号已绑定其他任务内容。",
                    409,
                )
                reference = row["task_ref"]
            else:
                self._event(
                    c, reference, "task_created", {"client_request_ref": request.client_request_ref}
                )
                self._task_state(c, row, T.WAITING_CLARIFICATION if questions else T.PLANNING)
                if compiled:
                    self._install(c, row, compiled, planner_version)
        return self.get_task(actor, reference)

    def get_task(self, actor: Actor, reference: str) -> dict:
        self.security.require_scope(actor, "tasks.read")
        with self.db.transaction() as c:
            task = self._task(c, actor.subject_ref, reference)
            operations = c.execute(
                "SELECT operation_ref,step_ref,action,arguments,digest,status,result,error_code,delegation_ref FROM operations WHERE task_ref=%s ORDER BY operation_ref",
                (reference,),
            ).fetchall()
            plans = c.execute(
                "SELECT version,plan_ref,steps,planner_version,created_at FROM plans WHERE task_ref=%s ORDER BY version",
                (reference,),
            ).fetchall()
            evidence = c.execute(
                "SELECT evidence_ref,operation_ref,kind,record_ref,source,data,verified_at FROM evidence WHERE subject_ref=%s AND operation_ref IN (SELECT operation_ref FROM operations WHERE task_ref=%s) ORDER BY evidence_ref",
                (actor.subject_ref, reference),
            ).fetchall()
        current = plans[-1]["steps"] if plans else []
        ids = {step["operation_ref"] for step in current}
        current_ops = [operation for operation in operations if operation["operation_ref"] in ids]
        proof = {item["operation_ref"] for item in evidence}
        verified = bool(current_ops) and all(
            operation["status"] == O.SUCCEEDED.value and operation["operation_ref"] in proof
            for operation in current_ops
        )
        return {
            key: task[key]
            for key in (
                "task_ref",
                "subject_ref",
                "user_request",
                "status",
                "questions",
                "current_version",
                "stop_requested",
                "created_at",
                "updated_at",
            )
        } | {
            "constraints": task["constraints"],
            "plans": plans,
            "operations": operations,
            "evidence": evidence,
            "verified_success": task["status"] == T.COMPLETED.value and verified,
            "simulation": True,
        }

    def list_tasks(self, actor: Actor, limit: int = 50, before: str | None = None) -> list[dict]:
        self.security.require_scope(actor, "tasks.read")
        with self.db.transaction() as c:
            return c.execute(
                "SELECT task_ref,user_request,status,current_version,stop_requested,created_at FROM tasks WHERE subject_ref=%s AND (%s::text IS NULL OR task_ref<%s) ORDER BY task_ref DESC LIMIT %s",
                (actor.subject_ref, before, before, limit),
            ).fetchall()

    def trace(self, actor: Actor, task_ref: str, after: int = 0, limit: int = 200) -> list[dict]:
        self.security.require_scope(actor, "tasks.read")
        with self.db.transaction() as c:
            self._task(c, actor.subject_ref, task_ref)
            return c.execute(
                "SELECT sequence,kind,data,occurred_at FROM task_events WHERE task_ref=%s AND sequence>%s ORDER BY sequence LIMIT %s",
                (task_ref, after, limit),
            ).fetchall()

    def approve(self, actor: Actor, operation_ref: str, request: ApprovalRequest) -> dict:
        with self.db.transaction() as c:
            operation = self._operation(c, actor.subject_ref, operation_ref, True)
            self.security.require_scope(actor, "approvals.write", c)
            for scope in required_scopes(operation["action"]):
                self.security.require_scope(actor, scope, c)
            task = self._task(c, actor.subject_ref, operation["task_ref"])
            require(not task["stop_requested"], "TASK_STOPPED", "任务已停止。", 409)
            require(
                ACTIONS[operation["action"]].write,
                "READ_REQUIRES_NO_APPROVAL",
                "读操作不需要业务写授权。",
                409,
            )
            require(
                operation["status"]
                in {O.PREPARING.value, O.WAITING_APPROVAL.value, O.AUTHORIZED.value},
                "OPERATION_NOT_APPROVABLE",
                "当前操作不能再次授权。",
                409,
            )
            require(
                request.expected_digest == operation["digest"],
                "STALE_PREVIEW",
                "操作内容已变化，请重新查看预览。",
                409,
            )
            preview = self.bank.preview(
                actor.subject_ref, operation["action"], operation["arguments"]
            )
            existing = c.execute(
                "SELECT approval_ref,expires_at FROM approvals WHERE operation_ref=%s AND status='ACTIVE' AND expires_at>now() FOR UPDATE",
                (operation_ref,),
            ).fetchone()
            if existing:
                return {**existing, "operation_ref": operation_ref, "preview": preview}
            c.execute(
                "UPDATE approvals SET status='REVOKED' WHERE operation_ref=%s AND status='ACTIVE'",
                (operation_ref,),
            )
            approval, expiry = (
                new_ref("approval"),
                datetime.now(UTC) + timedelta(seconds=request.ttl_seconds),
            )
            c.execute(
                "INSERT INTO approvals(approval_ref,subject_ref,operation_ref,digest,expires_at,status) VALUES (%s,%s,%s,%s,%s,'ACTIVE')",
                (approval, actor.subject_ref, operation_ref, operation["digest"], expiry),
            )
            if operation["status"] == O.PREPARING.value:
                self._op_state(c, operation, O.WAITING_APPROVAL)
            if operation["status"] != O.AUTHORIZED.value:
                self._op_state(c, operation, O.AUTHORIZED)
            self._event(
                c,
                task["task_ref"],
                "approval_issued",
                {"approval_ref": approval, "operation_ref": operation_ref},
            )
            return {
                "approval_ref": approval,
                "operation_ref": operation_ref,
                "expires_at": expiry,
                "preview": preview,
            }

    def revoke_approval(self, actor: Actor, approval_ref: str) -> dict:
        with self.db.transaction() as c:
            self.security.require_scope(actor, "approvals.write", c)
            approval = c.execute(
                "SELECT * FROM approvals WHERE approval_ref=%s AND subject_ref=%s",
                (approval_ref, actor.subject_ref),
            ).fetchone()
            require(approval is not None, "APPROVAL_NOT_FOUND", "授权不存在或不可访问。", 404)
            operation = self._operation(c, actor.subject_ref, approval["operation_ref"], True)
            approval = c.execute(
                "SELECT * FROM approvals WHERE approval_ref=%s FOR UPDATE", (approval_ref,)
            ).fetchone()
            require(
                approval["status"] != "CONSUMED",
                "ALREADY_SUBMITTED",
                "授权已用于提交，后续需核实原操作。",
                409,
            )
            c.execute(
                "UPDATE approvals SET status='REVOKED' WHERE approval_ref=%s", (approval_ref,)
            )
            if operation["status"] == O.AUTHORIZED.value:
                self._op_state(c, operation, O.WAITING_APPROVAL)
            self._event(
                c, operation["task_ref"], "approval_revoked", {"approval_ref": approval_ref}
            )
        return {"approval_ref": approval_ref, "status": "REVOKED"}

    def add_delegation(self, actor: Actor, request: DelegationRequest) -> dict:
        self.security.require_scope(actor, "approvals.write")
        self.security.require_scope(actor, "transfers.write")
        self.security.require_scope(actor, "accounts.read")
        for recipient in request.recipient_refs:
            self.bank.preview(
                actor.subject_ref,
                "transfer.create",
                {
                    "source_account_ref": request.source_account_ref,
                    "recipient_ref": recipient,
                    "amount_minor": 1,
                    "currency": "CNY",
                    "fee_minor": 0,
                    "minimum_remaining_balance_minor": 0,
                },
            )
        with self.db.transaction() as c:
            self.security.require_scope(actor, "approvals.write", c)
            return authorization.create_delegation(c, actor.subject_ref, request)

    def revoke_delegation(self, actor: Actor, reference: str) -> dict:
        with self.db.transaction() as c:
            self.security.require_scope(actor, "approvals.write", c)
            row = c.execute(
                "UPDATE delegations SET revoked=true WHERE delegation_ref=%s AND subject_ref=%s RETURNING delegation_ref",
                (reference, actor.subject_ref),
            ).fetchone()
            require(row is not None, "DELEGATION_NOT_FOUND", "委托不存在或不可访问。", 404)
        return {"delegation_ref": reference, "revoked": True}

    def bind_delegation(self, actor: Actor, operation_ref: str, reference: str) -> dict:
        with self.db.transaction() as c:
            self.security.require_scope(actor, "approvals.write", c)
            operation = self._operation(c, actor.subject_ref, operation_ref, True)
            task = self._task(c, actor.subject_ref, operation["task_ref"])
            require(
                not task["stop_requested"]
                and operation["action"] == "transfer.create"
                and operation["status"]
                in {O.PREPARING.value, O.WAITING_APPROVAL.value, O.AUTHORIZED.value},
                "INVALID_DELEGATION_BINDING",
                "当前操作不能绑定委托。",
                409,
            )
            grant = c.execute(
                "SELECT 1 FROM delegations WHERE delegation_ref=%s AND subject_ref=%s AND NOT revoked AND expires_at>now()",
                (reference, actor.subject_ref),
            ).fetchone()
            require(grant is not None, "DELEGATION_NOT_FOUND", "委托不存在或已失效。", 404)
            c.execute(
                "UPDATE operations SET delegation_ref=%s WHERE operation_ref=%s",
                (reference, operation_ref),
            )
            self._event(
                c,
                operation["task_ref"],
                "delegation_bound",
                {"operation_ref": operation_ref, "delegation_ref": reference},
            )
        return {"operation_ref": operation_ref, "delegation_ref": reference}

    def replan(self, actor: Actor, task_ref: str, request: ReplanRequest) -> dict:
        self.security.require_scope(actor, "tasks.write")
        with self.db.task_lock(task_ref) as acquired:
            require(acquired, "TASK_BUSY", "任务正在执行，暂时不能替换计划。", 409)
            with self.db.transaction() as c:
                task = self._task(c, actor.subject_ref, task_ref)
                require(
                    not task["stop_requested"]
                    and task["status"] not in {T.COMPLETED.value, T.TERMINATED.value},
                    "TASK_CLOSED",
                    "任务已结束。",
                    409,
                )
                require(
                    request.expected_version == task["current_version"],
                    "PLAN_VERSION_CONFLICT",
                    "计划版本已变化。",
                    409,
                )
                old = c.execute(
                    "SELECT * FROM operations WHERE task_ref=%s", (task_ref,)
                ).fetchall()
            constraints = Constraints.model_validate(task["constraints"])
            goals = normalize_steps(request.steps, constraints)
            required = {
                row["step_ref"]: row
                for row in old
                if row["status"] in {O.SUCCEEDED.value, O.SUBMITTING.value, O.UNKNOWN.value}
            }
            by_step = {step["step_ref"]: step for step in goals}
            for step_ref, operation in required.items():
                require(
                    step_ref in by_step
                    and operation_digest(
                        actor.subject_ref,
                        by_step[step_ref]["action"],
                        by_step[step_ref]["arguments"],
                    )
                    == operation["digest"],
                    "IMMUTABLE_OPERATION",
                    "已完成或待核实操作必须保留。",
                    409,
                )
            # 既定事实可跨业务状态保留，不因再次预览已赎回份额而被错误拒绝。
            self._validate_goals(
                actor, [step for step in goals if step["step_ref"] not in required]
            )
            candidate, version = self.planner.propose(goals, constraints)
            compiled = compile_candidate(goals, candidate, constraints)
            keep = {
                operation_digest(actor.subject_ref, step["action"], step["arguments"])
                + step["step_ref"]
                for step in compiled
            }
            with self.db.transaction() as c:
                task = self._task(c, actor.subject_ref, task_ref, True)
                require(
                    not task["stop_requested"]
                    and request.expected_version == task["current_version"],
                    "PLAN_VERSION_CONFLICT",
                    "任务或计划已变化。",
                    409,
                )
                for operation in old:
                    if operation["digest"] + operation["step_ref"] not in keep and operation[
                        "status"
                    ] in {O.PREPARING.value, O.WAITING_APPROVAL.value, O.AUTHORIZED.value}:
                        self._op_state(c, operation, O.CANCELLED_PRE_SUBMIT)
                        c.execute(
                            "UPDATE approvals SET status='REVOKED' WHERE operation_ref=%s AND status='ACTIVE'",
                            (operation["operation_ref"],),
                        )
                self._task_state(c, task, T.PLANNING)
                c.execute("UPDATE tasks SET goals=%s WHERE task_ref=%s", (Jsonb(goals), task_ref))
                self._install(c, task, compiled, version)
        return self.get_task(actor, task_ref)

    def stop(self, actor: Actor, task_ref: str) -> dict:
        with self.db.transaction() as c:
            self.security.require_scope(actor, "tasks.write", c)
            self._task(c, actor.subject_ref, task_ref, True)
            c.execute(
                "UPDATE tasks SET stop_requested=true,run_requested=false WHERE task_ref=%s",
                (task_ref,),
            )
            operations = c.execute(
                "SELECT * FROM operations WHERE task_ref=%s ORDER BY operation_ref FOR UPDATE",
                (task_ref,),
            ).fetchall()
            for operation in operations:
                if operation["status"] in {
                    O.PREPARING.value,
                    O.WAITING_APPROVAL.value,
                    O.AUTHORIZED.value,
                }:
                    self._op_state(c, operation, O.CANCELLED_PRE_SUBMIT)
                    c.execute(
                        "UPDATE approvals SET status='REVOKED' WHERE operation_ref=%s AND status='ACTIVE'",
                        (operation["operation_ref"],),
                    )
            self._event(c, task_ref, "stop_requested", {})
            self._refresh(c, actor.subject_ref, task_ref)
        return self.get_task(actor, task_ref)

    def compensation_preview(self, actor: Actor, operation_ref: str) -> dict:
        self.security.require_scope(actor, "tasks.read")
        with self.db.transaction() as c:
            operation = self._operation(c, actor.subject_ref, operation_ref)
        for scope in required_scopes(operation["action"]):
            self.security.require_scope(actor, scope)
        result = {
            "operation_ref": operation_ref,
            "available": False,
            "requires_new_approval": True,
            "steps": [],
        }
        if operation["status"] != O.SUCCEEDED.value:
            return {**result, "reason": "OPERATION_NOT_CONFIRMED"}
        if operation["action"] != "card.set_lock":
            return {**result, "reason": "NO_AUTOMATIC_REVERSAL"}
        receipt = operation["result"]
        current = self.bank.preview(actor.subject_ref, operation["action"], operation["arguments"])
        if current["current_state"] != receipt["state"]:
            return {**result, "reason": "RESOURCE_CHANGED"}
        return {
            **result,
            "available": True,
            "reason": "NEW_CARD_OPERATION_REQUIRED",
            "steps": [
                {
                    "step_ref": "restore-card",
                    "action": "card.set_lock",
                    "arguments": {
                        "card_ref": operation["arguments"]["card_ref"],
                        "locked": receipt["previous_state"] == "LOCKED",
                    },
                    "depends_on": [],
                    "funds_available_after": [],
                }
            ],
        }

    def _claim(self, actor: Actor, operation_ref: str) -> dict | None:
        with self.db.transaction() as c:
            # 与停止操作保持一致的锁顺序：任务行先于操作行。
            row = self._operation(c, actor.subject_ref, operation_ref)
            task = self._task(c, actor.subject_ref, row["task_ref"], True)
            operation = self._operation(c, actor.subject_ref, operation_ref, True)
            if task["stop_requested"] or operation["status"] not in {
                O.PREPARING.value,
                O.WAITING_APPROVAL.value,
                O.AUTHORIZED.value,
            }:
                return None
            definition = ACTIONS[operation["action"]]
            for scope in required_scopes(operation["action"]):
                self.security.require_scope(actor, scope, c)
            try:
                self.bank.preview(actor.subject_ref, operation["action"], operation["arguments"])
            except ServiceError as exc:
                self._op_state(c, operation, O.FAILED, error=exc.code)
                return None
            if definition.write:
                if operation["status"] == O.PREPARING.value:
                    self._op_state(c, operation, O.WAITING_APPROVAL)
                if not authorization.reserve(c, operation):
                    self._op_state(c, operation, O.WAITING_APPROVAL, error="AUTHORIZATION_REQUIRED")
                    return None
                if operation["status"] != O.AUTHORIZED.value:
                    self._op_state(c, operation, O.AUTHORIZED)
            for scope in required_scopes(operation["action"]):
                self.security.require_scope(actor, scope, c)
            self._op_state(c, operation, O.SUBMITTING)
            return operation

    def _unknown(self, subject: str, operation_ref: str, code: str) -> None:
        with self.db.transaction() as c:
            operation = self._operation(c, subject, operation_ref, True)
            if operation["status"] in {O.SUBMITTING.value, O.UNKNOWN.value}:
                self._op_state(c, operation, O.UNKNOWN, error=code)

    def _reconcile(self, subject: str, operation_ref: str, fence: bool = True) -> None:
        with self.db.transaction() as c:
            operation = self._operation(c, subject, operation_ref)
        if operation["status"] not in {O.SUBMITTING.value, O.UNKNOWN.value}:
            return
        try:
            receipt = self.bank.lookup(
                subject,
                operation_ref,
                operation["digest"],
                fence,
                operation["action"],
                operation["arguments"],
            )
            if not receipt or receipt["status"] == "PENDING":
                self._unknown(subject, operation_ref, "RESULT_PENDING")
                return
            if receipt["status"] == "SUCCEEDED":
                receipt, proof = self.bank.verify(subject, operation_ref, operation["digest"])
                if not receipt or not proof:
                    self._unknown(subject, operation_ref, "EVIDENCE_UNAVAILABLE")
                    return
            else:
                proof = []
        except (ServiceError, psycopg.Error):
            self._unknown(subject, operation_ref, "RECONCILIATION_UNAVAILABLE")
            return
        with self.db.transaction() as c:
            operation = self._operation(c, subject, operation_ref, True)
            if operation["status"] not in {O.SUBMITTING.value, O.UNKNOWN.value}:
                return
            for item in proof:
                c.execute(
                    "INSERT INTO evidence(evidence_ref,operation_ref,subject_ref,kind,record_ref,source,data) VALUES (%s,%s,%s,%s,%s,'simulated-bank',%s) ON CONFLICT(operation_ref,kind) DO NOTHING",
                    (
                        new_ref("evidence"),
                        operation_ref,
                        subject,
                        item["kind"],
                        item["record_ref"],
                        Jsonb(item["data"]),
                    ),
                )
            if receipt["status"] == "FAILED":
                authorization.release_failed(c, operation_ref)
            self._op_state(
                c,
                operation,
                O.SUCCEEDED if receipt["status"] == "SUCCEEDED" else O.FAILED,
                result=receipt,
                error=receipt.get("error_code"),
            )

    def _execute(self, actor: Actor, operation_ref: str) -> None:
        operation = self._claim(actor, operation_ref)
        if operation is None:
            return
        try:
            receipt = self.bank.execute(
                actor.subject_ref, operation_ref, operation["action"], operation["arguments"]
            )
        except NotSubmitted:
            self._reconcile(actor.subject_ref, operation_ref)
            return
        except ResultUnknown:
            self._unknown(actor.subject_ref, operation_ref, "RESPONSE_LOST")
            return
        except WorkerCrash:
            raise
        except (psycopg.Error, ServiceError):
            self._unknown(actor.subject_ref, operation_ref, "ADAPTER_UNAVAILABLE")
            return
        if (
            receipt.get("operation_ref") != operation_ref
            or receipt.get("transaction_ref") != operation_ref
            or receipt.get("status") not in {"PENDING", "SUCCEEDED", "FAILED"}
        ):
            self._unknown(actor.subject_ref, operation_ref, "INVALID_ADAPTER_RESPONSE")
            return
        self._reconcile(actor.subject_ref, operation_ref, fence=False)

    def _refresh(self, c, subject: str, task_ref: str) -> None:
        task = self._task(c, subject, task_ref, True)
        if task["status"] in {T.COMPLETED.value, T.TERMINATED.value} or not task["current_version"]:
            if task["stop_requested"] and task["status"] not in {
                T.COMPLETED.value,
                T.TERMINATED.value,
            }:
                self._task_state(c, task, T.TERMINATED)
            return
        plan = c.execute(
            "SELECT steps FROM plans WHERE task_ref=%s AND version=%s",
            (task_ref, task["current_version"]),
        ).fetchone()
        rows = c.execute(
            "SELECT status FROM operations WHERE operation_ref=ANY(%s)",
            ([step["operation_ref"] for step in plan["steps"]],),
        ).fetchall()
        statuses = [row["status"] for row in rows]
        if statuses and all(status == O.SUCCEEDED.value for status in statuses):
            target = T.COMPLETED
        elif task["stop_requested"] and not any(
            status in {O.SUBMITTING.value, O.UNKNOWN.value} for status in statuses
        ):
            target = T.TERMINATED
        elif any(status in {O.FAILED.value, O.CANCELLED_PRE_SUBMIT.value} for status in statuses):
            target = (
                T.PARTIALLY_COMPLETED
                if any(status == O.SUCCEEDED.value for status in statuses)
                else T.TERMINATED
            )
        elif any(status == O.SUCCEEDED.value for status in statuses):
            target = T.PARTIALLY_COMPLETED
        elif any(status in {O.SUBMITTING.value, O.UNKNOWN.value} for status in statuses):
            target = T.EXECUTING
        elif any(status in {O.WAITING_APPROVAL.value, O.AUTHORIZED.value} for status in statuses):
            target = T.WAITING_APPROVAL
        else:
            target = T.EXECUTING
        if task["status"] == T.PLANNING.value and target in {T.COMPLETED, T.PARTIALLY_COMPLETED}:
            self._task_state(c, task, T.EXECUTING)
        self._task_state(c, task, target)

    def run(self, actor: Actor, task_ref: str) -> dict:
        self.security.require_scope(actor, "tasks.write")
        with self.db.task_lock(task_ref) as acquired:
            if not acquired:
                return self.get_task(actor, task_ref) | {"run_in_progress": True}
            with self.db.transaction() as c:
                task = self._task(c, actor.subject_ref, task_ref, True)
                if task["stop_requested"] or task["status"] in {
                    T.COMPLETED.value,
                    T.TERMINATED.value,
                }:
                    return self.get_task(actor, task_ref)
                require(
                    task["current_version"] > 0,
                    "CLARIFICATION_REQUIRED",
                    "任务需要补全业务信息。",
                    409,
                )
                c.execute(
                    "UPDATE tasks SET run_requested=true,runner_token=%s WHERE task_ref=%s",
                    (actor.token_digest, task_ref),
                )
                self._task_state(c, task, T.EXECUTING)
                plan = c.execute(
                    "SELECT steps FROM plans WHERE task_ref=%s AND version=%s",
                    (task_ref, task["current_version"]),
                ).fetchone()["steps"]
            steps = {step["step_ref"]: step for step in plan}
            for step_ref in TopologicalSorter(
                {step["step_ref"]: step["depends_on"] for step in plan}
            ).static_order():
                step = steps[step_ref]
                with self.db.transaction() as c:
                    task = self._task(c, actor.subject_ref, task_ref)
                    operation = self._operation(c, actor.subject_ref, step["operation_ref"])
                    dependencies = [
                        self._operation(c, actor.subject_ref, steps[name]["operation_ref"])
                        for name in step["depends_on"]
                    ]
                if operation["status"] in {O.SUBMITTING.value, O.UNKNOWN.value}:
                    self._reconcile(actor.subject_ref, operation["operation_ref"])
                    continue
                if (
                    operation["status"]
                    in {O.SUCCEEDED.value, O.FAILED.value, O.CANCELLED_PRE_SUBMIT.value}
                    or task["stop_requested"]
                ):
                    continue
                if any(
                    item["status"] in {O.FAILED.value, O.CANCELLED_PRE_SUBMIT.value}
                    for item in dependencies
                ):
                    with self.db.transaction() as c:
                        row = self._operation(
                            c, actor.subject_ref, operation["operation_ref"], True
                        )
                        if row["status"] in {
                            O.PREPARING.value,
                            O.WAITING_APPROVAL.value,
                            O.AUTHORIZED.value,
                        }:
                            self._op_state(
                                c, row, O.CANCELLED_PRE_SUBMIT, error="DEPENDENCY_FAILED"
                            )
                    continue
                if any(item["status"] != O.SUCCEEDED.value for item in dependencies):
                    continue
                if step["funds_available_after"] and any(
                    not item["result"].get("funds_available")
                    for item in dependencies
                    if item["step_ref"] in step["funds_available_after"]
                ):
                    continue
                self._execute(actor, operation["operation_ref"])
            with self.db.transaction() as c:
                self._refresh(c, actor.subject_ref, task_ref)
        return self.get_task(actor, task_ref)

    def recover(self, limit: int = 100) -> dict:
        with self.db.transaction() as c:
            tasks = c.execute(
                "SELECT DISTINCT t.task_ref,t.subject_ref,t.runner_token,t.run_requested,t.stop_requested FROM tasks t LEFT JOIN operations o USING(task_ref) WHERE o.status IN ('SUBMITTING','UNKNOWN') OR (t.run_requested AND NOT t.stop_requested AND t.status NOT IN ('COMPLETED','TERMINATED') AND (t.status='EXECUTING' OR o.status='AUTHORIZED')) LIMIT %s",
                (limit,),
            ).fetchall()
        recovered = 0
        for task in tasks:
            with self.db.task_lock(task["task_ref"]) as acquired:
                if not acquired:
                    continue
                with self.db.transaction() as c:
                    rows = c.execute(
                        "SELECT operation_ref FROM operations WHERE task_ref=%s AND status IN ('SUBMITTING','UNKNOWN')",
                        (task["task_ref"],),
                    ).fetchall()
                for row in rows:
                    self._reconcile(task["subject_ref"], row["operation_ref"])
                    recovered += 1
                with self.db.transaction() as c:
                    self._refresh(c, task["subject_ref"], task["task_ref"])
            if task["run_requested"] and not task["stop_requested"] and task["runner_token"]:
                try:
                    actor = self.security.authenticate_digest(task["runner_token"])
                    self.run(actor, task["task_ref"])
                except ServiceError as exc:
                    with self.db.transaction() as c:
                        c.execute(
                            "UPDATE tasks SET run_requested=false WHERE task_ref=%s",
                            (task["task_ref"],),
                        )
                        self._event(c, task["task_ref"], "resume_blocked", {"code": exc.code})
        return {"tasks_examined": len(tasks), "operations_reconciled": recovered}

    def callback(
        self,
        event_ref: str,
        operation_ref: str,
        timestamp: int,
        body: bytes,
        signature: str,
        secret: str | None,
    ) -> dict:
        require(secret is not None, "CALLBACK_DISABLED", "回调接收尚未配置。", 503)
        require(
            abs(datetime.now(UTC).timestamp() - timestamp) <= 300,
            "STALE_CALLBACK",
            "回调已过期。",
            401,
        )
        expected = hmac.new(
            secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        require(
            hmac.compare_digest(expected, signature), "INVALID_SIGNATURE", "回调签名无效。", 401
        )
        fingerprint = hashlib.sha256(body).hexdigest()
        with self.db.transaction() as c:
            operation = c.execute(
                "SELECT * FROM operations WHERE operation_ref=%s", (operation_ref,)
            ).fetchone()
            require(operation is not None, "OPERATION_NOT_FOUND", "操作不存在。", 404)
            old = c.execute(
                "INSERT INTO callback_events(event_ref,operation_ref,digest) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING event_ref",
                (event_ref, operation_ref, fingerprint),
            ).fetchone()
            if old is None:
                row = c.execute(
                    "SELECT digest FROM callback_events WHERE event_ref=%s", (event_ref,)
                ).fetchone()
                require(
                    row["digest"] == fingerprint,
                    "CALLBACK_CONFLICT",
                    "回调编号已绑定其他内容。",
                    409,
                )
        # 回调状态仅是通知，最终状态重新查询银行，不采信通知里的成功声明。
        with self.db.task_lock(operation["task_ref"]) as acquired:
            if acquired:
                self._reconcile(operation["subject_ref"], operation_ref, fence=False)
                with self.db.transaction() as c:
                    self._refresh(c, operation["subject_ref"], operation["task_ref"])
        return {"event_ref": event_ref, "accepted": True, "duplicate": old is None}
