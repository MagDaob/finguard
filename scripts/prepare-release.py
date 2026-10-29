"""检查版本和便携包，运行实际 EXE 自检，准备不覆盖旧版本的发布附件。"""

import argparse
import ast
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {
    "no_debit_before_confirmation",
    "confirmed_transfer_evidence",
    "stable_request_and_no_double_debit",
    "single_instance",
    "restart_preserves_balance_and_evidence",
    "owned_ports_closed",
}


OPTIONAL_CHECKS = {
    "chat_without_task_and_no_model",
    "clarification_without_task",
    "classified_task_counts",
    "wallet_and_conversation_persist",
}


class ReleaseError(ValueError):
    pass


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def versions(root):
    projects = [
        "pyproject.toml",
        "packages/bank-contracts/pyproject.toml",
        "packages/plugin-contracts/pyproject.toml",
        "services/bank-simulator/pyproject.toml",
        "packaging/windows/pyproject.toml",
    ]
    metadata = {
        name: tomllib.loads((root / name).read_text(encoding="utf-8"))["project"]
        for name in projects
    }
    version = metadata["pyproject.toml"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError("Release version must use major.minor.patch")
    if any(
        item.get("version") != version or item.get("license") != "Apache-2.0"
        for item in metadata.values()
    ):
        raise ReleaseError("Python component version or license mismatch")
    frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    locked = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    if (
        frontend["version"] != version
        or frontend.get("license") != "Apache-2.0"
        or locked["version"] != version
        or locked["packages"][""]["version"] != version
        or locked["packages"][""].get("license") != "Apache-2.0"
    ):
        raise ReleaseError("Frontend version or license mismatch")
    module = ast.parse((root / "packaging/windows/desktop_runtime.py").read_text(encoding="utf-8"))
    runtime_version = next(
        (
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets)
        ),
        None,
    )
    resource = (root / "packaging/windows/version.txt").read_text(encoding="utf-8")
    tuple_version = tuple(map(int, version.split("."))) + (0,)
    tree = ast.parse(resource)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    strings = {
        ast.literal_eval(node.args[0]): ast.literal_eval(node.args[1])
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "StringStruct"
    }
    numbers = {
        kw.arg: ast.literal_eval(kw.value)
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "FixedFileInfo"
        for kw in node.keywords
        if kw.arg in {"filevers", "prodvers"}
    }
    if (
        runtime_version != version
        or any(strings.get(name) != version for name in ("FileVersion", "ProductVersion"))
        or any(numbers.get(name) != tuple_version for name in ("filevers", "prodvers"))
    ):
        raise ReleaseError("Desktop runtime or Windows resource version mismatch")
    for folder in (
        "",
        "packages/bank-contracts",
        "packages/plugin-contracts",
        "services/bank-simulator",
    ):
        if (root / folder / "LICENSE").read_bytes() != (root / "LICENSE").read_bytes() or (
            root / folder / "NOTICE"
        ).read_bytes() != (root / "NOTICE").read_bytes():
            raise ReleaseError("Component license text differs from the project license")
    return version


def private_values(root):
    values = []
    for path in (root / ".env", root / "services/bank-simulator/.env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if not separator or name.strip().startswith("#"):
                continue
            value = value.strip().strip('"').strip("'")
            if "DATABASE_URL" in name:
                value = unquote(urlsplit(value).password or "")
            elif not any(part in name for part in ("API_KEY", "PASSWORD", "SECRET")):
                continue
            if len(value) >= 8 and not value.startswith("REPLACE_"):
                values.append(value.encode())
    return values


def safe_name(name):
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(
            part in {"", ".", ".."} or ":" in part or part.endswith((" ", "."))
            for part in name.rstrip("/").split("/")
        )
    ):
        raise ReleaseError("Unsafe archive path")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if any(
        any(ord(c) < 32 or c in '<>"|?*' for c in part) or part.split(".")[0].upper() in reserved
        for part in name.rstrip("/").split("/")
    ):
        raise ReleaseError("Unsafe Windows filename")
    return path


def inspect_archive(archive, version, secrets=(), license_text=None, notice_text=None):
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 12000 or sum(item.file_size for item in entries) > 1_000_000_000:
            raise ReleaseError("Unexpected package size")
        actual, folded = {}, set()
        for item in entries:
            name = safe_name(item.filename)
            if name.parts[0] != "FinGuard" or stat.S_ISLNK(item.external_attr >> 16):
                raise ReleaseError("Unexpected archive root or symbolic link")
            key = item.filename.rstrip("/").casefold()
            if key in folded:
                raise ReleaseError("Duplicate archive path")
            folded.add(key)
            if item.is_dir():
                continue
            parts = {part.casefold() for part in name.parts}
            base = name.name.casefold()
            if (
                parts & {".git", ".venv", "node_modules", "__pycache__", "database", "artifacts"}
                or base.startswith(".env")
                or base.endswith(
                    (".log", ".local", ".local.json", ".local.toml", ".local.yaml", ".local.yml")
                )
            ):
                raise ReleaseError("Private or generated runtime file in package: " + item.filename)
            data = package.read(item)
            if any(value in data for value in secrets) or any(
                marker in data
                for marker in (b"-----BEGIN PRIVATE KEY-----", b"-----BEGIN RSA PRIVATE KEY-----")
            ):
                raise ReleaseError("Private material found in package: " + item.filename)
            actual[name.relative_to("FinGuard").as_posix()] = hashlib.sha256(data).hexdigest()
        manifest = json.loads(package.read("FinGuard/manifest.json"))
        if manifest.get("version") != version or manifest.get("platform") != "windows-x64":
            raise ReleaseError("Package version or platform mismatch")
        claimed = manifest.get("files", {})
        if (
            not claimed
            or set(actual) - {"manifest.json"} != set(claimed)
            or any(actual[name] != value for name, value in claimed.items())
        ):
            raise ReleaseError("Package manifest does not match all files")
        required = {
            "FinGuard.exe",
            "LICENSE",
            "NOTICE",
            "使用说明.txt",
            "frontend/index.html",
            "services/backend/finguard-api.exe",
            "services/bank-simulator/finguard-bank.exe",
            "postgresql/bin/initdb.exe",
            "postgresql/bin/postgres.exe",
            "postgresql/bin/pg_ctl.exe",
            "postgresql/bin/psql.exe",
            "licenses/packages.json",
            "licenses/npm-packages.json",
        }
        if not required <= set(actual):
            raise ReleaseError("Required release file missing")
        if license_text is not None and package.read("FinGuard/LICENSE") != license_text:
            raise ReleaseError("Packaged project license mismatch")
        if notice_text is not None and package.read("FinGuard/NOTICE") != notice_text:
            raise ReleaseError("Packaged project NOTICE mismatch")
        inventory = {
            "format": "finguard-dependency-inventory-v1",
            "scope": "build-environment-and-frontend-production-resolution",
            "not_a_complete_runtime_sbom": True,
            "python_build_environment": json.loads(package.read("FinGuard/licenses/packages.json")),
            "frontend_production_resolution": json.loads(
                package.read("FinGuard/licenses/npm-packages.json")
            ),
            "database": {
                "name": "PostgreSQL",
                "version": "18.6",
                "packaging_modification": "process-level UTF-8 manifest",
            },
            "external_runtime": {"name": "Microsoft Edge WebView2 Runtime", "bundled": False},
        }
    return manifest, inventory, len(actual)


def validate_report(report, version):
    if (
        report.get("version") != version
        or report.get("status") != "passed"
        or report.get("paid_model_calls") != 0
        or not CHECKS <= set(report.get("checks", []))
    ):
        raise ReleaseError("EXE self-test did not confirm all required checks")
    result = {
        "version": version,
        "status": "passed",
        "checks": sorted(CHECKS | (set(report.get("checks", [])) & OPTIONAL_CHECKS)),
        "paid_model_calls": 0,
    }
    for key in ("startup_seconds", "resume_seconds"):
        if key in report:
            value = report[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ReleaseError("Invalid self-test duration")
            result[key] = value
    return result


def self_test(archive, scratch, version):
    if os.name != "nt":
        raise ReleaseError("Actual EXE verification requires Windows")
    extracted = scratch / "中文程序目录"
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            destination = (extracted / item.filename).resolve()
            if not destination.is_relative_to(extracted.resolve()):
                raise ReleaseError("Unsafe extraction destination")
            if not item.is_dir():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
    report_path = scratch / "self-test.json"
    process = subprocess.run(
        [
            str(extracted / "FinGuard/FinGuard.exe"),
            "--self-test",
            "--data-dir",
            str(scratch / "中文模拟数据"),
            "--report",
            str(report_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
        creationflags=0x08000000,
    )
    if process.returncode or not report_path.exists():
        raise ReleaseError(
            "EXE self-test failed; inspect the private release-preparation directory"
        )
    return validate_report(json.loads(report_path.read_text(encoding="utf-8")), version)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_directory(directory):
    lines = (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    expected = {}
    for line in lines:
        checksum, separator, name = line.partition("  ")
        if (
            not separator
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or len(safe_name(name).parts) != 1
            or name in expected
            or name == "SHA256SUMS"
        ):
            raise ReleaseError("Invalid checksum manifest")
        expected[name] = checksum
    files = {p.name for p in directory.iterdir() if p.is_file()}
    if (
        any(not p.is_file() or p.is_symlink() for p in directory.iterdir())
        or files != set(expected) | {"SHA256SUMS"}
        or any(digest(directory / name) != checksum for name, checksum in expected.items())
    ):
        raise ReleaseError("Release directory file set or checksum mismatch")
    metadata = json.loads((directory / "release.json").read_text(encoding="utf-8"))
    proof = json.loads((directory / "VERIFICATION.json").read_text(encoding="utf-8"))
    required = {
        "README.md",
        "RELEASE-NOTES.md",
        "THIRD-PARTY-NOTICES.md",
        "LICENSE",
        "NOTICE",
        "DEPENDENCIES.json",
        "release.json",
        "VERIFICATION.json",
        "SHA256SUMS",
        metadata["archive"]["name"],
    }
    if files != required:
        raise ReleaseError("Release attachment set is incomplete or has unexpected files")
    archive = directory / metadata["archive"]["name"]
    if (
        len(safe_name(metadata["archive"]["name"]).parts) != 1
        or proof["archive_sha256"] != digest(archive)
        or metadata["archive"]["sha256"] != proof["archive_sha256"]
    ):
        raise ReleaseError("Verification is not bound to the release archive")
    validate_report(proof["self_test"], metadata["version"])
    manifest, inventory, count = inspect_archive(
        archive,
        metadata["version"],
        license_text=(directory / "LICENSE").read_bytes(),
        notice_text=(directory / "NOTICE").read_bytes(),
    )
    if (
        proof["files_checked"] != count
        or proof["manifest_canonical_sha256"]
        != hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        or json.loads((directory / "DEPENDENCIES.json").read_text(encoding="utf-8")) != inventory
    ):
        raise ReleaseError("Release evidence or dependency inventory does not match the package")
    return {
        "version": metadata["version"],
        "files": len(files),
        "archive_sha256": proof["archive_sha256"],
        "status": "verified",
        "published": False,
    }


def prepare(root, archive, output=None):
    version = versions(root)
    name = f"FinGuard-{version}-windows-x64.zip"
    if archive.name != name:
        raise ReleaseError("Archive name does not match the release version")
    destination = (output or root / f"dist/releases/{version}/windows-x64").resolve()
    if not destination.is_relative_to((root / "dist/releases").resolve()) or destination.exists():
        raise ReleaseError(
            "Release output must be a new directory under dist/releases; existing releases are immutable"
        )
    scratch = root / "artifacts/release-preparation" / uuid4().hex
    scratch.mkdir(parents=True)
    stage = scratch / "attachments"
    stage.mkdir()
    shutil.copy2(archive, stage / name)
    archive = stage / name
    sha = digest(archive)
    manifest, inventory, count = inspect_archive(
        archive,
        version,
        private_values(root),
        (root / "LICENSE").read_bytes(),
        (root / "NOTICE").read_bytes(),
    )
    inputs = manifest.get("inputs", {})
    for source, expected in inputs.items():
        path = (root / source).resolve()
        if (
            not path.is_relative_to(root.resolve())
            or not path.is_file()
            or digest(path) != expected
        ):
            raise ReleaseError("Build input mismatch; rebuild before preparing this release")
    if not inputs:
        raise ReleaseError("Package build inputs are missing")
    report = self_test(archive, scratch, version)
    timestamp = datetime.now(UTC).isoformat()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    replacements = {
        "VERSION": version,
        "ARCHIVE": name,
        "PREPARED_DATE": timestamp[:10],
        "LICENSE_STATUS": "Apache-2.0",
    }
    for filename in ("README.md", "THIRD-PARTY-NOTICES.md"):
        content = (root / "packaging/release/templates" / filename).read_text(encoding="utf-8")
        for key, value in replacements.items():
            content = content.replace("{{" + key + "}}", value)
        if "{{" in content:
            raise ReleaseError("Unresolved release template")
        (stage / filename).write_text(content, encoding="utf-8")
    shutil.copy2(root / f"docs/releases/{version}.md", stage / "RELEASE-NOTES.md")
    for filename in ("LICENSE", "NOTICE"):
        shutil.copy2(root / filename, stage / filename)
    dump(stage / "DEPENDENCIES.json", inventory)
    dump(
        stage / "VERIFICATION.json",
        {
            "format": "finguard-release-verification-v1",
            "archive_sha256": sha,
            "manifest_canonical_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True).encode()
            ).hexdigest(),
            "files_checked": count,
            "known_local_secrets_found": False,
            "build_inputs_match": True,
            "self_test": report,
        },
    )
    dump(
        stage / "release.json",
        {
            "format": "finguard-release-v1",
            "product": "FinGuard",
            "version": version,
            "platform": "windows-x64",
            "channel": "preview",
            "status": "prepared-not-published",
            "published": False,
            "planned_tag": "v" + version,
            "license": "Apache-2.0",
            "code_signing": "unsigned",
            "prepared_at": timestamp,
            "preparation_revision": revision,
            "build_source_revision": manifest.get("source_revision"),
            "build_source_dirty": manifest.get("source_dirty"),
            "archive": {"name": name, "bytes": archive.stat().st_size, "sha256": sha},
            "limitations": [
                "simulated-business-only",
                "webview2-required",
                "target-os-gui-coverage-incomplete",
                "no-automatic-update-or-cross-computer-migration",
            ],
        },
    )
    files = sorted(stage.iterdir(), key=lambda p: p.name)
    (stage / "SHA256SUMS").write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )
    verify_directory(stage)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not stage.resolve().is_relative_to((root / "artifacts/release-preparation").resolve()):
        raise ReleaseError("Unsafe staging directory")
    stage.rename(destination)
    return {**verify_directory(destination), "directory": str(destination)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            result = verify_directory(args.verify)
        else:
            if not args.self_test:
                parser.error(
                    "Release preparation requires --self-test; use --verify for existing attachments"
                )
            version = versions(ROOT)
            result = prepare(
                ROOT,
                (args.archive or ROOT / f"dist/FinGuard-{version}-windows-x64.zip").resolve(),
                args.output,
            )
        print(json.dumps(result, ensure_ascii=False))
    except (ReleaseError, OSError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
