#!/usr/bin/env python3
"""Run short capability-aware captures on an optional physical Android device."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perfetto_tools.runtime import (  # noqa: E402
    DeviceInfo,
    RuntimeFailure,
    check_feature_compatibility,
    list_adb_devices,
    probe_device,
    resolve_adb,
    select_device,
)


@dataclass(frozen=True)
class SmokeItem:
    config: str


@dataclass(frozen=True)
class SmokeResult:
    config: str
    status: str
    detail: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def build_smoke_plan(api_level: int) -> list[SmokeItem]:
    if api_level < 23:
        return []
    configs = ["general"]
    if api_level >= 29:
        configs.append("cpu")
    if api_level >= 31:
        configs.append("jank")
    return [SmokeItem(config) for config in configs]


def run_smoke_plan(
    info: DeviceInfo,
    plan: Sequence[SmokeItem],
    *,
    output_dir: Path,
    runner: Runner = subprocess.run,
    repo_root: Path = REPO_ROOT,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    capture = repo_root / "capture" / "perfetto_capture.py"
    for item in plan:
        output = output_dir / f"{item.config}.perfetto-trace"
        command = [
            sys.executable,
            str(capture),
            "--config",
            item.config,
            "--time",
            "1",
            "--output",
            str(output),
            "--no-open",
            "--serial",
            info.serial,
        ]
        try:
            completed = runner(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            results.append(SmokeResult(item.config, "FAIL", "capture timed out"))
            continue
        except OSError as exc:
            results.append(SmokeResult(item.config, "FAIL", f"could not start capture: {exc}"))
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no diagnostics").strip()
            results.append(
                SmokeResult(item.config, "FAIL", f"exit {completed.returncode}: {detail}")
            )
        elif not output.is_file() or output.stat().st_size == 0:
            results.append(SmokeResult(item.config, "FAIL", "trace is missing or empty"))
        else:
            results.append(SmokeResult(item.config, "PASS", f"{output.stat().st_size} bytes"))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-device", action="store_true")
    parser.add_argument("--serial")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        adb = resolve_adb(REPO_ROOT)
        devices = list_adb_devices(adb)
        if not devices:
            print("[NOT AVAILABLE] Android device: none connected/authorized")
            return 4 if args.require_device else 0
        selected = select_device(devices, args.serial)
        info = probe_device(adb, selected.serial)
        check_feature_compatibility("general", info)
    except RuntimeFailure as exc:
        print(f"[FAIL] Android device: {exc}", file=sys.stderr)
        return 4

    plan = build_smoke_plan(info.api_level)
    with tempfile.TemporaryDirectory(prefix="perfetto-device-smoke-") as temp_name:
        results = run_smoke_plan(info, plan, output_dir=Path(temp_name))
    for result in results:
        print(f"[{result.status}] {result.config}: {result.detail}")
    return 4 if any(result.status == "FAIL" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
