import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import pytest

pytestmark = pytest.mark.postgres


@contextmanager
def server(database, worker):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "FINGUARD_DATABASE_URL": database.dsn,
        "FINGUARD_DATABASE_SCHEMA": database.schema,
        "FINGUARD_WORKER_ENABLED": "true" if worker else "false",
        "FINGUARD_WORKER_INTERVAL_SECONDS": "0.1",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "finguard.backend.api:create_app",
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


def test_real_http_process_restart_reconciles_committed_payment(database, backend, actor):
    token = backend.security.issue_token(actor.subject_ref)
    headers = {"Authorization": f"Bearer {token}"}
    with server(database, False) as client:
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
        backend.bank.inject_fault(operation["operation_ref"], "crash_after_commit")
        assert client.post(f"/v1/tasks/{task['task_ref']}/run", headers=headers).status_code == 500
        assert backend.get_task(actor, task["task_ref"])["operations"][0]["status"] == "SUBMITTING"
        assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 120000
    with server(database, True) as client:
        deadline = time.monotonic() + 15
        while True:
            result = client.get(f"/v1/tasks/{task['task_ref']}", headers=headers).json()
            if result["verified_success"]:
                break
            assert time.monotonic() < deadline, result
            time.sleep(0.1)
        assert result["status"] == "COMPLETED" and len(result["evidence"]) == 3
    with database.transaction() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM bank_ledger WHERE operation_ref=%s AND amount_minor<0",
                (operation["operation_ref"],),
            ).fetchone()["n"]
            == 1
        )
