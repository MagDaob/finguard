from fastapi import FastAPI
from fastapi.testclient import TestClient

from finguard.backend.web import register_workbench


def test_workbench_spa_routes_do_not_mask_api_or_serve_local_files(tmp_path):
    folder = tmp_path / "dist"
    folder.mkdir()
    (folder / "index.html").write_text("<title>FinGuard</title>", encoding="utf-8")
    (folder / "secret.local.json").write_text("private fixture", encoding="utf-8")
    app = FastAPI()

    @app.get("/v1/me")
    def me():
        return {"subject_ref": "fixture"}

    register_workbench(app, folder)
    client = TestClient(app)
    assert "FinGuard" in client.get("/tasks/task-fixture").text
    assert client.get("/tasks/task-fixture").headers["cache-control"] == "no-store"
    assert client.get("/v1/me").json() == {"subject_ref": "fixture"}
    for path in ("/v1/missing", "/.env", "/secret.local.json", "/assets/missing.js"):
        assert client.get(path).status_code == 404


def test_api_only_install_does_not_require_a_frontend(tmp_path):
    app = FastAPI()
    register_workbench(app, tmp_path / "absent")
    assert TestClient(app).get("/").status_code == 404
