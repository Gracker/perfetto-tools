#!/usr/bin/env python3
"""Run deterministic source checks without setup, update queries or devices."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL_CHECKS_SUPPORTED = os.name != "nt"


def verify(repo_root: Path = REPO_ROOT) -> None:
    if SHELL_CHECKS_SUPPORTED:
        for tool in ("git", "bash", "shellcheck"):
            if shutil.which(tool) is None:
                raise RuntimeError(f"Required verification tool is missing: {tool}")

    subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v"],
        cwd=repo_root,
        check=True,
    )
    if not SHELL_CHECKS_SUPPORTED:
        print("Bash source checks: NOT AVAILABLE on Windows; checked on Unix hosts.")
        return

    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.sh"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    scripts = [os.fsdecode(path) for path in tracked.split(b"\0") if path]
    # bash accepts only one script operand: batch invocation silently misses files.
    for script in scripts:
        subprocess.run(["bash", "-n", "--", script], cwd=repo_root, check=True)
    if scripts:
        subprocess.run(["shellcheck", "--", *scripts], cwd=repo_root, check=True)


def main() -> int:
    try:
        verify()
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
