#!/usr/bin/env python3
"""Cross-platform Perfetto capture entry.

Resolves a preset config or lightweight category list, checks the device, then
invokes the pinned official record_android_trace script. One responsibility:
produce a trace file.
"""
import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.version_info < (3, 10) or sys.version_info >= (3, 15):
    sys.exit(
        "perfetto_capture.py requires Python 3.10-3.14 "
        f"(running {sys.version.split()[0]}). Run the repository setup first."
    )

# Directory layout: capture/ sits next to configs/, official/, and the package.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from perfetto_tools.runtime import (  # noqa: E402
    RuntimeFailure,
    check_feature_compatibility,
    list_adb_devices,
    probe_device,
    resolve_adb,
    select_device,
    tracebox_for_device,
)


class ConfigError(Exception):
    pass


def normalize_lightweight_duration(value):
    """Return an official-helper duration, defaulting unitless values to seconds."""
    value = str(value).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", value)
    numeric = float(match.group(1)) if match else 0
    if not match or not math.isfinite(numeric) or numeric <= 0:
        raise ConfigError(
            f"--time must be a positive number with optional s/m/h unit, got {value!r}"
        )
    number, unit = match.groups()
    return f"{number}{unit or 's'}"


def normalize_buffer_size(value):
    """Return a normalized positive Perfetto ring-buffer size."""
    normalized = str(value).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(mb|gb)", normalized)
    numeric = float(match.group(1)) if match else 0
    if not match or not math.isfinite(numeric) or numeric <= 0:
        raise ConfigError(
            f"--buffer must be a positive number followed by mb or gb, got {value!r}"
        )
    return normalized


def validate_category(value):
    """Reject empty category/event tokens and embedded shell/control spacing."""
    value = str(value)
    if not value or value.startswith("-") or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ConfigError(
            f"Invalid category {value!r}; use one non-empty token without whitespace "
            "or a leading hyphen"
        )
    return value


def validate_app(value):
    """Validate an atrace app/package identifier or the supported `*` wildcard."""
    value = str(value)
    segment = r"[A-Za-z_][A-Za-z0-9_]*"
    if value != "*" and not re.fullmatch(
        rf"{segment}(?:\.{segment})*(?::{segment})?", value
    ):
        raise ConfigError(
            f"Invalid --app value {value!r}; use an Android app identifier or '*'"
        )
    return value


def validate_output_path(value):
    """Validate the file destination before any device-side action."""
    output = Path(value).expanduser()
    if output.exists() and output.is_dir():
        raise ConfigError(f"output path is a directory, expected a trace file: {output}")
    parent = output.parent
    if not parent.is_dir():
        raise ConfigError(f"output parent is not an existing directory: {parent}")
    if not os.access(parent, os.W_OK):
        raise ConfigError(f"output parent is not writable: {parent}")
    return str(output)


_CONFIGS_DIR = _REPO_ROOT / "configs"
_OFFICIAL = _REPO_ROOT / "official" / "record_android_trace"
_TRACES_DIR = _REPO_ROOT / "traces"
_TRACEBOX_DIR = _REPO_ROOT / "tools" / "tracebox"


def list_configs(configs_dir=None):
    configs_dir = Path(configs_dir or _CONFIGS_DIR)
    names = []
    for f in sorted(configs_dir.glob("*.pbtx")):
        names.append(f.stem)  # "02_jank_frame"
    return names


def resolve_config(name, configs_dir=None):
    """Resolve a user-supplied short name to an absolute .pbtx path.

    Matching rules (first wins):
      1. Exact filename match ("02_jank_frame.pbtx").
      2. Exact stem match ("02_jank_frame").
      3. Case-insensitive substring match of `name` in the stem.
    Ambiguous substring matches (>1 candidate) raise ConfigError listing them.
    No match raises ConfigError.
    """
    configs_dir = Path(configs_dir or _CONFIGS_DIR)
    name = name.strip()
    lname = name.lower().removesuffix(".pbtx")

    all_files = sorted(configs_dir.glob("*.pbtx"))
    if not all_files:
        raise ConfigError(f"No .pbtx configs found in {configs_dir}")

    # 1. Exact filename
    for f in all_files:
        if f.name == name:
            return str(f.resolve())

    # 2. Exact stem
    for f in all_files:
        if f.stem.lower() == lname:
            return str(f.resolve())

    # 3. Substring (case-insensitive) — but only if unambiguous
    candidates = [f for f in all_files if lname in f.stem.lower()]
    if len(candidates) == 1:
        return str(candidates[0].resolve())
    if len(candidates) > 1:
        cands = ", ".join(f.stem for f in candidates)
        raise ConfigError(
            f"Ambiguous config name '{name}'. Matches: {cands}. "
            f"Be more specific."
        )

    available = ", ".join(f.stem for f in all_files)
    raise ConfigError(
        f"Unknown config '{name}'. Available: {available}"
    )


def _positive_preset_seconds(seconds):
    try:
        numeric_seconds = float(seconds)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"--time must be positive seconds in preset mode, got {seconds!r}"
        ) from exc
    if not math.isfinite(numeric_seconds) or numeric_seconds <= 0:
        raise ConfigError(
            f"--time must be finite positive seconds, got {seconds!r}"
        )
    return numeric_seconds


def apply_duration(config_text, seconds):
    """Return config_text with its top-level duration_ms set to seconds*1000.

    record_android_trace ignores -t/-b/-a when a full -c/--config is supplied
    (those short flags are 'only when not using -c'). So to honor --time we
    rewrite the config's own duration_ms instead of passing -t. Pure + testable.
    """
    ms = int(round(_positive_preset_seconds(seconds) * 1000))
    # Match a TOP-LEVEL duration_ms only — i.e. at column 0 (no leading
    # whitespace). Nested fields inside data_sources{...} are always indented, so
    # `^duration_ms` (no \s*) will not touch them even on their own line.
    if re.search(r"(?m)^duration_ms\s*:", config_text):
        return re.sub(
            r"(?m)^duration_ms\s*:\s*\d+",
            f"duration_ms: {ms}",
            config_text,
            count=1,
        )
    return f"duration_ms: {ms}\n{config_text}"


def materialize_config(config_path, seconds):
    """Write a temp .pbtx with duration_ms overridden; return its path.

    Caller is responsible for cleanup. If `seconds` is falsy, returns the
    original path unchanged (config's own duration_ms wins).
    """
    if not seconds:
        return config_path
    text = Path(config_path).read_text()
    fd, tmp = tempfile.mkstemp(suffix=".pbtx", prefix="capture_")
    with os.fdopen(fd, "w") as f:
        f.write(apply_duration(text, seconds))
    return tmp


def build_official_environment(adb_path, base_env=None):
    """Expose the repository-resolved adb to the unmodified upstream helper."""
    environment = dict(os.environ if base_env is None else base_env)
    if os.path.isabs(adb_path):
        adb_dir = str(Path(adb_path).resolve().parent)
        current_path = environment.get("PATH", "")
        path_entries = current_path.split(os.pathsep) if current_path else []
        environment["PATH"] = os.pathsep.join(
            [adb_dir, *(entry for entry in path_entries if entry != adb_dir)]
        )
    return environment


def prepare_device(args, feature):
    """Select, probe, and capability-check one device before capture."""
    adb_path = resolve_adb(_REPO_ROOT)
    selected = select_device(list_adb_devices(adb_path), args.serial)
    info = probe_device(adb_path, selected.serial)
    warnings = check_feature_compatibility(feature, info)

    # Always pin the selected serial in the upstream command. This prevents a
    # second device attached after preflight from changing capture behavior.
    args.serial = selected.serial
    print(
        f"[capture] device : {info.serial} "
        f"(Android API {info.api_level}, {info.abi}, {info.build_type})"
    )
    for warning in warnings:
        print(f"[capture] WARNING: {warning}", file=sys.stderr)
    return adb_path, info, warnings


def build_official_command(args, output, config_path=None, sideload_path=None):
    """Build the argv for preset-config or lightweight-category capture."""
    cmd = [sys.executable, str(_OFFICIAL)]
    if sideload_path:
        cmd += ["--sideload-path", str(sideload_path)]
    if config_path:
        if args.app or args.buffer:
            raise ConfigError(
                "--app and --buffer are only valid with --categories; "
                "edit the selected pbtxt for equivalent preset-mode control"
            )
        cmd += ["-c", config_path, "-o", output]
    else:
        if not args.categories:
            raise ConfigError("--categories requires at least one Perfetto category")
        cmd += ["-o", output]
        if args.time:
            cmd += ["-t", normalize_lightweight_duration(args.time)]
        if args.buffer:
            cmd += ["-b", args.buffer]
        for app in args.app:
            cmd += ["-a", app]

    if args.serial:
        cmd += ["-s", args.serial]
    if args.no_open:
        cmd.append("-n")
    if not config_path:
        cmd += args.categories
    return cmd


def validate_capture_mode_args(args, config_path):
    """Validate mode-specific values before checking or touching a device."""
    if config_path:
        if args.app or args.buffer:
            raise ConfigError(
                "--app and --buffer are only valid with --categories; "
                "edit the selected pbtxt for equivalent preset-mode control"
            )
        if args.time:
            _positive_preset_seconds(args.time)
    else:
        if args.time:
            normalize_lightweight_duration(args.time)
        if args.buffer:
            args.buffer = normalize_buffer_size(args.buffer)
        args.categories = [validate_category(value) for value in args.categories]
        args.app = [validate_app(value) for value in args.app]


def validate_list_mode_args(args):
    """Reject silently ignored capture options in local/device listing modes."""
    if not (args.list_configs or args.list_categories):
        return
    invalid = [
        flag
        for flag, used in (
            ("--time", args.time is not None),
            ("--buffer", args.buffer is not None),
            ("--app", bool(args.app)),
            ("--output", args.output is not None),
            ("--no-open", args.no_open),
            ("--serial", args.list_configs and args.serial is not None),
        )
        if used
    ]
    if invalid:
        mode = "--list-configs" if args.list_configs else "--list-categories"
        raise ConfigError(f"{mode} cannot be combined with {', '.join(invalid)}")


def run_capture(args):
    if args.list_configs:
        print("Available configs:")
        for n in list_configs():
            print(f"  {n}")
        return 0

    if args.list_categories:
        adb_path, _info, _warnings = prepare_device(args, "categories")
        cmd = [sys.executable, str(_OFFICIAL), "--list"]
        if args.serial:
            cmd += ["-s", args.serial]
        return subprocess.call(cmd, env=build_official_environment(adb_path))

    config_path = resolve_config(args.config) if args.config else None
    validate_capture_mode_args(args, config_path)

    # Default output: traces/<timestamp>_<configstem>.perfetto-trace
    if args.output:
        out = args.output
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        stem = Path(config_path).stem if config_path else "categories"
        _TRACES_DIR.mkdir(exist_ok=True)
        out = str(_TRACES_DIR / f"{ts}_{stem}.perfetto-trace")
    out = validate_output_path(out)

    # Print what we resolved before the device check, so a no-device smoke test
    # still shows the wiring is correct up to adb.
    if config_path:
        print(f"[capture] config : {config_path}")
    else:
        print(f"[capture] categories: {' '.join(args.categories)}")
    if args.time:
        duration = (
            f"{args.time}s" if config_path else normalize_lightweight_duration(args.time)
        )
        print(f"[capture] duration: {duration}")
    print(f"[capture] output : {out}")

    feature = Path(config_path).stem if config_path else "categories"
    adb_path, info, _warnings = prepare_device(args, feature)
    sideload_path = tracebox_for_device(info, _TRACEBOX_DIR)
    if sideload_path and not sideload_path.is_file():
        raise RuntimeFailure(
            f"Bundled legacy tracebox is missing: {sideload_path}. "
            "Restore repository artifacts and rerun setup.",
            3,
        )

    # --time is honored by rewriting duration_ms into a temp config, because
    # record_android_trace ignores -t when -c is given. Falls through to the
    # config's own duration_ms when --time is absent.
    run_config = materialize_config(config_path, args.time) if config_path else None
    is_temp = bool(config_path and run_config != config_path)
    cmd = build_official_command(args, out, run_config, sideload_path)

    print(f"[capture] running: {' '.join(cmd)}")
    try:
        return subprocess.call(cmd, env=build_official_environment(adb_path))
    finally:
        if is_temp:
            try:
                os.remove(run_config)
            except OSError:
                pass


def build_parser():
    p = argparse.ArgumentParser(
        prog="perfetto_capture",
        description="Capture a Perfetto trace on a connected Android device.",
        epilog="Run with --list-configs to see available config names.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("-c", "--config", help="Preset config short name (e.g. jank, general, 02)")
    mode.add_argument(
        "--categories",
        nargs="+",
        metavar="CATEGORY",
        help="Lightweight Perfetto/atrace categories (e.g. sched freq gfx view)",
    )
    mode.add_argument("--list-configs", action="store_true", help="List preset config names and exit")
    mode.add_argument(
        "--list-categories",
        action="store_true",
        help="List categories available on the connected device and exit",
    )
    p.add_argument("-t", "--time", help="Duration; unitless means seconds, lightweight mode also accepts s/m/h")
    p.add_argument("-b", "--buffer", help="Lightweight mode ring buffer size (e.g. 32mb)")
    p.add_argument(
        "-a",
        "--app",
        action="append",
        default=[],
        help="Lightweight mode app for atrace annotations; repeat for multiple apps",
    )
    p.add_argument("-o", "--output", help="Output .perfetto-trace path (default: traces/<ts>_<cfg>)")
    p.add_argument("-s", "--serial", help="ADB device serial (when multiple connected)")
    p.add_argument("--no-open", action="store_true", help="Do not open the trace in a browser")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not (args.config or args.categories or args.list_configs or args.list_categories):
        print(
            "ERROR: choose --config, --categories, --list-configs, or --list-categories.",
            file=sys.stderr,
        )
        return 2
    try:
        validate_list_mode_args(args)
        return run_capture(args)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except RuntimeFailure as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.exit_code
    except OSError as e:
        print(f"ERROR: host filesystem/process operation failed: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
