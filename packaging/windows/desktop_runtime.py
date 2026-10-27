"""桌面进程管理：独立用户数据、随机端口、受保护凭据和有界退出。"""

import base64
import ctypes
import hashlib
import hmac
import json
import logging
import msvcrt
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

VERSION = "0.3.0"
CREATE_NO_WINDOW = 0x08000000


class StartupError(RuntimeError):
    pass


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(value: bytes, decrypt=False) -> bytes:
    """DPAPI 绑定当前 Windows 用户，不在磁盘保存明文密码。"""
    source = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    function = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise StartupError("无法读取本机受保护配置，请使用建立此数据的 Windows 用户启动。")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.data)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class InstanceLock:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / "instance.lock").open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            self.file.close()
            raise StartupError("FinGuard 已在运行，请使用已打开的窗口。") from error

    def close(self):
        self.file.seek(0)
        msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()


def attach_job():
    """启动器退出或崩溃后，Windows 回收本程序的全部后代进程。"""

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD),
            ("minimum", ctypes.c_size_t),
            ("maximum", ctypes.c_size_t),
            ("active", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", IO),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000
    if (
        not handle
        or not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())
    ):
        raise StartupError("无法建立桌面进程隔离，请关闭重复实例后重试。")
    return handle


class Runtime:
    def __init__(self, bundle: Path, data: Path):
        self.bundle, self.data = bundle.resolve(), data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = InstanceLock(self.data)
        self.log = logging.getLogger("finguard.desktop")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self.handler = RotatingFileHandler(
            self.data / "desktop.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        self.log.addHandler(self.handler)
        self.config_file = self.data / "settings.local.json"
        if self.config_file.exists():
            document = json.loads(self.config_file.read_text(encoding="utf-8"))
            self.config = json.loads(protect(base64.b64decode(document["protected"]), decrypt=True))
        else:
            self.config = {
                "version": 1,
                "db_port": free_port(),
                "admin": secrets.token_hex(32),
                "app": secrets.token_hex(32),
                "bank": secrets.token_hex(32),
                "service": secrets.token_hex(32),
            }
            self.save()
        self.backend = self.bundle / "services/backend/finguard-api.exe"
        self.bank = self.bundle / "services/bank-simulator/finguard-bank.exe"
        self.pg = self.bundle / "postgresql/bin"
        self.api_port, self.bank_port = free_port(), free_port()
        while len({self.api_port, self.bank_port, self.config["db_port"]}) < 3:
            self.api_port, self.bank_port = free_port(), free_port()
        self.origin = f"http://127.0.0.1:{self.api_port}"
        self.probe_key = secrets.token_hex(32)
        self.processes = []
        self.handles = []
        self.token = None
        self.db_started = False
        self.closed = False
        self.stop_event = threading.Event()
        self.guard = threading.RLock()
        self.http = build_opener(ProxyHandler({}))
        # 显式丢弃开发环境变量，避免继承本机真实模型密钥。
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("FINGUARD_", "SIMBANK_", "PG", "PYTHON"))
            and k != "DEEPSEEK_API_KEY"
        }
        self.env.update(
            {
                "PYINSTALLER_RESET_ENVIRONMENT": "1",
                "PYTHONUTF8": "1",
                "FINGUARD_DATABASE_URL": f"postgresql://fg_app:{self.config['app']}@127.0.0.1:{self.config['db_port']}/finguard_app",
                "FINGUARD_DESKTOP_PROBE_KEY": self.probe_key,
                "FINGUARD_BANK_API_KEY": self.config["service"],
                "FINGUARD_BANK_URL": f"http://127.0.0.1:{self.bank_port}",
                "FINGUARD_MODEL_PROVIDER": "deterministic",
                "FINGUARD_WORKER_ENABLED": "true",
                "FINGUARD_EVALUATION_ENABLED": "false",
                "FINGUARD_FRONTEND_DIRECTORY": str(self.bundle / "frontend"),
                "FINGUARD_DESKTOP_STOP_FILE": str(self.data / "backend.stop"),
                "SIMBANK_DATABASE_URL": f"postgresql://fg_bank:{self.config['bank']}@127.0.0.1:{self.config['db_port']}/finguard_bank",
                "SIMBANK_DESKTOP_PROBE_KEY": self.probe_key,
                "SIMBANK_API_KEY": self.config["service"],
                "SIMBANK_DESKTOP_STOP_FILE": str(self.data / "bank.stop"),
            }
        )

    def save(self):
        encoded = base64.b64encode(protect(json.dumps(self.config).encode())).decode()
        temporary = self.config_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "protected": encoded}), encoding="utf-8")
        temporary.replace(self.config_file)

    def command(self, executable, args, *, environment=None, input_text=None, timeout=40):
        # PostgreSQL 后台子进程可能继承管道；临时文件避免等待管道 EOF 而卡住退出。
        with (
            tempfile.TemporaryFile(dir=self.data) as incoming,
            tempfile.TemporaryFile(dir=self.data) as outgoing,
            tempfile.TemporaryFile(dir=self.data) as errors,
        ):
            if input_text:
                incoming.write(input_text.encode("utf-8"))
                incoming.seek(0)
            result = subprocess.run(
                [str(executable), *args],
                stdin=incoming,
                stdout=outgoing,
                stderr=errors,
                cwd=self.data,
                env=environment or self.service_env(executable),
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
            outgoing.seek(0)
            errors.seek(0)
            stdout = outgoing.read().decode("utf-8", errors="replace")
            stderr = errors.read().decode("utf-8", errors="replace")
        if result.returncode:
            detail = stderr[-4000:]
            for key in ("admin", "app", "bank", "service"):
                detail = detail.replace(self.config[key], "[REDACTED]")
            self.log.error("%s exited=%d: %s", Path(executable).name, result.returncode, detail)
            raise StartupError(
                f"组件 {Path(executable).name} 未能完成启动。数据已保留，请重启软件。"
            )
        return stdout

    def pg_env(self):
        return {
            **self.service_env(self.pg / "psql.exe"),
            "PGPASSWORD": self.config["admin"],
            "PGCLIENTENCODING": "UTF8",
        }

    def service_env(self, executable):
        excluded = (
            ("FINGUARD_",)
            if Path(executable) == self.bank
            else ("SIMBANK_",)
            if Path(executable) == self.backend
            else ("FINGUARD_", "SIMBANK_")
        )
        return {k: v for k, v in self.env.items() if not k.startswith(excluded)}

    def start(self, progress=lambda _: None):
        with self.guard:
            started = time.monotonic()
            try:
                for path in [
                    self.backend,
                    self.bank,
                    self.pg / "postgres.exe",
                    self.bundle / "frontend/index.html",
                ]:
                    if not path.is_file():
                        raise StartupError("安装文件不完整。请解压完整便携包或重新安装。")
                progress("正在准备本机数据库…")
                cluster = self.data / "database"
                if not (cluster / "PG_VERSION").exists():
                    password_file = self.data / "init-password.tmp"
                    password_file.write_text(self.config["admin"], encoding="utf-8")
                    try:
                        self.command(
                            self.pg / "initdb.exe",
                            [
                                "-D",
                                str(cluster),
                                "-U",
                                "fg_owner",
                                "-A",
                                "scram-sha-256",
                                f"--pwfile={password_file}",
                                "--encoding=UTF8",
                                "--locale=C",
                            ],
                            timeout=90,
                        )
                    finally:
                        password_file.unlink(missing_ok=True)
                self.check_cancelled()
                self.command(
                    self.pg / "pg_ctl.exe",
                    [
                        "start",
                        "-D",
                        str(cluster),
                        "-l",
                        str(self.data / "database.log"),
                        "-o",
                        f"-h 127.0.0.1 -p {self.config['db_port']} -c max_connections=30 -c shared_buffers=32MB",
                        "-w",
                        "-t",
                        "30",
                    ],
                )
                self.db_started = True
                self.database_roles()
                self.check_cancelled()
                progress("正在恢复模拟银行…")
                self.command(self.bank, ["init"])
                self.launch(self.bank, self.bank_port, "bank")
                self.wait_ready(self.bank_port)
                self.check_cancelled()
                progress("正在恢复任务与业务证据…")
                self.command(self.backend, ["init"])
                self.launch(self.backend, self.api_port, "backend")
                self.wait_ready(self.api_port)
                self.log.info(
                    "ready version=%s startup_seconds=%.2f", VERSION, time.monotonic() - started
                )
            except Exception:
                self.close()
                raise

    def database_roles(self):
        statements = []
        for role, password, database in [
            ("fg_app", self.config["app"], "finguard_app"),
            ("fg_bank", self.config["bank"], "finguard_bank"),
        ]:
            statements.extend(
                [
                    f"SELECT 'CREATE ROLE {role} LOGIN PASSWORD ''{password}''' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}')\\gexec",
                    f"SELECT 'CREATE DATABASE {database} OWNER {role}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='{database}')\\gexec",
                    f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC; GRANT CONNECT ON DATABASE {database} TO {role};",
                ]
            )
        self.command(
            self.pg / "psql.exe",
            [
                "-X",
                "-q",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.config["db_port"]),
                "-U",
                "fg_owner",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            environment=self.pg_env(),
            input_text="\n".join(statements) + "\n",
        )

    def check_cancelled(self):
        if self.stop_event.is_set():
            raise StartupError("启动已取消。")

    def launch(self, executable, port, name):
        (self.data / f"{name}.stop").unlink(missing_ok=True)
        log_path = self.data / f"{name}.log"
        if log_path.exists() and log_path.stat().st_size > 1_000_000:
            log_path.replace(self.data / f"{name}.previous.log")
        handle = log_path.open("w", encoding="utf-8")
        self.handles.append(handle)
        process = subprocess.Popen(
            [str(executable), "serve", str(port)],
            cwd=self.data,
            env=self.service_env(executable),
            stdout=handle,
            stderr=handle,
            creationflags=CREATE_NO_WINDOW,
        )
        self.processes.append((name, process))

    def request(self, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        with self.http.open(
            Request(self.origin + path, data=data, headers=headers), timeout=15
        ) as response:
            return json.load(response)

    def wait_ready(self, port):
        nonce = secrets.token_hex(16)
        expected = hmac.new(
            self.probe_key.encode(), ("finguard-desktop-v1|" + nonce).encode(), hashlib.sha256
        ).hexdigest()
        for _ in range(160):
            self.check_cancelled()
            if any(process.poll() is not None for _, process in self.processes):
                raise StartupError("本机服务意外退出。请重启软件，原任务记录将保留。")
            try:
                with self.http.open(f"http://127.0.0.1:{port}/health/ready", timeout=1) as response:
                    if response.status == 200:
                        with self.http.open(
                            f"http://127.0.0.1:{port}/health/desktop?nonce={nonce}", timeout=1
                        ) as identity:
                            if not hmac.compare_digest(
                                json.load(identity).get("signature", ""), expected
                            ):
                                raise StartupError("本机端口身份不匹配。请关闭软件后重试。")
                        return
            except (URLError, TimeoutError):
                pass
            self.stop_event.wait(0.25)
        raise StartupError("本机服务启动超时。请关闭软件后重试。")

    def session(self):
        with self.guard:
            self.check_cancelled()
            if not self.processes or any(p.poll() is not None for _, p in self.processes):
                raise StartupError("本机服务已退出，请重启软件恢复原任务。")
            self.revoke()
            self.token = json.loads(self.command(self.backend, ["session"]))["token"]
            return self.token

    def revoke(self):
        if self.token:
            try:
                self.request("/v1/tokens/revoke-current", {}, self.token)
            except (HTTPError, URLError, TimeoutError):
                self.log.warning("Session revocation unavailable; original expiry remains in force")
            self.token = None

    def close(self):
        self.stop_event.set()
        with self.guard:
            if self.closed:
                return
            self.closed = True
            self.revoke()
            for name, process in reversed(self.processes):
                (self.data / f"{name}.stop").touch()
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
            for handle in self.handles:
                handle.close()
            if self.db_started:
                try:
                    self.command(
                        self.pg / "pg_ctl.exe",
                        ["stop", "-D", str(self.data / "database"), "-m", "fast", "-w", "-t", "15"],
                        timeout=20,
                    )
                except (StartupError, subprocess.TimeoutExpired):
                    self.log.warning(
                        "Database shutdown incomplete; Windows process isolation will reclaim it"
                    )
            self.log.info("closed")
            self.log.removeHandler(self.handler)
            self.handler.close()
            self.lock.close()
