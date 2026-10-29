"""按受信银行目录连接和导入资产；不接受客户端提供服务地址或账号归属。"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import Field

from finguard.backend.catalog import RequestModel
from finguard.backend.errors import require
from finguard.domain.values import Reference, new_ref


class ConnectRequest(RequestModel):
    request_ref: Reference
    bank_ref: Reference


class ImportRequest(RequestModel):
    asset_refs: list[Reference] = Field(min_length=1, max_length=100)


class EnrollmentAdapter(Protocol):
    def assets(self, subject: str, connection_ref: str) -> list[dict]: ...
    def disconnect(self, subject: str, connection_ref: str) -> None: ...


@dataclass(frozen=True)
class EnrollmentBank:
    bank_ref: str
    name: str
    provider: str
    environment: str
    adapter: EnrollmentAdapter


class SimulationEnrollment:
    def __init__(self, bank):
        self.bank = bank

    def assets(self, subject, connection_ref):
        accounts = self.bank.list_accounts(subject)
        cards = self.bank.list_services(subject, "card")
        return [
            dict(
                asset_ref="account:" + a["account_ref"],
                remote_ref=a["account_ref"],
                kind="account",
                display_name="模拟账户",
                state="ACTIVE" if a["active"] else "INACTIVE",
            )
            for a in accounts
        ] + [
            dict(
                asset_ref="card:" + a["resource_ref"],
                remote_ref=a["resource_ref"],
                kind="card",
                display_name="模拟银行卡",
                state=a["state"],
            )
            for a in cards
        ]

    def disconnect(self, subject, connection_ref):
        # 模拟身份不持有独立的银行 OAuth 授权；真实适配器须处理自身授权生命周期。
        pass


class Wallet:
    def __init__(self, backend, banks=()):
        self.backend = backend
        self.db = backend.db
        self.banks = {b.bank_ref: b for b in banks}
        require(len(self.banks) == len(banks), "BANK_CATALOG_CONFLICT", "银行目录编号重复。", 500)
        require(
            all(b.environment == "simulation" for b in banks),
            "ENROLLMENT_AUTH_UNAVAILABLE",
            "真实银行授权流程尚未实现，不能注册为可连接银行。",
            503,
        )

    def _scope(self, actor, write=False):
        for scope in ("accounts.read", "services.read", *(["tasks.write"] if write else [])):
            self.backend.security.require_scope(actor, scope)

    def catalog(self, actor):
        self._scope(actor)
        return [
            dict(
                bank_ref=b.bank_ref,
                name=b.name,
                provider=b.provider,
                environment=b.environment,
                capabilities=["accounts.read", "cards.read"],
                authorization="current_simulation_identity",
            )
            for b in self.banks.values()
        ]

    def _connection(self, actor, reference):
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT * FROM wallet_connections WHERE connection_ref=%s AND subject_ref=%s",
                (reference, actor.subject_ref),
            ).fetchone()
        require(row is not None, "NOT_FOUND", "银行连接不存在。", 404)
        return row

    def _bank(self, connection):
        bank = self.banks.get(connection["bank_ref"])
        require(bank is not None, "BANK_NOT_CONFIGURED", "银行连接器当前不可用。", 503)
        require(
            connection["state"] == "CONNECTED",
            "BANK_DISCONNECTED",
            "银行连接已移除，请重新连接。",
            409,
        )
        return bank

    def connect(self, actor, request):
        self._scope(actor, True)
        bank = self.banks.get(request.bank_ref)
        require(bank is not None, "BANK_NOT_CONFIGURED", "所选银行尚未接入。", 422)
        with self.db.task_lock("wallet:" + actor.subject_ref) as acquired:
            require(acquired, "WALLET_BUSY", "卡包正在更新，请稍后核对。", 409)
            with self.db.transaction() as c:
                old = c.execute(
                    "SELECT * FROM wallet_connections WHERE subject_ref=%s AND request_ref=%s",
                    (actor.subject_ref, request.request_ref),
                ).fetchone()
            if old:
                require(
                    old["bank_ref"] == request.bank_ref,
                    "REQUEST_CONFLICT",
                    "请求编号已绑定其他银行。",
                    409,
                )
                self._bank(old)
                return old
            with self.db.transaction() as c:
                existing = c.execute(
                    "SELECT 1 FROM wallet_connections WHERE subject_ref=%s AND bank_ref=%s AND state='CONNECTED'",
                    (actor.subject_ref, request.bank_ref),
                ).fetchone()
            require(
                not existing, "BANK_ALREADY_CONNECTED", "银行已连接，请在已有连接中继续导入。", 409
            )
            reference = new_ref("connection")
            # 通过实际银行接口验证当前主体可以读取资源，失败时不创建连接。
            bank.adapter.assets(actor.subject_ref, reference)
            with self.db.transaction() as c:
                return c.execute(
                    "INSERT INTO wallet_connections(connection_ref,subject_ref,request_ref,bank_ref) VALUES (%s,%s,%s,%s) RETURNING *",
                    (reference, actor.subject_ref, request.request_ref, request.bank_ref),
                ).fetchone()

    def available(self, actor, reference):
        self._scope(actor)
        connection = self._connection(actor, reference)
        return self._bank(connection).adapter.assets(actor.subject_ref, reference)

    def snapshot(self, actor):
        self._scope(actor)
        with self.db.transaction() as c:
            connections = c.execute(
                "SELECT * FROM wallet_connections WHERE subject_ref=%s ORDER BY created_at DESC",
                (actor.subject_ref,),
            ).fetchall()
            assets = c.execute(
                "SELECT a.* FROM wallet_assets a JOIN wallet_connections c USING(connection_ref) WHERE c.subject_ref=%s AND c.state='CONNECTED' ORDER BY a.synced_at DESC,a.asset_ref",
                (actor.subject_ref,),
            ).fetchall()
        return dict(
            connections=[
                {
                    **c,
                    "bank_name": self.banks[c["bank_ref"]].name
                    if c["bank_ref"] in self.banks
                    else "连接器不可用",
                }
                for c in connections
            ],
            assets=assets,
        )

    def import_assets(self, actor, reference, request):
        self._scope(actor, True)
        with self.db.task_lock("wallet:" + actor.subject_ref) as acquired:
            require(acquired, "WALLET_BUSY", "卡包正在更新。", 409)
            available = {a["asset_ref"]: a for a in self.available(actor, reference)}
            require(
                len(set(request.asset_refs)) == len(request.asset_refs)
                and all(r in available for r in request.asset_refs),
                "INVALID_BANK_ASSET",
                "所选资产不属于此连接或已不可用，请重新读取。",
                422,
            )
            with self.db.transaction() as c:
                for ref in request.asset_refs:
                    a = available[ref]
                    c.execute(
                        "INSERT INTO wallet_assets(asset_ref,connection_ref,kind,remote_ref,display_name,state) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(connection_ref,kind,remote_ref) DO UPDATE SET display_name=EXCLUDED.display_name,state=EXCLUDED.state,synced_at=now()",
                        (
                            new_ref("asset"),
                            reference,
                            a["kind"],
                            a["remote_ref"],
                            a["display_name"],
                            a["state"],
                        ),
                    )
        return self.snapshot(actor)

    def sync(self, actor, reference):
        self._scope(actor, True)
        with self.db.task_lock("wallet:" + actor.subject_ref) as acquired:
            require(acquired, "WALLET_BUSY", "卡包正在更新。", 409)
            available = {(a["kind"], a["remote_ref"]): a for a in self.available(actor, reference)}
            with self.db.transaction() as c:
                rows = c.execute(
                    "SELECT * FROM wallet_assets WHERE connection_ref=%s", (reference,)
                ).fetchall()
                for row in rows:
                    fresh = available.get((row["kind"], row["remote_ref"]))
                    c.execute(
                        "UPDATE wallet_assets SET state=%s,display_name=%s,synced_at=now() WHERE asset_ref=%s",
                        (
                            fresh["state"] if fresh else "UNAVAILABLE",
                            fresh["display_name"] if fresh else row["display_name"],
                            row["asset_ref"],
                        ),
                    )
        return self.snapshot(actor)

    def disconnect(self, actor, reference):
        self._scope(actor, True)
        with self.db.task_lock("wallet:" + actor.subject_ref) as acquired:
            require(acquired, "WALLET_BUSY", "卡包正在更新。", 409)
            connection = self._connection(actor, reference)
            if connection["state"] == "CONNECTED":
                self._bank(connection).adapter.disconnect(actor.subject_ref, reference)
                with self.db.transaction() as c:
                    c.execute(
                        "UPDATE wallet_connections SET state='DISCONNECTED' WHERE connection_ref=%s",
                        (reference,),
                    )
        return self.snapshot(actor)
