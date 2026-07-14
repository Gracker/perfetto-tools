# Migrating from Systrace to Perfetto

Android SDK Platform-Tools removed `systrace` in 33.0.1. New system traces
should be captured with Perfetto and opened in the Perfetto UI. This repository
does not bundle or emulate `systrace.py`; it provides two maintained Perfetto
capture modes instead.

Old copies under `platform-tools/systrace/`, Python 2 launch wrappers, and scripts
that generate self-contained `trace.html` files are historical inputs, not tools
to vendor back into a current SDK. Keeping an old executable also keeps its old
ADB/category/parser assumptions and does not restore platform support.

Run the repository bootstrap first (`./tools/setup.sh` or Windows
`tools\setup.ps1`). The lightweight replacement below then uses the pinned
official Perfetto helper and the same device-state/API checks as preset capture.

## Choose the replacement

| Old workflow | Current replacement |
|---|---|
| `systrace.py`, SDK `systrace` launcher, or copied legacy wrapper | `capture.sh --categories ...` |
| Hand-maintained long category command | Versioned `capture.sh --config ...` preset |
| Interactive probe selection | Perfetto UI → **Record new trace** |
| On-device recording | Android **System Tracing** developer option/tile |
| Open generated `trace.html` | Open `.perfetto-trace` at `https://ui.perfetto.dev` |

The repository's preset configs are the preferred path for repeatable startup,
jank, CPU, and memory investigations. Lightweight category mode exists for old
Systrace command lines and quick ad-hoc captures.
On Windows x86_64, use `capture\capture.bat` in place of
`./capture/capture.sh`; the flags are the same.

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
| copied `systrace.py` / Python 2 environment | repository-managed Python + pinned `record_android_trace` |

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

| API | Maintained repository path |
|---|---|
| 22 and older | Unsupported; keep a historical trace for analysis rather than restoring `systrace.py` |
| 23–28 | Perfetto helper + repository-bundled ABI-specific tracebox; base capture is OEM/ftrace best effort |
| 29 | System Perfetto when `traced` runs, bundled tracebox otherwise |
| 30 | System Perfetto; no FrameTimeline FPS yet |
| 31+ | System Perfetto with FrameTimeline jank/FPS support |

The on-device System Tracing app exists on Android 9+. Android 10+ records
Perfetto format; Android 9 records the older Systrace format, which Perfetto UI
can still open for analysis. That file compatibility is not a reason to use the
removed host collector for new traces.

The repository ships the legacy Android tracebox binaries recorded in the
official v57.2 manifest and passes `--sideload-path`; normal legacy capture does
not download them. See the full [compatibility matrix](compatibility.md).

For supported capture details, use the upstream guides:

- <https://perfetto.dev/docs/getting-started/system-tracing>
- <https://perfetto.dev/docs/getting-started/atrace>
- <https://developer.android.com/tools/releases/platform-tools>
