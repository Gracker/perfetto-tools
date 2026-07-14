#!/usr/bin/env python3
"""Shared post-bootstrap setup for verified repository-local tools."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BIN_DIR = REPO_ROOT / ".bin"
VERSIONS_FILE = SCRIPT_DIR / "tool-versions.env"


class SetupError(Exception):
    """A setup failure with a remediation-oriented message."""


def read_env_manifest(path: Path = VERSIONS_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            raise SetupError(f"Malformed version manifest line: {raw_line!r}")
        values[key] = value
    return values


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundled_artifacts(repo_root: Path = REPO_ROOT) -> list[str]:
    verified: list[str] = []
    checksum_file = repo_root / "tools" / "sha256.txt"
    for raw_line in checksum_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        expected, relative = line.split(maxsplit=1)
        artifact = repo_root / relative
        if not artifact.is_file():
            raise SetupError(f"Bundled artifact missing: {relative}")
        actual = sha256_file(artifact)
        if actual != expected:
            raise SetupError(
                f"Bundled artifact checksum mismatch: {relative}\n"
                f"  expected {expected}\n  actual   {actual}"
            )
        verified.append(relative)
    if not verified:
        raise SetupError("No bundled artifact checksums were found")
    return verified


def verify_python_environment(versions: dict[str, str]) -> None:
    expected_python = tuple(int(part) for part in versions["PYTHON_VERSION"].split("."))
    actual_python = sys.version_info[:3]
    if actual_python != expected_python:
        raise SetupError(
            "Managed Python version mismatch: expected "
            f"{versions['PYTHON_VERSION']}, got {platform.python_version()}"
        )
    expected_packages = {"perfetto": "0.57.2", "protobuf": "6.33.6"}
    for package, expected in expected_packages.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise SetupError(
                f"Managed package missing: {package}; rerun the native setup command"
            ) from exc
        if actual != expected:
            raise SetupError(
                f"Managed package mismatch: {package} expected {expected}, got {actual}"
            )


def host_key() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine == "arm64":
            return "MAC_ARM64"
        if machine in {"x86_64", "amd64"}:
            return "MAC_AMD64"
    if sys.platform.startswith("linux"):
        libc_name = platform.libc_ver()[0].lower()
        if libc_name and libc_name != "glibc":
            raise SetupError(f"Unsupported Linux C library: {libc_name}; glibc is required")
        if machine in {"aarch64", "arm64"}:
            return "LINUX_ARM64"
        if machine in {"x86_64", "amd64"}:
            return "LINUX_AMD64"
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "WINDOWS_AMD64"
    raise SetupError(f"Unsupported host: {sys.platform}/{machine}")


def adb_executable(platform_tools: Path) -> Path:
    return platform_tools / ("adb.exe" if os.name == "nt" else "adb")


def adb_version(adb: Path) -> str:
    try:
        result = subprocess.run(
            [str(adb), "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError(f"ADB is not runnable: {adb}: {exc}") from exc
    match = re.search(r"^Version\s+(\d+\.\d+\.\d+)(?:-|$)", result.stdout, re.MULTILINE)
    if not match:
        raise SetupError(f"Could not parse ADB version from {adb}: {result.stdout.strip()}")
    return match.group(1)


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        root = destination.resolve()
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise SetupError(f"Unsafe archive path: {member.filename}")
        bundle.extractall(destination)


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "perfetto-tools-setup"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
    except (OSError, TimeoutError) as exc:
        destination.unlink(missing_ok=True)
        raise SetupError(f"Download failed: {url}: {exc}") from exc


def _platform_tools_source_is_current(directory: Path, expected: str) -> bool:
    properties = directory / "source.properties"
    adb = adb_executable(directory)
    if not properties.is_file() or not adb.is_file():
        return False
    revision = None
    for line in properties.read_text(encoding="utf-8").splitlines():
        if line.startswith("Pkg.Revision="):
            revision = line.partition("=")[2].strip()
            break
    if revision != expected:
        return False
    try:
        return adb_version(adb) == expected
    except SetupError:
        return False


def _install_platform_tools(
    managed_dir: Path,
    url: str,
    expected_sha256: str,
    expected_version: str,
) -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="platform-tools-", dir=BIN_DIR) as temp_name:
        temp_dir = Path(temp_name)
        archive = temp_dir / "platform-tools.zip"
        _download(url, archive)
        actual = sha256_file(archive)
        if actual != expected_sha256:
            raise SetupError(
                "Platform-Tools archive checksum mismatch\n"
                f"  expected {expected_sha256}\n  actual   {actual}"
            )
        extracted = temp_dir / "extracted"
        extracted.mkdir()
        _safe_extract_zip(archive, extracted)
        candidate = extracted / "platform-tools"
        candidate_adb = adb_executable(candidate)
        if not candidate_adb.is_file():
            raise SetupError("Platform-Tools archive does not contain the expected adb")
        if os.name != "nt":
            candidate_adb.chmod(candidate_adb.stat().st_mode | 0o111)
        if not _platform_tools_source_is_current(candidate, expected_version):
            raise SetupError(
                f"Platform-Tools archive did not report expected version {expected_version}"
            )
        staging = BIN_DIR / ".platform-tools.new"
        backup = BIN_DIR / ".platform-tools.old"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        shutil.move(str(candidate), staging)
        if managed_dir.exists():
            managed_dir.rename(backup)
        staging.rename(managed_dir)
        shutil.rmtree(backup, ignore_errors=True)
    return adb_executable(managed_dir)


def ensure_platform_tools(
    repo_root: Path = REPO_ROOT,
    versions: dict[str, str] | None = None,
) -> Path | None:
    versions = versions or read_env_manifest()
    override = os.environ.get("PERFETTO_TOOLS_ADB")
    if override:
        adb = Path(override).expanduser().resolve()
        if not adb.is_file():
            raise SetupError(f"PERFETTO_TOOLS_ADB is not a file: {adb}")
        actual = adb_version(adb)
        expected = versions["PLATFORM_TOOLS_VERSION"]
        if actual != expected:
            print(
                f"[setup] WARNING: explicit ADB override is {actual}; pinned version is {expected}.",
                file=sys.stderr,
            )
        return adb

    key = host_key()
    if key == "LINUX_ARM64":
        print(
            "[setup] WARNING: Google does not ship Linux ARM64 Platform-Tools; "
            "analysis is ready, capture requires PERFETTO_TOOLS_ADB.",
            file=sys.stderr,
        )
        return None

    artifact_key = "MAC" if key.startswith("MAC_") else key
    managed_dir = repo_root / ".bin" / "platform-tools"
    expected = versions["PLATFORM_TOOLS_VERSION"]
    if _platform_tools_source_is_current(managed_dir, expected):
        return adb_executable(managed_dir)

    url = versions[f"PT_{artifact_key}_URL"]
    expected_sha = versions[f"PT_{artifact_key}_SHA256"]
    return _install_platform_tools(managed_dir, url, expected_sha, expected)


def _ensure_unix_adb_link(adb: Path | None) -> None:
    if adb is None or os.name == "nt" or adb.parent.name != "platform-tools":
        return
    link = BIN_DIR / "adb"
    link.unlink(missing_ok=True)
    link.symlink_to(Path("platform-tools") / "adb")


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        versions = read_env_manifest()
        verify_python_environment(versions)
        verified = verify_bundled_artifacts()
        adb = ensure_platform_tools(versions=versions)
        _ensure_unix_adb_link(adb)
    except SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    print(f"[setup] Python: {platform.python_version()} ({Path(sys.executable)})")
    print("[setup] perfetto: 0.57.2; protobuf: 6.33.6")
    print(f"[setup] verified bundled artifacts: {len(verified)}")
    if adb is not None:
        print(f"[setup] adb: {adb} ({adb_version(adb)})")
    print("[setup] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
