"""Bounded ADB operations and Android compatibility checks.

Exit-code classes are part of the command-line contract:

* 3: host setup or executable problem
* 4: ADB/device connection problem
* 5: Android/device capability problem
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


DEFAULT_ADB_TIMEOUT = 15


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    details: str


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    api_level: int
    abi: str
    build_type: str
    traced_state: str


class RuntimeFailure(Exception):
    """Expected host, device, or compatibility failure with a stable exit code."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


Runner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_adb(
    repo_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Resolve explicit override, managed ADB, then a usable PATH fallback."""
    environment = os.environ if environment is None else environment
    override = environment.get("PERFETTO_TOOLS_ADB")
    if override:
        candidate = Path(override).expanduser().resolve()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise RuntimeFailure(
            "PERFETTO_TOOLS_ADB does not point to an executable file: "
            f"{candidate}",
            3,
        )

    managed_candidates = [
        repo_root / ".bin" / "adb",
        repo_root / ".bin" / "adb.exe",
        repo_root / ".bin" / "platform-tools" / "adb",
        repo_root / ".bin" / "platform-tools" / "adb.exe",
    ]
    for candidate in managed_candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    discovered = which("adb")
    if discovered:
        return discovered
    raise RuntimeFailure(
        "ADB executable was not found. Run the repository setup first "
        "('./tools/setup.sh' or '.\\tools\\setup.ps1'), or set "
        "PERFETTO_TOOLS_ADB explicitly.",
        3,
    )


def run_adb(
    args: Sequence[str],
    *,
    adb: str = "adb",
    serial: str | None = None,
    timeout: int | float = DEFAULT_ADB_TIMEOUT,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded ADB command and translate OS/process errors."""
    command = [str(adb)]
    if serial:
        command.extend(["-s", serial])
    command.extend(str(arg) for arg in args)
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeFailure(
            f"ADB command timed out after {timeout:g} seconds: {' '.join(command)}. "
            "Reconnect the device and retry.",
            4,
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeFailure(
            f"ADB executable is unavailable ({adb}). Run the repository setup first.",
            3,
        ) from exc
    except PermissionError as exc:
        raise RuntimeFailure(
            f"ADB executable is not runnable ({adb}). Check host file permissions.",
            3,
        ) from exc
    except OSError as exc:
        raise RuntimeFailure(f"Could not start ADB ({adb}): {exc}", 3) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise RuntimeFailure(
            f"ADB command failed (exit {result.returncode}): {detail}", 4
        )
    return result


def parse_adb_devices(output: str) -> list[AdbDevice]:
    """Parse `adb devices -l` without collapsing non-ready states."""
    devices: list[AdbDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        fields = line.split(maxsplit=2)
        if len(fields) < 2:
            continue
        serial, state = fields[:2]
        details = fields[2] if len(fields) == 3 else ""
        if state == "no" and details.startswith("permissions"):
            state = "no permissions"
            details = details.removeprefix("permissions").strip()
        devices.append(AdbDevice(serial, state, details))
    return devices


def list_adb_devices(
    adb: str,
    *,
    timeout: int | float = DEFAULT_ADB_TIMEOUT,
    runner: Runner = subprocess.run,
) -> list[AdbDevice]:
    result = run_adb(
        ["devices", "-l"], adb=adb, timeout=timeout, runner=runner
    )
    if not any(
        line.strip() == "List of devices attached"
        for line in result.stdout.splitlines()
    ):
        detail = result.stdout.strip() or "empty output"
        raise RuntimeFailure(
            f"ADB devices returned unexpected output: {detail}",
            4,
        )
    return parse_adb_devices(result.stdout)


def _state_failure(device: AdbDevice) -> RuntimeFailure:
    if device.state == "unauthorized":
        return RuntimeFailure(
            f"Device {device.serial} is unauthorized. Unlock it and authorize the "
            "USB debugging prompt, then rerun `adb devices -l`.",
            4,
        )
    if device.state == "offline":
        return RuntimeFailure(
            f"Device {device.serial} is offline; reconnect USB (or restart ADB) "
            "and wait for its state to become `device`.",
            4,
        )
    if device.state == "no permissions":
        return RuntimeFailure(
            f"ADB has no permissions for device {device.serial}. Configure host USB "
            "udev rules/group access, reconnect it, and retry.",
            4,
        )
    return RuntimeFailure(
        f"Device {device.serial} is not ready (ADB state: {device.state}). "
        "Run `adb devices -l` and resolve that state before retrying.",
        4,
    )


def select_device(
    devices: Iterable[AdbDevice], serial: str | None = None
) -> AdbDevice:
    """Select exactly one ready device or explain its precise state."""
    detected = list(devices)
    if serial:
        selected = next((device for device in detected if device.serial == serial), None)
        if selected is None:
            summary = ", ".join(
                f"{device.serial} ({device.state})" for device in detected
            ) or "none"
            raise RuntimeFailure(
                f"Requested device {serial} was not found. Detected: {summary}.", 4
            )
        if selected.state != "device":
            raise _state_failure(selected)
        return selected

    ready = [device for device in detected if device.state == "device"]
    if len(ready) == 1:
        return ready[0]
    if len(ready) > 1:
        serials = ", ".join(device.serial for device in ready)
        raise RuntimeFailure(
            f"Found multiple ready Android devices ({serials}); pass --serial <id>.",
            4,
        )
    if len(detected) == 1:
        raise _state_failure(detected[0])
    if detected:
        summary = ", ".join(
            f"{device.serial} ({device.state})" for device in detected
        )
        raise RuntimeFailure(
            f"No ready Android device. Detected: {summary}. Resolve a device state "
            "or pass --serial after it becomes `device`.",
            4,
        )
    raise RuntimeFailure(
        "No Android device was detected. Connect one, enable USB debugging, "
        "authorize this computer, and verify with `adb devices -l`.",
        4,
    )


_PROBE_SCRIPT = """\
printf 'api='; getprop ro.build.version.sdk
printf 'abi='; getprop ro.product.cpu.abi
printf 'build_type='; getprop ro.build.type
printf 'traced='; getprop init.svc.traced
"""


def probe_device(
    adb: str,
    serial: str,
    *,
    timeout: int | float = DEFAULT_ADB_TIMEOUT,
    runner: Runner = subprocess.run,
) -> DeviceInfo:
    """Read the compatibility properties in one bounded device shell call."""
    result = run_adb(
        ["shell", _PROBE_SCRIPT],
        adb=adb,
        serial=serial,
        timeout=timeout,
        runner=runner,
    )
    properties: dict[str, str] = {}
    for line in result.stdout.replace("\r", "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key.strip()] = value.strip()
    try:
        api_level = int(properties.get("api", ""))
    except ValueError as exc:
        raise RuntimeFailure(
            "ADB connected, but could not determine Android API level from "
            f"ro.build.version.sdk (received {properties.get('api')!r}).",
            5,
        ) from exc

    abi = properties.get("abi", "")
    build_type = properties.get("build_type", "")
    if not abi or not build_type:
        missing = ", ".join(
            key for key in ("abi", "build_type") if not properties.get(key)
        )
        raise RuntimeFailure(
            f"ADB connected, but required device properties are missing: {missing}.",
            5,
        )
    return DeviceInfo(
        serial=serial,
        api_level=api_level,
        abi=abi,
        build_type=build_type,
        traced_state=properties.get("traced", "").lower(),
    )


_TRACEBOX_BY_ABI = {
    "armeabi": "android-arm",
    "armeabi-v7a": "android-arm",
    "arm64-v8a": "android-arm64",
    "x86": "android-x86",
    "x86_64": "android-x64",
}


def tracebox_for_device(info: DeviceInfo, tracebox_dir: Path) -> Path | None:
    """Select a legacy on-device tracing binary when system tracing is absent."""
    needs_tracebox = info.api_level < 29 or (
        info.api_level == 29 and info.traced_state not in {"running", "restarting"}
    )
    if not needs_tracebox:
        return None
    artifact = _TRACEBOX_BY_ABI.get(info.abi)
    if artifact is None:
        raise RuntimeFailure(
            f"Legacy Android tracing does not have a bundled tracebox for ABI "
            f"{info.abi!r} (device API {info.api_level}).",
            5,
        )
    return Path(tracebox_dir) / artifact


def check_feature_compatibility(feature: str, info: DeviceInfo) -> list[str]:
    """Reject impossible capture modes and return actionable degradation warnings."""
    if info.api_level < 23:
        raise RuntimeFailure(
            f"Android 6 (API 23) or newer is required; device {info.serial} is "
            f"API {info.api_level}.",
            5,
        )

    normalized = feature.lower().removesuffix(".pbtx")
    needs_frame_timeline = normalized == "fps" or "jank" in normalized
    if needs_frame_timeline and info.api_level < 31:
        raise RuntimeFailure(
            f"{feature} requires Android 12 (API 31) or newer because its FPS/jank "
            f"results use FrameTimeline; device is API {info.api_level}.",
            5,
        )

    warnings: list[str] = []
    if "full" in normalized and info.api_level < 31:
        warnings.append(
            f"The full config includes FrameTimeline, which requires API 31; "
            f"device API {info.api_level} will produce a partial trace."
        )
    if info.api_level < 29:
        warnings.append(
            f"Android API {info.api_level} uses bundled legacy tracebox. Kernel "
            "ftrace availability is OEM-dependent, so this capture is best effort."
        )
    return warnings
