# Official script archive

This directory holds a pinned snapshot of Google's `record_android_trace` script
from the [perfetto](https://github.com/google/perfetto) repo. It is used at
runtime by `../capture/` and `../fps-test/`.

It is an amalgamated Python script that handles ADB capture, config upload,
polling, trace pull, and optional browser opening. The repository wrapper owns
ADB selection and supplies one of the bundled tracebox binaries on legacy
Android, preventing the helper's normal first-use tracebox download.

## Why a snapshot

- Works offline / behind firewalls.
- Reproducible behavior (pinned to a commit).
- Single source of truth inside this repo.

## Current version

Perfetto v57.2. [`VERSION`](VERSION) records the full inspected `main` commit,
snapshot date, and helper SHA256. The latest inspected main commit moved while
the helper content remained byte-for-byte unchanged; both facts are retained.

## Updating

```bash
curl -fL https://raw.githubusercontent.com/google/perfetto/main/tools/record_android_trace \
  -o official/record_android_trace
chmod +x official/record_android_trace
shasum -a 256 official/record_android_trace
# Update the full commit hash, embedded tool version, date, SHA256, and pin tests
```

Then re-test `../capture/capture.sh --config general --time 3` on a device.
`../tools/check_updates.py --check` automates the stable/content comparison and
treats unrelated main-branch movement as informational when the bytes match.

## Important interface notes (used by `capture/` and `fps-test/`)

- `-c <config>` makes the script **ignore** `-t`/`-b`/`-a` (those short flags only
  apply without `-c`). To honor a duration, `capture/` rewrites the config's
  `duration_ms` into a temp file instead of passing `-t`.
- `--no-open` returns after pulling the trace without serving/opening it — needed
  when capture is run in the background (e.g. by `fps-test`).
- `-s <serial>` selects the ADB device when several are attached.
- Lightweight `-t`/`-b`/`-a` plus category arguments are exposed by
  `capture/ --categories`; they are not mixed into full-config mode.

## License

Upstream is Apache 2.0 (The Android Open Source Project). The script header
retains its original license notice.
