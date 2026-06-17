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
  - macOS Python 3.12: also `pip install certifi` then
    `export SSL_CERT_FILE="$(python3 -c 'import certifi; print(certifi.where())')"`
    (trace_processor_shell download otherwise fails SSL verification)

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
