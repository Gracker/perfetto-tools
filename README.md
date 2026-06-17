# Perfetto Tools

A consolidated toolkit for capturing [Perfetto](https://perfetto.dev/) traces on
Android, plus Simpleperf capture and automated swipe-based FPS testing.

## What's inside

| Directory | Purpose |
|---|---|
| [`official/`](official/) | Snapshot of Google's `record_android_trace` script, pinned to a version. |
| [`capture/`](capture/) | Cross-platform one-shot Perfetto capture (Win `.bat` / Mac+Linux `.sh`). |
| [`configs/`](configs/) | 6 prebuilt trace configs for common scenarios (startup, jank, CPU, memory...). |
| [`simpleperf/`](simpleperf/) | Simpleperf capture scripts (standalone, or in parallel with a Perfetto trace). |
| [`fps-test/`](fps-test/) | Automated swipe test that captures a trace and computes per-source FPS / dropped frames. |

## Quick start (capture a trace)

```bash
# Mac / Linux
./capture/capture.sh --config general --time 10

# Windows
capture\capture.bat --config general --time 10
```

See each subdirectory's README for details.

## Quick start (FPS swipe test)

```bash
pip install perfetto                       # trace_processor, needed for FPS compute
# ...launch your app and navigate to the scrollable screen...
./fps-test/run_fps_test.sh 12 com.example.app
```

## Requirements

- `adb` on PATH (device connected, USB debugging on)
- Python 3.9+
- For FPS testing: `pip install perfetto` (trace_processor)

## Design

- [`docs/superpowers/specs/2026-06-17-perfetto-tools-design.md`](docs/superpowers/specs/2026-06-17-perfetto-tools-design.md) — design
- [`docs/superpowers/plans/2026-06-17-perfetto-tools.md`](docs/superpowers/plans/2026-06-17-perfetto-tools.md) — implementation plan
- [`docs/spike-notes.md`](docs/spike-notes.md) — on-device schema findings that shaped the FPS code
