# Perfetto Tools

A consolidated toolkit for capturing [Perfetto](https://perfetto.dev/) traces on
Android, plus Simpleperf capture and automated swipe-based FPS testing.

**Self-contained**: the trace_processor_shell binaries ship in the repo, and adb
is auto-installed by `./tools/setup.sh` if missing. No run-time downloads on a
supported host.

## What's inside

| Directory | Purpose |
|---|---|
| [`tools/`](tools/) | One-time setup (`setup.sh`), adb resolution (`resolve.sh`), prebuilt `trace_processor_shell` (5 platforms). |
| [`official/`](official/) | Snapshot of Google's `record_android_trace` script, pinned to a version. |
| [`capture/`](capture/) | Cross-platform one-shot Perfetto capture (Win `.bat` / Mac+Linux `.sh`). |
| [`configs/`](configs/) | 6 prebuilt trace configs for common scenarios (startup, jank, CPU, memory...). |
| [`simpleperf/`](simpleperf/) | Simpleperf capture scripts (standalone, or in parallel with a Perfetto trace). |
| [`fps-test/`](fps-test/) | Automated swipe test that captures a trace and computes per-source FPS / dropped frames. |

## Quick start

```bash
# 1. One-time: ensure adb + verify the prebuilt trace_processor_shell binaries.
./tools/setup.sh

# 2. (For FPS analysis only) the Python SQL client:
pip install perfetto

# 3. Capture a trace (Ctrl+C stops early — perfetto keeps what it captured):
./capture/capture.sh --config general --time 10
#   Windows: capture\capture.bat --config general --time 10

# 4. FPS swipe test (pip install perfetto first):
#    ...launch your app and navigate to the scrollable screen...
./fps-test/run_fps_test.sh 12 com.example.app
```

See each subdirectory's README for details.

## Requirements

- **adb**: auto-installed by `./tools/setup.sh` if not on PATH. Override with
  `PERFETTO_TOOLS_ADB=/path/to/adb`.
- **Python 3.9+**.
- **`pip install perfetto`** — only for the FPS analysis step (the Python SQL
  client; the ~12MB native `trace_processor_shell` ships in `tools/`, so no
  run-time binary download).

## Testing

Pure-logic unit tests (config name resolution, FPS/jank math, swipe pattern
parsing, input-event de-dup):

```bash
python3 -m pytest tests/ -v
```

Device-dependent flows (capture, simpleperf, end-to-end fps-test) are verified
manually against a real Android device. The whole repo was developed and
acceptance-tested on an OPPO P0110 (API 36, `user` build, 120Hz). See each
subdirectory's README and [`docs/spike-notes.md`](docs/spike-notes.md) for the
schema findings that shaped the code.

## Design

- [`docs/superpowers/specs/2026-06-17-perfetto-tools-design.md`](docs/superpowers/specs/2026-06-17-perfetto-tools-design.md) — design
- [`docs/superpowers/plans/2026-06-17-perfetto-tools.md`](docs/superpowers/plans/2026-06-17-perfetto-tools.md) — implementation plan
- [`docs/spike-notes.md`](docs/spike-notes.md) — on-device schema findings that shaped the FPS code
