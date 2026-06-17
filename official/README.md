# Official script archive

This directory holds a pinned snapshot of Google's `record_android_trace` script
from the [perfetto](https://github.com/google/perfetto) repo. It is used at
runtime by `../capture/` and `../fps-test/`.

It is an amalgamated, self-contained Python script (no pip dependencies) that
handles: locating adb, sideloading tracebox on older devices, pushing the config,
running `perfetto --background`, polling for completion, pulling the trace, and
optionally opening it in the browser.

## Why a snapshot

- Works offline / behind firewalls.
- Reproducible behavior (pinned to a commit).
- Single source of truth inside this repo.

## Current version

See [`VERSION`](VERSION).

## Updating

```bash
curl -fL https://raw.githubusercontent.com/google/perfetto/master/tools/record_android_trace \
  -o official/record_android_trace
chmod +x official/record_android_trace
# Update the commit hash + date in VERSION
```

Then re-test `../capture/capture.sh --config general --time 3` on a device.

## Important interface notes (used by `capture/` and `fps-test/`)

- `-c <config>` makes the script **ignore** `-t`/`-b`/`-a` (those short flags only
  apply without `-c`). To honor a duration, `capture/` rewrites the config's
  `duration_ms` into a temp file instead of passing `-t`.
- `--no-open` returns after pulling the trace without serving/opening it — needed
  when capture is run in the background (e.g. by `fps-test`).
- `-s <serial>` selects the ADB device when several are attached.

## License

Upstream is Apache 2.0 (The Android Open Source Project). The script header
retains its original license notice.
