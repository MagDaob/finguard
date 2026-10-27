"""独立模拟银行，仅接受可信项目服务的认证调用。"""

import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from finguard_bank_contracts.actions import ACTIONS, canonical, operation_digest
from finguard_bank_contracts.errors import (
    NotSubmitted,
    ResultUnknown,
    ServiceError,
    WorkerCrash,
    require,
)
from finguard_bank_contracts.values import Reference
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from finguard_simulator.bank import SimulatedBank
from finguard_simulator.config import Settings
from finguard_simulator.database import Database


class Call(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: Reference | None = None
    kind: Literal["card", "subscription"] | None = None
    account_ref: Reference | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 50
    before: Reference | None = None
    action: Reference | None = None
    arguments: dict | None = None
    operation_ref: Reference | None = None
    operation: Reference | None = None
    fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    fence_if_absent: bool = False


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    bank = SimulatedBank(database)

    @asynccontextmanager
    async def lifespan(app):
        database.migrate()
        yield

    app = FastAPI(title="FinGuard Bank Simulator", lifespan=lifespan)
    app.state.bank = bank

    def authenticated(authorization: Annotated[str | None, Header()] = None):
        require(
            authorization is not None
            and hmac.compare_digest(
                authorization.encode(), f"Bearer {settings.api_key.get_secret_value()}".encode()
            ),
            "UNAUTHENTICATED",
            "需要有效的服务凭据。",
            401,
        )

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, exc):
        return JSONResponse({"error": {"code": "INVALID_REQUEST"}}, status_code=422)

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc):
        return JSONResponse({"error": {"code": "DATABASE_UNAVAILABLE"}}, status_code=503)

    @app.get("/health/ready")
    def ready():
        with database.transaction() as c:
            c.execute("SELECT 1 FROM simulator_migrations LIMIT 1")
        return {"status": "ready", "service": "bank-simulator"}

    @app.post("/v1/bank/{method}", dependencies=[Depends(authenticated)])
    def call(
        method: Literal[
            "list_accounts",
            "list_recipients",
            "list_services",
            "list_products",
            "list_positions",
            "list_ledger",
            "preview",
            "execute",
            "lookup",
            "verify",
        ],
        body: Call,
    ):
        payload = body.model_dump()
        if method != "list_products":
            require(body.subject is not None, "INVALID_REQUEST", "缺少主体。", 422)
        if method in {"preview", "execute"} or (method == "lookup" and body.fence_if_absent):
            require(
                body.action in ACTIONS and body.arguments is not None,
                "INVALID_REQUEST",
                "缺少业务上下文。",
                422,
            )
            payload["arguments"] = (
                ACTIONS[body.action]
                .arguments.model_validate_json(canonical(body.arguments))
                .model_dump(mode="json")
            )
            if method == "lookup":
                require(
                    body.fingerprint
                    == operation_digest(body.subject, body.action, payload["arguments"]),
                    "OPERATION_CONFLICT",
                    "核实摘要与参数不匹配。",
                    409,
                )
        fields = {
            "list_accounts": ("subject",),
            "list_recipients": ("subject",),
            "list_services": ("subject", "kind"),
            "list_products": (),
            "list_positions": ("subject",),
            "list_ledger": ("subject", "account_ref", "limit", "before"),
            "preview": ("subject", "action", "arguments"),
            "execute": ("subject", "operation_ref", "action", "arguments"),
            "lookup": (
                "subject",
                "operation",
                "fingerprint",
                "fence_if_absent",
                "action",
                "arguments",
            ),
            "verify": ("subject", "operation", "fingerprint"),
        }[method]
        for name in fields:
            if name not in {"before", "action", "arguments"} or method in {"preview", "execute"}:
                require(payload[name] is not None, "INVALID_REQUEST", "缺少请求字段。", 422)
        try:
            result = getattr(bank, method)(**{name: payload[name] for name in fields})
            return {"result": jsonable_encoder(result)}
        except NotSubmitted:
            return JSONResponse({"error": {"code": "NOT_SUBMITTED"}}, status_code=503)
        except (ResultUnknown, WorkerCrash):
            return JSONResponse({"error": {"code": "RESULT_UNKNOWN"}}, status_code=503)

    return app
