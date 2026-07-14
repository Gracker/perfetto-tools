import json

from perfetto_tools.runtime import AdbDevice, DeviceInfo
from tools.doctor import Check, collect_checks, exit_code_for, main


def _check(checks, name):
    return next(check for check in checks if check.name == name)


def test_doctor_reports_wrong_perfetto_version_as_failure():
    checks = collect_checks(
        package_versions={"perfetto": "0.56.0", "protobuf": "7.35.1"},
        devices=[],
    )

    package = _check(checks, "perfetto package")
    assert package.status == "FAIL"
    assert "expected 0.57.2" in package.detail
    assert package.exit_code == 3


def test_doctor_without_device_reports_not_available_not_pass():
    checks = collect_checks(devices=[], require_device=False)

    device = _check(checks, "Android device")
    assert device.status == "NOT AVAILABLE"
    assert exit_code_for(checks) == 0


def test_doctor_required_device_is_a_device_failure():
    checks = collect_checks(devices=[], require_device=True)

    device = _check(checks, "Android device")
    assert device.status == "FAIL"
    assert device.exit_code == 4
    assert exit_code_for(checks) == 4


def test_doctor_reports_fps_version_incompatibility():
    devices = [AdbDevice("SERIAL", "device", "")]
    info = DeviceInfo("SERIAL", 30, "arm64-v8a", "user", "running")

    checks = collect_checks(
        devices=devices,
        device_info=info,
        require_device=True,
        feature="fps",
    )

    compatibility = _check(checks, "Android compatibility")
    assert compatibility.status == "FAIL"
    assert "API 31" in compatibility.detail
    assert compatibility.exit_code == 4


def test_doctor_json_output_has_stable_check_shape(capsys):
    result = main(["--json"], collector=lambda **_kwargs: [Check("demo", "PASS", "ok")])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload == [
        {"name": "demo", "status": "PASS", "detail": "ok", "exit_code": 0}
    ]
