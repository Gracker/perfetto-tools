#!/usr/bin/env python3
"""Cross-platform Perfetto capture entry.

Resolves a config short-name to a .pbtx path, checks the device, then invokes
the archived official record_android_trace script. One responsibility: produce
a trace file.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class ConfigError(Exception):
    pass


# Directory layout: capture/ sits next to configs/ and official/.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_CONFIGS_DIR = _REPO_ROOT / "configs"
_OFFICIAL = _REPO_ROOT / "official" / "record_android_trace"
_TRACES_DIR = _REPO_ROOT / "traces"


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


def apply_duration(config_text, seconds):
    """Return config_text with its top-level duration_ms set to seconds*1000.

    record_android_trace ignores -t/-b/-a when a full -c/--config is supplied
    (those short flags are 'only when not using -c'). So to honor --time we
    rewrite the config's own duration_ms instead of passing -t. Pure + testable.
    """
    if float(seconds) <= 0:
        raise ConfigError(f"--time must be > 0 seconds, got {seconds!r}")
    ms = int(round(float(seconds) * 1000))
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


def check_adb_device(serial=None):
    """Ensure exactly one usable device (or the one named by --serial)."""
    try:
        out = subprocess.run(
            ["adb", "devices"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()[1:]
    except FileNotFoundError:
        sys.exit(
            "ERROR: 'adb' not found on PATH. Install Android Platform Tools:\n"
            "  https://developer.android.com/studio/releases/platform-tools"
        )

    devices = [ln.split()[0] for ln in out if ln.strip() and "device" in ln]
    if serial:
        if serial not in devices:
            sys.exit(f"ERROR: device --serial {serial} not connected/authorized.\n"
                     f"adb devices says: {devices or 'none'}")
        return serial
    if len(devices) == 0:
        sys.exit("ERROR: no device connected. Run `adb devices` and authorize.")
    if len(devices) > 1:
        sys.exit(f"ERROR: multiple devices ({devices}). Pass --serial <id>.")
    return devices[0]


def run_capture(args):
    if args.list_configs:
        print("Available configs:")
        for n in list_configs():
            print(f"  {n}")
        return 0

    config_path = resolve_config(args.config)

    # Default output: traces/<timestamp>_<configstem>.perfetto-trace
    if args.output:
        out = args.output
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        stem = Path(config_path).stem
        _TRACES_DIR.mkdir(exist_ok=True)
        out = str(_TRACES_DIR / f"{ts}_{stem}.perfetto-trace")

    # Print what we resolved before the device check, so a no-device smoke test
    # still shows the wiring is correct up to adb.
    print(f"[capture] config : {config_path}")
    if args.time:
        print(f"[capture] duration override -> {args.time}s")
    print(f"[capture] output : {out}")

    check_adb_device(args.serial)

    # --time is honored by rewriting duration_ms into a temp config, because
    # record_android_trace ignores -t when -c is given. Falls through to the
    # config's own duration_ms when --time is absent.
    run_config = materialize_config(config_path, args.time)
    is_temp = run_config != config_path

    cmd = [
        sys.executable, str(_OFFICIAL),
        "-c", run_config,
        "-o", out,
    ]
    if args.serial:
        cmd += ["-s", args.serial]
    if args.no_open:
        cmd += ["--no-open"]

    print(f"[capture] running: {' '.join(cmd)}")
    try:
        return subprocess.call(cmd)
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
    p.add_argument("-c", "--config", help="Config short name (e.g. jank, general, 02)")
    p.add_argument("-t", "--time", help="Trace duration in seconds, e.g. 10 (overrides the config's duration_ms)")
    p.add_argument("-o", "--output", help="Output .perfetto-trace path (default: traces/<ts>_<cfg>)")
    p.add_argument("-s", "--serial", help="ADB device serial (when multiple connected)")
    p.add_argument("--no-open", action="store_true", help="Do not open the trace in a browser")
    p.add_argument("--list-configs", action="store_true", help="List available config names and exit")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.list_configs and not args.config:
        print("ERROR: --config is required (or use --list-configs).", file=sys.stderr)
        return 2
    try:
        return run_capture(args)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
