"""为界面验收建立临时双服务；退出后清理本次命名空间，不调用付费模型。"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from finguard_simulator.config import Settings as SimulatorSettings
from finguard_simulator.database import Database as SimulatorDatabase
from finguard_simulator.demo import seed_demo as seed_bank
from psycopg import sql

from finguard.backend.config import Settings
from finguard.backend.database import Database
from finguard.backend.demo import seed_demo
from finguard.backend.security import ADMIN_SCOPES, Security


def main():
    root = Path(__file__).resolve().parents[1]
    runtime = root / "artifacts" / "frontend-qa"
    runtime.mkdir(parents=True, exist_ok=True)
    stop_file = runtime / "stop.local"
    for port in (8002, 8003):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"QA port {port} is already in use")
    stop_file.unlink(missing_ok=True)
    schema = "fgui_" + uuid4().hex[:20]
    settings = Settings.from_env(root / ".env")
    simulator = SimulatorSettings.from_env(root / "services/bank-simulator/.env")
    database = Database(settings.database_url.get_secret_value(), schema)
    bank_database = SimulatorDatabase(simulator.database_url.get_secret_value(), schema)
    service_key = secrets.token_hex(32)
    processes = []
    handles = []
    try:
        database.migrate()
        bank_database.migrate()
        seed_demo(database)
        seed_bank(bank_database)
        security = Security(database)
        token_file = runtime / "session.local.json"
        token_file.write_text(
            json.dumps(
                {
                    "user_token": security.issue_token("user-demo-001"),
                    "operator_token": security.issue_token("operator-demo", ADMIN_SCOPES),
                }
            ),
            encoding="utf-8",
        )
        token_file.chmod(0o600)
        env = {
            **os.environ,
            "FINGUARD_DATABASE_URL": database.dsn,
            "FINGUARD_DATABASE_SCHEMA": schema,
            "FINGUARD_BANK_API_KEY": service_key,
            "FINGUARD_BANK_URL": "http://127.0.0.1:8003",
            "FINGUARD_MODEL_PROVIDER": "deterministic",
            "FINGUARD_MODEL_URL": "",
            "FINGUARD_EVALUATION_ENABLED": "true",
            "FINGUARD_WORKER_ENABLED": "true",
            "SIMBANK_DATABASE_URL": bank_database.dsn,
            "SIMBANK_DATABASE_SCHEMA": schema,
            "SIMBANK_API_KEY": service_key,
        }
        for name, module, port in (
            ("bank", "finguard_simulator.api:create_app", 8003),
            ("backend", "finguard.backend.api:create_app", 8002),
        ):
            handle = (runtime / f"{name}.log").open("w", encoding="utf-8")
            handles.append(handle)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    module,
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=env,
                stdout=handle,
                stderr=handle,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            processes.append(process)
            with httpx.Client(timeout=2, trust_env=False) as client:
                for _ in range(60):
                    if process.poll() is not None:
                        raise RuntimeError(f"QA {name} exited; inspect ignored QA log")
                    try:
                        if client.get(f"http://127.0.0.1:{port}/health/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError(f"QA {name} did not become ready")
        print(
            "QA_READY api=http://127.0.0.1:8002 session=artifacts/frontend-qa/session.local.json",
            flush=True,
        )
        while not stop_file.exists() and all(p.poll() is None for p in processes):
            time.sleep(1)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for handle in handles:
            handle.close()
        assert schema.startswith("fgui_") and len(schema) == 25
        for db in (database, bank_database):
            with psycopg.connect(db.dsn) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )
        (runtime / "session.local.json").unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
