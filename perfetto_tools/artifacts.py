"""Integrity and host-selection helpers for repository-bundled binaries."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
from pathlib import Path


class ArtifactFailure(RuntimeError):
    """A bundled artifact is missing, unsupported, or failed integrity checks."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_checksum_manifest(repo_root: Path) -> dict[Path, str]:
    manifest = repo_root / "tools" / "sha256.txt"
    checksums: dict[Path, str] = {}
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ArtifactFailure(
            f"Bundled checksum manifest is missing or unavailable: {exc}. "
            "Restore the repository files and rerun setup."
        ) from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            raise ArtifactFailure(f"Malformed checksum manifest line: {raw_line!r}")
        expected, relative_text = fields
        relative = Path(relative_text)
        if (
            len(expected) != 64
            or any(char not in "0123456789abcdef" for char in expected)
            or relative.is_absolute()
            or ".." in relative.parts
            or relative in checksums
        ):
            raise ArtifactFailure(f"Malformed checksum manifest line: {raw_line!r}")
        checksums[relative] = expected
    if not checksums:
        raise ArtifactFailure("Bundled checksum manifest has no artifact entries")
    return checksums


def verify_bundled_artifacts(repo_root: Path) -> list[Path]:
    verified: list[Path] = []
    for relative, expected in read_checksum_manifest(repo_root).items():
        artifact = repo_root / relative
        if not artifact.is_file():
            raise ArtifactFailure(
                f"Bundled artifact is missing: {relative}. Restore the repository "
                "files and rerun setup."
            )
        actual = sha256_file(artifact)
        if actual != expected:
            raise ArtifactFailure(
                f"Bundled artifact checksum mismatch: {relative}; expected "
                f"{expected}, got {actual}. Restore it and rerun setup."
            )
        verified.append(artifact)
    return verified


def verify_official_helper(repo_root: Path) -> Path:
    metadata_path = repo_root / "official" / "VERSION"
    try:
        metadata_lines = metadata_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ArtifactFailure(
            f"Official helper metadata is unavailable: {exc}. Restore the repository files."
        ) from exc
    metadata = {}
    for raw_line in metadata_lines:
        if ":" in raw_line:
            key, value = raw_line.split(":", 1)
            metadata[key.strip()] = value.strip()
    expected = metadata.get("sha256", "")
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ArtifactFailure("official/VERSION has no valid lowercase SHA256 pin")
    helper = repo_root / "official" / "record_android_trace"
    if not helper.is_file():
        raise ArtifactFailure("Pinned official/record_android_trace is missing")
    actual = sha256_file(helper)
    if actual != expected:
        raise ArtifactFailure(
            "Pinned official/record_android_trace checksum mismatch; restore the "
            "repository file and rerun setup."
        )
    return helper


def trace_processor_relative_path(
    system: str | None = None,
    machine: str | None = None,
    sys_platform: str | None = None,
) -> Path:
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    sys_platform = sys_platform or sys.platform

    name: str | None = None
    if system == "Darwin":
        if machine == "arm64":
            name = "mac-arm64"
        elif machine in {"x86_64", "amd64"}:
            name = "mac-amd64"
    elif system == "Linux":
        if machine in {"aarch64", "arm64"}:
            name = "linux-arm64"
        elif machine in {"x86_64", "amd64"}:
            name = "linux-amd64"
    elif system == "Windows" or sys_platform == "win32":
        if machine in {"x86_64", "amd64"}:
            name = "windows-amd64.exe"
    if name is None:
        raise ArtifactFailure(
            f"No bundled trace_processor_shell supports {system}/{machine}."
        )
    return Path("tools") / "trace_processor_shell" / name


def verified_trace_processor(
    repo_root: Path,
    *,
    system: str | None = None,
    machine: str | None = None,
    sys_platform: str | None = None,
) -> Path:
    relative = trace_processor_relative_path(system, machine, sys_platform)
    path = repo_root / relative
    checksums = read_checksum_manifest(repo_root)
    expected = checksums.get(relative)
    if not path.is_file() or expected is None:
        raise ArtifactFailure(
            f"Bundled trace_processor_shell is missing: {relative}. Restore the "
            "repository artifacts and rerun setup."
        )
    actual = sha256_file(path)
    if actual != expected:
        raise ArtifactFailure(
            f"Bundled trace_processor_shell checksum mismatch: {relative}. "
            "Restore the repository artifacts and rerun setup."
        )
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise ArtifactFailure(
            f"Bundled trace_processor_shell is not executable: {relative}. "
            "Restore its file mode and rerun setup."
        )
    return path
