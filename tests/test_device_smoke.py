import subprocess

import pytest

from perfetto_tools.runtime import DeviceInfo
from tools.device_smoke import SmokeItem, build_smoke_plan, run_smoke_plan


@pytest.mark.parametrize(
    ("api", "configs"),
    [
        (23, ["general"]),
        (29, ["general", "cpu"]),
        (31, ["general", "cpu", "jank"]),
    ],
)
def test_device_smoke_plan_matches_android_capabilities(api, configs):
    assert [item.config for item in build_smoke_plan(api)] == configs


def test_device_smoke_requires_nonempty_trace(tmp_path):
    info = DeviceInfo("SERIAL", 31, "arm64-v8a", "user", "running")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    results = run_smoke_plan(
        info,
        [SmokeItem("general")],
        output_dir=tmp_path,
        runner=runner,
    )

    assert results[0].status == "FAIL"
    assert "missing or empty" in results[0].detail


def test_device_smoke_passes_nonempty_trace(tmp_path):
    info = DeviceInfo("SERIAL", 31, "arm64-v8a", "user", "running")

    def runner(command, **_kwargs):
        output = command[command.index("--output") + 1]
        with open(output, "wb") as trace:
            trace.write(b"trace")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    results = run_smoke_plan(
        info,
        [SmokeItem("general")],
        output_dir=tmp_path,
        runner=runner,
    )

    assert results[0].status == "PASS"
