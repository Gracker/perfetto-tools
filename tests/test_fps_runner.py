from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "fps-test" / "run_fps_test.sh"


def test_fps_runner_uses_doctor_fps_device_preflight_before_capture():
    source = RUNNER.read_text()
    doctor = source.index('"${PYTHON}" "${DOCTOR}" --device --feature fps')
    capture = source.index('"${CAPTURE}" --config jank')

    assert doctor < capture


def test_fps_runner_validates_duration_and_optional_package():
    source = RUNNER.read_text()

    assert "DURATION" in source and "positive" in source
    assert "GFXINFO_PKG" in source and "Android package" in source


def test_fps_runner_waits_for_real_trace_readiness_without_fixed_sleep():
    source = RUNNER.read_text()

    assert "STARTUP_TIMEOUT" in source
    assert "Trace started" in source
    assert 'kill -0 "${CAPTURE_PID}"' in source
    assert "sleep 2" not in source


def test_fps_runner_reaps_background_capture_on_every_exit_path():
    source = RUNNER.read_text()

    assert "trap cleanup EXIT INT TERM" in source
    assert 'wait "${CAPTURE_PID}"' in source
