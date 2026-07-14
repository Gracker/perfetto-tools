import argparse
import os
import sys

import pytest

from perfetto_tools.runtime import AdbDevice, DeviceInfo, RuntimeFailure

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "capture"))

from perfetto_capture import (  # noqa: E402
    ConfigError,
    build_official_environment,
    build_official_command,
    build_parser,
    main,
    normalize_lightweight_duration,
    prepare_device,
)


def _args(**overrides):
    values = {
        "app": [],
        "buffer": None,
        "categories": None,
        "no_open": False,
        "serial": None,
        "time": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10", "10s"),
        ("1.5", "1.5s"),
        ("10s", "10s"),
        ("2m", "2m"),
        ("1h", "1h"),
    ],
)
def test_normalize_lightweight_duration(value, expected):
    assert normalize_lightweight_duration(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "ten", "10ms", ""])
def test_normalize_lightweight_duration_rejects_invalid_values(value):
    with pytest.raises(ConfigError, match="positive number"):
        normalize_lightweight_duration(value)


def test_capture_modes_are_mutually_exclusive():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--config", "general", "--categories", "sched"])


def test_build_official_command_for_preset_config():
    args = _args(no_open=True, serial="device-1")

    command = build_official_command(
        args,
        output="trace.perfetto-trace",
        config_path="config.pbtx",
    )

    assert command == [
        sys.executable,
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "official", "record_android_trace")
        ),
        "-c",
        "config.pbtx",
        "-o",
        "trace.perfetto-trace",
        "-s",
        "device-1",
        "-n",
    ]


def test_build_official_command_for_lightweight_categories():
    args = _args(
        app=["com.example.one", "com.example.two"],
        buffer="64mb",
        categories=["sched", "freq", "gfx", "view"],
        no_open=True,
        time="12",
    )

    command = build_official_command(args, output="trace.perfetto-trace")

    assert command[2:] == [
        "-o",
        "trace.perfetto-trace",
        "-t",
        "12s",
        "-b",
        "64mb",
        "-a",
        "com.example.one",
        "-a",
        "com.example.two",
        "-n",
        "sched",
        "freq",
        "gfx",
        "view",
    ]


def test_config_mode_rejects_lightweight_only_flags():
    args = _args(app=["com.example.app"])

    with pytest.raises(ConfigError, match="only valid with --categories"):
        build_official_command(args, output="trace.perfetto-trace", config_path="config.pbtx")


def test_official_environment_exposes_resolved_adb_to_upstream_script():
    environment = build_official_environment(
        "/custom/android/platform-tools/adb",
        {"PATH": "/usr/bin:/bin", "KEEP": "value"},
    )

    assert environment["PATH"] == "/custom/android/platform-tools:/usr/bin:/bin"
    assert environment["KEEP"] == "value"


def test_prepare_device_uses_shared_runtime_and_pins_selected_serial(monkeypatch):
    import perfetto_capture

    info = DeviceInfo("SERIAL", 31, "arm64-v8a", "user", "running")
    monkeypatch.setattr(perfetto_capture, "resolve_adb", lambda _root: "/managed/adb")
    monkeypatch.setattr(
        perfetto_capture,
        "list_adb_devices",
        lambda _adb: [AdbDevice("SERIAL", "device", "model:Pixel")],
    )
    monkeypatch.setattr(perfetto_capture, "probe_device", lambda _adb, _serial: info)
    monkeypatch.setattr(
        perfetto_capture,
        "check_feature_compatibility",
        lambda feature, device: [f"{feature}:{device.api_level}"],
    )
    args = _args()

    adb, selected, warnings = prepare_device(args, "general")

    assert (adb, selected, warnings) == ("/managed/adb", info, ["general:31"])
    assert args.serial == "SERIAL"


def test_main_preserves_runtime_failure_exit_code(capsys, monkeypatch):
    import perfetto_capture

    def fail(_args):
        raise RuntimeFailure("device is offline; reconnect it", 4)

    monkeypatch.setattr(perfetto_capture, "run_capture", fail)

    assert main(["--config", "general"]) == 4
    assert "ERROR: device is offline; reconnect it" in capsys.readouterr().err
