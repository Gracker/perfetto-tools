# Migrating from Systrace to Perfetto

Android SDK Platform-Tools removed `systrace` in 33.0.1. New system traces
should be captured with Perfetto and opened in the Perfetto UI. This repository
does not bundle or emulate `systrace.py`; it provides two maintained Perfetto
capture modes instead.

## Choose the replacement

| Old workflow | Current replacement |
|---|---|
| `systrace.py` with common categories | `capture.sh --categories ...` |
| Repeatable scenario config | `capture.sh --config ...` |
| Interactive probe selection | Perfetto UI → **Record new trace** |
| On-device recording | Android **System Tracing** developer option/tile |
| Open generated `trace.html` | Open `.perfetto-trace` at `https://ui.perfetto.dev` |

The repository's preset configs are the preferred path for repeatable startup,
jank, CPU, and memory investigations. Lightweight category mode exists for old
Systrace command lines and quick ad-hoc captures.

## Command migration

Legacy command:

```bash
python systrace.py -o trace.html -t 10 -b 32768 \
  -a com.example.app sched freq idle am wm gfx view
```

Perfetto lightweight equivalent:

```bash
./capture/capture.sh \
  --categories sched freq idle am wm gfx view \
  --time 10 \
  --buffer 32mb \
  --app com.example.app \
  --output traces/example.perfetto-trace
```

Useful mappings:

| Systrace flag/concept | Perfetto Tools |
|---|---|
| `-t 10` | `--time 10` (`10s`, `2m`, and `1h` are also accepted) |
| `-b 32768` (KiB) | `--buffer 32mb` |
| `-a package` | `--app package` (repeatable) |
| trailing categories | `--categories category ...` |
| `--list-categories` | `--list-categories` (queries the connected device) |
| HTML output | binary `.perfetto-trace` output |

For a maintained full config instead:

```bash
./capture/capture.sh --config general --time 10
./capture/capture.sh --config startup --time 15
./capture/capture.sh --config jank --time 12
```

## Systrace is obsolete; atrace is not

The names are easy to conflate:

- **Systrace** was the removed host-side Python collector and HTML viewer.
- **atrace** is Android userspace trace instrumentation. Perfetto still records
  its categories through the `linux.ftrace` data source, using
  `atrace_categories` and `atrace_apps` in a full config.

Do not delete `atrace` categories such as `am`, `wm`, `gfx`, `view`, and `input`
when migrating. They provide framework/app annotations inside a Perfetto trace.
For app code, AndroidX Tracing is the current high-level instrumentation API.

## Android version boundaries

- The official `record_android_trace` helper supports Android M+ and can
  sideload `tracebox` where the system Perfetto service is unavailable.
- This repository's base presets target Android 10+.
- FrameTimeline-based jank/FPS analysis requires Android 12+ (API 31+).
- The on-device System Tracing app exists on Android 9+. Android 10+ records
  Perfetto format; Android 9 records the older Systrace format, which Perfetto UI
  can still open for analysis.

For supported capture details, use the upstream guides:

- <https://perfetto.dev/docs/getting-started/system-tracing>
- <https://perfetto.dev/docs/getting-started/atrace>
- <https://developer.android.com/tools/releases/platform-tools>
