#!/usr/bin/env python3
"""Report reproducible host readiness and optional Android device readiness."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perfetto_tools.artifacts import (  # noqa: E402
    ArtifactFailure,
    verified_trace_processor,
    verify_bundled_artifacts,
)
from perfetto_tools.runtime import (  # noqa: E402
    AdbDevice,
    DeviceInfo,
    RuntimeFailure,
    check_feature_compatibility,
    list_adb_devices,
    probe_device,
    resolve_adb,
    run_adb,
    select_device,
)


EXPECTED_PYTHON = "3.13.14"
EXPECTED_PACKAGES = {"perfetto": "0.57.2", "protobuf": "6.33.6"}
EXPECTED_ADB = "37.0.0"
EXPECTED_PERFETTO = "57.2"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    exit_code: int = 0


def _package_versions() -> dict[str, str]:
    versions = {}
    for package in EXPECTED_PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "NOT INSTALLED"
    return versions


def _check_runtime(checks: list[Check], repo_root: Path) -> None:
    actual = platform.python_version()
    executable = Path(sys.executable)
    managed = executable.parent.parent == repo_root / ".venv"
    if actual != EXPECTED_PYTHON:
        checks.append(
            Check(
                "managed Python",
                "FAIL",
                f"expected {EXPECTED_PYTHON}, got {actual} ({executable})",
                3,
            )
        )
    elif not managed:
        checks.append(
            Check(
                "managed Python",
                "WARN",
                f"version is correct but interpreter is external: {executable}",
            )
        )
    else:
        checks.append(Check("managed Python", "PASS", f"{actual} ({executable})"))


def _check_packages(checks: list[Check], versions: Mapping[str, str]) -> None:
    for package, expected in EXPECTED_PACKAGES.items():
        actual = versions.get(package, "NOT INSTALLED")
        if actual == expected:
            checks.append(Check(f"{package} package", "PASS", actual))
        else:
            checks.append(
                Check(
                    f"{package} package",
                    "FAIL",
                    f"expected {expected}, got {actual}",
                    3,
                )
            )


def _check_artifacts(checks: list[Check], repo_root: Path) -> None:
    try:
        verified = verify_bundled_artifacts(repo_root)
        shell = verified_trace_processor(repo_root)
        result = subprocess.run(
            [str(shell), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        if f"Perfetto v{EXPECTED_PERFETTO}-" not in result.stdout:
            raise ArtifactFailure(
                f"trace processor reported an unexpected version: {result.stdout.strip()}"
            )
    except (ArtifactFailure, OSError, subprocess.SubprocessError) as exc:
        checks.append(Check("bundled artifacts", "FAIL", str(exc), 3))
        return
    checks.append(Check("bundled artifacts", "PASS", f"{len(verified)} checksums"))
    checks.append(Check("trace processor", "PASS", str(shell)))


def _check_output(checks: list[Check], repo_root: Path) -> None:
    traces = repo_root / "traces"
    try:
        traces.mkdir(exist_ok=True)
    except OSError as exc:
        checks.append(Check("trace output", "FAIL", str(exc), 3))
        return
    if not traces.is_dir() or not os.access(traces, os.W_OK):
        checks.append(Check("trace output", "FAIL", f"not writable: {traces}", 3))
    else:
        checks.append(Check("trace output", "PASS", str(traces)))


def _adb_version(adb: str) -> str:
    result = run_adb(["version"], adb=adb, timeout=10)
    match = re.search(r"^Version\s+(\d+\.\d+\.\d+)(?:-|$)", result.stdout, re.MULTILINE)
    if not match:
        raise RuntimeFailure(f"Could not parse ADB version: {result.stdout.strip()}", 3)
    return match.group(1)


def _device_status(
    checks: list[Check],
    *,
    adb: str | None,
    devices: Sequence[AdbDevice] | None,
    device_info: DeviceInfo | None,
    require_device: bool,
    feature: str,
    serial: str | None,
) -> None:
    if adb is None and devices is None:
        status = "FAIL" if require_device else "NOT AVAILABLE"
        checks.append(Check("Android device", status, "ADB host check failed", 4 if require_device else 0))
        checks.append(Check("Android compatibility", "NOT AVAILABLE", "no selected device"))
        return
    try:
        detected = list(devices) if devices is not None else list_adb_devices(adb or "adb")
        if not detected:
            status = "FAIL" if require_device else "NOT AVAILABLE"
            checks.append(
                Check(
                    "Android device",
                    status,
                    "none connected/authorized",
                    4 if require_device else 0,
                )
            )
            checks.append(Check("Android compatibility", "NOT AVAILABLE", "no selected device"))
            return
        selected = select_device(detected, serial)
        info = device_info or probe_device(adb or "adb", selected.serial)
    except RuntimeFailure as exc:
        checks.append(
            Check(
                "Android device",
                "FAIL" if require_device else "WARN",
                str(exc),
                4 if require_device else 0,
            )
        )
        checks.append(Check("Android compatibility", "NOT AVAILABLE", "no ready device"))
        return

    checks.append(
        Check(
            "Android device",
            "PASS",
            f"{info.serial}, API {info.api_level}, {info.abi}, {info.build_type}",
        )
    )
    try:
        warnings = check_feature_compatibility(feature, info)
    except RuntimeFailure as exc:
        checks.append(Check("Android compatibility", "FAIL", str(exc), 4))
        return
    if warnings:
        checks.append(Check("Android compatibility", "WARN", " ".join(warnings)))
    else:
        checks.append(Check("Android compatibility", "PASS", f"{feature} supported"))


def collect_checks(
    require_device: bool = False,
    *,
    feature: str = "general",
    serial: str | None = None,
    package_versions: Mapping[str, str] | None = None,
    devices: Sequence[AdbDevice] | None = None,
    device_info: DeviceInfo | None = None,
    repo_root: Path = REPO_ROOT,
) -> list[Check]:
    checks: list[Check] = []
    _check_runtime(checks, repo_root)
    _check_packages(checks, _package_versions() if package_versions is None else package_versions)
    _check_artifacts(checks, repo_root)
    _check_output(checks, repo_root)

    adb: str | None = None
    try:
        adb = resolve_adb(repo_root)
        version = _adb_version(adb)
        if version != EXPECTED_ADB:
            checks.append(
                Check("ADB", "FAIL", f"expected {EXPECTED_ADB}, got {version} ({adb})", 3)
            )
        else:
            managed = (repo_root / ".bin") in Path(adb).parents
            status = "PASS" if managed else "WARN"
            detail = f"{version} ({adb})" + ("" if managed else "; external override/PATH")
            checks.append(Check("ADB", status, detail))
    except RuntimeFailure as exc:
        checks.append(Check("ADB", "FAIL", str(exc), 3))

    _device_status(
        checks,
        adb=adb,
        devices=devices,
        device_info=device_info,
        require_device=require_device,
        feature=feature,
        serial=serial,
    )
    return checks


def exit_code_for(checks: Sequence[Check]) -> int:
    return max((check.exit_code for check in checks if check.status == "FAIL"), default=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", action="store_true", help="require one ready device")
    parser.add_argument("--feature", default="general", help="compatibility feature/config")
    parser.add_argument("--serial", help="ADB serial when multiple devices are connected")
    parser.add_argument("--json", action="store_true", help="emit machine-readable checks")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    collector: Callable[..., list[Check]] = collect_checks,
) -> int:
    args = build_parser().parse_args(argv)
    checks = collector(
        require_device=args.device,
        feature=args.feature,
        serial=args.serial,
    )
    if args.json:
        print(json.dumps([asdict(check) for check in checks], indent=2))
    else:
        width = max((len(check.status) for check in checks), default=4)
        for check in checks:
            print(f"[{check.status:<{width}}] {check.name}: {check.detail}")
    return exit_code_for(checks)


if __name__ == "__main__":
    raise SystemExit(main())

