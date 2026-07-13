# tools/ — environment setup & binaries

Makes the repo self-contained: you don't need adb or trace_processor_shell
pre-installed, and nothing is downloaded at run time on a supported host.

## One-time setup

```bash
./tools/setup.sh
```

This:
1. **Verifies** the 5 shipped `trace_processor_shell` binaries against
   `tools/sha256.txt` (Perfetto v57.2, ~65MB total).
2. Resolves a working **Python 3.9+**, skipping broken PATH entries.
3. **adb**: if the shared resolver finds an explicit override, `.bin/adb`, or a
   PATH copy, leaves it alone. Otherwise downloads verified Google Platform-Tools
   37.0.0 into `.bin/` and lifts macOS Gatekeeper quarantine if present.
   (Linux-arm64 / Windows: manual install — it tells you.)

Checksum verification is mandatory. A mismatched download is deleted and setup
fails before extraction.

Idempotent — safe to re-run.

## What's in here

| File | Purpose |
|---|---|
| `setup.sh` | One-time environment prep (see above). |
| `resolve.sh adb` | Prints the adb path to use. Called by every script. Precedence: `$PERFETTO_TOOLS_ADB` → `.bin/adb` → PATH. |
| `resolve.sh python` | Prints a working Python 3.9+ path. Precedence: `$PERFETTO_TOOLS_PYTHON` → `.venv` → all PATH candidates. |
| `sha256.txt` | SHA256 of the 5 shipped `trace_processor_shell` binaries. `awk '!/^#/ && NF' tools/sha256.txt \| shasum -a 256 -c -` to self-verify on macOS. |
| `trace_processor_shell/` | Prebuilt Perfetto v57.2 binaries: mac-arm64, mac-amd64, linux-amd64, linux-arm64, windows-amd64.exe. |

## How trace_processor_shell is wired in

`fps-test/_tp_shell_patch.py` (auto-imported as `sitecustomize` when
`PYTHONPATH` includes `fps-test/`, which `run_fps_test.sh` sets) monkeypatches
the `perfetto` pip package's `PLATFORM_DELEGATE` so it returns the local binary
instead of downloading. `compute_fps.py` itself is unchanged. If the local
binary is missing or the platform doesn't match, it falls back to the pip
package's normal download — so analysis never hard-fails.

Install the matching Python SQL client with
`python -m pip install -r requirements.txt`. The native binary does not come
from the network at run time.

## Overriding adb

```bash
export PERFETTO_TOOLS_ADB=/custom/path/to/adb   # highest precedence
export PERFETTO_TOOLS_PYTHON=/custom/path/to/python
```
