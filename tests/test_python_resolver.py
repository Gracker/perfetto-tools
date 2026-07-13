import os
import stat
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="tests the Bash resolver")


REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLVER = REPO_ROOT / "tools" / "resolve.sh"


def _write_executable(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_resolver(env):
    return subprocess.run(
        ["/bin/bash", str(RESOLVER), "python"],
        capture_output=True,
        env=env,
        text=True,
    )


def test_python_resolver_rejects_invalid_explicit_override(tmp_path):
    broken = tmp_path / "broken-python"
    _write_executable(broken, "#!/bin/sh\nexit 1\n")
    env = os.environ.copy()
    env["PERFETTO_TOOLS_PYTHON"] = str(broken)

    result = _run_resolver(env)

    assert result.returncode == 1
    assert "PERFETTO_TOOLS_PYTHON" in result.stderr
    assert "Python 3.9+" in result.stderr


def test_python_resolver_skips_broken_first_path_candidate(tmp_path):
    broken = tmp_path / "first" / "python3"
    healthy = tmp_path / "second" / "python3"
    _write_executable(broken, "#!/bin/sh\nexit 137\n")
    _write_executable(
        healthy,
        "#!/bin/sh\nexec /usr/bin/python3 \"$@\"\n",
    )
    env = os.environ.copy()
    env.pop("PERFETTO_TOOLS_PYTHON", None)
    env["PATH"] = os.pathsep.join(
        [str(broken.parent), str(healthy.parent), "/usr/bin", "/bin"]
    )

    result = _run_resolver(env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(healthy)
