"""PostgreSQL 事务和任务互斥；业务请求不跨外部调用持有数据库事务。"""

import re
from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class Database:
    def __init__(self, dsn: str, schema: str = "finguard"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("数据库命名空间无效。")
        self.dsn = dsn
        self.schema = schema

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as connection:
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {} ").format(sql.Identifier(self.schema))
            )
            connection.execute("SET LOCAL statement_timeout = '15s'")
            connection.execute("SET LOCAL lock_timeout = '8s'")
            yield connection

    def migrate(self) -> None:
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"migration:{self.schema}",),
            )
            connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema))
            )
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema))
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for resource in sorted(
                files("finguard.backend.migrations").iterdir(), key=lambda item: item.name
            ):
                if not resource.name.endswith(".sql"):
                    continue
                version = int(resource.name.split("-", 1)[0])
                if connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=%s", (version,)
                ).fetchone():
                    continue
                connection.execute(resource.read_text(encoding="utf-8"), prepare=False)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))

    @contextmanager
    def task_lock(self, task_ref: str):
        with psycopg.connect(self.dsn, autocommit=True, connect_timeout=5) as connection:
            key = f"{self.schema}:task:{task_ref}"
            acquired = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
            ).fetchone()[0]
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
