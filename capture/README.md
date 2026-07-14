# Perfetto Capture

One-shot trace capture on a connected Android device. Wraps the pinned official
`record_android_trace` with two explicit modes: full preset configs and
lightweight Perfetto categories.

Run the repository's native setup and `tools/doctor.py` first. Capture performs
a bounded ADB/device/API preflight and uses stable exit classes: argument errors
(`2`), host setup (`3`), device state (`4`), and Android incompatibility (`5`).

## Usage

Mac / Linux:
```bash
./capture.sh --config general --time 10
./capture.sh -c jank -t 8
./capture.sh --categories sched freq gfx view -t 10 -b 32mb -a com.example.app
./capture.sh --list-configs
./capture.sh --list-categories
```

Windows:
```bat
capture.bat --config general --time 10
```

For strictly offline capture, pass `--no-open`; otherwise the completed trace is
opened with `ui.perfetto.dev`.

## Options

| Flag | Meaning |
|---|---|
| `-c, --config <name>` | Config short name (`general`, `jank`, `02`, ...) or `--list-configs` |
| `--categories <name ...>` | Lightweight Perfetto/atrace category list; mutually exclusive with `--config` |
| `-t, --time <duration>` | Unitless seconds; lightweight mode also accepts `s`, `m`, or `h` |
| `-b, --buffer <size>` | Lightweight mode buffer such as `32mb` |
| `-a, --app <package>` | Lightweight mode atrace app; repeat for multiple packages |
| `-o, --output <path>` | Output file (default `traces/<ts>_<cfg>.perfetto-trace`) |
| `-s, --serial <id>` | ADB serial when multiple devices connected |
| `--no-open` | Don't open the trace in a browser |
| `--list-configs` | List available configs and exit |
| `--list-categories` | Query categories available on the connected device |

## Config name resolution

Names match against `../configs/*.pbtx`:
- exact stem (`02_jank_frame`),
- number prefix (`02`),
- case-insensitive keyword (`jank`, `JANK`).

Ambiguous matches error out and list candidates.

## How `--time` works

`record_android_trace` **ignores** `-t` when a full `-c/--config` is given (its
short flags only apply without `-c`). To honor `--time`, this wrapper rewrites
the config's top-level `duration_ms` into a temp file and passes that. The
nested `duration_ms` fields inside `data_sources{...}` are never touched. If
`--time` is omitted, the config's own `duration_ms` is used as-is.

In lightweight mode, duration, buffer, app, and categories are passed to the
official helper's supported short-option interface. See
[`../docs/systrace-migration.md`](../docs/systrace-migration.md) for old command
mappings.

## Requirements

- `../tools/setup.sh` on Mac/Linux or `../tools/setup.ps1` on Windows x86_64.
- One connected Android 6+ device in ADB state `device`. Unauthorized, offline,
  no-permission, zero-device, and multi-device states have distinct guidance.
- API 23–28 uses the bundled v57.2 tracebox. API 29 uses system Perfetto when
  `traced` is running and the bundled tracebox otherwise. No tracebox is fetched
  during normal capture.
- Jank/FPS configs require Android 12 / API 31. Other presets on legacy devices
  are best effort because OEM ftrace events vary.
- Linux ARM64 capture requires an external `PERFETTO_TOOLS_ADB`; Windows supports
  this core capture entry but not the Bash FPS/Simpleperf orchestration.

See [`../docs/compatibility.md`](../docs/compatibility.md) for the full matrix.
