"""发布文件完整性、版本与隐私边界；不执行测试替身中的可执行文件。"""

import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_tools_test", ROOT / "scripts/prepare-release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def archive(tmp_path, *, extra=None, tamper=False):
    files = {
        name: b"fixture"
        for name in (
            "FinGuard.exe",
            "使用说明.txt",
            "frontend/index.html",
            "services/backend/finguard-api.exe",
            "services/bank-simulator/finguard-bank.exe",
            "postgresql/bin/initdb.exe",
            "postgresql/bin/postgres.exe",
            "postgresql/bin/pg_ctl.exe",
            "postgresql/bin/psql.exe",
        )
    }
    files.update(
        {
            "LICENSE": (ROOT / "LICENSE").read_bytes(),
            "NOTICE": (ROOT / "NOTICE").read_bytes(),
            "licenses/packages.json": b"[]",
            "licenses/npm-packages.json": b"[]",
        }
    )
    files.update(extra or {})
    manifest = {
        "version": "0.3.0",
        "platform": "windows-x64",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    if tamper:
        files["FinGuard.exe"] = b"changed after the manifest was created"
    path = tmp_path / "FinGuard-0.3.0-windows-x64.zip"
    with zipfile.ZipFile(path, "w") as package:
        for name, data in files.items():
            package.writestr("FinGuard/" + name, data)
        package.writestr("FinGuard/manifest.json", json.dumps(manifest))
    return path


def test_current_components_have_matching_versions_and_apache_license():
    assert release.versions(ROOT) == "0.3.0"


def test_archive_has_complete_manifest_and_project_license(tmp_path):
    manifest, inventory, count = release.inspect_archive(
        archive(tmp_path),
        "0.3.0",
        license_text=(ROOT / "LICENSE").read_bytes(),
        notice_text=(ROOT / "NOTICE").read_bytes(),
    )
    assert manifest["version"] == "0.3.0" and count == len(manifest["files"]) + 1
    assert inventory["not_a_complete_runtime_sbom"] is True


def test_tampered_archive_is_rejected(tmp_path):
    with pytest.raises(release.ReleaseError, match="manifest"):
        release.inspect_archive(archive(tmp_path, tamper=True), "0.3.0")


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "C:/outside",
        "FinGuard/../escape",
        "FinGuard\\escape",
        "FinGuard/CON.txt",
        "FinGuard/log\n.txt",
        "FinGuard/file. ",
    ],
)
def test_unsafe_windows_archive_names_are_rejected(name):
    with pytest.raises(release.ReleaseError):
        release.safe_name(name)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "settings.local.json",
        "database/PG_VERSION",
        "logs/backend.log",
        "node_modules/fixture.js",
    ],
)
def test_runtime_files_cannot_be_released(tmp_path, name):
    with pytest.raises(release.ReleaseError, match="runtime"):
        release.inspect_archive(archive(tmp_path, extra={name: b"private fixture"}), "0.3.0")


def test_known_secret_is_not_allowed_in_arbitrary_file(tmp_path):
    with pytest.raises(release.ReleaseError, match="Private material"):
        release.inspect_archive(
            archive(tmp_path, extra={"notes.txt": b"fixture-secret"}), "0.3.0", (b"fixture-secret",)
        )


def test_version_and_license_conflicts_are_rejected(tmp_path):
    with pytest.raises(release.ReleaseError, match="version"):
        release.inspect_archive(archive(tmp_path), "9.9.9")
    with pytest.raises(release.ReleaseError, match="license"):
        release.inspect_archive(archive(tmp_path), "0.3.0", license_text=b"different license")


def test_report_requires_every_check_and_strips_unrelated_fields():
    report = {
        "version": "0.3.0",
        "status": "passed",
        "checks": list(release.CHECKS),
        "paid_model_calls": 0,
        "token": "private-fixture",
    }
    assert "token" not in release.validate_report(report, "0.3.0")
    report["checks"] = []
    with pytest.raises(release.ReleaseError, match="required"):
        release.validate_report(report, "0.3.0")


def test_preparation_does_not_overwrite_a_previous_release(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("prior release", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="immutable"):
        release.prepare(ROOT, tmp_path / "FinGuard-0.3.0-windows-x64.zip", target)
    assert (target / "keep.txt").read_text() == "prior release"


def test_directory_verification_detects_attachment_tampering(tmp_path):
    (tmp_path / "README.md").write_text("original", encoding="utf-8")
    checksum = release.digest(tmp_path / "README.md")
    (tmp_path / "SHA256SUMS").write_text(checksum + "  README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="checksum"):
        release.verify_directory(tmp_path)


def test_project_version_conflict_is_detected_before_packaging(tmp_path):
    paths = [
        "pyproject.toml",
        "packages/bank-contracts/pyproject.toml",
        "packages/plugin-contracts/pyproject.toml",
        "services/bank-simulator/pyproject.toml",
        "packaging/windows/pyproject.toml",
    ]
    for name in paths:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    target = tmp_path / "packages/bank-contracts/pyproject.toml"
    target.write_text(
        target.read_text().replace('version = "0.3.0"', 'version = "0.4.0"'), encoding="utf-8"
    )
    with pytest.raises(release.ReleaseError, match="component version"):
        release.versions(tmp_path)


def test_optional_current_desktop_checks_are_preserved_but_unknown_fields_are_not():
    report = {
        "version": "0.3.0",
        "status": "passed",
        "checks": [*release.CHECKS, "wallet_and_conversation_persist", "unknown-check"],
        "paid_model_calls": 0,
    }
    result = release.validate_report(report, "0.3.0")
    assert "wallet_and_conversation_persist" in result["checks"]
    assert "unknown-check" not in result["checks"]
