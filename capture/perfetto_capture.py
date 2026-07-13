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

if sys.version_info < (3, 9):
    sys.exit(
        "perfetto_capture.py requires Python 3.9+ "
        f"(running {sys.version.split()[0]}). "
        "The script uses str.removesuffix(), which was added in Python 3.9."
    )


class ConfigError(Exception):
    pass


def normalize_lightweight_duration(value):
    """Return an official-helper duration, defaulting unitless values to seconds."""
    value = str(value).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", value)
    if not match or float(match.group(1)) <= 0:
        raise ConfigError(
            f"--time must be a positive number with optional s/m/h unit, got {value!r}"
        )
    number, unit = match.groups()
    return f"{number}{unit or 's'}"


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


def _resolve_adb():
    """Return the adb executable path, mirroring tools/resolve.sh's precedence.

    Order: $PERFETTO_TOOLS_ADB -> <repo>/.bin/adb -> PATH lookup. Returns the
    bare string 'adb' as a last resort so the caller's FileNotFoundError handler
    still fires with a helpful message if nothing is installed.
    """
    env = os.environ.get("PERFETTO_TOOLS_ADB")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    bin_adb = _REPO_ROOT / ".bin" / "adb"
    if bin_adb.is_file() and os.access(bin_adb, os.X_OK):
        return str(bin_adb)
    return "adb"


def check_adb_device(serial=None, adb=None):
    """Ensure exactly one usable device (or the one named by --serial)."""
    adb = adb or _resolve_adb()
    try:
        out = subprocess.run(
            [adb, "devices"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()[1:]
    except FileNotFoundError:
        sys.exit(
            "ERROR: 'adb' not found. Options:\n"
            "  - run './tools/setup.sh' to install it into .bin/\n"
            "  - set PERFETTO_TOOLS_ADB=/path/to/adb\n"
            "  - put Android Platform Tools on PATH\n"
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


def build_official_command(args, output, config_path=None):
    """Build the argv for preset-config or lightweight-category capture."""
    if config_path:
        if args.app or args.buffer:
            raise ConfigError(
                "--app and --buffer are only valid with --categories; "
                "edit the selected pbtxt for equivalent preset-mode control"
            )
        cmd = [
            sys.executable,
            str(_OFFICIAL),
            "-c",
            config_path,
            "-o",
            output,
        ]
    else:
        if not args.categories:
            raise ConfigError("--categories requires at least one Perfetto category")
        cmd = [sys.executable, str(_OFFICIAL), "-o", output]
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
    elif args.time:
        normalize_lightweight_duration(args.time)


def run_capture(args):
    if args.list_configs:
        print("Available configs:")
        for n in list_configs():
            print(f"  {n}")
        return 0

    if args.list_categories:
        adb_path = _resolve_adb()
        check_adb_device(args.serial, adb_path)
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

    adb_path = _resolve_adb()
    check_adb_device(args.serial, adb_path)

    # --time is honored by rewriting duration_ms into a temp config, because
    # record_android_trace ignores -t when -c is given. Falls through to the
    # config's own duration_ms when --time is absent.
    run_config = materialize_config(config_path, args.time) if config_path else None
    is_temp = bool(config_path and run_config != config_path)
    cmd = build_official_command(args, out, run_config)

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
        return run_capture(args)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
