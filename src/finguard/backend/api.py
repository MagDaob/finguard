"""面向前端和测试客户端的认证 API。"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, ValidationError

from finguard.backend.catalog import (
    ApprovalRequest,
    DelegationRequest,
    ReplanRequest,
    RequestModel,
    TaskCreate,
    required_scopes,
)
from finguard.backend.config import Settings
from finguard.backend.database import Database
from finguard.backend.errors import ServiceError, require
from finguard.backend.evaluation import ReplayRunner, ReplaySuite
from finguard.backend.planning import GatewayPlanner
from finguard.backend.policies import Policies, PolicyPlanner
from finguard.backend.security import Actor
from finguard.backend.service import Backend
from finguard.domain.values import Reference

logger = logging.getLogger("finguard")


class BodyLimitMiddleware:
    def __init__(self, app, maximum: int = 1048576):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.maximum:
                return await JSONResponse(
                    {"error": {"code": "BODY_TOO_LARGE", "message": "请求体超出限制。"}},
                    status_code=413,
                )(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class DelegationBinding(RequestModel):
    delegation_ref: Reference


class CallbackRequest(RequestModel):
    event_ref: Reference
    operation_ref: Reference
    reported_status: Annotated[str, Field(max_length=64)] | None = None


class PromptCandidate(RequestModel):
    prompt: Annotated[str, Field(min_length=1, max_length=8000)]


class PolicyRelease(RequestModel):
    expected_evaluation_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    reviewed: Annotated[bool, Field(strict=True)]


def create_app(settings: Settings | None = None, planner=None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    if planner is None and settings.model_url:
        planner = GatewayPlanner(
            settings.model_url,
            settings.model_api_key.get_secret_value() if settings.model_api_key else None,
            settings.model_allow_local_http,
        )
    gateway = planner if isinstance(planner, GatewayPlanner) else None
    backend = Backend(database, PolicyPlanner(database, gateway) if gateway else planner)
    policies = Policies(database, gateway)

    async def recover_loop(app):
        while True:
            try:
                await asyncio.to_thread(backend.recover)
                app.state.worker_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                # 不把连接字符串、外部文本和令牌写入日志。
                app.state.worker_error = "RECOVERY_UNAVAILABLE"
                logger.error("recovery_unavailable")
            await asyncio.sleep(settings.worker_interval_seconds)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(database.migrate)
        app.state.worker_error = None
        worker = asyncio.create_task(recover_loop(app)) if settings.worker_enabled else None
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker

    app = FastAPI(title="FinGuard simulated financial backend", version="0.2.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.state.backend = backend
    app.state.settings = settings
    bearer = HTTPBearer(auto_error=False)

    def current_actor(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Actor:
        require(
            credentials is not None and credentials.scheme.lower() == "bearer",
            "UNAUTHENTICATED",
            "需要有效的访问令牌。",
            401,
        )
        return backend.security.authenticate(credentials.credentials)

    # 函数局部依赖必须使用实际对象，供 FastAPI 生成可验证的 OpenAPI。
    actor_dependency = Depends(current_actor)

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}},
            status_code=exc.status,
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(_request: Request, exc):
        errors = [
            {"loc": list(item["loc"]), "type": item["type"], "message": item["msg"]}
            for item in exc.errors()
        ]
        return JSONResponse(
            {"error": {"code": "INVALID_REQUEST", "details": errors}}, status_code=422
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(_request: Request, _exc):
        return JSONResponse(
            {"error": {"code": "DATABASE_UNAVAILABLE", "message": "数据库暂时不可用。"}},
            status_code=503,
        )

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "up", "mode": "simulation"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        with database.transaction() as c:
            c.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
        require(
            not getattr(app.state, "worker_error", None),
            "WORKER_UNAVAILABLE",
            "恢复任务暂时不可用。",
            503,
        )
        return {"status": "ready", "mode": "simulation"}

    @app.get("/v1/me", tags=["identity"])
    def me(actor: Actor = actor_dependency):
        return {"subject_ref": actor.subject_ref, "scopes": sorted(actor.scopes)}

    @app.post("/v1/tokens/revoke-current", tags=["identity"])
    def revoke_current(actor: Actor = actor_dependency):
        backend.security.revoke(actor)
        return {"revoked": True}

    @app.get("/v1/accounts", tags=["business"])
    def accounts(actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "accounts.read")
        return backend.bank.list_accounts(actor.subject_ref)

    @app.get("/v1/recipients", tags=["business"])
    def recipients(actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "accounts.read")
        return backend.bank.list_recipients(actor.subject_ref)

    @app.get("/v1/cards", tags=["business"])
    def cards(actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "services.read")
        return backend.bank.list_services(actor.subject_ref, "card")

    @app.get("/v1/subscriptions", tags=["business"])
    def subscriptions(actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "services.read")
        return backend.bank.list_services(actor.subject_ref, "subscription")

    @app.get("/v1/products", tags=["business"])
    def products(actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "products.read")
        with database.transaction() as c:
            risk = c.execute(
                "SELECT risk_level FROM principals WHERE subject_ref=%s", (actor.subject_ref,)
            ).fetchone()["risk_level"]
        return [
            {
                **product,
                "risk_eligible": product["risk_level"] <= risk,
                "simulation_fixed_value": True,
            }
            for product in backend.bank.list_products()
        ]

    @app.get("/v1/positions", tags=["business"])
    def positions(actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "products.read")
        backend.security.require_scope(actor, "accounts.read")
        return backend.bank.list_positions(actor.subject_ref)

    @app.get("/v1/tools", tags=["planning"])
    def tools(actor: Actor = actor_dependency):
        from finguard.backend.catalog import ACTIONS

        backend.security.require_scope(actor, "tasks.read")
        return [
            {
                "action": name,
                "arguments_schema": item.arguments.model_json_schema(),
                "requires_approval": item.write,
                "required_scopes": required_scopes(name),
                "idempotency": "STABLE_OPERATION_REF",
                "timeout_policy": "RECONCILE",
                "simulation": True,
            }
            for name, item in ACTIONS.items()
        ]

    @app.get("/v1/accounts/{account_ref}/ledger", tags=["business"])
    def ledger(
        account_ref: str,
        actor: Actor = actor_dependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: str | None = None,
    ):
        backend.security.require_scope(actor, "accounts.read")
        backend.security.require_scope(actor, "transactions.read")
        with database.transaction() as c:
            backend.bank._account(c, actor.subject_ref, account_ref)
            return c.execute(
                "SELECT entry_ref,operation_ref,amount_minor,category,refund_of,occurred_at FROM bank_ledger WHERE account_ref=%s AND (%s::text IS NULL OR entry_ref<%s) ORDER BY entry_ref DESC LIMIT %s",
                (account_ref, before, before, limit),
            ).fetchall()

    @app.get("/v1/delegations", tags=["authorization"])
    def delegations(
        actor: Actor = actor_dependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: str | None = None,
    ):
        backend.security.require_scope(actor, "approvals.write")
        with database.transaction() as c:
            return c.execute(
                "SELECT * FROM delegations WHERE subject_ref=%s AND (%s::text IS NULL OR delegation_ref<%s) ORDER BY delegation_ref DESC LIMIT %s",
                (actor.subject_ref, before, before, limit),
            ).fetchall()

    @app.get("/v1/operations/{operation_ref}/approvals", tags=["authorization"])
    def approvals(operation_ref: str, actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "approvals.write")
        with database.transaction() as c:
            backend._operation(c, actor.subject_ref, operation_ref)
            return c.execute(
                "SELECT * FROM approvals WHERE operation_ref=%s AND subject_ref=%s ORDER BY issued_at",
                (operation_ref, actor.subject_ref),
            ).fetchall()

    @app.get("/v1/operations/{operation_ref}/compensation-preview", tags=["authorization"])
    def compensation_preview(operation_ref: str, actor: Actor = actor_dependency):
        return backend.compensation_preview(actor, operation_ref)

    @app.post("/v1/tasks", status_code=201, tags=["tasks"])
    def create_task(request: TaskCreate, actor: Actor = actor_dependency):
        return backend.create_task(actor, request)

    @app.get("/v1/tasks", tags=["tasks"])
    def tasks(
        actor: Actor = actor_dependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: str | None = None,
    ):
        return backend.list_tasks(actor, limit, before)

    @app.get("/v1/tasks/{task_ref}", tags=["tasks"])
    def task(task_ref: str, actor: Actor = actor_dependency):
        return backend.get_task(actor, task_ref)

    @app.post("/v1/tasks/{task_ref}/plans", tags=["tasks"])
    def replan(task_ref: str, request: ReplanRequest, actor: Actor = actor_dependency):
        return backend.replan(actor, task_ref, request)

    @app.post("/v1/tasks/{task_ref}/run", tags=["tasks"])
    def run(task_ref: str, actor: Actor = actor_dependency):
        return backend.run(actor, task_ref)

    @app.post("/v1/tasks/{task_ref}/stop", tags=["tasks"])
    def stop(task_ref: str, actor: Actor = actor_dependency):
        return backend.stop(actor, task_ref)

    @app.get("/v1/tasks/{task_ref}/trace", tags=["diagnostics"])
    def trace(
        task_ref: str,
        actor: Actor = actor_dependency,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ):
        return backend.trace(actor, task_ref, after, limit)

    @app.get("/v1/operations/{operation_ref}/preview", tags=["authorization"])
    def preview(operation_ref: str, actor: Actor = actor_dependency):
        with database.transaction() as c:
            operation = backend._operation(c, actor.subject_ref, operation_ref)
        for scope in required_scopes(operation["action"]):
            backend.security.require_scope(actor, scope)
        return {
            "operation_ref": operation_ref,
            "digest": operation["digest"],
            "preview": backend.bank.preview(
                actor.subject_ref, operation["action"], operation["arguments"]
            ),
        }

    @app.post("/v1/operations/{operation_ref}/approvals", tags=["authorization"])
    def approve(operation_ref: str, request: ApprovalRequest, actor: Actor = actor_dependency):
        return backend.approve(actor, operation_ref, request)

    @app.post("/v1/approvals/{approval_ref}/revoke", tags=["authorization"])
    def revoke_approval(approval_ref: str, actor: Actor = actor_dependency):
        return backend.revoke_approval(actor, approval_ref)

    @app.post("/v1/delegations", status_code=201, tags=["authorization"])
    def add_delegation(request: DelegationRequest, actor: Actor = actor_dependency):
        return backend.add_delegation(actor, request)

    @app.post("/v1/delegations/{delegation_ref}/revoke", tags=["authorization"])
    def revoke_delegation(delegation_ref: str, actor: Actor = actor_dependency):
        return backend.revoke_delegation(actor, delegation_ref)

    @app.post("/v1/operations/{operation_ref}/delegation", tags=["authorization"])
    def bind_delegation(
        operation_ref: str, request: DelegationBinding, actor: Actor = actor_dependency
    ):
        return backend.bind_delegation(actor, operation_ref, request.delegation_ref)

    @app.post("/v1/callbacks/bank", tags=["callbacks"])
    async def callback(
        request: Request,
        x_finguard_timestamp: str = Header(default=""),
        x_finguard_signature: str = Header(default=""),
    ):
        body = await request.body()
        parsed = CallbackRequest.model_validate_json(body)
        try:
            timestamp = int(x_finguard_timestamp)
        except ValueError as exc:
            raise ServiceError("INVALID_SIGNATURE", "回调时间无效。", 401) from exc
        return await asyncio.to_thread(
            backend.callback,
            parsed.event_ref,
            parsed.operation_ref,
            timestamp,
            body,
            x_finguard_signature,
            settings.callback_secret.get_secret_value() if settings.callback_secret else None,
        )

    @app.get("/v1/metrics", tags=["diagnostics"])
    def metrics(actor: Actor = actor_dependency):
        backend.security.require_scope(actor, "tasks.read")
        with database.transaction() as c:
            task_counts = c.execute(
                "SELECT status,count(*) AS count FROM tasks WHERE subject_ref=%s GROUP BY status",
                (actor.subject_ref,),
            ).fetchall()
            operation_counts = c.execute(
                "SELECT status,count(*) AS count FROM operations WHERE subject_ref=%s GROUP BY status",
                (actor.subject_ref,),
            ).fetchall()
        return {"tasks": task_counts, "operations": operation_counts, "simulation": True}

    @app.get("/v1/export/{task_ref}", tags=["diagnostics"])
    def export(task_ref: str, actor: Actor = actor_dependency):
        task = backend.get_task(actor, task_ref)
        trace = backend.trace(actor, task_ref, limit=1000)
        return jsonable_encoder(
            {
                "format_version": "1",
                "task": task,
                "trace": trace,
                "planner_mode": "gateway" if settings.model_url else "deterministic",
                "contains_credentials": False,
            }
        )

    def evaluation_access(actor: Actor):
        backend.security.require_scope(actor, "evaluation.manage")
        require(settings.evaluation_enabled, "EVALUATION_DISABLED", "离线评估入口尚未启用。", 503)

    @app.post("/v1/evaluation/replays", tags=["evaluation"])
    def replay(suite: ReplaySuite, actor: Actor = actor_dependency):
        evaluation_access(actor)
        require(
            len(suite.cases) <= 5,
            "SUITE_TOO_LARGE",
            "HTTP 回放最多包含五个用例，更大套件请使用命令行。",
            422,
        )
        return ReplayRunner(database).run(suite)

    @app.post("/v1/evaluation/candidates", status_code=201, tags=["evaluation"])
    def candidate(request: PromptCandidate, actor: Actor = actor_dependency):
        evaluation_access(actor)
        return policies.create(actor, request.prompt)

    @app.post("/v1/evaluation/candidates/from-failures", status_code=201, tags=["evaluation"])
    def candidate_from_failures(suite: ReplaySuite, actor: Actor = actor_dependency):
        evaluation_access(actor)
        require(len(suite.cases) <= 5, "SUITE_TOO_LARGE", "HTTP 失败归因最多包含五个用例。", 422)
        return policies.propose_from_failures(actor, suite)

    @app.get("/v1/evaluation/candidates/{candidate_ref}", tags=["evaluation"])
    def candidate_status(candidate_ref: str, actor: Actor = actor_dependency):
        evaluation_access(actor)
        with database.transaction() as c:
            row = c.execute(
                "SELECT * FROM optimization_candidates WHERE candidate_ref=%s", (candidate_ref,)
            ).fetchone()
        require(row is not None, "CANDIDATE_NOT_FOUND", "候选不存在。", 404)
        return row

    @app.post("/v1/evaluation/candidates/{candidate_ref}/evaluate", tags=["evaluation"])
    def evaluate(candidate_ref: str, suite: ReplaySuite, actor: Actor = actor_dependency):
        evaluation_access(actor)
        require(len(suite.cases) <= 5, "SUITE_TOO_LARGE", "HTTP 对照评估最多包含五个用例。", 422)
        return policies.evaluate(actor, candidate_ref, suite)

    @app.post("/v1/evaluation/candidates/{candidate_ref}/release", tags=["evaluation"])
    def release(candidate_ref: str, request: PolicyRelease, actor: Actor = actor_dependency):
        evaluation_access(actor)
        return policies.release(
            actor, candidate_ref, request.expected_evaluation_digest, request.reviewed
        )

    @app.post("/v1/evaluation/candidates/{candidate_ref}/rollback", tags=["evaluation"])
    def rollback(candidate_ref: str, actor: Actor = actor_dependency):
        evaluation_access(actor)
        return policies.rollback(actor, candidate_ref)

    return app
