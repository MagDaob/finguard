import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from uuid import uuid4

import httpx
import psycopg
import pytest
from finguard_simulator.bank import SimulatedBank
from finguard_simulator.database import Database as SimulatorDatabase
from finguard_simulator.demo import seed_demo as seed_bank
from psycopg import sql

from finguard.backend.demo import seed_demo
from finguard.backend.security import Security

pytestmark = pytest.mark.postgres


@contextmanager
def server(database, worker, bank_url=None, bank_key=None, simulator=False):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "FINGUARD_MODEL_PROVIDER": "deterministic",
        "FINGUARD_MODEL_URL": "",
        "FINGUARD_DATABASE_URL": database.dsn,
        "FINGUARD_DATABASE_SCHEMA": database.schema,
        "FINGUARD_WORKER_ENABLED": "true" if worker else "false",
        "FINGUARD_WORKER_INTERVAL_SECONDS": "0.1",
        "FINGUARD_BANK_URL": bank_url or "",
        "FINGUARD_BANK_API_KEY": bank_key or "",
    }
    if simulator:
        env.update(
            SIMBANK_DATABASE_URL=database.dsn,
            SIMBANK_DATABASE_SCHEMA=database.schema,
            SIMBANK_API_KEY=bank_key,
        )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "finguard_simulator.api:create_app" if simulator else "finguard.backend.api:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, "HTTP process stopped before becoming ready"
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "HTTP startup timed out"
                time.sleep(0.1)
            yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def test_real_http_process_restart_reconciles_committed_payment(database):
    schema = "fg_bank_test_" + uuid4().hex[:20]
    bank_db = SimulatorDatabase(database.dsn, schema)
    bank_db.migrate()
    seed_bank(bank_db)
    seed_demo(database)
    bank = SimulatedBank(bank_db)
    key = "isolated-test-service-key-" + uuid4().hex
    token = Security(database).issue_token("user-demo-001")
    try:
        with server(bank_db, False, bank_key=key, simulator=True) as simulator:
            assert (
                simulator.post(
                    "/v1/bank/list_accounts", json={"subject": "user-demo-001"}
                ).status_code
                == 401
            )
            _restart_case(database, simulator, key, bank, bank_db, token)
    finally:
        with psycopg.connect(bank_db.dsn) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _restart_case(database, simulator, key, bank, bank_db, token):
    headers = {"Authorization": f"Bearer {token}"}
    with server(database, False, str(simulator.base_url).rstrip("/"), key) as client:
        with database.transaction() as c:
            assert (
                c.execute("SELECT to_regclass('bank_accounts') AS table_name").fetchone()[
                    "table_name"
                ]
                is None
            )
        response = client.post(
            "/v1/tasks",
            headers=headers,
            json={
                "client_request_ref": "http-restart",
                "user_request": "给小王转300元，至少保留1000元",
            },
        )
        assert response.status_code == 201
        task = response.json()
        operation = task["operations"][0]
        assert (
            client.post(
                f"/v1/operations/{operation['operation_ref']}/approvals",
                headers=headers,
                json={"expected_digest": operation["digest"]},
            ).status_code
            == 200
        )
        bank.inject_fault(operation["operation_ref"], "response_lost")
        response = client.post(f"/v1/tasks/{task['task_ref']}/run", headers=headers)
        assert response.status_code == 200
        assert response.json()["operations"][0]["status"] == "UNKNOWN"
        assert bank.list_accounts("user-demo-001")[0]["balance_minor"] == 120000
    with server(database, True, str(simulator.base_url).rstrip("/"), key) as client:
        deadline = time.monotonic() + 15
        while True:
            result = client.get(f"/v1/tasks/{task['task_ref']}", headers=headers).json()
            if result["verified_success"]:
                break
            assert time.monotonic() < deadline, result
            time.sleep(0.1)
        assert result["status"] == "COMPLETED" and len(result["evidence"]) == 3
    with bank_db.transaction() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM bank_ledger WHERE operation_ref=%s AND amount_minor<0",
                (operation["operation_ref"],),
            ).fetchone()["n"]
            == 1
        )
