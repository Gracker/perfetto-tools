# Perfetto Capture

One-shot trace capture on a connected Android device. Wraps the pinned official
`record_android_trace` with two explicit modes: full preset configs and
lightweight Perfetto categories.

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

- `adb` resolvable by `../tools/resolve.sh`, one device connected and authorized.
- Python 3.9+.
- The archived official script at `../official/record_android_trace` (included).
