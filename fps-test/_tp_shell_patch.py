"""Preload hook: make the perfetto pip package use our prebuilt trace_processor_shell.

Imported automatically via PYTHONPATH (as `sitecustomize.py`) before compute_fps.py
touches the perfetto package. It patches the package's global PLATFORM_DELEGATE so
get_shell_path() returns the binary shipped in ../tools/trace_processor_shell/
instead of downloading it over HTTPS (which fails on macOS Python 3.12 due to
missing CA certs, and requires network).

This is deliberately a side-effect module: importing it performs the patch.
Falls through to the package's download behavior if no prebuilt matches the host.
"""
import os
import sys


def _local_tp_shell_path():
    import platform as _plat
    system = _plat.system()
    machine = _plat.machine().lower()
    here = os.path.dirname(os.path.abspath(__file__))
    tp_dir = os.path.normpath(os.path.join(here, "..", "tools", "trace_processor_shell"))

    def _pick(arch, is_win=False):
        name = f"{arch}.exe" if is_win else arch
        p = os.path.join(tp_dir, name)
        return p if os.path.isfile(p) else None

    if system == "Darwin":
        return _pick("mac-arm64" if machine == "arm64" else "mac-amd64")
    if system == "Linux":
        if machine in ("aarch64", "arm64"):
            return _pick("linux-arm64")
        return _pick("linux-amd64")
    if system == "Windows" or sys.platform == "win32":
        return _pick("windows-amd64", is_win=True)
    return None


def _install():
    try:
        import perfetto.trace_processor.api as _api
        import perfetto.trace_processor.platform as _p

        if getattr(_api.PLATFORM_DELEGATE, "_perfetto_tools_patched", False):
            return

        class _LocalShellDelegate(_p.PlatformDelegate):
            def get_shell_path(self, bin_path=None):
                local = _local_tp_shell_path()
                if local:
                    return local
                return super().get_shell_path(bin_path)

        _LocalShellDelegate._perfetto_tools_patched = True
        _api.PLATFORM_DELEGATE = _LocalShellDelegate
    except Exception as e:  # noqa: BLE001 — patch must never break analysis
        print(f"[fps] WARNING: local trace_processor_shell patch failed ({e}); "
              f"falling back to pip package download.", flush=True)


_install()
