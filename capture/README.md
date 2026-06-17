# Perfetto Capture

One-shot trace capture on a connected Android device. Wraps the archived
`record_android_trace` with config-name resolution and sensible defaults.

## Usage

Mac / Linux:
```bash
./capture.sh --config general --time 10
./capture.sh -c jank -t 8
./capture.sh --list-configs
```

Windows:
```bat
capture.bat --config general --time 10
```

## Options

| Flag | Meaning |
|---|---|
| `-c, --config <name>` | Config short name (`general`, `jank`, `02`, ...) or `--list-configs` |
| `-t, --time <sec>` | Duration in seconds, e.g. `10` (overrides the config's `duration_ms`) |
| `-o, --output <path>` | Output file (default `traces/<ts>_<cfg>.perfetto-trace`) |
| `-s, --serial <id>` | ADB serial when multiple devices connected |
| `--no-open` | Don't open the trace in a browser |
| `--list-configs` | List available configs and exit |

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

## Requirements

- `adb` on PATH, one device connected & authorized.
- Python 3.9+.
- The archived official script at `../official/record_android_trace` (included).
