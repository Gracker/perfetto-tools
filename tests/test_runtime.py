import subprocess
import stat

import pytest

from perfetto_tools.runtime import (
    AdbDevice,
    DeviceInfo,
    RuntimeFailure,
    check_feature_compatibility,
    list_adb_devices,
    parse_adb_devices,
    probe_device,
    resolve_adb,
    run_adb,
    select_device,
    tracebox_for_device,
)


def test_resolve_adb_finds_managed_platform_tools_layout(tmp_path):
    adb = tmp_path / ".bin" / "platform-tools" / "adb.exe"
    adb.parent.mkdir(parents=True)
    adb.write_bytes(b"managed-adb")
    adb.chmod(adb.stat().st_mode | stat.S_IXUSR)

    assert resolve_adb(tmp_path, environment={}, which=lambda _name: None) == str(adb)


def test_resolve_adb_normalizes_relative_explicit_override(tmp_path, monkeypatch):
    adb = tmp_path / "custom-adb"
    adb.write_bytes(b"adb")
    adb.chmod(adb.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(tmp_path)

    resolved = resolve_adb(
        tmp_path / "repo",
        environment={"PERFETTO_TOOLS_ADB": "./custom-adb"},
        which=lambda _name: None,
    )

    assert resolved == str(adb.resolve())


def test_parse_adb_devices_preserves_non_ready_states():
    output = """List of devices attached
READY\tdevice product:foo model:Pixel_9 transport_id:1
AUTH\tunauthorized usb:1-1
LOST\toffline transport_id:3
USB no permissions (user in plugdev group); see [http://developer.android.com/tools/device.html]
"""

    assert parse_adb_devices(output) == [
        AdbDevice("READY", "device", "product:foo model:Pixel_9 transport_id:1"),
        AdbDevice("AUTH", "unauthorized", "usb:1-1"),
        AdbDevice("LOST", "offline", "transport_id:3"),
        AdbDevice(
            "USB",
            "no permissions",
            "(user in plugdev group); see [http://developer.android.com/tools/device.html]",
        ),
    ]


def test_parse_adb_devices_ignores_daemon_messages_and_blank_lines():
    output = """* daemon not running; starting now at tcp:5037
* daemon started successfully
List of devices attached

"""

    assert parse_adb_devices(output) == []


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("unauthorized", "authorize.*USB debugging"),
        ("offline", "offline.*reconnect"),
        ("no permissions", "permissions.*udev"),
    ],
)
def test_select_device_explains_non_ready_state(state, message):
    with pytest.raises(RuntimeFailure, match=message) as exc:
        select_device([AdbDevice("ABC", state, "")])

    assert exc.value.exit_code == 4


def test_select_device_requires_serial_when_multiple_are_ready():
    devices = [AdbDevice("A", "device", ""), AdbDevice("B", "device", "")]

    with pytest.raises(RuntimeFailure, match="multiple.*A.*B.*--serial") as exc:
        select_device(devices)

    assert exc.value.exit_code == 4
    assert select_device(devices, "B") == devices[1]


def test_select_device_reports_requested_serial_state():
    devices = [AdbDevice("A", "device", ""), AdbDevice("B", "offline", "")]

    with pytest.raises(RuntimeFailure, match="B.*offline.*reconnect"):
        select_device(devices, "B")


def test_select_device_explains_empty_device_list():
    with pytest.raises(RuntimeFailure, match="No Android device.*USB debugging") as exc:
        select_device([])

    assert exc.value.exit_code == 4


def test_run_adb_converts_timeout_to_typed_failure():
    def timeout_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["adb", "devices"], 5)

    with pytest.raises(RuntimeFailure, match="timed out after 5") as exc:
        run_adb(["devices", "-l"], adb="/managed/adb", runner=timeout_runner, timeout=5)

    assert exc.value.exit_code == 4


def test_run_adb_converts_missing_executable_to_host_failure():
    def missing_runner(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    with pytest.raises(RuntimeFailure, match="ADB executable.*setup") as exc:
        run_adb(["devices"], adb="/missing/adb", runner=missing_runner)

    assert exc.value.exit_code == 3


def test_run_adb_converts_nonzero_result_to_device_failure():
    def failed_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            ["adb", "shell"], 1, stdout="", stderr="error: device offline\n"
        )

    with pytest.raises(RuntimeFailure, match="device offline") as exc:
        run_adb(["shell", "getprop"], adb="adb", runner=failed_runner)

    assert exc.value.exit_code == 4


def test_list_adb_devices_rejects_malformed_success_output():
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="unexpected output\n", stderr="")

    with pytest.raises(RuntimeFailure, match="unexpected output") as exc:
        list_adb_devices("adb", runner=runner)

    assert exc.value.exit_code == 4


def test_probe_device_uses_one_bounded_shell_call():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="api=31\nabi=arm64-v8a\nbuild_type=user\ntraced=running\n",
            stderr="",
        )

    info = probe_device("/managed/adb", "SERIAL", runner=runner, timeout=7)

    assert info == DeviceInfo("SERIAL", 31, "arm64-v8a", "user", "running")
    assert len(calls) == 1
    assert calls[0][0][:3] == ["/managed/adb", "-s", "SERIAL"]
    assert calls[0][0][3] == "shell"
    assert calls[0][1]["timeout"] == 7


def test_probe_device_rejects_malformed_properties():
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="api=unknown\n", stderr="")

    with pytest.raises(RuntimeFailure, match="could not determine Android API") as exc:
        probe_device("adb", "SERIAL", runner=runner)

    assert exc.value.exit_code == 5


@pytest.mark.parametrize(
    ("api", "abi", "traced", "expected"),
    [
        (23, "armeabi-v7a", "", "android-arm"),
        (28, "arm64-v8a", "", "android-arm64"),
        (29, "x86", "stopped", "android-x86"),
        (29, "x86_64", "running", None),
        (30, "arm64-v8a", "stopped", None),
    ],
)
def test_tracebox_selection(api, abi, traced, expected, tmp_path):
    info = DeviceInfo("A", api, abi, "user", traced)
    selected = tracebox_for_device(info, tmp_path)

    assert (selected.name if selected else None) == expected


def test_tracebox_selection_rejects_unknown_legacy_abi(tmp_path):
    info = DeviceInfo("A", 28, "riscv64", "user", "")

    with pytest.raises(RuntimeFailure, match="ABI.*riscv64") as exc:
        tracebox_for_device(info, tmp_path)

    assert exc.value.exit_code == 5


@pytest.mark.parametrize("feature", ["jank", "02_jank_frame", "fps"])
def test_frametimeline_features_are_rejected_before_android_12(feature):
    info = DeviceInfo("A", 30, "arm64-v8a", "user", "running")

    with pytest.raises(RuntimeFailure, match="Android 12.*API 31") as exc:
        check_feature_compatibility(feature, info)

    assert exc.value.exit_code == 5


def test_full_config_warns_before_android_12():
    info = DeviceInfo("A", 29, "arm64-v8a", "user", "running")

    warnings = check_feature_compatibility("05_full", info)

    assert any("FrameTimeline" in warning and "API 31" in warning for warning in warnings)


def test_android_before_m_is_rejected():
    info = DeviceInfo("A", 22, "armeabi-v7a", "user", "")

    with pytest.raises(RuntimeFailure, match="Android 6.*API 23") as exc:
        check_feature_compatibility("general", info)

    assert exc.value.exit_code == 5
