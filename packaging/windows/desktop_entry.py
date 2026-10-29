"""FinGuard Windows 桌面入口；业务运行在两个独立的可执行文件中。"""

import argparse
import ctypes
import html
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from desktop_runtime import VERSION, Runtime, StartupError, attach_job

SPLASH = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
body{margin:0;background:#f5f7fa;color:#193b36;font:16px 'Microsoft YaHei',sans-serif;display:grid;place-items:center;height:100vh}
main{max-width:520px;padding:40px}b{display:inline-grid;place-items:center;border:2px solid #18776c;border-radius:12px;width:48px;height:54px;font-size:30px;color:#18776c}
h1{font-size:30px;margin:24px 0 14px}p{line-height:1.8;color:#516762}small{color:#516762;line-height:1.8}
.bar{height:4px;background:#dae9e3;overflow:hidden;border-radius:4px;margin:28px 0}.bar:after{content:'';display:block;width:40%;height:100%;background:#18776c;animation:move 1.5s infinite alternate}@keyframes move{to{transform:translateX(150%)}}
@media(prefers-reduced-motion:reduce){.bar:after{animation:none}}</style>
<main><b>F</b><h1>FinGuard</h1><p id="status">正在启动金融任务工作台…</p><div class="bar"></div><small>本机模拟环境 · 首次启动需要建立独立数据库。<br>账户、任务和凭证会保存在当前 Windows 用户目录。</small></main></html>"""


def self_test(bundle, data, report_path):
    report = {"version": VERSION, "checks": [], "paid_model_calls": 0}
    started = time.monotonic()
    runtime = Runtime(bundle, data)
    ports = [runtime.config["db_port"], runtime.api_port, runtime.bank_port]
    try:
        runtime.start()
        report["startup_seconds"] = round(time.monotonic() - started, 2)
        token = runtime.session()
        accounts = runtime.request("/v1/accounts", token=token)
        before = accounts[0]["balance_minor"]
        assert not runtime.request("/v1/chat/status", token=token)["enabled"]
        conversation = "desktop-chat-" + uuid4().hex
        chat_body = {"message_ref": "message-one", "content": "你好", "expected_sequence": 0}
        reply = runtime.request(f"/v1/conversations/{conversation}/messages", chat_body, token)
        for _ in range(40):
            reply = runtime.request(f"/v1/conversations/{conversation}", token=token)
            if reply["turns"][0]["ready"]:
                break
            time.sleep(0.25)
        assert (
            reply["turns"][0]["task_ref"] is None
            and reply["turns"][0]["error_code"] == "CHAT_NOT_CONFIGURED"
        )
        assert runtime.request("/v1/task-groups/counts", token=token)["all"] == 0
        report["checks"].append("chat_without_task_and_no_model")
        draft = runtime.request(
            f"/v1/conversations/{conversation}/messages",
            {
                "message_ref": "message-two",
                "content": "给小王转账，至少保留1000元",
                "expected_sequence": 1,
            },
            token,
        )
        assert draft["turns"][-1]["kind"] == "draft" and draft["turns"][-1]["task_ref"] is None
        assert runtime.request("/v1/task-groups/counts", token=token)["all"] == 0
        report["checks"].append("clarification_without_task")
        banks = runtime.request("/v1/wallet/banks", token=token)
        assert len(banks) == 1 and banks[0]["bank_ref"] == "simulated-bank"
        connection = runtime.request(
            "/v1/wallet/connections",
            {"request_ref": "desktop-wallet", "bank_ref": banks[0]["bank_ref"]},
            token,
        )
        assets = runtime.request(
            f"/v1/wallet/connections/{connection['connection_ref']}/assets", token=token
        )
        imported = runtime.request(
            f"/v1/wallet/connections/{connection['connection_ref']}/import",
            {"asset_refs": [a["asset_ref"] for a in assets]},
            token,
        )
        assert len(imported["assets"]) == len(assets) > 0
        body = {
            "client_request_ref": "desktop-test-" + uuid4().hex,
            "user_request": "给小王转300元，至少保留1000元",
        }
        task = runtime.request("/v1/tasks", body, token)
        operation = task["operations"][0]
        assert runtime.request("/v1/accounts", token=token)[0]["balance_minor"] == before
        report["checks"].append("no_debit_before_confirmation")
        runtime.request(
            f"/v1/operations/{operation['operation_ref']}/approvals",
            {"expected_digest": operation["digest"]},
            token,
        )
        result = runtime.request(f"/v1/tasks/{task['task_ref']}/run", {}, token)
        for _ in range(40):
            if result["status"] == "COMPLETED":
                break
            time.sleep(0.25)
            result = runtime.request(f"/v1/tasks/{task['task_ref']}", token=token)
        assert result["verified_success"] and result["evidence"]
        assert runtime.request("/v1/accounts", token=token)[0]["balance_minor"] == before - 30000
        runtime.request(f"/v1/tasks/{task['task_ref']}/run", {}, token)
        duplicate = runtime.request("/v1/tasks", body, token)
        assert duplicate["task_ref"] == task["task_ref"]
        assert runtime.request("/v1/accounts", token=token)[0]["balance_minor"] == before - 30000
        report["checks"].extend(
            ["confirmed_transfer_evidence", "stable_request_and_no_double_debit"]
        )
        counts = runtime.request("/v1/task-groups/counts", token=token)
        assert counts["completed"] == 1 and counts["all"] == 1
        assert len(runtime.request("/v1/task-groups/completed", token=token)) == 1
        report["checks"].append("classified_task_counts")
        try:
            Runtime(bundle, data)
        except StartupError:
            report["checks"].append("single_instance")
        else:
            raise AssertionError("Second instance was allowed")
    finally:
        runtime.close()
    resumed = Runtime(bundle, data)
    try:
        resume_start = time.monotonic()
        resumed.start()
        report["resume_seconds"] = round(time.monotonic() - resume_start, 2)
        token = resumed.session()
        assert resumed.request("/v1/accounts", token=token)[0]["balance_minor"] == before - 30000
        assert resumed.request(f"/v1/tasks/{task['task_ref']}", token=token)["verified_success"]
        report["checks"].append("restart_preserves_balance_and_evidence")
        assert len(resumed.request("/v1/wallet", token=token)["assets"]) == len(assets)
        assert len(resumed.request(f"/v1/conversations/{conversation}", token=token)["turns"]) == 2
        report["checks"].append("wallet_and_conversation_persist")
    finally:
        resumed.close()
    import socket

    for port in [*ports, resumed.api_port, resumed.bank_port]:
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    report["checks"].append("owned_ports_closed")
    report["status"] = "passed"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    bundle = (
        Path(sys.executable).parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2] / "dist/FinGuard"
    )
    data = options.data_dir or Path(os.environ["LOCALAPPDATA"]) / "FinGuard"
    # 保留句柄直至进程退出；不能提前关闭，否则会终止当前程序。
    job_handle = attach_job()
    if options.self_test:
        if options.data_dir is None or options.report is None:
            raise StartupError("自检必须指定独立数据目录和报告路径。")
        self_test(bundle, data, options.report)
        return job_handle
    import webview

    runtime = Runtime(bundle, data)
    closing = threading.Event()
    complete = threading.Event()

    class Bridge:
        def session(self):
            current = urlsplit(window.get_current_url() or "")
            if (current.scheme, current.netloc) != ("http", urlsplit(runtime.origin).netloc):
                raise StartupError("本机会话只提供给当前工作台。")
            return {"token": runtime.session()}

    window = webview.create_window(
        "FinGuard · 金融任务工作台",
        html=SPLASH,
        js_api=Bridge(),
        width=runtime.config.get("width", 1440),
        height=runtime.config.get("height", 900),
        min_size=(900, 640),
        background_color="#f5f7fa",
        text_select=True,
        zoomable=True,
    )

    def progress(message):
        if not closing.is_set():
            window.evaluate_js(
                "document.getElementById('status').textContent = " + json.dumps(message)
            )

    def startup():
        try:
            runtime.start(progress)
            if not closing.is_set():
                window.load_url(runtime.origin + "/tasks")
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, StartupError)
                else "启动未完成。请重启软件，已有数据会保留。"
            )
            runtime.log.warning("Startup failed (%s)", type(error).__name__)
            if not closing.is_set():
                window.load_html(SPLASH.replace("正在启动金融任务工作台…", html.escape(message)))

    def close():
        if complete.is_set():
            return True
        if not closing.is_set():
            closing.set()
            runtime.stop_event.set()
            try:
                runtime.config.update(
                    width=max(900, min(3840, window.width)),
                    height=max(640, min(2160, window.height)),
                )
                runtime.save()
            except Exception:
                pass
            window.set_title("FinGuard · 正在安全退出…")

            def stop():
                runtime.close()
                complete.set()
                window.destroy()

            threading.Thread(target=stop, daemon=True).start()
        return False

    window.events.closing += close
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    try:
        webview.start(
            startup,
            gui="edgechromium",
            debug=False,
            private_mode=True,
            storage_path=str(data / "webview"),
        )
    finally:
        runtime.close()
    return job_handle


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if "--self-test" in sys.argv:
            report_arg = (
                sys.argv[sys.argv.index("--report") + 1] if "--report" in sys.argv else None
            )
            if report_arg:
                Path(report_arg).write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "error_type": type(error).__name__,
                            "message": str(error)
                            if isinstance(error, StartupError)
                            else "Desktop self-test failed",
                        }
                    ),
                    encoding="utf-8",
                )
            sys.exit(1)
        message = (
            str(error)
            if isinstance(error, StartupError)
            else "FinGuard 无法启动。请确认已安装 Microsoft Edge WebView2 Runtime，并保留完整软件目录。"
        )
        ctypes.windll.user32.MessageBoxW(None, message, "FinGuard", 0x10)
        sys.exit(1)
