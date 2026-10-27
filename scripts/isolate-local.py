"""把本机单库原型复制到两个数据库；保留原库和模型配置。"""

import hashlib
import json
import secrets
import socket
from pathlib import Path

import psycopg
from finguard_simulator.database import Database as BankDatabase
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from finguard.backend.config import Settings
from finguard.backend.database import Database

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "artifacts/service-isolation/state.local.json"
APP_TABLES = (
    "principals",
    "access_tokens",
    "tasks",
    "plans",
    "delegations",
    "operations",
    "approvals",
    "authorization_uses",
    "evidence",
    "task_events",
    "callback_events",
    "optimization_candidates",
    "policy_events",
)
BANK_TABLES = (
    "bank_customers",
    "bank_accounts",
    "bank_recipients",
    "bank_products",
    "bank_positions",
    "bank_services",
    "bank_commands",
    "bank_ledger",
    "bank_faults",
)


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o600)


def save(state):
    private_write(STATE, json.dumps(state, indent=2) + "\n")


def set_env(path, updates):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output = []
    remaining = dict(updates)
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            remaining.pop(key, None)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in remaining.items())
    private_write(path, "\n".join(output) + "\n")


def copy_tables(source_dsn, source_schema, target_dsn, target_schema, tables, bank=False):
    counts = {}
    with psycopg.connect(source_dsn) as source, psycopg.connect(target_dsn) as target:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for table in tables:
            target_name = sql.Identifier(target_schema, table)
            if target.execute(sql.SQL("SELECT count(*) FROM {}").format(target_name)).fetchone()[0]:
                raise ValueError("Target contains data; refusing to overwrite")
            columns = [
                row[0]
                for row in target.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                    (target_schema, table),
                )
            ]
            source_table = "principals" if table == "bank_customers" else table
            source_name = sql.Identifier(source_schema, source_table)
            columns_sql = sql.SQL(",").join(map(sql.Identifier, columns))
            with source.cursor().copy(
                sql.SQL("COPY {} ({}) TO STDOUT (FORMAT BINARY)").format(source_name, columns_sql)
            ) as outbound:
                with target.cursor().copy(
                    sql.SQL("COPY {} ({}) FROM STDIN (FORMAT BINARY)").format(
                        target_name, columns_sql
                    )
                ) as inbound:
                    while block := outbound.read():
                        inbound.write(block)
            counts[table] = target.execute(
                sql.SQL("SELECT count(*) FROM {}").format(target_name)
            ).fetchone()[0]
            if not bank and table in {"task_events", "policy_events"}:
                sequence = target.execute(
                    "SELECT pg_get_serial_sequence(%s,'sequence')", (f"{target_schema}.{table}",)
                ).fetchone()[0]
                target.execute(
                    sql.SQL(
                        "SELECT setval(%s,COALESCE(max(sequence),1),count(*)>0) FROM {}"
                    ).format(target_name),
                    (sequence,),
                )
    return counts


def main():
    for port in (8000, 8001):
        with socket.socket() as listener:
            if listener.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError("Stop local services before migration")
    settings = Settings.from_env(ROOT / ".env")
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        state = {
            "source_dsn": settings.database_url.get_secret_value(),
            "source_schema": settings.database_schema,
            "service_key": secrets.token_hex(32),
            "passwords": {
                "finguard_app": secrets.token_hex(32),
                "finguard_simulator": secrets.token_hex(32),
            },
        }
        with psycopg.connect(state["source_dsn"], autocommit=True) as admin:
            for name in state["passwords"]:
                if admin.execute(
                    "SELECT 1 FROM pg_database WHERE datname=%s UNION ALL SELECT 1 FROM pg_roles WHERE rolname=%s",
                    (name, name),
                ).fetchone():
                    raise ValueError("Target database or role already exists")
        if conninfo_to_dict(state["source_dsn"]).get("host") not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("The helper only supports a local PostgreSQL source")
        save(state)
    admin_dsn = state["source_dsn"]
    dsns = {}
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        for name, password in state["passwords"].items():
            if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
                    ).format(sql.Identifier(name), sql.Literal(password))
                )
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(name), sql.Identifier(name)
                    )
                )
            admin.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
            dsns[name] = make_conninfo(
                **{
                    **conninfo_to_dict(admin_dsn),
                    "dbname": name,
                    "user": name,
                    "password": password,
                }
            )
    app_db = Database(dsns["finguard_app"], "finguard")
    bank_db = BankDatabase(dsns["finguard_simulator"], "simbank")
    app_db.migrate()
    bank_db.migrate()
    with psycopg.connect(admin_dsn) as source:
        source_exists = source.execute(
            "SELECT to_regclass(%s)", (f"{state['source_schema']}.schema_migrations",)
        ).fetchone()[0]
    if not source_exists:
        Database(admin_dsn, state["source_schema"]).migrate()
    for name, db, tables in (("app", app_db, APP_TABLES), ("bank", bank_db, BANK_TABLES)):
        if name + "_counts" not in state:
            with psycopg.connect(admin_dsn) as source:
                bank_exists = source.execute(
                    "SELECT to_regclass(%s)", (f"{state['source_schema']}.bank_accounts",)
                ).fetchone()[0]
            if name == "bank" and not bank_exists:
                archive = (
                    "legacy_bank_"
                    + hashlib.md5(
                        state["source_schema"].encode(), usedforsecurity=False
                    ).hexdigest()[:20]
                )
                with psycopg.connect(admin_dsn) as source:
                    if source.execute(
                        "SELECT to_regclass(%s)", (f"{archive}.bank_accounts",)
                    ).fetchone()[0]:
                        raise ValueError(
                            "Legacy bank data is archived; export the archive before migration"
                        )
                from finguard_simulator.demo import seed_demo

                seed_demo(bank_db)
                state[name + "_counts"] = {"fresh_demo": 1}
                save(state)
                continue
            state[name + "_counts"] = copy_tables(
                admin_dsn, state["source_schema"], db.dsn, db.schema, tables, bank=name == "bank"
            )
            save(state)
    # 运行进程只得到自己的数据库凭据；管理员连接保留在忽略的迁移记录中。
    set_env(
        ROOT / ".env",
        {
            "FINGUARD_DATABASE_URL": app_db.dsn,
            "FINGUARD_DATABASE_SCHEMA": app_db.schema,
            "FINGUARD_BANK_URL": "http://127.0.0.1:8001",
            "FINGUARD_BANK_API_KEY": state["service_key"],
        },
    )
    set_env(
        ROOT / "services/bank-simulator/.env",
        {
            "SIMBANK_DATABASE_URL": bank_db.dsn,
            "SIMBANK_DATABASE_SCHEMA": bank_db.schema,
            "SIMBANK_API_KEY": state["service_key"],
        },
    )
    blocked = []
    for own_dsn, forbidden in ((app_db.dsn, "finguard_simulator"), (bank_db.dsn, "finguard_app")):
        try:
            with psycopg.connect(
                make_conninfo(**{**conninfo_to_dict(own_dsn), "dbname": forbidden}),
                connect_timeout=3,
            ):
                pass
        except psycopg.OperationalError:
            blocked.append(True)
        else:
            blocked.append(False)
    if not all(blocked):
        raise ValueError("Cross-database access was not denied")
    print(
        json.dumps(
            {
                "status": "ISOLATED",
                "cross_database_access_denied": True,
                "app_rows": sum(state["app_counts"].values()),
                "bank_rows": sum(state["bank_counts"].values()),
                "original_database_preserved": True,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 管理命令不得把连接信息写入控制台。
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
