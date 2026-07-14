# Perfetto Tools Modernization Design

> Historical snapshot, superseded by the 2026-07-14 runtime-hardening design.
> Version/Python/setup statements below describe the audited baseline or that
> completed migration, not the current support contract. See
> `docs/compatibility.md` for current truth.

**Date:** 2026-07-13

## Goal

Modernize the repository around the current Perfetto toolchain, replace obsolete
Systrace capture guidance with executable Perfetto equivalents, make tool pins
internally verifiable, and preserve the existing preset-config workflow.

## Current-state findings

- `official/record_android_trace` was fetched from Perfetto's frozen `master`
  branch (`db10888e…`) and embeds v49.0 prebuilts. Perfetto's source of truth is
  now `main`; the current script embeds v57.2.
- The bundled `trace_processor_shell` binaries are v49.0 while the current Python
  package is 0.57.2.
- `tools/setup.sh` pins Platform-Tools 35.0.2. Both configured archive hashes are
  wrong, and a mismatch only warns before installing the unverified archive.
- Android removed `systrace` from Platform-Tools in 33.0.1. `atrace` remains a
  supported instrumentation source inside Perfetto and must not be removed.
- The config/FPS unit-test baseline is green (43 tests), but tool pins, shell
  resolution, and lightweight capture command construction are not covered.
- This Mac's first `python3` on `PATH` is unusable, while a later Python 3.13 is
  healthy. The scripts currently stop at the first command name instead of
  resolving a working Python 3.9+ interpreter.

## Chosen architecture

### Two explicit capture modes

`capture/perfetto_capture.py` remains the single cross-platform capture core.
It exposes two mutually exclusive modes:

1. **Preset config mode** — `--config general` keeps the existing full pbtxt
   workflow and duration override behavior.
2. **Lightweight category mode** — `--categories sched freq gfx view` delegates
   category, app, buffer, and duration arguments to the official Perfetto helper.
   This is the supported replacement for old `systrace.py` command lines.

There will be no compatibility copy of `systrace.py`. Keeping an obsolete
collector would preserve the wrong runtime boundary. A migration guide maps old
flags and terminology to the new mode while explaining that `atrace` categories
are still valid Perfetto inputs.

### Reproducible toolchain

- Pin `record_android_trace` to inspected Perfetto `main` commit `4f2c163…`.
- Align the five bundled `trace_processor_shell` binaries and the Python
  `perfetto` dependency at v57.2/0.57.2.
- Pin latest stable Android Platform-Tools 37.0.0 with verified Google archive
  hashes. A checksum mismatch becomes fatal and deletes the bad archive.
- Add repository tests that prove metadata, bundled hashes, embedded upstream
  version, and Python dependency remain aligned.

### Runtime resolution

Extend `tools/resolve.sh` to resolve a working Python 3.9+ interpreter using:

1. `PERFETTO_TOOLS_PYTHON`, if explicitly set and valid;
2. repository `.venv/bin/python`;
3. every `python3`/`python` candidate on `PATH`, not only the first broken one.

The Unix capture and FPS entrypoints use the resolver. Windows prefers
`PERFETTO_TOOLS_PYTHON`, then the Python launcher, then `python`.

## Error handling and safety

- Capture-mode-only flags fail with actionable errors instead of being silently
  ignored.
- Lightweight durations accept positive numbers (interpreted as seconds) or
  explicit `s`, `m`, and `h` units.
- Tool archive verification fails closed.
- Upstream snapshots remain pinned and checksummed; normal capture stays offline.
- Existing configs and FPS semantics are unchanged.

## Verification

- Red/green unit tests for capture command construction and Python resolution.
- Pin-integrity tests covering all bundled binaries and metadata.
- Full Python suite.
- Shell syntax and ShellCheck for repository shell entrypoints.
- CLI smoke checks for config listing, both help surfaces, tool versions, and
  checksum verification.
- Final diff, architecture, documentation-link, and `git diff --check` review.

Device-dependent capture remains a documented manual gate when no authorized
Android device is available; host-side command construction is automated.
