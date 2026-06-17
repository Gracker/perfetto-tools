# tools/ — environment setup & binaries

Makes the repo self-contained: you don't need adb or trace_processor_shell
pre-installed, and nothing is downloaded at run time on a supported host.

## One-time setup

```bash
./tools/setup.sh
```

This:
1. **Verifies** the 5 shipped `trace_processor_shell` binaries against
   `tools/sha256.txt` (integrity check — they ship in the repo, ~50MB total).
2. **adb**: if `adb` is already on your PATH, leaves it alone. Otherwise downloads
   Google's platform-tools into `.bin/` and lifts the macOS Gatekeeper
   quarantine if present. (Linux-arm64 / Windows: manual install — it tells you.)

Idempotent — safe to re-run.

## What's in here

| File | Purpose |
|---|---|
| `setup.sh` | One-time environment prep (see above). |
| `resolve.sh adb` | Prints the adb path to use. Called by every script. Precedence: `$PERFETTO_TOOLS_ADB` → `.bin/adb` → PATH. |
| `sha256.txt` | SHA256 of the 5 shipped `trace_processor_shell` binaries. `shasum -a 256 -c tools/sha256.txt` to self-verify. |
| `trace_processor_shell/` | Prebuilt native binaries (perfetto v49.0): mac-arm64, mac-amd64, linux-amd64, linux-arm64, windows-amd64.exe. Used by `compute_fps.py` so trace analysis needs no network. |

## How trace_processor_shell is wired in

`fps-test/_tp_shell_patch.py` (auto-imported as `sitecustomize` when
`PYTHONPATH` includes `fps-test/`, which `run_fps_test.sh` sets) monkeypatches
the `perfetto` pip package's `PLATFORM_DELEGATE` so it returns the local binary
instead of downloading. `compute_fps.py` itself is unchanged. If the local
binary is missing or the platform doesn't match, it falls back to the pip
package's normal download — so analysis never hard-fails.

You still need `pip install perfetto` (the Python SQL client). But the ~12MB
native binary no longer comes from the network at run time.

## Overriding adb

```bash
export PERFETTO_TOOLS_ADB=/custom/path/to/adb   # highest precedence
```
