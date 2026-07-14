# Perfetto Tools Runtime Compatibility Hardening Design

**Date:** 2026-07-14

## Objective

Turn the current modernized tool set into a predictable user-facing product:
one setup command creates a repository-owned runtime, capture rejects unsupported
or broken states before starting work, compatibility boundaries are explicit,
and CI proves the host-side contract on every fully supported desktop platform.

The design does not claim that a source checkout can eliminate the network,
physical Android device, USB authorization, OEM kernel capabilities, or OS
facilities needed to download and run binaries. It does guarantee that after a
successful setup, normal capture and analysis do not depend on ambient Python,
Python packages, ADB, or a run-time Perfetto binary download on fully supported
hosts.

## Current Evidence and Gaps

- Perfetto v57.2, `perfetto==0.57.2`, and stable Platform-Tools 37.0.0 are the
  current reviewed pins. The upstream `record_android_trace` content still
  matches the local snapshot.
- The current setup exits as soon as any ADB is resolvable. On the audit host it
  therefore selected ADB 36.0.0 instead of installing the pinned 37.0.0.
- The current Python resolver selected a global Python whose installed Perfetto
  package was 0.56.0. Tests passed because they did not exercise real analysis.
- Android M-P capture causes the upstream helper to download tracebox into the
  user's home directory at run time.
- Device states `unauthorized` and `offline` collapse into a generic no-device
  message; an ADB command failure raises an uncaught `CalledProcessError`.
- The only CI host is Linux x86_64. Windows setup, Windows capture entry, and
  macOS behavior are not exercised by CI.

## Supported Contract

### Host matrix

| Host | Setup | Capture | Trace analysis | FPS/Simpleperf automation |
|---|---:|---:|---:|---:|
| macOS arm64/x86_64 | self-contained | supported | supported | supported |
| Linux glibc x86_64 | self-contained | supported | supported | supported |
| Windows x86_64 | self-contained | supported | supported | not supported by the Bash orchestration |
| Linux glibc arm64 | Python/analysis self-contained | external ADB required | supported | supported when ADB is supplied |
| Windows arm64, Linux musl, other hosts | explicit unsupported/best-effort diagnostic | not guaranteed | not guaranteed | not supported |

“Self-contained” means setup downloads verified, pinned artifacts into `.bin/`
and creates `.venv/`; it does not edit shell profiles or global Python/package
state. Explicit `PERFETTO_TOOLS_ADB` and `PERFETTO_TOOLS_PYTHON` overrides remain
escape hatches and are reported as non-hermetic by the doctor. An external
Python override must be CPython 3.10-3.14; setup itself always uses the managed
3.13.14 runtime.

### Android matrix

| Android | Capture behavior |
|---|---|
| API 23-28 (M-P) | use a bundled v57.2 tracebox selected by device ABI; no run-time download |
| API 29 (Q) | use system tracing when services are running, otherwise use bundled tracebox |
| API 30 (R) | system Perfetto; basic and preset capture supported |
| API 31+ (S+) | full contract, including FrameTimeline jank/FPS capture |

General/startup/CPU/memory presets are supported on API 29+ and best-effort on
API 23-28 because OEM kernels may omit ftrace events. `jank` and FPS are rejected
before capture below API 31. `full` remains usable below API 31 but emits a clear
warning that FrameTimeline/input sources will be absent. Android 14+
SurfaceFlinger latency limitations remain a documented auxiliary-tool boundary.

## Architecture

### 1. Pinned bootstrap layer

`tools/tool-versions.env` is the bootstrap manifest for Python 3.13.14, uv
0.11.28, Platform-Tools 37.0.0, and per-platform archive SHA256 values.
`tools/setup.sh` and `tools/setup.ps1` only perform the minimum OS-native work
needed to download and verify uv. They then run `uv sync --frozen` with
repository-local cache/Python directories so uv installs the managed Python and
locked dependencies into `.venv/` without touching global state.

After the managed environment exists, `tools/setup_runtime.py` performs the
shared cross-platform work: verify bundled artifacts, install or verify the
repository-local ADB, validate exact versions, and print the doctor summary.
The default path never accepts a PATH ADB as satisfying the pinned setup. An
explicit override is allowed but must be runnable and is labeled external.

`pyproject.toml` and `uv.lock` become the reproducible Python dependency source.
The existing requirements files remain simple pip-compatible projections and
tests enforce that all version pins agree.

### 2. Shared host/device runtime boundary

`perfetto_tools/runtime.py` owns:

- repository path and platform detection;
- ADB resolution, bounded subprocess execution, and version parsing;
- parsing every `adb devices -l` state without losing unauthorized/offline
  context;
- device probing for API level, ABI, build type, and tracing-service state;
- Android feature compatibility and local tracebox selection;
- typed user-facing failures with stable exit categories.

`capture/perfetto_capture.py` delegates these responsibilities instead of
maintaining a second resolver/parser. `tools/doctor.py` uses the same boundary,
so diagnostics and real capture cannot disagree.

### 3. Offline legacy-Android path

Ship the four v57.2 Android tracebox artifacts (`arm`, `arm64`, `x86`, `x64`)
already described by the pinned upstream manifest. Add them to the repository
checksum file and verify them during setup and CI. For API 23-28, and for API 29
whose traced services are unavailable, capture passes `--sideload-path` to the
unmodified upstream helper.

### 4. Input and exception hardening

Before any device action, capture validates duration, buffer size, category,
app/package, output parent, and mode-specific arguments. Listing modes reject
irrelevant capture flags instead of silently ignoring them.

ADB discovery/probing has a finite timeout and converts missing executable,
permission failure, daemon failure, unauthorized, offline, multiple-device, bad
API output, unsupported ABI, and unsupported Android feature into concise
remediation messages. Known user errors return code 2; host setup failures return
3; device-state failures return 4; unsupported Android/feature combinations
return 5. The upstream capture exit code is preserved after preflight.

### 5. Automation and drift control

The Verify workflow uses an OS matrix for Ubuntu, macOS, and Windows. It runs
Python tests, the host binary smoke test, doctor/setup smokes, and the native
capture entry (`capture.sh` or `capture.bat`). Bash syntax and ShellCheck remain
an Ubuntu gate.

`tools/check_updates.py` compares the pinned Perfetto Python version, upstream
record helper content/commit, uv release, and Android stable Platform-Tools
metadata against authoritative endpoints. A separate scheduled/manual workflow
reports drift without making ordinary push verification depend on mutable
upstream state.

`tools/device_smoke.py` automates one-second capture validation when a physical
device is available: basic capture on API 23+, preset capture on API 29+, and
jank capture on API 31+. Absence of a device is an explicit `NOT AVAILABLE`, not
a passing device test.

## Testing Strategy

- TDD unit tests for device-list parsing, ADB failures/timeouts, API/ABI probes,
  tracebox selection, Android feature gates, argument validation, and exact
  error/exit behavior.
- Manifest/setup tests prove every URL/hash/version is present, the default
  setup ignores ambient PATH ADB, Windows uses the correct `-win.zip` archive,
  and runtime package pins agree with the lock/project metadata.
- Host tests execute the correct trace processor on macOS, Linux, and Windows;
  unsupported hosts skip with an explicit reason rather than raising `KeyError`.
- CI runs the full suite on all fully supported desktop hosts and both the
  minimum externally supported Python and the managed Python where practical.
- Physical-device smoke remains a separate evidence class because hosted CI has
  no authorized Android device. The final report must state whether it ran.

## Non-goals

- Reintroducing or emulating `systrace.py`.
- Claiming FrameTimeline support before Android 12.
- Bundling Android Studio, USB drivers, a local Perfetto UI, or an Android
  emulator image.
- Treating Linux ARM64 ADB from an unofficial source as equivalent to Google's
  Platform-Tools distribution.
- Rewriting FPS/Simpleperf Bash orchestration for Windows in this hardening pass.
