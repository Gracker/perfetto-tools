# Perfetto Tools

A consolidated toolkit for capturing [Perfetto](https://perfetto.dev/) traces on
Android, plus Simpleperf capture and automated swipe-based FPS testing.

**Self-contained**: the `trace_processor_shell` binaries ship in the repo, and
`adb` is auto-installed by `./tools/setup.sh` if missing. No run-time downloads on
a supported host.

---

## 5-minute start

```bash
# 1. Install deps + verify the shipped binaries (one-time)
./tools/setup.sh
pip install perfetto          # the Python SQL client (for FPS analysis only)

# 2. Plug in a device, enable USB debugging, confirm it's seen
adb devices                   # should list one device

# 3. Capture a 10-second trace (Ctrl+C stops early — perfetto keeps what it got)
./capture/capture.sh --config general --time 10
#    → writes traces/<timestamp>_general.perfetto-trace and opens it in your browser
#    Windows: capture\capture.bat --config general --time 10
```

That's capture done. To measure FPS while scrolling:

```bash
# Launch your app, navigate to the screen you want to test, then:
./fps-test/run_fps_test.sh 12 com.example.app
#    → captures a trace while auto-swiping (3 up, 3 down) and prints an FPS report
```

---

## Reading the FPS report

`run_fps_test.sh` produces output like this (real numbers from a 120Hz device):

```
Screen FPS (display/SF output): 95.7          ← what the user actually saw
Total frames (all sources): 391
  presented       : 391
  dropped         : 0   (never on screen)
  janky           : 0   (presented late)
Drop rate         : 0.00%

Per-frame-source breakdown:                   ← each producing surface, separately
  TX - com.example.../MainActivity#685  fps=99.2  frames=199 ...
  display                               fps=95.7  frames=192 ...

Per-gesture Screen-FPS (overall / press / fling):
  gesture 0: overall=82.7  press=102.4  fling=72.8
  gesture 1: overall=88.5  press= 99.6  fling=82.4
  ...
```

What each part means:

| Field | Meaning |
|---|---|
| **Screen FPS** | SurfaceFlinger's composited output (the `display` source) over the window. This is the refresh rate the user perceived. |
| **Per-source breakdown** | Each producing surface reported separately. A normal app shows one app surface + `display`. A SurfaceView/TextureView/video shows **its own extra source** — they are never summed, because a 120Hz screen with a 60fps list + 60fps video is still 120Hz. |
| **dropped** | `present_type = Dropped Frame` — the frame never reached the screen. Excluded from FPS. |
| **janky** | `jank_type ≠ None` and not dropped — the frame was shown but missed its deadline. Counted toward FPS, reported as a quality signal. |
| **press vs fling** | Each swipe gesture is split: `press` = finger down dragging, `fling` = finger up, inertia scrolling. **The fling number is the "scrolling smoothness" you usually care about.** |

### Why do my numbers differ from `dumpsys gfxinfo`?

`dumpsys gfxinfo` (the `dump_gfxinfo.sh` cross-check) reports from the moment you
`reset` to the moment you `dump` — covering the **whole test window including
press phases**. The trace's per-gesture FPS only counts **fling windows**. So
gfxinfo's total frame count will be higher; that's expected, not a bug.

On **Android 14+ (API 34+)**, `dumpsys SurfaceFlinger --latency` no longer emits
per-frame rows — `dump_gfxinfo.sh` detects this and writes a notice pointing to
the trace's `actual_frame_timeline_slice` instead. Use the trace for per-layer
timing on modern Android.

---

## Choosing a config

`./capture/capture.sh --config <name>` accepts a short name. Match by number,
keyword, or full stem (`02`, `jank`, `02_jank_frame` all work).

| Name | Use when | What it captures |
|---|---|---|
| `general` (`00`) | Default "what's going on" | sched + freq + atrace(am/wm/gfx/view) + memory |
| `startup` (`01`) | App cold-launch timing | + detailed am/wm, input, ss |
| `jank` (`02`) | Scroll jank / FPS | FrameTimeline + input events (also used by fps-test) |
| `cpu` (`03`) | Scheduling / thread analysis | detailed sched + freq + idle, no atrace |
| `memory` (`04`) | Memory issues | memory counters, lmk, page alloc |
| `full` (`05`) | "Catch everything" debugging | all of the above, large buffer |

`--list-configs` shows them. See [`configs/README.md`](configs/README.md).

---

## What's in this repo

| Directory | Purpose |
|---|---|
| [`tools/`](tools/) | One-time `setup.sh`, adb `resolve.sh`, prebuilt `trace_processor_shell` (5 platforms). |
| [`official/`](official/) | Pinned snapshot of Google's `record_android_trace`. |
| [`capture/`](capture/) | Cross-platform one-shot capture (`.bat` for Windows, `.sh` for Mac/Linux). |
| [`configs/`](configs/) | 6 prebuilt trace configs for common scenarios. |
| [`simpleperf/`](simpleperf/) | Simpleperf capture (standalone, or parallel with a trace). |
| [`fps-test/`](fps-test/) | Automated swipe test → per-source FPS / dropped frames. |

---

## Requirements

- **adb**: auto-installed by `./tools/setup.sh` if not on PATH. Override with
  `PERFETTO_TOOLS_ADB=/path/to/adb`.
- **Python 3.9+**.
- **`pip install perfetto`** — only for FPS analysis. The ~12MB native
  `trace_processor_shell` ships in `tools/` (no run-time download).
- An **Android device** connected via USB with debugging enabled.

---

## Troubleshooting

**`adb devices` shows nothing / `unauthorized`**
Enable Developer Options → USB debugging, then tap "Allow" on the device prompt.

**`capture.sh` says "no device connected"**
Same as above, or pass `-s <serial>` if you have several devices.

**`compute_fps.py` says "No FrameTimeline frames in trace"**
FrameTimeline needs Android 12 (API 31)+. If you're on 31+ and still see this, the
captured window had no surface rendering (e.g. a static home screen) — navigate to
a screen that actually animates/scrolls and re-capture.

**`compute_fps.py` says "no input events in trace"**
The trace couldn't derive fling windows from touch events. This is fine on `user`
builds — it falls back to script-recorded swipe timestamps automatically. Make
sure the config is `jank` (it includes the `input` atrace category).

**FPS numbers look wrong (e.g. 200+)**
You're reading "Total frames (all sources)" summed across multiple surfaces —
that's not screen FPS. Read the **`display`** source's FPS, or the
**"Screen FPS"** line. See "Reading the FPS report" above.

**simpleperf fails with "not supported on the device"**
On `user` builds, SELinux blocks `perf_event_open` even for debuggable apps. Use a
`userdebug`/`eng` build, `adb root`, or Perfetto's built-in `linux.perf` datasource
instead. See [`simpleperf/README.md`](simpleperf/README.md).

---

## Testing

```bash
python3 -m pytest tests/ -v     # 43 unit tests (config resolution, FPS math, swipe parsing)
```

Device-dependent flows (capture, simpleperf, fps-test end-to-end) are verified
manually. The repo was developed and acceptance-tested on an OPPO P0110 (API 36,
`user` build). See [`docs/spike-notes.md`](docs/spike-notes.md) for the on-device
schema findings that shaped the FPS code.

---

## Design docs

- [Design spec](docs/superpowers/specs/2026-06-17-perfetto-tools-design.md)
- [Implementation plan](docs/superpowers/plans/2026-06-17-perfetto-tools.md)
- [On-device spike notes](docs/spike-notes.md)
