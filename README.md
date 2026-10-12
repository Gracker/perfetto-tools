# Perfetto Tools

[简体中文](README.zh-CN.md)

A consolidated toolkit for capturing [Perfetto](https://perfetto.dev/) traces on
Android, plus Simpleperf capture and automated swipe-based FPS testing.

**Pinned and reproducible**: setup creates a repository-owned Python environment,
installs verified Platform-Tools 37.0.0 on supported hosts, and verifies the
bundled Perfetto v57.2 trace processors and legacy Android tracebox binaries.

<!-- android-performance-ecosystem:start -->
## Android performance ecosystem

The [Android Performance Ecosystem](https://github.com/Gracker/android-performance-ecosystem) brings its navigation Hub and seven core projects into an optional path from instrumentation and capture to analysis, system knowledge, and reproducible cases.

| Stage | Project | Purpose | Address |
| --- | --- | --- | --- |
| Navigate | [Android Performance Ecosystem](https://github.com/Gracker/android-performance-ecosystem) | Maintain the shared project map, handoff metadata, generated README navigation, and drift checks. | [GitHub](https://github.com/Gracker/android-performance-ecosystem) |
| Instrument | [TraceFix](https://github.com/Gracker/TraceFix) | Inject app-side android.os.Trace sections at build time so method work is visible at runtime. | [GitHub](https://github.com/Gracker/TraceFix) |
| Capture and measure | [Perfetto Tools](https://github.com/Gracker/perfetto-tools) | Capture repeatable Perfetto traces and collect FPS or Simpleperf measurements. | [GitHub](https://github.com/Gracker/perfetto-tools) |
| Analyze | [SmartPerfetto](https://github.com/Gracker/SmartPerfetto) | Investigate traces with an AI-assisted Web UI, CLI, reports, sessions, comparisons, and evidence workflow. | [GitHub](https://github.com/Gracker/SmartPerfetto) |
| Agent analysis | [Perfetto Skills](https://github.com/Gracker/Perfetto-Skills) | Give agents a portable Perfetto analysis Skill for Android, Linux, and Chromium, with selected assets synchronized through pinned workflows. | [GitHub](https://github.com/Gracker/Perfetto-Skills) |
| Learn | [Android Performance Blog](https://github.com/Gracker/Gracker.github.io) | Teach Perfetto and Systrace analysis through articles, system explanations, and case studies. | [AndroidPerformance.com](https://www.androidperformance.com/) · [GitHub](https://github.com/Gracker/Gracker.github.io) |
| System knowledge | [Android Internal Wiki](https://github.com/Gracker/android-internals-wiki) | An alpha knowledge base for Android mechanisms from App to Framework, Native, and Kernel. | [GitHub](https://github.com/Gracker/android-internals-wiki) |
| Reproduce | [Trace for Blog (SystraceForBlog)](https://github.com/Gracker/SystraceForBlog) | Provide the Perfetto, Systrace, and related case files used by articles for hands-on reproduction. | [GitHub](https://github.com/Gracker/SystraceForBlog) |
<!-- android-performance-ecosystem:end -->

---

## 5-minute start

Mac / Linux:

```bash
./tools/setup.sh
.venv/bin/python tools/doctor.py

# Plug in a device, enable USB debugging, and authorize this computer.
.venv/bin/python tools/doctor.py --device --feature general

# Capture a 10-second trace (Ctrl+C stops early; Perfetto preserves the trace).
./capture/capture.sh --config general --time 10
```

Windows x86_64 (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup.ps1
.\.venv\Scripts\python.exe tools\doctor.py
capture\capture.bat --config general --time 10
```

The first setup needs HTTPS. Normal capture/analysis uses only repository-owned
tools afterward; add `--no-open` when the browser/UI must also stay offline.
A physical device and its USB authorization cannot be bundled.

To measure FPS while scrolling on Mac/Linux (Android 12 / API 31+):

```bash
# Launch your app, navigate to the screen you want to test, then:
./fps-test/run_fps_test.sh 12 com.example.app
#    → captures a trace while auto-swiping (3 up, 3 down) and prints an FPS report
```

Migrating an old `systrace.py` command? Use lightweight Perfetto mode:

```bash
./capture/capture.sh --categories sched freq gfx view \
  --time 10 --buffer 32mb --app com.example.app
```

See the [Systrace migration guide](docs/systrace-migration.md) for flag mappings,
Android-version boundaries, and why supported `atrace` categories remain.

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

Preset mode is for repeatable, full pbtxt configs. `--categories` is a separate
lightweight mode for ad-hoc capture and old Systrace-style category lists; flags
from the two modes are intentionally not mixed.

---

## What's in this repo

| Directory | Purpose |
|---|---|
| [`tools/`](tools/) | Native bootstrap, doctor/update/device-smoke commands, verified ADB, 5 trace processors, and 4 Android tracebox binaries. |
| [`official/`](official/) | Pinned snapshot of Google's `record_android_trace`. |
| [`capture/`](capture/) | Cross-platform one-shot capture (`.bat` for Windows, `.sh` for Mac/Linux). |
| [`configs/`](configs/) | 6 prebuilt trace configs for common scenarios. |
| [`simpleperf/`](simpleperf/) | Simpleperf capture (standalone, or parallel with a trace). |
| [`fps-test/`](fps-test/) | Automated swipe test → per-source FPS / dropped frames. |

---

## Requirements and support boundary

- On macOS arm64/x86_64, Linux glibc x86_64, and Windows x86_64, use the native
  setup command above; do not preinstall Python, pip packages, or ADB.
- Linux glibc ARM64 analysis is managed, but capture requires an explicit
  `PERFETTO_TOOLS_ADB` because Google publishes no Platform-Tools archive for
  that host.
- External Python 3.10–3.14 and ADB overrides remain available for unusual
  environments, but they are non-hermetic escape hatches and doctor reports them.
- A connected Android 6+ device with USB debugging is required for capture.
  FrameTimeline jank/FPS specifically requires Android 12 / API 31+.
- Windows supports core Perfetto capture and local Python analysis. The Bash FPS
  and Simpleperf orchestration scripts are Mac/Linux only.

See the authoritative [compatibility matrix](docs/compatibility.md).

---

## Troubleshooting

**Doctor/capture reports no device, `unauthorized`, `offline`, or permissions**
The messages distinguish these states. Connect and authorize a device, reconnect
an offline device, or configure Linux USB/udev access. With several ready
devices, pass `--serial <id>`.

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

Run setup once for a new checkout, and again when managed dependency or tool pins
change. It needs HTTPS; it is not part of every source verification:

```bash
./tools/setup.sh
```

For routine source changes, use the existing managed runtime without querying
upstream versions or connecting a device:

```bash
.venv/bin/python tools/verify.py
```

Windows uses `.\.venv\Scripts\python.exe tools\verify.py`. This runs the complete
Python suite, including bundled artifact hashes and the host trace processor.
Unix also checks every tracked Bash script's syntax and runs ShellCheck; install
ShellCheck first. Missing required tools or a failed check returns a nonzero exit.
The source gate keeps the complete suite; no reduced test tier is needed.

Run `.venv/bin/python tools/doctor.py` after bootstrap or host runtime changes.
Device capture changes also require `tools/device_smoke.py --require-device` on
an authorized Android device; an absent device is `NOT AVAILABLE`, not a pass.
`tools/check_updates.py --check` is a separate online maintenance check, run by
the scheduled/manual Tool drift workflow. Upstream drift does not invalidate
otherwise reproducible source tests.

CI bootstraps Ubuntu, macOS, and Windows, runs the same source entry, then
separately verifies native doctor/CLI readiness. See
[`tools/README.md`](tools/README.md) for commands and
[`docs/spike-notes.md`](docs/spike-notes.md) for existing device findings.

---

## Maintainer references

- [Compatibility matrix](docs/compatibility.md)
- [Runtime, device and update commands](tools/README.md)
- [Systrace migration](docs/systrace-migration.md)
- [On-device findings](docs/spike-notes.md)
