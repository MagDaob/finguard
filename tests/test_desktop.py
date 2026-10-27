"""Windows 桌面边界检查；不启动 GUI，也不调用模型服务。"""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop only")


@pytest.fixture
def desktop():
    source = Path(__file__).resolve().parents[1] / "packaging/windows/desktop_runtime.py"
    spec = importlib.util.spec_from_file_location("desktop_runtime_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_protected_config_roundtrip_and_tamper(desktop):
    secret = b"fake-local-config-for-test"
    encrypted = desktop.protect(secret)
    assert secret not in encrypted
    assert desktop.protect(encrypted, decrypt=True) == secret
    with pytest.raises(desktop.StartupError):
        desktop.protect(b"not-a-dpapi-envelope", decrypt=True)


def test_desktop_isolates_environment_and_requires_single_instance(desktop, tmp_path, monkeypatch):
    monkeypatch.setenv("FINGUARD_DEEPSEEK_API_KEY", "fake-never-forward")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-never-forward-alias")
    monkeypatch.setenv("FINGUARD_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("FINGUARD_MODEL_URL", "https://model.example/should-not-be-used")
    runtime = desktop.Runtime(tmp_path / "bundle", tmp_path / "user-data")
    try:
        assert runtime.env["FINGUARD_MODEL_PROVIDER"] == "deterministic"
        assert "FINGUARD_DEEPSEEK_API_KEY" not in runtime.env
        assert "DEEPSEEK_API_KEY" not in runtime.env
        assert "FINGUARD_MODEL_URL" not in runtime.env
        assert runtime.env["FINGUARD_DATABASE_URL"] != runtime.env["SIMBANK_DATABASE_URL"]
        assert not any(k.startswith("FINGUARD_") for k in runtime.service_env(runtime.bank))
        assert not any(k.startswith("SIMBANK_") for k in runtime.service_env(runtime.backend))
        assert not any(
            k.startswith(("FINGUARD_", "SIMBANK_"))
            for k in runtime.service_env(runtime.pg / "psql.exe")
        )
        assert runtime.env["FINGUARD_BANK_URL"].startswith("http://127.0.0.1:")
        stored = json.loads(runtime.config_file.read_text())
        assert "protected" in stored
        assert runtime.config["admin"] not in runtime.config_file.read_text()
        with pytest.raises(desktop.StartupError, match="已在运行"):
            desktop.Runtime(tmp_path / "bundle", tmp_path / "user-data")
        original = runtime.config.copy()
    finally:
        runtime.close()
    resumed = desktop.Runtime(tmp_path / "bundle", tmp_path / "user-data")
    try:
        assert resumed.config == original
    finally:
        resumed.close()


def test_incomplete_desktop_package_stops_without_starting_services(desktop, tmp_path):
    runtime = desktop.Runtime(tmp_path / "missing-bundle", tmp_path / "user-data")
    with pytest.raises(desktop.StartupError, match="安装文件不完整"):
        runtime.start()
    assert runtime.closed and runtime.processes == [] and not runtime.db_started


def test_ready_response_cannot_impersonate_a_desktop_service(desktop, tmp_path, monkeypatch):
    runtime = desktop.Runtime(tmp_path / "bundle", tmp_path / "user-data")

    class Response(io.BytesIO):
        status = 200

    def forged(url, **kwargs):
        return Response(
            b'{"signature":"forged"}' if "/health/desktop" in url else b'{"status":"ready"}'
        )

    monkeypatch.setattr(runtime.http, "open", forged)
    try:
        with pytest.raises(desktop.StartupError, match="身份不匹配"):
            runtime.wait_ready(runtime.api_port)
    finally:
        runtime.close()


def test_utf8_manifest_preserves_existing_execution_permissions():
    source = Path(__file__).resolve().parents[1] / "packaging/windows/postgres_utf8.py"
    spec = importlib.util.spec_from_file_location("postgres_utf8_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security></trustInfo></assembly>'
    changed = module.utf8_manifest(original)
    assert b'level="asInvoker"' in changed and b'uiAccess="false"' in changed
    assert b"activeCodePage" in changed and b"UTF-8" in changed
    assert module.utf8_manifest(changed).count(b"<activeCodePage") == 1
