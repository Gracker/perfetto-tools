import subprocess

import pytest

from tools import verify


@pytest.fixture
def commands(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b"capture/a.sh\0tools/a b.sh\0")

    monkeypatch.setattr(verify.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(verify.subprocess, "run", run)
    monkeypatch.setattr(verify, "SHELL_CHECKS_SUPPORTED", True)
    return calls


def test_verifies_each_tracked_shell_with_stable_cwd(commands, tmp_path):
    verify.verify(tmp_path)

    assert [args for args, _ in commands] == [
        [verify.sys.executable, "-m", "pytest", "tests/", "-v"],
        ["git", "ls-files", "-z", "--", "*.sh"],
        ["bash", "-n", "--", "capture/a.sh"],
        ["bash", "-n", "--", "tools/a b.sh"],
        ["shellcheck", "--", "capture/a.sh", "tools/a b.sh"],
    ]
    assert all(kwargs["cwd"] == tmp_path and kwargs["check"] for _, kwargs in commands)


def test_windows_runs_tests_and_reports_shell_boundary(commands, monkeypatch, capsys):
    monkeypatch.setattr(verify, "SHELL_CHECKS_SUPPORTED", False)
    verify.verify()

    assert len(commands) == 1
    assert commands[0][0][1:3] == ["-m", "pytest"]
    assert "NOT AVAILABLE on Windows" in capsys.readouterr().out


def test_missing_shellcheck_fails_before_running_checks(commands, monkeypatch, capsys):
    monkeypatch.setattr(verify.shutil, "which", lambda name: None if name == "shellcheck" else name)

    assert verify.main() == 1
    assert commands == []
    assert "shellcheck" in capsys.readouterr().err


@pytest.mark.parametrize("failed_command", ["pytest", "git", "bash", "shellcheck"])
def test_command_failure_propagates_and_stops(commands, monkeypatch, failed_command):
    original = verify.subprocess.run

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if (args[0] == failed_command) or (failed_command == "pytest" and "pytest" in args):
            raise subprocess.CalledProcessError(7, args)
        return result

    monkeypatch.setattr(verify.subprocess, "run", run)

    assert verify.main() == 7
    last = commands[-1][0]
    assert failed_command in last


def test_syntax_failure_in_later_script_is_not_missed(commands, monkeypatch):
    original = verify.subprocess.run

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if args[0] == "bash" and args[-1] == "tools/a b.sh":
            raise subprocess.CalledProcessError(2, args)
        return result

    monkeypatch.setattr(verify.subprocess, "run", run)

    assert verify.main() == 2
    assert commands[-1][0] == ["bash", "-n", "--", "tools/a b.sh"]
