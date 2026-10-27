"""从旧版单库升级时，保留已有模拟账本。"""

import hashlib
from importlib.resources import files
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from finguard.backend.database import Database


@pytest.mark.postgres
def test_legacy_bank_data_is_archived_instead_of_dropped(database):
    schema = "fg_legacy_test_" + uuid4().hex[:20]
    archive = "legacy_bank_" + hashlib.md5(schema.encode(), usedforsecurity=False).hexdigest()[:20]
    old = Database(database.dsn, schema)
    try:
        with psycopg.connect(database.dsn) as c:
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            c.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            c.execute(
                files("finguard.backend.migrations")
                .joinpath("001-initial.sql")
                .read_text(encoding="utf-8"),
                prepare=False,
            )
            c.execute(
                "CREATE TABLE schema_migrations(version integer PRIMARY KEY, applied_at timestamptz DEFAULT now())"
            )
            c.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            c.execute("INSERT INTO principals VALUES ('owner','Legacy',1,true)")
            c.execute(
                "INSERT INTO bank_accounts VALUES ('legacy-account','owner',100,100,'CNY',true)"
            )
        old.migrate()
        with old.transaction() as c:
            assert (
                c.execute("SELECT to_regclass('bank_accounts') AS name").fetchone()["name"] is None
            )
            assert (
                c.execute(
                    sql.SQL("SELECT balance_minor FROM {}.bank_accounts").format(
                        sql.Identifier(archive)
                    )
                ).fetchone()["balance_minor"]
                == 100
            )
    finally:
        with psycopg.connect(database.dsn) as c:
            for name in (archive, schema):
                c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))
