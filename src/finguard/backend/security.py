"""高熵访问令牌只保存摘要；主体和权限均由服务端记录解析。"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from finguard.backend.catalog import USER_SCOPES
from finguard.backend.database import Database
from finguard.backend.errors import require

ADMIN_SCOPES = (*USER_SCOPES, "evaluation.manage", "simulation.manage")


@dataclass(frozen=True)
class Actor:
    subject_ref: str
    token_digest: str
    scopes: frozenset[str]


class Security:
    def __init__(self, database: Database):
        self.db = database

    def provision(self, subject: str, name: str, risk_level: int = 1) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO principals(subject_ref,display_name,risk_level) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (subject, name, risk_level),
            )

    def issue_token(
        self, subject: str, scopes: tuple[str, ...] = USER_SCOPES, ttl_seconds: int = 86400
    ) -> str:
        require(
            set(scopes) <= set(ADMIN_SCOPES) and 0 < ttl_seconds <= 2592000,
            "INVALID_TOKEN_POLICY",
            "令牌权限或期限无效。",
        )
        token = secrets.token_urlsafe(32)
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT active FROM principals WHERE subject_ref=%s", (subject,)
            ).fetchone()
            require(bool(row and row["active"]), "UNKNOWN_SUBJECT", "主体不存在或已停用。", 404)
            c.execute(
                "INSERT INTO access_tokens(digest,subject_ref,scopes,expires_at) VALUES (%s,%s,%s,%s)",
                (
                    token_digest,
                    subject,
                    Jsonb(scopes),
                    datetime.now(UTC) + timedelta(seconds=ttl_seconds),
                ),
            )
        return token

    def authenticate(self, token: str) -> Actor:
        require(20 <= len(token) <= 256, "UNAUTHENTICATED", "访问令牌无效。", 401)
        return self.authenticate_digest(hashlib.sha256(token.encode()).hexdigest())

    def authenticate_digest(self, token_digest: str) -> Actor:
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT t.subject_ref,t.scopes FROM access_tokens t JOIN principals p USING(subject_ref) WHERE t.digest=%s AND NOT t.revoked AND t.expires_at>clock_timestamp() AND p.active",
                (token_digest,),
            ).fetchone()
        require(row is not None, "UNAUTHENTICATED", "访问令牌无效或已过期。", 401)
        return Actor(row["subject_ref"], token_digest, frozenset(row["scopes"]))

    def require_scope(self, actor: Actor, scope: str, connection=None) -> None:
        if connection is None:
            with self.db.transaction() as c:
                return self.require_scope(actor, scope, c)
        row = connection.execute(
            "SELECT t.scopes,t.expires_at FROM access_tokens t JOIN principals p USING(subject_ref) WHERE t.digest=%s AND t.subject_ref=%s AND NOT t.revoked AND t.expires_at>clock_timestamp() AND p.active FOR SHARE OF t,p",
            (actor.token_digest, actor.subject_ref),
        ).fetchone()
        require(row is not None, "UNAUTHENTICATED", "执行身份已失效。", 401)
        require(row["expires_at"] > datetime.now(UTC), "UNAUTHENTICATED", "执行身份已过期。", 401)
        require(scope in row["scopes"], "SCOPE_DENIED", "当前身份缺少操作权限。", 403)

    def revoke(self, actor: Actor) -> None:
        with self.db.transaction() as c:
            c.execute(
                "UPDATE access_tokens SET revoked=true WHERE digest=%s AND subject_ref=%s",
                (actor.token_digest, actor.subject_ref),
            )
