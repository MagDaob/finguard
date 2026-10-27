"""在 Windows 上构建独立桌面包，仅复制明确允许的运行文件。"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "artifacts/desktop-build"
OUTPUT = ROOT / "dist/FinGuard"
PYTHON = ROOT / "packaging/windows/.venv/Scripts/python.exe"
PG_HASH = "fbe23da234ee31547bf8a36d29dfd81e82b849df2d2b78d2eecb43d360252f8c"


def run(args):
    subprocess.run([str(x) for x in args], cwd=ROOT, check=True)


def replace_folder(source, target):
    target = target.resolve()
    if not target.is_relative_to(OUTPUT.resolve()) or target == OUTPUT.resolve():
        raise RuntimeError("Refuse replacement outside desktop output")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def freeze(name, entry, dist, extras):
    run(
        [
            PYTHON,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--noupx",
            "--name",
            name,
            "--distpath",
            dist,
            "--workpath",
            BUILD / "pyinstaller",
            "--specpath",
            BUILD,
            "--exclude-module",
            "pytest",
            "--exclude-module",
            "tkinter",
            *extras,
            ROOT / "packaging/windows" / entry,
        ]
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-services", action="store_true")
    options = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Windows executable must be built on Windows")
    BUILD.mkdir(parents=True, exist_ok=True)
    run(["uv", "sync", "--project", "packaging/windows", "--locked", "--python", "3.12"])
    run(["npm.cmd", "ci", "--prefix", "frontend"])
    run(["npm.cmd", "run", "build", "--prefix", "frontend"])
    run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            ROOT / "packaging/windows/make-icon.ps1",
            "-Output",
            BUILD / "FinGuard.ico",
        ]
    )
    freeze(
        "FinGuard",
        "desktop_entry.py",
        ROOT / "dist",
        [
            "--windowed",
            "--icon",
            BUILD / "FinGuard.ico",
            "--version-file",
            ROOT / "packaging/windows/version.txt",
            "--exclude-module",
            "finguard",
            "--exclude-module",
            "finguard_simulator",
            "--exclude-module",
            "psycopg",
        ],
    )
    if not options.reuse_services:
        build_services()
    assemble()


def build_services():
    freeze(
        "finguard-api",
        "backend_entry.py",
        BUILD / "engines",
        [
            "--collect-data",
            "finguard",
            "--exclude-module",
            "finguard_simulator",
            "--exclude-module",
            "webview",
        ],
    )
    freeze(
        "finguard-bank",
        "bank_entry.py",
        BUILD / "engines",
        [
            "--collect-data",
            "finguard_simulator",
            "--exclude-module",
            "finguard",
            "--exclude-module",
            "webview",
        ],
    )


def assemble():
    replace_folder(BUILD / "engines/finguard-api", OUTPUT / "services/backend")
    replace_folder(BUILD / "engines/finguard-bank", OUTPUT / "services/bank-simulator")
    replace_folder(ROOT / "frontend/dist", OUTPUT / "frontend")
    archive = ROOT / "artifacts/postgresql/postgresql-18.6-1-windows-x64-binaries.zip"
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        with (
            urlopen(
                "https://get.enterprisedb.com/postgresql/postgresql-18.6-1-windows-x64-binaries.zip",
                timeout=120,
            ) as response,
            archive.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    with archive.open("rb") as downloaded:
        if hashlib.file_digest(downloaded, "sha256").hexdigest() != PG_HASH:
            raise RuntimeError("PostgreSQL source checksum mismatch")
    pg = OUTPUT / "postgresql"
    (pg / "bin").mkdir(parents=True, exist_ok=True)
    allowed = {
        "postgres.exe",
        "pg_ctl.exe",
        "initdb.exe",
        "psql.exe",
        "pg_dump.exe",
        "pg_restore.exe",
    }
    licenses = OUTPUT / "licenses"
    licenses.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            path = Path(item.filename)
            if item.is_dir():
                continue
            if item.filename.startswith(("pgsql/lib/", "pgsql/share/")) or (
                item.filename.startswith("pgsql/bin/")
                and (
                    path.name in allowed
                    or path.suffix == ".dll"
                    and not path.name.lower().startswith("wx")
                )
            ):
                target = (pg / path.relative_to("pgsql")).resolve()
                if not target.is_relative_to(pg.resolve()):
                    raise RuntimeError("Unsafe PostgreSQL archive path")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(item))
            if item.filename in {
                "pgsql/server_license.txt",
                "pgsql/commandlinetools_3rd_party_licenses.txt",
                "pgsql/doc/src/sgml/html/legalnotice.html",
            }:
                (licenses / ("postgresql-" + item.filename.replace("/", "-"))).write_bytes(
                    package.read(item)
                )
    # 使用冻结环境的发行元数据收集第三方许可，避免读取开发配置。
    run([PYTHON, ROOT / "packaging/windows/postgres_utf8.py", pg / "bin"])
    (licenses / "PostgreSQL-PACKAGING-NOTICE.txt").write_text(
        "PostgreSQL 18.6 Windows binaries from the verified EDB archive.\nThe unsigned executables in this distribution include a process-level UTF-8 activeCodePage application manifest for non-ASCII path compatibility. Existing execution permissions are preserved.\nThe upstream archive is unmodified; packaged file hashes are recorded in manifest.json.\nUpstream: https://www.enterprisedb.com/download-postgresql-binaries\n",
        encoding="utf-8",
    )
    run([PYTHON, ROOT / "packaging/windows/licenses.py", licenses])
    npm_packages = []
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    for location, metadata in lock["packages"].items():
        if not location.startswith("node_modules/") or metadata.get("dev"):
            continue
        package_folder = (ROOT / "frontend" / location).resolve()
        if not package_folder.is_relative_to((ROOT / "frontend/node_modules").resolve()):
            raise RuntimeError("Unsafe npm package location")
        npm_packages.append(
            {
                "name": location.removeprefix("node_modules/"),
                "version": metadata["version"],
                "license": metadata.get("license", "See upstream distribution"),
            }
        )
        for path in package_folder.iterdir():
            if path.is_file() and path.name.lower().startswith(("license", "copying", "notice")):
                destination = licenses / "npm" / location.removeprefix("node_modules/") / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
    (licenses / "npm-packages.json").write_text(
        json.dumps(npm_packages, indent=2), encoding="utf-8"
    )
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, OUTPUT / name)
    (OUTPUT / "使用说明.txt").write_text(
        "FinGuard 0.3.0 Windows x64 便携版\n\n双击 FinGuard.exe 启动。请保留整个目录，不能只复制主程序。\n目标系统为 Windows 10 1903 及之后版本、Windows 11 x64，需要 Microsoft Edge WebView2 Runtime。\n\n数据位于 %LOCALAPPDATA%\\FinGuard；升级或重装不重置数据。\n模拟环境使用虚构账户，默认不调用付费模型。\n启动后点击‘进入本机工作台’，无需复制访问令牌。\n关闭窗口后等待安全退出，不要在任务执行中强制关机。\n项目采用 Apache-2.0，许可和项目声明见 LICENSE、NOTICE；第三方许可见 licenses。\n",
        encoding="utf-8",
    )
    files = sorted(
        path for path in OUTPUT.rglob("*") if path.is_file() and path != OUTPUT / "manifest.json"
    )
    forbidden = [
        p
        for p in files
        if p.name == ".env"
        or p.suffix in {".log", ".local"}
        or "node_modules" in p.parts
        or "__pycache__" in p.parts
    ]
    if forbidden:
        raise RuntimeError("Unexpected development/runtime file in package")
    manifest = {
        "version": "0.3.0",
        "platform": "windows-x64",
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        "inputs": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                *sorted(
                    p
                    for p in (ROOT / "packaging/windows").iterdir()
                    if p.is_file()
                    and not p.name.startswith(".")
                    and ".local." not in p.name
                    and p.suffix in {".py", ".toml", ".lock", ".ps1", ".txt"}
                ),
                ROOT / "LICENSE",
                ROOT / "NOTICE",
                ROOT / "pyproject.toml",
                ROOT / "uv.lock",
                ROOT / "frontend/package.json",
                ROOT / "frontend/package-lock.json",
                ROOT / "scripts/build-windows.py",
            ]
            if p.is_file()
        },
        "files": {
            p.relative_to(OUTPUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
        },
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    package_path = ROOT / "dist/FinGuard-0.3.0-windows-x64.zip"
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in sorted(OUTPUT.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(OUTPUT.parent))
    checksum = hashlib.sha256(package_path.read_bytes()).hexdigest()
    package_path.with_suffix(".zip.sha256").write_text(
        checksum + "  " + package_path.name + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "executable": str(OUTPUT / "FinGuard.exe"),
                "archive": str(package_path),
                "archive_bytes": package_path.stat().st_size,
                "sha256": checksum,
            }
        )
    )


if __name__ == "__main__":
    main()
