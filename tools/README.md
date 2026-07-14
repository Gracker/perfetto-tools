# tools/ — managed runtime, diagnostics, and bundled binaries

## One-time setup

Mac / Linux:

```bash
./tools/setup.sh
.venv/bin/python tools/doctor.py
```

Windows x86_64 (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup.ps1
.\.venv\Scripts\python.exe tools\doctor.py
```

The bootstrap reads `tool-versions.env`, verifies the matching uv archive,
creates a repository-owned CPython 3.13.14 environment from `uv.lock`, verifies
all bundled native artifacts, and installs a verified repository-owned
Platform-Tools 37.0.0 where Google publishes one. It does not modify global
Python packages, SDKs, or shell profiles.

The first setup needs HTTPS plus native checksum/archive commands. Re-running it
is idempotent. A checksum, version, archive-layout, or executable check fails
closed before the managed tool is replaced.

## Support boundary

macOS arm64/x86_64, Linux glibc x86_64, and Windows x86_64 get a managed Python,
packages, trace processor, and ADB. Linux glibc ARM64 gets managed analysis but
requires `PERFETTO_TOOLS_ADB`, because Google has no Linux ARM64 Platform-Tools
archive. See [`../docs/compatibility.md`](../docs/compatibility.md).

External overrides are explicit escape hatches:

```bash
export PERFETTO_TOOLS_ADB=/custom/path/to/adb
export PERFETTO_TOOLS_PYTHON=/custom/path/to/python  # CPython 3.10–3.14
```

They are intentionally not selected by setup from PATH. `doctor.py` labels an
external tool non-hermetic, and an invalid explicit override fails instead of
silently choosing something else.

## Commands and manifests

| File | Purpose |
|---|---|
| `setup.sh` / `setup.ps1` | Native verified bootstrap for Unix / Windows |
| `setup_runtime.py` | Shared post-bootstrap Python/package/artifact/ADB checks |
| `tool-versions.env` | uv, Python, Platform-Tools versions, URLs, and SHA256 pins |
| `resolve.sh` | Explicit override → repository-managed tool → compatible PATH fallback |
| `doctor.py` | Human/JSON host and optional device readiness (`--device --feature fps`) |
| `check_updates.py` | Stable upstream/content drift checker; Canary is informational |
| `device_smoke.py` | API-aware one-second physical-device capture plan |
| `sha256.txt` | Checksums for 5 trace processors and 4 Android tracebox binaries |
| `trace_processor_shell/` | Local Perfetto v57.2 analysis binaries |
| `tracebox/` | Local Perfetto v57.2 API 23–28/API 29 fallback binaries |

Verify every bundled artifact on macOS:

```bash
awk '!/^#/ && NF' tools/sha256.txt | shasum -a 256 -c -
```

## Local trace processing

`fps-test/sitecustomize.py` imports the single implementation in
`fps-test/_tp_shell_patch.py`; `compute_fps.py` also installs that delegate
explicitly. The delegate selects the host binary from `sha256.txt`, verifies it,
and fails with setup guidance if it is absent or modified. It never calls the
Perfetto package's network download fallback.

## Device and update checks

`doctor.py` distinguishes `PASS`, `WARN`, `FAIL`, and `NOT AVAILABLE`. An absent
optional device is not a pass; `--device` makes it required. Examples:

```bash
.venv/bin/python tools/doctor.py --json
.venv/bin/python tools/doctor.py --device --feature general
.venv/bin/python tools/doctor.py --device --feature fps
.venv/bin/python tools/device_smoke.py --require-device
```

`check_updates.py --check` queries authoritative PyPI package, GitHub, Android
repository, and Perfetto raw-content endpoints. It fails on stable version or
record-helper content drift. The scheduled Tool drift workflow runs this outside
normal push/PR verification so mutable upstream state cannot break a source
commit nondeterministically.
