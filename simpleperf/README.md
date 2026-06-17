# Simpleperf Capture

Two independent shell scripts.

## simpleperf_only.sh

Standalone CPU profile of an app's main process.

```bash
./simpleperf/simpleperf_only.sh com.example.app 10
```

Outputs `traces/simpleperf_<ts>.data`. View with simpleperf's `report_html.py`
(ships with the Android NDK):

```bash
python3 report_html.py -i traces/simpleperf_<ts>.data
```

## simpleperf_with_trace.sh

Runs simpleperf **and** a Perfetto trace (config `03_cpu_sched`) for the same
time window.

```bash
./simpleperf/simpleperf_with_trace.sh com.example.app 10
```

Outputs both `traces/simpleperf_<ts>.data` and `traces/<ts>_cpu.perfetto-trace`.

## Requirements

- The target app must be **debuggable**, OR the device must be rooted
  (`adb root`). simpleperf needs `perf_event_open`, which Android restricts for
  non-debuggable/release apps.
- `adb` + `simpleperf` on the device (simpleperf ships with the system image on
  modern Android; otherwise push from the NDK).

### Device kernel may block simpleperf entirely (important)

Even on a rooted device or with a debuggable app, **some `user`-build kernels
disable `perf_event_open` at the kernel/SELinux layer** — not just hardware PMU
events, but *all* events including software ones like `cpu-clock`. On such
devices simpleperf fails with:

```
simpleperf W  cpu-cycles event is not supported on the device.
```

This is a **device limitation, not a script or app bug.** Verified empirically on
an OPPO P0110 (ColorOS, API 36 `user` build): `perf_event_paranoid` reads `-1`
(suggesting access is allowed), yet every event — software included — is
rejected. There is no workaround from userspace; you need a `userdebug`/`eng`
build or a different device. The scripts detect this, print a hint, and exit 1.

If you only need CPU callstacks (not simpleperf's `.data` format), prefer
Perfetto's built-in CPU profiling (below) — it does not depend on
`perf_event_open` and works on stock devices.

## Alternative: Perfetto's built-in CPU profiling

Perfetto itself can capture CPU callstack sampling via the `linux.perf` datasource
in a single trace, which avoids running two tools. Use that if you don't
specifically need simpleperf's `.data` format / `report_html.py`. See the
[Perfetto CPU profiling docs](https://perfetto.dev/docs/getting-started/cpu-profiling).
