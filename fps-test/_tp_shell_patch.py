"""Force the Perfetto Python package to use the verified local shell binary."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perfetto_tools.artifacts import verified_trace_processor  # noqa: E402


def local_tp_shell_path(
    *,
    repo_root: Path = REPO_ROOT,
    system: str | None = None,
    machine: str | None = None,
    sys_platform: str | None = None,
) -> str:
    return str(
        verified_trace_processor(
            repo_root,
            system=system,
            machine=machine,
            sys_platform=sys_platform,
        )
    )


def install() -> None:
    """Install a fail-closed local delegate into perfetto.trace_processor."""
    import perfetto.trace_processor.api as api
    import perfetto.trace_processor.platform as perfetto_platform

    if getattr(api.PLATFORM_DELEGATE, "_perfetto_tools_patched", False):
        return

    class LocalShellDelegate(perfetto_platform.PlatformDelegate):
        _perfetto_tools_patched = True

        def get_shell_path(self, bin_path=None, fetch_latest=False):
            del bin_path, fetch_latest
            return local_tp_shell_path()

    api.PLATFORM_DELEGATE = LocalShellDelegate
