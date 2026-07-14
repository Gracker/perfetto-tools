# Compatibility and environment contract

This page is the authoritative support boundary for Perfetto Tools. “Supported”
means the repository bootstrap, tests, doctor, and native entrypoint are covered
by automation. It does not mean that a physical Android device, USB permission,
or every OEM kernel tracepoint can be bundled.

## Host platforms

| Host | Bootstrap | Perfetto capture | Local analysis | FPS automation | Simpleperf scripts | Environment status |
|---|---|---:|---:|---:|---:|---|
| macOS arm64 | `./tools/setup.sh` | Supported | Supported | Supported | Supported | Self-contained after setup |
| macOS x86_64 | `./tools/setup.sh` | Supported | Supported | Supported | Supported | Self-contained after setup |
| Linux glibc x86_64 | `./tools/setup.sh` | Supported | Supported | Supported | Supported | Self-contained after setup |
| Linux glibc arm64 | `./tools/setup.sh` | External ADB required | Supported | External ADB required | External ADB required | Analysis-only until `PERFETTO_TOOLS_ADB` is set |
| Windows x86_64 | `tools\setup.ps1` | Supported via `capture.bat` | Supported | Python analysis only | Not supported | Core Perfetto path is self-contained; Bash orchestration is not provided |
| Linux musl, 32-bit hosts, other architectures | — | Unsupported | Unsupported | Unsupported | Unsupported | No managed runtime/artifact contract |

The managed toolchain is CPython 3.13.14, uv 0.11.28, Perfetto Python 0.57.2,
protobuf 6.33.6, Perfetto native tools v57.2, and Android Platform-Tools
37.0.0. Explicit `PERFETTO_TOOLS_PYTHON` and `PERFETTO_TOOLS_ADB` values are
escape hatches; doctor reports an external tool as non-hermetic.

Google does not publish a Linux ARM64 Platform-Tools archive. The repository
therefore cannot honestly make capture on that host independent of the user's
ADB. This does not affect local trace analysis because the Linux ARM64
`trace_processor_shell` is bundled.

## Android versions and capture capabilities

| Android API | Capture transport | Preset/lightweight capture | Jank/FPS | Notes |
|---|---|---|---|---|
| 22 and older | — | Unsupported | Unsupported | Android 6 / API 23 is the minimum |
| 23–28 | Bundled v57.2 tracebox sideload | Best effort | Unsupported | OEM kernel/ftrace availability varies; no runtime download |
| 29 | System Perfetto when `traced` runs; bundled tracebox otherwise | Supported | Unsupported | Fallback is selected during bounded device preflight |
| 30 | System Perfetto | Supported | Unsupported | FrameTimeline is not available yet |
| 31–33 | System Perfetto | Supported | Supported | Android 12 / API 31 adds FrameTimeline |
| 34 and newer | System Perfetto | Supported | Supported | `dumpsys SurfaceFlinger --latency` may not emit rows; use FrameTimeline |

`general`, `startup`, `cpu`, `memory`, and lightweight category capture are the
normal paths. The `full` config can run before API 31, but doctor/capture warn
that its FrameTimeline portion will be absent. `jank` and the automated FPS
runner fail before capture on API 30 or older so they cannot produce a plausible
but invalid result.

On debuggable/userdebug/eng builds, structured input events may provide gesture
boundaries. Production `user` builds often omit those values; FPS analysis then
uses `input` atrace ACTION slices and finally the runner's device-clock swipe
markers. This fallback is deliberate and tested.

Kernel ftrace events, vendor tracepoints, and Simpleperf access remain OEM/build
capabilities. A valid trace may omit an unavailable event. Simpleperf can be
blocked by SELinux or `perf_event_open` policy even when Perfetto capture works.

## Offline and external boundaries

The first setup needs HTTPS plus the host's native shell, archive, and checksum
facilities. It downloads verified uv, managed Python/packages, and (where Google
publishes it) Platform-Tools. Every archive is checked against a repository pin
before extraction.

After setup, capture with `--no-open`, trace processing, FPS computation, and
legacy Android tracebox sideloading do not download tools or packages. The
default browser-opening path uses `ui.perfetto.dev`; use `--no-open` for a
strictly offline capture and open the file later or with a separately hosted UI.

The following can never be supplied by the repository:

- a physical Android device or emulator;
- USB/network reachability and the device's debugging authorization prompt;
- OEM kernel tracepoints, root, debuggable app state, or Simpleperf permission;
- Google Platform-Tools for Linux ARM64, because no official archive exists.

Run `tools/doctor.py` for host readiness. Add `--device --feature <name>` when a
ready device is required, for example `--feature fps`. An absent optional device
is `NOT AVAILABLE`, never `PASS`.

## Automation coverage

Push and pull-request verification bootstraps and tests Ubuntu, macOS, and
Windows independently. Ubuntu also runs Bash syntax, ShellCheck, and every
bundled-artifact checksum. `tools/device_smoke.py` builds an API-aware physical
device plan; without a device it reports `NOT AVAILABLE`, while
`--require-device` makes that condition fail.

Mutable upstream status is intentionally separate: the weekly/manual Tool drift
workflow checks PyPI Perfetto, uv releases, stable/Canary Platform-Tools, and the
content of Perfetto's `record_android_trace`. Canary availability is
informational; stable or helper-content drift requires maintenance.
