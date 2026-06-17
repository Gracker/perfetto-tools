# Perfetto Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single repo that consolidates Perfetto trace capture (cross-platform one-shot script), official script archival, prebuilt configs, Simpleperf capture, and automated swipe-based FPS testing for Android.

**Architecture:** Five loosely-coupled blocks under one repo root. Block 2 (capture) wraps the archived official `record_android_trace` script (Block 1). Block 5 (fps-test) reuses Block 2's capture and Block 3's `02_jank_frame.pbtx` config. Block 4 (simpleperf) is independent shell scripts. Unix philosophy: one script, one function.

**Tech Stack:** Python 3.9+ (capture core, fps compute — uses `dataclasses`, `from __future__ import annotations`, `str.removesuffix`), Bash (entry wrappers, simpleperf, fps-test orchestrator), Windows BAT (Windows entry), Perfetto trace_processor Python API, adb, simpleperf.

**Spec:** `docs/superpowers/specs/2026-06-17-perfetto-tools-design.md`

**Testing note:** Pure unit testing applies to logic that doesn't touch devices (config name resolution, swipe sequence generation, FPS math). Device-dependent flows (capture, simpleperf record, fps-test end-to-end) are verified by explicit manual acceptance commands against a real Android device. Both forms are given per task.

**Environment note:** This directory is NOT a git repo. Tasks include `git init` as the first commit step; subsequent tasks commit incrementally. If the user has already initialized git, skip the init and just commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `README.md` | Repo entry point: what's here, quick start, navigation to each block |
| `LICENSE` | Apache 2.0 (matches upstream Perfetto scripts) |
| `.gitignore` | Ignore `traces/`, Python caches, OS cruft |
| `official/record_android_trace` | Official script snapshot (downloaded, not authored) |
| `official/VERSION` | Commit hash + download date of the snapshot |
| `official/README.md` | Source URL, version, update procedure |
| `capture/perfetto_capture.py` | Core: arg parse, config name → path resolution, device check, invoke official script |
| `capture/capture.sh` | Mac/Linux entry wrapper |
| `capture/capture.bat` | Windows entry wrapper |
| `capture/README.md` | Usage (3 platforms), examples |
| `configs/00_general.pbtx` … `05_full.pbtx` | 6 prebuilt trace configs |
| `configs/README.md` | Per-config purpose, datasources, Android version, trace size |
| `simpleperf/simpleperf_only.sh` | Standalone simpleperf record + pull |
| `simpleperf/simpleperf_with_trace.sh` | simpleperf + Perfetto trace in parallel |
| `simpleperf/README.md` | Usage, debuggable-app requirement, linux.perf alternative note |
| `fps-test/run_fps_test.sh` | Orchestrator: start capture → swipe sequence → pull → call compute_fps |
| `fps-test/compute_fps.py` | trace_processor SQL: fling windows from input; frames from FrameTimeline + per-layer BufferQueue (multi-source); per-source FPS/jank + TextureView single-buffer overwrite drops |
| `fps-test/swipe_pattern.txt` | Tunable swipe params (coords, duration, counts) |
| `fps-test/dump_gfxinfo.sh` | Auxiliary: dumpsys gfxinfo framestats + SurfaceFlinger --latency, before/after, as an independent cross-check of the trace FPS |
| `fps-test/README.md` | Usage, flow, output format, troubleshooting |
| `tests/test_config_resolver.py` | Unit tests for config name resolution |
| `tests/test_swipe_pattern.py` | Unit tests for swipe sequence generation |
| `tests/test_compute_fps_math.py` | Unit tests for FPS/jank math over synthetic frame data |

---

## Task 0: Device schema spike (do FIRST — de-risks the core assumptions)

The highest-risk parts of this plan are not pure logic — they are assumptions
about Perfetto config syntax and trace_processor schema that can only be
confirmed against a real device + real trace. Validate them BEFORE writing the
six configs and the FPS SQL, otherwise the wrong assumptions get baked in and
only surface at the final manual acceptance.

This task produces no shipped code — just confirmed facts recorded in
`docs/spike-notes.md`. Requires one connected Android device (ideally a
**debuggable/userdebug** build so input + FrameTimeline are both available).

> Test app: the maintainer's phone is connected over USB with the demo APKs from
> [Friends-Circle-Demo-Apks-For-Power-and-Performance-Test](https://github.com/Gracker/Friends-Circle-Demo-Apks-For-Power-and-Performance-Test)
> already installed. These are a scrolling "friends circle" feed built for
> power/performance testing and include multiple rendering paths — a good target
> for Step 3b (multi-source) and the single-buffer/TextureView cases. Use them as
> the standard spike + acceptance target.

- [ ] **Step 1: Confirm the minimal config parses on-device**

Write a throwaway `/tmp/min.pbtx` with: a `linux.ftrace` data source using
`atrace_categories`/`atrace_apps`, plus `android.surfaceflinger.frametimeline`
and `android.input.inputevent` data sources. Push and capture a few seconds:

```bash
adb push /tmp/min.pbtx /data/local/tmp/c.pbtx
adb shell cat /data/local/tmp/c.pbtx \| perfetto --txt -c - -o /data/local/tmp/t.pftrace -d
adb pull /data/local/tmp/t.pftrace /tmp/spike.pftrace
```
Record: does it parse with NO error? (If `android.atrace`/`atrace_config` is
used instead, it must FAIL — confirming the fix in Task 2.)

- [ ] **Step 2: Confirm the FrameTimeline / frame schema**

```bash
python3 -c "
from perfetto.trace_processor import TraceProcessor
tp = TraceProcessor(trace='/tmp/spike.pftrace')
for r in tp.query('SELECT name FROM sqlite_schema WHERE name LIKE \"%frame%\"'):
    print(r.name)
print('---columns of actual_frame_timeline_slice---')
for r in tp.query('SELECT * FROM actual_frame_timeline_slice LIMIT 1'):
    print(r.__dict__)
"
```
Record the real table + column names for frames and the jank field. Update
`_query_frames()` in Task 7 to match what you find (the plan's version is a
best-effort starting point).

- [ ] **Step 3: Confirm the input schema + `TO_REALTIME` availability**

Query the `android.input` module: confirm the STRING action lives in
`android_input_events.event_action` (vs numeric `android_motion_events.action`),
and verify `SELECT TO_REALTIME(ts) ...` works (and its unit). Also confirm the
device's `adb shell date +%s%N` prints pure nanoseconds (toybox) — the fallback
clock path depends on both. If structured input is absent (user build), confirm
the device-clock fallback path instead.

- [ ] **Step 3b: Confirm per-layer BufferQueue events (multi-source FPS)**

This is what lets fps-test count NON-pipeline sources (SurfaceView, TextureView,
ImageReader, WebView, Flutter, video) and detect single-buffer overwrites. On a
screen with a video/SurfaceView/TextureView, check `frame_slice` FIRST (it pairs
producer/consumer per buffer, with buffer identity — far better than fuzzy slice
search):

```bash
python3 -c "
from perfetto.trace_processor import TraceProcessor
tp = TraceProcessor(trace='/tmp/spike.pftrace')
# Preferred: stdlib frame_slice (layer_name, frame_number, *_time columns).
for r in tp.query('INCLUDE PERFETTO MODULE android.frames.timeline; SELECT * FROM frame_slice LIMIT 1'):
    print('frame_slice cols:', r.__dict__)
for r in tp.query('INCLUDE PERFETTO MODULE android.frames.timeline; SELECT layer_name, COUNT(*) c FROM frame_slice GROUP BY 1 ORDER BY c DESC LIMIT 20'):
    print('layer:', r.layer_name, r.c)
# Only if frame_slice is unavailable, fall back to raw BufferQueue slices:
# SELECT t.name, s.name, COUNT(*) FROM slice s JOIN track t ON s.track_id=t.id
#   WHERE s.name LIKE '%ueue%' OR s.name LIKE '%atch%' OR s.name LIKE '%cquire%' ...
"
```
Record: whether `frame_slice` exists and its real columns; the per-layer list and
which is the app-pipeline layer (to skip, since FrameTimeline covers it); and which
layers are single-buffered TextureViews (the only ones that get overwrite
detection). This replaces the PLACEHOLDER in `_query_raw_buffer_events` and feeds
`_query_buffer_queue_frames(single_buffer_layers=...)`. Reconcile
`_query_buffer_queue_frames` and config `02` against this.

- [ ] **Step 4: Write `docs/spike-notes.md` and commit**

Capture every confirmed fact: exact data-source names, frame/input table+column
names, jank semantics, `TO_REALTIME` behavior, Android version. Tasks 2 and 7
must be reconciled against this file before they are considered done.

```bash
git add docs/spike-notes.md
git commit -m "spike: confirm perfetto config + trace_processor schema on-device"
```

If NO device is available, this task is blocked — flag it to the user rather than
proceeding on unverified assumptions. The pure-logic tasks (1, 2-skeleton, the
unit-tested math) can proceed, but the configs' data sources and the FPS SQL must
be marked PROVISIONAL until the spike runs.

---

## Task 1: Repo skeleton

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `.gitignore`
- Create: `traces/.gitkeep`
- Create: `docs/superpowers/plans/2026-06-17-perfetto-tools.md` (this file — already exists)

- [ ] **Step 1: Write `.gitignore`**

```
# Trace outputs
traces/
!traces/.gitkeep

# Python
__pycache__/
*.pyc
.pytest_cache/

# simpleperf outputs
*.data
perf_report.html

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 2: Write `LICENSE` (Apache 2.0 header pointer)**

Use the full Apache 2.0 text. Get it via:
```bash
curl -sL https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE
```
Verify it starts with `Apache License` and is ~11KB. If curl fails (offline), write the standard Apache 2.0 full text manually — it is public domain boilerplate.

- [ ] **Step 3: Write top-level `README.md`**

```markdown
# Perfetto Tools

A consolidated toolkit for capturing [Perfetto](https://perfetto.dev/) traces on
Android, plus Simpleperf capture and automated swipe-based FPS testing.

## What's inside

| Directory | Purpose |
|---|---|
| [`official/`](official/) | Snapshot of Google's `record_android_trace` script, pinned to a version. |
| [`capture/`](capture/) | Cross-platform one-shot Perfetto capture (Win `.bat` / Mac+Linux `.sh`). |
| [`configs/`](configs/) | 6 prebuilt trace configs for common scenarios (startup, jank, CPU, memory...). |
| [`simpleperf/`](simpleperf/) | Simpleperf capture scripts (standalone, or in parallel with a Perfetto trace). |
| [`fps-test/`](fps-test/) | Automated swipe test that captures a trace and computes FPS / dropped frames. |

## Quick start (capture a trace)

```bash
# Mac / Linux
./capture/capture.sh --config general --time 10

# Windows
capture\capture.bat --config general --time 10
```

See each subdirectory's README for details.

## Requirements

- `adb` on PATH (device connected, USB debugging on)
- Python 3.9+
- For FPS testing: `pip install perfetto` (trace_processor)

## Design

See [`docs/superpowers/specs/2026-06-17-perfetto-tools-design.md`](docs/superpowers/specs/2026-06-17-perfetto-tools-design.md).
```

- [ ] **Step 4: Create `traces/.gitkeep`** (empty file, ensures dir exists but contents ignored)

- [ ] **Step 5: Init git and commit**

```bash
cd "/Users/chris/Code/SmartPerfetto/Perfetto Tools"
git init
git add README.md LICENSE .gitignore traces/.gitkeep docs/
git commit -m "chore: repo skeleton + design doc + implementation plan"
```

Expected: one commit created.

---

## Task 2: Block 3 — Prebuilt configs

Configs are built first because Blocks 2 and 5 depend on them.

**Files:**
- Create: `configs/00_general.pbtx`
- Create: `configs/01_app_startup.pbtx`
- Create: `configs/02_jank_frame.pbtx`
- Create: `configs/03_cpu_sched.pbtx`
- Create: `configs/04_memory.pbtx`
- Create: `configs/05_full.pbtx`
- Create: `configs/README.md`

- [ ] **Step 1: Write `configs/00_general.pbtx`**

This is the everyday default. Buffers sized for a 10–20s trace.

```
# General-purpose trace.
# Datasources: sched, freq, idle, core atrace categories, memory counters.
# Good default for most "what's going on" investigations.
duration_ms: 10000
write_into_file: false
buffers {
    size_kb: 65536
    fill_policy: DISCARD
}
data_sources {
    config {
        name: "linux.ftrace"
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "sched/sched_wakeup_new"
            ftrace_events: "sched/sched_process_exit"
            ftrace_events: "power/cpu_frequency"
            ftrace_events: "power/cpu_idle"
            ftrace_events: "power/suspend_resume"
            # ATrace categories/apps belong INSIDE the ftrace data source.
            # There is no standalone `android.atrace` data source / `atrace_config`.
            atrace_categories: "am"
            atrace_categories: "wm"
            atrace_categories: "gfx"
            atrace_categories: "view"
            atrace_categories: "sched"
            atrace_apps: "*"
        }
    }
}
data_sources {
    config {
        name: "linux.process_stats"
        target_buffer: 0
    }
}
data_sources {
    config {
        name: "linux.system_info"
        target_buffer: 0
    }
}
```

- [ ] **Step 2: Write `configs/01_app_startup.pbtx`**

```
# App startup / cold launch trace.
# Adds detailed ActivityManager + WindowManager + view, with atrace for all apps.
duration_ms: 15000
buffers { size_kb: 65536  fill_policy: DISCARD }
data_sources {
    config {
        name: "linux.ftrace"
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "sched/sched_wakeup_new"
            ftrace_events: "power/cpu_frequency"
            ftrace_events: "power/cpu_idle"
            ftrace_events: "power/suspend_resume"
            ftrace_events: "sched/sched_process_free"
            atrace_categories: "am"
            atrace_categories: "wm"
            atrace_categories: "view"
            atrace_categories: "gfx"
            atrace_categories: "input"
            atrace_categories: "ss"
            atrace_categories: "sched"
            atrace_apps: "*"
        }
    }
}
data_sources { config { name: "linux.process_stats" } }
data_sources { config { name: "linux.system_info" } }
```

- [ ] **Step 3: Write `configs/02_jank_frame.pbtx`** (critical — used by fps-test)

This MUST include the **FrameTimeline** data source (`android.surfaceflinger.frametimeline`,
NOT just the `gfx` atrace category — `gfx` only gives SurfaceFlinger slices, it does
not populate the `actual_frame_timeline_slice` / `android_frames` tables) and, for
precise fling windowing, the structured **input** data source
(`android.input.inputevent`).

> ⚠️ Build requirement: `android.input.inputevent` only records on
> **debuggable / userdebug / eng** builds. On production `user` builds, structured
> input events are unavailable, and `compute_fps.py` falls back to device-clock
> swipe markers (see Task 7/8). FrameTimeline requires **Android 12 (API 31)+**.

```
# Jank / scroll smoothness trace.
# Used by fps-test/run_fps_test.sh.
# Includes: FrameTimeline (android.surfaceflinger.frametimeline), structured input
# (android.input.inputevent), plus gfx/view/wm atrace for context.
# NOTE: the `gfx` atrace category emits per-layer BufferQueue slices
# (dequeue/queue/acquire/latch per surface) — these are what let fps-test count
# NON-pipeline frame sources (SurfaceView, TextureView/SurfaceTexture, ImageReader,
# WebView, Flutter, video) that FrameTimeline alone misses, and detect TextureView
# single-buffer overwrites. The Task 0 spike confirms whether a dedicated
# graphics-frame-event data source is also needed on the target Android version.
duration_ms: 10000
buffers { size_kb: 65536  fill_policy: DISCARD }
data_sources {
    config {
        name: "linux.ftrace"
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "sched/sched_wakeup_new"
            ftrace_events: "power/cpu_frequency"
            ftrace_events: "power/cpu_idle"
            atrace_categories: "gfx"
            atrace_categories: "view"
            atrace_categories: "input"
            atrace_categories: "sched"
            atrace_categories: "wm"
            atrace_apps: "*"
        }
    }
}
data_sources { config { name: "linux.process_stats" } }
data_sources { config { name: "linux.system_info" } }
# FrameTimeline: the authoritative source for actual/expected frame timing + jank.
data_sources { config { name: "android.surfaceflinger.frametimeline" } }
# Structured input events (debuggable/userdebug/eng builds only).
data_sources { config { name: "android.input.inputevent" } }
```

- [ ] **Step 4: Write `configs/03_cpu_sched.pbtx`**

```
# CPU scheduling / thread analysis trace.
# Detailed sched events + frequency + idle. No atrace (kernel-focused).
duration_ms: 10000
buffers { size_kb: 131072  fill_policy: DISCARD }
data_sources {
    config {
        name: "linux.ftrace"
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "sched/sched_wakeup"
            ftrace_events: "sched/sched_wakeup_new"
            ftrace_events: "sched/sched_process_exit"
            ftrace_events: "sched/sched_migrate_task"
            ftrace_events: "sched/sched_stat_runtime"
            ftrace_events: "power/cpu_frequency"
            ftrace_events: "power/cpu_idle"
            ftrace_events: "power/suspend_resume"
        }
    }
}
data_sources { config { name: "linux.process_stats" } }
data_sources { config { name: "linux.system_info" } }
```

- [ ] **Step 5: Write `configs/04_memory.pbtx`**

```
# Memory investigation trace.
# Memory counters (RSS/PSS polled), low-memory-killer events.
duration_ms: 10000
buffers { size_kb: 65536  fill_policy: DISCARD }
data_sources {
    config {
        name: "linux.ftrace"
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "kmem/kmem_mm_page_alloc"
            ftrace_events: "compaction/mm_compaction_begin"
            ftrace_events: "oom/oom_score_adj_update"
        }
    }
}
data_sources {
    config {
        name: "linux.process_stats"
        process_stats_config {
            proc_stats_poll_ms: 1000
            record_thread_names: true
        }
    }
}
data_sources {
    config {
        name: "linux.system_info"
        target_buffer: 0
    }
}
```

- [ ] **Step 6: Write `configs/05_full.pbtx`**

```
# Full / kitchen-sink trace for debugging. Large output. Big buffer.
duration_ms: 10000
buffers { size_kb: 262144  fill_policy: DISCARD }
data_sources {
    config {
        name: "linux.ftrace"
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "sched/sched_wakeup_new"
            ftrace_events: "sched/sched_process_exit"
            ftrace_events: "sched/sched_stat_runtime"
            ftrace_events: "power/cpu_frequency"
            ftrace_events: "power/cpu_idle"
            ftrace_events: "power/suspend_resume"
            ftrace_events: "kmem/kmem_mm_page_alloc"
            atrace_categories: "am"
            atrace_categories: "wm"
            atrace_categories: "gfx"
            atrace_categories: "view"
            atrace_categories: "input"
            atrace_categories: "sched"
            atrace_categories: "binder_driver"
            atrace_categories: "dalvik"
            atrace_apps: "*"
        }
    }
}
data_sources { config { name: "linux.process_stats" } }
data_sources { config { name: "linux.system_info" } }
data_sources { config { name: "android.surfaceflinger.frametimeline" } }
data_sources { config { name: "android.input.inputevent" } }
```

- [ ] **Step 7: Write `configs/README.md`**

```markdown
# Prebuilt Trace Configs

Each `.pbtx` is a text-protobuf Perfetto config for a common Android performance
scenario. Pass its name (or number/keyword) to the capture script.

| File | Scenario | Key datasources | Approx. size (10s) |
|---|---|---|---|
| `00_general.pbtx` | General default | sched, freq, atrace(am/wm/gfx/view), mem | ~5–15 MB |
| `01_app_startup.pbtx` | App cold launch | + detailed am/wm, input, ss | ~10–20 MB |
| `02_jank_frame.pbtx` | Scroll / jank | **frametimeline**, **input** (debuggable), gfx, view | ~10–20 MB |
| `03_cpu_sched.pbtx` | CPU / scheduling | detailed sched, freq, idle | ~20–40 MB |
| `04_memory.pbtx` | Memory | mem counters, lmk, page alloc | ~5–15 MB |
| `05_full.pbtx` | Full debug | everything above, large buffer | ~50–100 MB |

## Notes

- ATrace is configured **inside** `linux.ftrace` via `atrace_categories` /
  `atrace_apps`. There is no standalone `android.atrace` data source — using one
  makes the config fail to parse.
- `02_jank_frame.pbtx` is also used by `fps-test/`. It adds the
  `android.surfaceflinger.frametimeline` data source (authoritative frame timing,
  Android 12+) and the `android.input.inputevent` data source for precise fling
  windowing. Structured input only records on **debuggable / userdebug / eng**
  builds; on `user` builds fps-test falls back to device-clock swipe markers.
- Base atrace configs target Android 10+ (API 29+). On older devices, capture
  falls back to sideloading tracebox automatically (handled by
  `record_android_trace`). FrameTimeline specifically needs API 31+.

## Validating a config

After editing one, check it parses on a connected device:

```bash
adb push configs/00_general.pbtx /data/local/tmp/c.pbtx
adb shell cat /data/local/tmp/c.pbtx \| perfetto --txt -c - -o /data/local/tmp/t.pftrace --background
```

No error output = OK. (Requires a device with `perfetto`; tracebox is sideloaded
on API < 29.)
```

- [ ] **Step 8: Commit**

```bash
git add configs/
git commit -m "feat(configs): add 6 prebuilt trace configs"
```

---

## Task 3: Block 1 — Archive official script

**Files:**
- Create: `official/record_android_trace`
- Create: `official/VERSION`
- Create: `official/README.md`

- [ ] **Step 1: Download the official script snapshot**

```bash
mkdir -p official
curl -fL https://raw.githubusercontent.com/google/perfetto/master/tools/record_android_trace -o official/record_android_trace
chmod +x official/record_android_trace
```

Expected: file ~30–40KB, starts with `#!/usr/bin/env python3`.

- [ ] **Step 2: Record the version**

Get the current commit hash and date:

```bash
COMMIT=$(curl -fsSL https://api.github.com/repos/google/perfetto/commits/master | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'])")
DATE=$(date -u +%Y-%m-%d)
cat > official/VERSION <<EOF
source: https://raw.githubusercontent.com/google/perfetto/master/tools/record_android_trace
repo:  https://github.com/google/perfetto
commit: ${COMMIT}
snapshot_date: ${DATE}

To update:
  curl -fL https://raw.githubusercontent.com/google/perfetto/master/tools/record_android_trace -o official/record_android_trace
  chmod +x official/record_android_trace
  # then refresh the commit hash and date in this file
EOF
```

If the GitHub API call fails (rate limit / offline), set `commit: <unknown-fetch-manually>` and fill the date; do NOT block on it.

- [ ] **Step 3: Write `official/README.md`**

```markdown
# Official script archive

This directory holds a pinned snapshot of Google's `record_android_trace` script
from the [perfetto](https://github.com/google/perfetto) repo. It is used at
runtime by `../capture/` and `../fps-test/`.

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

## License

Upstream is Apache 2.0 (The Android Open Source Project). The script header
retains its original license notice.
```

- [ ] **Step 4: Smoke-test the script (no device needed)**

```bash
python3 official/record_android_trace --help
```

Expected: prints usage with `-o`, `-t`, `-c`, `--serial`, `--no-open` options. Exit 0.

- [ ] **Step 5: Commit**

```bash
git add official/
git commit -m "feat(official): archive record_android_trace snapshot + VERSION"
```

---

## Task 4: Block 2 — Capture script (core + config resolver, TDD)

The config-name → path resolution logic is pure and testable. Build it TDD first,
then wire it into the full capture flow.

**Files:**
- Create: `capture/perfetto_capture.py`
- Create: `tests/test_config_resolver.py`

- [ ] **Step 1: Write the failing test for config resolution**

`tests/test_config_resolver.py`:

```python
import os
import sys
import pytest

# Make capture/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'capture'))
from perfetto_capture import resolve_config, list_configs, apply_duration, ConfigError


CONFIGS_DIR = os.path.join(os.path.dirname(__file__), '..', 'configs')


def test_resolve_by_full_number_prefix():
    # "00" → 00_general.pbtx
    p = resolve_config("00", CONFIGS_DIR)
    assert os.path.basename(p) == "00_general.pbtx"


def test_resolve_by_keyword():
    # "jank" → 02_jank_frame.pbtx (keyword match in filename)
    p = resolve_config("jank", CONFIGS_DIR)
    assert os.path.basename(p) == "02_jank_frame.pbtx"


def test_resolve_case_insensitive():
    p = resolve_config("JANK", CONFIGS_DIR)
    assert os.path.basename(p) == "02_jank_frame.pbtx"


def test_resolve_returns_absolute_path():
    p = resolve_config("general", CONFIGS_DIR)
    assert os.path.isabs(p)


def test_unknown_name_raises():
    with pytest.raises(ConfigError) as exc:
        resolve_config("nonexistent", CONFIGS_DIR)
    assert "nonexistent" in str(exc.value)


def test_ambiguous_match_raises_with_candidates():
    # "0" matches all six files → ambiguous
    with pytest.raises(ConfigError):
        resolve_config("0", CONFIGS_DIR)


def test_list_configs_returns_all():
    names = list_configs(CONFIGS_DIR)
    assert len(names) == 6
    assert "00_general" in names
    assert "05_full" in names


def test_resolve_strips_pbtx_extension_in_input():
    # User passes the full filename
    p = resolve_config("02_jank_frame.pbtx", CONFIGS_DIR)
    assert os.path.basename(p) == "02_jank_frame.pbtx"


def test_apply_duration_replaces_existing():
    text = "duration_ms: 10000\nbuffers { size_kb: 1024 }\n"
    out = apply_duration(text, 8)
    assert "duration_ms: 8000" in out
    assert "duration_ms: 10000" not in out
    # Only the duration line changes; the rest is untouched.
    assert "buffers { size_kb: 1024 }" in out


def test_apply_duration_inserts_when_missing():
    text = "buffers { size_kb: 1024 }\n"
    out = apply_duration(text, 5)
    assert out.startswith("duration_ms: 5000\n")


def test_apply_duration_only_first_occurrence():
    # A duration_ms inside a nested datasource must NOT be the one rewritten.
    text = "duration_ms: 10000\ndata_sources { config { duration_ms: 999 } }\n"
    out = apply_duration(text, 3)
    assert "duration_ms: 3000" in out
    assert "duration_ms: 999" in out  # nested one preserved


def test_apply_duration_nested_only_inserts_top_level():
    # No TOP-LEVEL duration_ms, only an INDENTED nested one on its own line
    # (the realistic multi-line shape). Must insert at top, NOT touch the nested.
    text = (
        "buffers { size_kb: 1024 }\n"
        "data_sources {\n"
        "    config {\n"
        "        duration_ms: 999\n"   # indented → not top-level
        "    }\n"
        "}\n"
    )
    out = apply_duration(text, 4)
    assert out.startswith("duration_ms: 4000\n")
    assert "        duration_ms: 999" in out  # nested line untouched


def test_apply_duration_rejects_nonpositive():
    with pytest.raises(ConfigError):
        apply_duration("duration_ms: 1000\n", 0)
    with pytest.raises(ConfigError):
        apply_duration("duration_ms: 1000\n", -5)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "/Users/chris/Code/SmartPerfetto/Perfetto Tools"
python3 -m pytest tests/test_config_resolver.py -v
```

Expected: collection error / `ModuleNotFoundError: No module named 'perfetto_capture'` or `ImportError: cannot import name 'resolve_config'`.

- [ ] **Step 3: Implement the resolver + full capture script**

`capture/perfetto_capture.py`:

```python
#!/usr/bin/env python3
"""Cross-platform Perfetto capture entry.

Resolves a config short-name to a .pbtx path, checks the device, then invokes
the archived official record_android_trace script. One responsibility: produce
a trace file.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class ConfigError(Exception):
    pass


# Directory layout: capture/ sits next to configs/ and official/.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_CONFIGS_DIR = _REPO_ROOT / "configs"
_OFFICIAL = _REPO_ROOT / "official" / "record_android_trace"
_TRACES_DIR = _REPO_ROOT / "traces"


def list_configs(configs_dir=None):
    configs_dir = Path(configs_dir or _CONFIGS_DIR)
    names = []
    for f in sorted(configs_dir.glob("*.pbtx")):
        names.append(f.stem)  # "02_jank_frame"
    return names


def resolve_config(name, configs_dir=None):
    """Resolve a user-supplied short name to an absolute .pbtx path.

    Matching rules (first wins):
      1. Exact filename match ("02_jank_frame.pbtx").
      2. Exact stem match ("02_jank_frame").
      3. Case-insensitive substring match of `name` in the stem.
    Ambiguous substring matches (>1 candidate) raise ConfigError listing them.
    No match raises ConfigError.
    """
    configs_dir = Path(configs_dir or _CONFIGS_DIR)
    name = name.strip()
    lname = name.lower().removesuffix(".pbtx")

    all_files = sorted(configs_dir.glob("*.pbtx"))
    if not all_files:
        raise ConfigError(f"No .pbtx configs found in {configs_dir}")

    # 1. Exact filename
    for f in all_files:
        if f.name == name:
            return str(f.resolve())

    # 2. Exact stem
    for f in all_files:
        if f.stem.lower() == lname:
            return str(f.resolve())

    # 3. Substring (case-insensitive) — but only if unambiguous
    candidates = [f for f in all_files if lname in f.stem.lower()]
    if len(candidates) == 1:
        return str(candidates[0].resolve())
    if len(candidates) > 1:
        cands = ", ".join(f.stem for f in candidates)
        raise ConfigError(
            f"Ambiguous config name '{name}'. Matches: {cands}. "
            f"Be more specific."
        )

    available = ", ".join(f.stem for f in all_files)
    raise ConfigError(
        f"Unknown config '{name}'. Available: {available}"
    )


def apply_duration(config_text, seconds):
    """Return config_text with its top-level duration_ms set to seconds*1000.

    record_android_trace ignores -t/-b/-a when a full -c/--config is supplied
    (those short flags are 'only when not using -c'). So to honor --time we
    rewrite the config's own duration_ms instead of passing -t. Pure + testable.
    """
    if float(seconds) <= 0:
        raise ConfigError(f"--time must be > 0 seconds, got {seconds!r}")
    ms = int(round(float(seconds) * 1000))
    # Match a TOP-LEVEL duration_ms only — i.e. at column 0 (no leading
    # whitespace). Nested fields inside data_sources{...} are always indented, so
    # `^duration_ms` (no \s*) will not touch them even on their own line.
    if re.search(r"(?m)^duration_ms\s*:", config_text):
        return re.sub(
            r"(?m)^duration_ms\s*:\s*\d+",
            f"duration_ms: {ms}",
            config_text,
            count=1,
        )
    return f"duration_ms: {ms}\n{config_text}"


def materialize_config(config_path, seconds):
    """Write a temp .pbtx with duration_ms overridden; return its path.

    Caller is responsible for cleanup. If `seconds` is falsy, returns the
    original path unchanged (config's own duration_ms wins).
    """
    if not seconds:
        return config_path
    text = Path(config_path).read_text()
    fd, tmp = tempfile.mkstemp(suffix=".pbtx", prefix="capture_")
    with os.fdopen(fd, "w") as f:
        f.write(apply_duration(text, seconds))
    return tmp


def check_adb_device(serial=None):
    """Ensure exactly one usable device (or the one named by --serial)."""
    try:
        out = subprocess.run(
            ["adb", "devices"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()[1:]
    except FileNotFoundError:
        sys.exit(
            "ERROR: 'adb' not found on PATH. Install Android Platform Tools:\n"
            "  https://developer.android.com/studio/releases/platform-tools"
        )

    devices = [ln.split()[0] for ln in out if ln.strip() and "device" in ln]
    if serial:
        if serial not in devices:
            sys.exit(f"ERROR: device --serial {serial} not connected/authorized.\n"
                     f"adb devices says: {devices or 'none'}")
        return serial
    if len(devices) == 0:
        sys.exit("ERROR: no device connected. Run `adb devices` and authorize.")
    if len(devices) > 1:
        sys.exit(f"ERROR: multiple devices ({devices}). Pass --serial <id>.")
    return devices[0]


def run_capture(args):
    if args.list_configs:
        print("Available configs:")
        for n in list_configs():
            print(f"  {n}")
        return 0

    config_path = resolve_config(args.config)

    # Default output: traces/<timestamp>_<configstem>.perfetto-trace
    if args.output:
        out = args.output
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        stem = Path(config_path).stem
        _TRACES_DIR.mkdir(exist_ok=True)
        out = str(_TRACES_DIR / f"{ts}_{stem}.perfetto-trace")

    # Print what we resolved before the device check, so a no-device smoke test
    # still shows the wiring is correct up to adb.
    print(f"[capture] config : {config_path}")
    if args.time:
        print(f"[capture] duration override -> {args.time}s")
    print(f"[capture] output : {out}")

    check_adb_device(args.serial)

    # --time is honored by rewriting duration_ms into a temp config, because
    # record_android_trace ignores -t when -c is given. Falls through to the
    # config's own duration_ms when --time is absent.
    run_config = materialize_config(config_path, args.time)
    is_temp = run_config != config_path

    cmd = [
        sys.executable, str(_OFFICIAL),
        "-c", run_config,
        "-o", out,
    ]
    if args.serial:
        cmd += ["-s", args.serial]
    if args.no_open:
        cmd += ["--no-open"]

    print(f"[capture] running: {' '.join(cmd)}")
    try:
        return subprocess.call(cmd)
    finally:
        if is_temp:
            try:
                os.remove(run_config)
            except OSError:
                pass


def build_parser():
    p = argparse.ArgumentParser(
        prog="perfetto_capture",
        description="Capture a Perfetto trace on a connected Android device.",
        epilog="Run with --list-configs to see available config names.",
    )
    p.add_argument("-c", "--config", help="Config short name (e.g. jank, general, 02)")
    p.add_argument("-t", "--time", help="Trace duration in seconds, e.g. 10 (overrides the config's duration_ms)")
    p.add_argument("-o", "--output", help="Output .perfetto-trace path (default: traces/<ts>_<cfg>)")
    p.add_argument("-s", "--serial", help="ADB device serial (when multiple connected)")
    p.add_argument("--no-open", action="store_true", help="Do not open the trace in a browser")
    p.add_argument("--list-configs", action="store_true", help="List available config names and exit")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.list_configs and not args.config:
        # `official` supports zero-config short-form (events), but our wrapper
        # requires a config to keep things simple.
        print("ERROR: --config is required (or use --list-configs).", file=sys.stderr)
        return 2
    try:
        return run_capture(args)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

Note: this project targets **Python 3.9+** (uses `str.removesuffix`, `dataclasses`,
and `from __future__ import annotations`). If you ever need 3.7–3.8, replace
`removesuffix` with:
```python
lname = lname[:-5] if lname.endswith(".pbtx") else lname
```
(Below 3.7 there is no `dataclasses`, so 3.9+ is the supported floor.)

- [ ] **Step 4: Run the unit tests to verify they pass**

```bash
python3 -m pytest tests/test_config_resolver.py -v
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add capture/perfetto_capture.py tests/test_config_resolver.py
git commit -m "feat(capture): add perfetto_capture core with config resolver + tests"
```

---

## Task 5: Block 2 — Platform entry wrappers

**Files:**
- Create: `capture/capture.sh`
- Create: `capture/capture.bat`
- Create: `capture/README.md`

- [ ] **Step 1: Write `capture/capture.sh`**

```bash
#!/usr/bin/env bash
# Mac / Linux entry: forwards all args to perfetto_capture.py.
# Resolve repo root relative to this script so it works from any CWD.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/perfetto_capture.py" "$@"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x capture/capture.sh
```

- [ ] **Step 3: Write `capture/capture.bat`**

```bat
@echo off
REM Windows entry: forwards all args to perfetto_capture.py.
REM %~dp0 = directory of this script, with trailing backslash.
setlocal
set "SCRIPT_DIR=%~dp0"
python "%SCRIPT_DIR%perfetto_capture.py" %*
exit /b %ERRORLEVEL%
```

- [ ] **Step 4: Write `capture/README.md`**

```markdown
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

## Requirements

- `adb` on PATH, one device connected & authorized.
- Python 3.9+.
- The archived official script at `../official/record_android_trace` (included).
```

- [ ] **Step 5: Smoke test (no device needed)**

```bash
./capture/capture.sh --list-configs
./capture/capture.sh --config jank --time 1 --no-open 2>&1 | head -5  # will fail at device check, that's fine
```

Expected: first prints 6 config names; second prints the config/output lines then errors at the device check (proves wiring works up to adb).

- [ ] **Step 6: Lint shell script**

```bash
shellcheck capture/capture.sh
```

Expected: no output (clean). If shellcheck isn't installed, skip and note it.

- [ ] **Step 7: Commit**

```bash
git add capture/capture.sh capture/capture.bat capture/README.md
git commit -m "feat(capture): add .sh/.bat entry wrappers + README"
```

---

## Task 6: Block 4 — Simpleperf scripts

**Files:**
- Create: `simpleperf/simpleperf_only.sh`
- Create: `simpleperf/simpleperf_with_trace.sh`
- Create: `simpleperf/README.md`

- [ ] **Step 1: Write `simpleperf/simpleperf_only.sh`**

```bash
#!/usr/bin/env bash
# Capture a standalone simpleperf CPU profile for an Android app's main process.
#
# Usage: simpleperf_only.sh <package_name> [duration_sec]
#   <package_name>  e.g. com.example.app
#   [duration_sec]  default 10
#
# Requires: app is debuggable (or device is rooted). Produces perf.data + pulls it.
set -euo pipefail

PKG="${1:?Usage: $0 <package_name> [duration_sec]}"
DURATION="${2:-10}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${REPO_ROOT}/traces"
mkdir -p "${OUT_DIR}"

TS="$(date +%Y%m%d_%H%M%S)"
REMOTE="/data/local/tmp/perf_${TS}.data"
LOCAL="${OUT_DIR}/simpleperf_${TS}.data"

echo "[simpleperf] package : ${PKG}"
echo "[simpleperf] duration: ${DURATION}s"

# Find the app's main pid.
PID="$(adb shell pidof "${PKG}" | tr -d '\r' | head -n1 || true)"
if [[ -z "${PID}" ]]; then
  echo "ERROR: no running process for ${PKG}. Launch the app first." >&2
  exit 1
fi
echo "[simpleperf] pid     : ${PID}"

echo "[simpleperf] recording..."
# -g: callchain based; --trace-offcpu: include off-cpu time (optional but useful).
if ! adb shell simpleperf record -p "${PID}" -g --duration "${DURATION}" -o "${REMOTE}"; then
  echo "ERROR: simpleperf record failed. Is the app debuggable? (or run 'adb root')" >&2
  exit 1
fi

echo "[simpleperf] pulling -> ${LOCAL}"
adb pull "${REMOTE}" "${LOCAL}"
adb shell rm -f "${REMOTE}"

echo ""
echo "Done: ${LOCAL}"
echo "View with: python3 -m simpleperf_report ${LOCAL}"
echo "  (or use simpleperf's report_html.py from the NDK to get an HTML report)"
```

- [ ] **Step 2: Write `simpleperf/simpleperf_with_trace.sh`**

```bash
#!/usr/bin/env bash
# Capture simpleperf AND a Perfetto trace in parallel.
#
# Usage: simpleperf_with_trace.sh <package_name> [duration_sec]
#
# simpleperf runs for the full duration in the background; a Perfetto trace
# (config 03_cpu_sched) is captured for the same window. Both land in traces/.
set -euo pipefail

PKG="${1:?Usage: $0 <package_name> [duration_sec]}"
DURATION="${2:-10}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${REPO_ROOT}/traces"
CAPTURE="${REPO_ROOT}/capture/capture.sh"
mkdir -p "${OUT_DIR}"

TS="$(date +%Y%m%d_%H%M%S)"
REMOTE="/data/local/tmp/perf_${TS}.data"
LOCAL="${OUT_DIR}/simpleperf_${TS}.data"
TRACE="${OUT_DIR}/${TS}_cpu.perfetto-trace"

PID="$(adb shell pidof "${PKG}" | tr -d '\r' | head -n1 || true)"
if [[ -z "${PID}" ]]; then
  echo "ERROR: no running process for ${PKG}. Launch the app first." >&2
  exit 1
fi

echo "[combined] package: ${PKG}  pid: ${PID}  duration: ${DURATION}s"

# The two windows are only approximately aligned: simpleperf starts first, then
# capture has its own adb/tracebox startup latency. Good enough for "roughly the
# same window"; for tight correlation use Perfetto's linux.perf in one trace.

# Cleanup runs even if capture fails under `set -e`, so we never orphan the
# background simpleperf or leave the remote perf.data behind.
SP_PID=""
cleanup() {
  if [[ -n "${SP_PID}" ]] && kill -0 "${SP_PID}" 2>/dev/null; then
    wait "${SP_PID}" 2>/dev/null || true
  fi
  adb shell rm -f "${REMOTE}" 2>/dev/null || true
}
trap cleanup EXIT

# 1. simpleperf in the background.
echo "[combined] starting simpleperf (background)..."
adb shell simpleperf record -p "${PID}" -g --duration "${DURATION}" -o "${REMOTE}" &
SP_PID=$!

# 2. Perfetto trace in the foreground, same duration. --no-open so it returns.
echo "[combined] starting perfetto trace (${DURATION}s)..."
"${CAPTURE}" --config cpu_sched --time "${DURATION}" --output "${TRACE}" --no-open

# 3. Wait for simpleperf to finish.
echo "[combined] waiting for simpleperf to finish..."
wait "${SP_PID}" || {
  echo "ERROR: simpleperf failed. App debuggable? (or 'adb root')" >&2
  exit 1
}
SP_PID=""  # reaped; stop the trap from re-waiting

adb pull "${REMOTE}" "${LOCAL}"
adb shell rm -f "${REMOTE}"

echo ""
echo "Done."
echo "  simpleperf: ${LOCAL}"
echo "  trace     : ${TRACE}"
echo ""
echo "Note: Perfetto can also capture CPU sampling directly via the 'linux.perf'"
echo "datasource in a single trace (avoids double perf_event_open overhead)."
echo "This script is for when you specifically need simpleperf's native output."
```

- [ ] **Step 3: Write `simpleperf/README.md`**

```markdown
# Simpleperf Capture

Two independent shell scripts.

## simpleperf_only.sh

Standalone CPU profile of an app's main process.

```bash
./simpleperf/simpleperf_only.sh com.example.app 10
```

Outputs `traces/simpleperf_<ts>.data`. View with simpleperf's `report_html.py`
(ships with the Android NDK).

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

## Alternative: Perfetto's built-in CPU profiling

Perfetto itself can capture CPU callstack sampling via the `linux.perf` datasource
in a single trace, which avoids running two tools. Use that if you don't
specifically need simpleperf's `.data` format / `report_html.py`. See the
[Perfetto CPU profiling docs](https://perfetto.dev/docs/getting-started/cpu-profiling).
```

- [ ] **Step 4: Lint**

```bash
shellcheck simpleperf/simpleperf_only.sh simpleperf/simpleperf_with_trace.sh
chmod +x simpleperf/simpleperf_only.sh simpleperf/simpleperf_with_trace.sh
```

Expected: no shellcheck output. (Skip shellcheck if not installed.)

- [ ] **Step 5: Commit**

```bash
git add simpleperf/
git commit -m "feat(simpleperf): add standalone + with-trace capture scripts"
```

---

## Task 7: Block 5 — FPS compute (TDD on the math)

The FPS/jank math is pure and the highest-risk logic. Build it TDD with synthetic
data, decoupled from trace_processor. It covers three things:
  - FPS / drop-rate over fling windows (presented frames only);
  - **multi-source** aggregation + per-source breakdown — FrameTimeline covers the
    app surfaces it tracks, so SurfaceView / TextureView / ImageReader / WebView /
    Flutter / video are counted from per-layer BufferQueue events (excluding layers
    FrameTimeline already covered) and never merged into one misleading number;
  - **TextureView single-buffer overwrite** detection — a queued buffer overwritten
    before being consumed was never displayed, so it counts as a dropped frame.
The trace_processor integration (FrameTimeline + per-layer BufferQueue) comes in
Task 8 and is pinned to real schema by the Task 0 spike.

**Files:**
- Create: `fps-test/compute_fps.py` (pure math: compute_fps_from_frames,
  summarize_by_source, detect_overwrite_drops, buffer_events_to_frames)
- Create: `tests/test_compute_fps_math.py`

- [ ] **Step 1: Write failing tests for FPS math**

`tests/test_compute_fps_math.py`:

```python
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fps-test'))
from compute_fps import (
    Frame, FlingWindow, BufferEvent, compute_fps_from_frames, summarize_windows,
    summarize_by_source, detect_overwrite_drops, buffer_events_to_frames, FpsReport,
)


def frame(ts_ns, dur_ns, dropped=False):
    return Frame(ts=ts_ns, dur=dur_ns, dropped=dropped)


def test_sixty_fps_one_second_window():
    # 60 frames of 16.67ms each within a 1s fling window, none dropped.
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 16_666_666, 16_666_666) for i in range(60)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.overall_fps == pytest.approx(60.0, abs=0.5)
    assert rep.total_frames == 60
    assert rep.dropped_frames == 0


def test_dropped_frames_counted():
    # 60 nominal slots; 5 dropped. FPS computed over presented frames only
    # (standard: dropped frames don't contribute to on-screen refresh rate).
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 16_666_666, 16_666_666, dropped=(i % 12 == 0))
              for i in range(60)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.total_frames == 60
    assert rep.dropped_frames == 5
    assert rep.presented_frames == 55


def test_frames_outside_window_excluded():
    # Two windows; frames between them excluded.
    w1 = FlingWindow(start_ns=0,       end_ns=500_000_000)
    w2 = FlingWindow(start_ns=800_000_000, end_ns=1_300_000_000)
    # 10 frames in w1, 0 in gap, 10 in w2
    frames = ([frame(i * 50_000_000, 16_666_666) for i in range(10)] +
              [frame(650_000_000, 16_666_666)] +   # in the gap → excluded
              [frame(800_000_000 + i * 50_000_000, 16_666_666) for i in range(10)])
    rep = compute_fps_from_frames(frames, [w1, w2])
    assert rep.total_frames == 20  # gap frame excluded


def test_empty_window_zero_fps():
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    rep = compute_fps_from_frames([], [w])
    assert rep.overall_fps == 0.0
    assert rep.total_frames == 0


def test_drop_rate_percent():
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 16_666_666, 16_666_666, dropped=(i < 10))
              for i in range(100)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.drop_rate == pytest.approx(10.0, abs=0.1)


def test_per_window_breakdown():
    w1 = FlingWindow(start_ns=0,           end_ns=500_000_000)
    w2 = FlingWindow(start_ns=600_000_000, end_ns=1_100_000_000)
    frames = ([frame(i * 50_000_000, 16_666_666) for i in range(10)] +
              [frame(600_000_000 + i * 50_000_000, 16_666_666) for i in range(10)])
    rep = compute_fps_from_frames(frames, [w1, w2])
    assert len(rep.windows) == 2
    assert rep.windows[0].frame_count == 10
    assert rep.windows[1].frame_count == 10


def test_summarize_windows_handles_drops():
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 16_666_666, 16_666_666, dropped=(i % 10 == 0))
              for i in range(60)]
    per_window = summarize_windows(frames, [w])
    assert len(per_window) == 1
    assert per_window[0].dropped == 6


def test_janky_frames_count_toward_fps():
    # A janky frame WAS presented (just late) → it still counts toward FPS, but is
    # reported separately. Dropped frames do not. This guards the jank/drop split.
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [Frame(i * 16_666_666, 16_666_666, janky=(i % 4 == 0))
              for i in range(60)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.janky_frames == 15
    assert rep.dropped_frames == 0
    assert rep.presented_frames == 60
    assert rep.overall_fps == pytest.approx(60.0, abs=0.5)  # jank does NOT lower FPS


# --- Multiple frame sources (SurfaceView / TextureView / video / ...) ---

def test_per_source_breakdown_not_merged():
    # Two sources active in the same window must be reported separately.
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    pipeline = [Frame(i * 16_666_666, 16_666_666, source="app-pipeline")
                for i in range(60)]
    video = [Frame(i * 33_333_333, 33_333_333, source="SurfaceView[video]")
             for i in range(30)]
    rep = compute_fps_from_frames(pipeline + video, [w])
    by = {s.source: s for s in rep.by_source}
    assert set(by) == {"app-pipeline", "SurfaceView[video]"}
    assert by["app-pipeline"].fps == pytest.approx(60.0, abs=0.5)
    assert by["SurfaceView[video]"].fps == pytest.approx(30.0, abs=0.5)
    # Aggregate still counts both.
    assert rep.total_frames == 90


# --- TextureView single-buffer overwrite = dropped ---

def test_overwrite_drop_detected():
    # queue, queue (overwrite!), acquire, queue, acquire → exactly 1 overwrite.
    ev = [
        BufferEvent(10, "TextureView[x]", "queue"),
        BufferEvent(20, "TextureView[x]", "queue"),    # overwrites the ts=10 buffer
        BufferEvent(25, "TextureView[x]", "acquire"),
        BufferEvent(40, "TextureView[x]", "queue"),
        BufferEvent(45, "TextureView[x]", "acquire"),
    ]
    drops = detect_overwrite_drops(ev, "TextureView[x]")
    assert len(drops) == 1
    assert drops[0].ts == 10
    assert drops[0].dropped is True
    assert drops[0].source == "TextureView[x]"


def test_overwrite_none_when_each_consumed():
    ev = [
        BufferEvent(10, "L", "queue"), BufferEvent(15, "L", "latch"),
        BufferEvent(20, "L", "queue"), BufferEvent(25, "L", "latch"),
    ]
    assert detect_overwrite_drops(ev, "L") == []


def test_buffer_events_to_frames_counts_presented_and_dropped():
    ev = [
        BufferEvent(10, "L", "queue"),
        BufferEvent(20, "L", "queue"),   # overwrite → ts=10 dropped
        BufferEvent(25, "L", "acquire"), # ts=20 presented
        BufferEvent(40, "L", "queue"),
        BufferEvent(45, "L", "latch"),   # ts=40 presented
    ]
    frames = buffer_events_to_frames(ev, "L")
    presented = [f for f in frames if not f.dropped]
    dropped = [f for f in frames if f.dropped]
    assert len(presented) == 2
    assert len(dropped) == 1
    assert all(f.source == "L" for f in frames)
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_compute_fps_math.py -v
```

Expected: ImportError for `Frame`, `compute_fps_from_frames`, etc.

- [ ] **Step 3: Implement the math**

`fps-test/compute_fps.py`:

```python
#!/usr/bin/env python3
"""Compute FPS and dropped-frame stats from a Perfetto trace.

This module has two layers:
  1. Pure math: Frame/FlingWindow/BufferEvent dataclasses, compute_fps_from_frames(),
     per-source aggregation, and single-buffer overwrite detection. Fully
     unit-tested with synthetic data (no trace_processor needed).
  2. trace_processor integration: load a real .perfetto-trace, extract fling
     windows from input events, gather frames from FrameTimeline AND per-layer
     BufferQueue events (so non-pipeline sources like SurfaceView / TextureView /
     ImageReader / WebView / Flutter / video are counted), call the math.

The math layer is run by tests/test_compute_fps_math.py. The integration layer
is exercised manually against a real device (and pinned to real schema in the
Task 0 spike).
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Frame:
    """One produced frame from ONE frame source (layer/surface).

    ts  : presentation/queue timestamp in nanoseconds.
    dur : frame duration in nanoseconds (Wall budget / actual).
    dropped : True if the frame was NEVER presented on screen — a single-buffer
              overwrite (TextureView) or a FrameTimeline 'Dropped Frame'
              present_type. Dropped frames do NOT count toward FPS.
    janky : True if the frame WAS presented but missed its deadline (FrameTimeline
            jank_type != 'None'). A janky frame still refreshed the screen, so it
            counts toward FPS — jank is a separate quality signal from drop.
    source : the frame-production source this frame belongs to (one per layer).
             FrameTimeline covers each app surface layer; SurfaceView,
             TextureView/SurfaceTexture, ImageReader, WebView, Flutter and video
             each render to their OWN layer and must be counted separately.
    """
    ts: int
    dur: int
    dropped: bool = False
    janky: bool = False
    source: str = "app-pipeline"


@dataclass
class BufferEvent:
    """One BufferQueue lifecycle event on a single layer, used to count frames
    for non-pipeline sources and to detect single-buffer overwrites.

    kind : 'queue'   — producer made a buffer ready (a candidate frame),
           'acquire' — consumer (SurfaceFlinger) took it,
           'latch'   — consumer latched it for display.
    'acquire'/'latch' both count as "consumed". A 'queue' that is followed by
    another 'queue' on the same layer with NO consume in between = an overwrite.
    """
    ts: int
    layer: str
    kind: str  # 'queue' | 'acquire' | 'latch'


@dataclass
class FlingWindow:
    """A time range [start_ns, end_ns) corresponding to one fling gesture
    (from finger-up ACTION_UP to the next finger-down ACTION_DOWN)."""
    start_ns: int
    end_ns: int


@dataclass
class WindowStat:
    index: int
    start_ns: int
    end_ns: int
    frame_count: int
    dropped: int
    janky: int
    fps: float


@dataclass
class SourceStat:
    source: str
    frame_count: int
    presented: int
    dropped: int
    janky: int
    fps: float


@dataclass
class FpsReport:
    # NOTE: overall_fps is the SUM of produced frames/s across every source. With
    # multiple sources (e.g. a 60fps list over a 30fps video) it is a throughput
    # number, NOT the screen refresh rate — read the per-source breakdown for
    # meaningful FPS. Kept for backward-compat; reported as "produced frames/s".
    overall_fps: float
    total_frames: int
    presented_frames: int
    dropped_frames: int
    janky_frames: int
    drop_rate: float  # percent
    windows: list  # list[WindowStat]
    by_source: list = field(default_factory=list)  # list[SourceStat]


def _frame_in_window(f: Frame, w: FlingWindow) -> bool:
    return w.start_ns <= f.ts < w.end_ns


def summarize_windows(frames, windows):
    """Per-window frame/drop counts. FPS over presented frames only."""
    out = []
    for i, w in enumerate(windows):
        in_w = [f for f in frames if _frame_in_window(f, w)]
        presented = [f for f in in_w if not f.dropped]
        dropped = sum(1 for f in in_w if f.dropped)
        janky = sum(1 for f in in_w if f.janky and not f.dropped)
        span_s = (w.end_ns - w.start_ns) / 1e9
        fps = (len(presented) / span_s) if span_s > 0 else 0.0
        out.append(WindowStat(
            index=i, start_ns=w.start_ns, end_ns=w.end_ns,
            frame_count=len(in_w), dropped=dropped, janky=janky, fps=fps,
        ))
    return out


def summarize_by_source(frames, windows):
    """Per-frame-source breakdown over the union of windows. Each source
    (layer/surface) is reported separately so multiple producers — SurfaceView,
    TextureView, ImageReader, WebView, Flutter, video — are never merged into one
    misleading number."""
    total_span_s = sum((w.end_ns - w.start_ns) for w in windows) / 1e9
    in_any = [f for f in frames if any(_frame_in_window(f, w) for w in windows)]
    sources = {}
    for f in in_any:
        sources.setdefault(f.source, []).append(f)
    out = []
    for source in sorted(sources):
        fs = sources[source]
        dropped = sum(1 for f in fs if f.dropped)
        janky = sum(1 for f in fs if f.janky and not f.dropped)
        presented = len(fs) - dropped
        fps = (presented / total_span_s) if total_span_s > 0 else 0.0
        out.append(SourceStat(
            source=source, frame_count=len(fs),
            presented=presented, dropped=dropped, janky=janky, fps=fps,
        ))
    return out


def detect_overwrite_drops(events, layer):
    """Single-buffer overwrite detection for ONE layer that the Task 0 spike has
    confirmed is single-buffered / async-drop (e.g. a TextureView/SurfaceTexture).

    A producer 'queue' whose buffer is overwritten by the NEXT 'queue' without
    any 'acquire'/'latch' (consume) in between was never displayed → a dropped
    frame. Returns synthetic dropped Frames (dur=0) tagged with `layer`, one per
    overwrite, timestamped at the overwritten queue.

    ⚠️ Only valid for confirmed single-buffer layers: on a normal double/triple-
    buffered layer, two queues without an intervening acquire is NOT proof of an
    overwrite (the consumer may acquire both later). Do not apply this blindly to
    every layer — gate it on the spike's single-buffer layer list.

    Pure sequence logic — unit-tested with synthetic event lists, no device.
    """
    seq = sorted((e for e in events if e.layer == layer), key=lambda e: e.ts)
    drops = []
    pending_queue_ts = None  # a queued-but-not-yet-consumed buffer
    for e in seq:
        if e.kind == "queue":
            if pending_queue_ts is not None:
                # Previous queued buffer was overwritten before being consumed.
                drops.append(Frame(ts=pending_queue_ts, dur=0,
                                   dropped=True, source=layer))
            pending_queue_ts = e.ts
        elif e.kind in ("acquire", "latch"):
            pending_queue_ts = None  # consumed; safe
    return drops


def buffer_events_to_frames(events, layer):
    """Turn a layer's BufferQueue events into Frames: each consumed buffer
    (queue → acquire/latch) is one presented frame; each overwrite is one
    dropped frame. Used for non-pipeline sources that FrameTimeline misses."""
    seq = sorted((e for e in events if e.layer == layer), key=lambda e: e.ts)
    frames = []
    pending = None
    for e in seq:
        if e.kind == "queue":
            if pending is not None:
                frames.append(Frame(ts=pending, dur=0, dropped=True, source=layer))
            pending = e.ts
        elif e.kind in ("acquire", "latch"):
            if pending is not None:
                frames.append(Frame(ts=pending, dur=0, dropped=False, source=layer))
                pending = None
    return frames


def compute_fps_from_frames(frames, windows) -> FpsReport:
    """Compute FPS over the union of fling windows, aggregated across ALL frame
    sources plus a per-source breakdown.

    FPS = presented frames / union-of-window-seconds.
    Dropped frames are counted but do not contribute to FPS (a dropped frame
    is not an on-screen refresh).
    """
    in_any = [f for f in frames if any(_frame_in_window(f, w) for w in windows)]
    total = len(in_any)
    dropped = sum(1 for f in in_any if f.dropped)
    janky = sum(1 for f in in_any if f.janky and not f.dropped)
    presented = total - dropped

    total_span_s = sum((w.end_ns - w.start_ns) for w in windows) / 1e9
    overall_fps = (presented / total_span_s) if total_span_s > 0 else 0.0
    drop_rate = (100.0 * dropped / total) if total > 0 else 0.0

    return FpsReport(
        overall_fps=overall_fps,
        total_frames=total,
        presented_frames=presented,
        dropped_frames=dropped,
        janky_frames=janky,
        drop_rate=drop_rate,
        windows=summarize_windows(frames, windows),
        by_source=summarize_by_source(frames, windows),
    )


# ---------------------------------------------------------------------------
# trace_processor integration (only imported when analyzing a real trace)
# ---------------------------------------------------------------------------

def _fallback_windows_from_log(swipe_log):
    """Fallback windows from run_fps_test.sh's swipe log.

    The log holds DEVICE REALTIME nanoseconds (recorded via `adb shell date
    +%s%N`), NOT host time — so they share a clock family with the trace and can
    be compared after converting frame ts to realtime (see analyze_trace).
    Each entry is an (start_ns, end_ns) device-realtime tuple."""
    return [FlingWindow(s, e) for s, e in swipe_log]


def analyze_trace(trace_path, swipe_log=None):
    """Load a trace and return an FpsReport.

    Primary path: derive precise fling windows from structured input events
    (android.input.inputevent — debuggable/userdebug/eng builds only); frames
    and windows are both in trace time.

    Fallback path: on user builds with no structured input, use the device-clock
    swipe markers from swipe_log. Those are device REALTIME ns, so we query
    frames in realtime too, keeping both sides on one clock.

    swipe_log: optional list of (start_ns, end_ns) device-realtime tuples.
    """
    from perfetto.trace_processor import TraceProcessor

    tp = TraceProcessor(trace=str(trace_path))

    # Decide the clock first (structured input → trace time; fallback → realtime).
    windows = _extract_fling_windows(tp)
    realtime = False
    if not windows:
        if not swipe_log:
            raise RuntimeError(
                "No fling windows: structured input is unavailable (needs a "
                "debuggable build with the android.input.inputevent data source) "
                "and no --swipe-log fallback was provided."
            )
        print("[fps] WARNING: no structured input events (user build?); using "
              "device-clock swipe markers (less precise).", flush=True)
        windows = _fallback_windows_from_log(swipe_log)
        realtime = True

    # Gather frames from ALL sources, not just the standard pipeline:
    #   (a) FrameTimeline → each app SURFACE frame, tagged by layer (precise jank
    #       vs drop). Returns the set of layers it already covers.
    #   (b) per-layer BufferQueue → SurfaceView / TextureView / ImageReader /
    #       WebView / Flutter / video, EXCLUDING layers (a) already counted (so a
    #       surface isn't double-counted), plus single-buffer overwrite drops.
    ft_frames = _query_frames(tp, realtime=realtime)
    covered_layers = {f.source for f in ft_frames}
    bq_frames = _query_buffer_queue_frames(
        tp, exclude_layers=covered_layers, realtime=realtime)

    return compute_fps_from_frames(ft_frames + bq_frames, windows)


def _extract_fling_windows(tp):
    """Derive fling windows from structured input: each ACTION_UP to the next
    ACTION_DOWN. Uses the android.input stdlib module, which is populated by the
    android.input.inputevent data source (debuggable/userdebug/eng builds only).
    Returns [] on user builds or any query failure → caller falls back.

    NOTE: android.input table/column names are schema-version dependent. Confirm
    the exact shape against a real trace in the Task 0 spike before relying on it.
    """
    windows = []
    try:
        # `android_input_events.event_action` is the STRING action (e.g.
        # 'ACTION_UP'); `android_motion_events.action` is a numeric code. Use the
        # string table. Confirm the exact table/column in the Task 0 spike.
        qr = tp.query("""
            INCLUDE PERFETTO MODULE android.input;
            SELECT ts, event_action
            FROM android_input_events
            ORDER BY ts
        """)
        ups, downs = [], []
        for row in qr:
            action = (getattr(row, "event_action", "") or "").upper()
            if "UP" in action:
                ups.append(row.ts)
            elif "DOWN" in action:
                downs.append(row.ts)
        for up_ts in ups:
            later_downs = [d for d in downs if d > up_ts]
            if later_downs:
                windows.append(FlingWindow(up_ts, min(later_downs)))
    except Exception as e:
        print(f"[fps] structured input query failed ({e}); will fall back.", flush=True)
    return windows


def _query_frames(tp, realtime=False):
    """Return per-SURFACE Frame list from FrameTimeline data, tagged by layer.

    Frames come from `actual_frame_timeline_slice`, populated by the
    android.surfaceflinger.frametimeline data source (NOT the gfx atrace
    category). Each app surface is its own `source` (layer_name), so multiple
    surfaces are never merged.

    Two DISTINCT signals (not the same thing!):
      - dropped : present_type marks a frame that never reached the display
                  ('Dropped Frame'). Excluded from FPS.
      - janky   : jank_type marks a frame presented LATE (missed deadline). Still
                  refreshed the screen, so it counts toward FPS — reported as a
                  separate quality metric.

    realtime=True maps each frame's trace ts to device REALTIME ns via
    TO_REALTIME(), so frames line up with the device-clock swipe-marker fallback.

    NOTE: PROVISIONAL — column names (layer_name / present_type / jank_type),
    surface-vs-display frame filtering, and TO_REALTIME() availability are
    schema-version dependent. Confirm against a real trace in the Task 0 spike.
    """
    ts_expr = "TO_REALTIME(ts)" if realtime else "ts"
    qr = tp.query(f"""
        SELECT
            {ts_expr} AS ts,
            dur,
            COALESCE(layer_name, 'app-pipeline') AS source,
            CASE WHEN present_type = 'Dropped Frame' THEN 1 ELSE 0 END AS dropped,
            CASE WHEN jank_type IS NOT NULL
                  AND jank_type NOT IN ('None', 'Buffer Stuffing')
                 THEN 1 ELSE 0 END AS janky
        FROM actual_frame_timeline_slice
        WHERE dur > 0
        ORDER BY ts
    """)
    frames = []
    for row in qr:
        frames.append(Frame(ts=row.ts, dur=row.dur,
                            dropped=bool(row.dropped), janky=bool(row.janky),
                            source=row.source))
    return frames


# Layer-name hints for single-buffer surfaces (TextureView/SurfaceTexture). These
# are a STARTING heuristic; the Task 0 spike replaces/extends this with the real
# single-buffer layer names confirmed on-device. Overwrite detection runs only on
# matching layers, never on every layer.
_SINGLE_BUFFER_HINTS = ("textureview", "surfacetexture")


def _looks_single_buffer(layer):
    name = (layer or "").lower()
    return any(h in name for h in _SINGLE_BUFFER_HINTS)


def _query_buffer_queue_frames(tp, exclude_layers=(), realtime=False,
                               single_buffer_layers=None):
    """Frames for NON-pipeline sources, from per-layer buffer activity.

    FrameTimeline covers the app surfaces it tracks; layers it does NOT cover —
    SurfaceView, TextureView/SurfaceTexture, ImageReader, WebView, Flutter, video —
    are picked up here from per-layer BufferQueue activity, excluding any layer
    FrameTimeline already counted (exclude_layers) so a surface isn't double-counted.

    PREFERRED source: the stdlib `frame_slice` (layer_name, frame_number,
    queue_to_acquire_time, acquire_to_latch_time, latch_to_present_time) — it
    already pairs producer/consumer per buffer, so we get one presented Frame per
    consumed buffer WITHOUT the naive "two queues, no acquire" false positives.

    exclude_layers : layers FrameTimeline already counted — skip to avoid double
                     counting the same surface.
    single_buffer_layers : layers the Task 0 spike confirmed are single-buffered
                     (e.g. specific TextureViews). ONLY these get overwrite-drop
                     detection from raw BufferQueue events; applying it to normal
                     double/triple-buffered layers would invent false drops. If
                     None, auto-detects by layer-name hint (_looks_single_buffer)
                     so TextureViews are covered out of the box; pass an explicit
                     list from the spike to be precise.

    NOTE: PROVISIONAL — `frame_slice`'s exact columns and the raw BufferQueue
    event shape are confirmed in the Task 0 spike (Step 3b). Returns [] on query
    failure so pipeline FPS still works.
    """
    ts_expr = "TO_REALTIME(ts)" if realtime else "ts"
    exclude = set(exclude_layers)
    frames = []

    # 1. Presented frames per non-pipeline layer, from frame_slice.
    try:
        qr = tp.query(f"""
            INCLUDE PERFETTO MODULE android.frames.timeline;
            SELECT {ts_expr} AS ts, dur, layer_name AS source
            FROM frame_slice
            WHERE dur > 0
            ORDER BY ts
        """)
        bq_layers = set()
        for row in qr:
            if row.source in exclude:
                continue
            bq_layers.add(row.source)
            frames.append(Frame(ts=row.ts, dur=row.dur,
                               dropped=False, source=row.source))
    except Exception as e:
        print(f"[fps] frame_slice query failed ({e}); pipeline-only FPS.", flush=True)
        return []

    # 2. Single-buffer overwrite drops, ONLY for single-buffer layers. Auto-detect
    #    TextureView/SurfaceTexture by name unless the spike gave an explicit list.
    if single_buffer_layers is None:
        single_buffer_layers = [l for l in bq_layers if _looks_single_buffer(l)]
    if single_buffer_layers:
        events = _query_raw_buffer_events(tp, single_buffer_layers, realtime)
        for layer in single_buffer_layers:
            frames += detect_overwrite_drops(events, layer)
    return frames


def _query_raw_buffer_events(tp, layers, realtime=False):
    """Raw per-layer BufferQueue events (queue/acquire/latch) for overwrite
    detection on confirmed single-buffer layers.

    PROVISIONAL: BufferQueue slices come from the `gfx` atrace category; the slice
    name (e.g. 'queueBuffer'/'acquireBuffer'/'latchBuffer') and how the layer name
    is carried (track name vs a join to a layer table) are confirmed in the Task 0
    spike (Step 3b). The `_bufferqueue_events` source below is a placeholder for
    whatever the spike pins down. Returns [] on failure.
    """
    ts_expr = "TO_REALTIME(ts)" if realtime else "ts"
    events = []
    try:
        qr = tp.query(f"""
            SELECT {ts_expr} AS ts, layer_name AS layer, slice_name AS name
            FROM _bufferqueue_events_PLACEHOLDER   -- replace per Task 0 spike
            ORDER BY ts
        """)
        wanted = set(layers)
        for row in qr:
            if row.layer not in wanted:
                continue
            n = (row.name or "").lower()
            if "queue" in n and "dequeue" not in n:
                kind = "queue"
            elif "acquire" in n:
                kind = "acquire"
            elif "latch" in n:
                kind = "latch"
            else:
                continue
            events.append(BufferEvent(ts=row.ts, layer=row.layer, kind=kind))
    except Exception as e:
        print(f"[fps] raw buffer-event query failed ({e}); no overwrite drops.",
              flush=True)
    return events


def format_report(report: FpsReport, trace_path: str) -> str:
    lines = []
    lines.append(f"=== FPS Report: {trace_path} ===")
    # Per-source FPS is the meaningful number. The aggregate below is the SUM of
    # produced frames/s across all sources (throughput), NOT a single screen FPS.
    lines.append(f"Produced frames/s (all sources, NOT screen FPS): {report.overall_fps:.1f}")
    lines.append(f"Total frames      : {report.total_frames}")
    lines.append(f"  presented       : {report.presented_frames}")
    lines.append(f"  dropped         : {report.dropped_frames}  (never on screen)")
    lines.append(f"  janky           : {report.janky_frames}  (presented late)")
    lines.append(f"Drop rate         : {report.drop_rate:.2f}%")
    lines.append("")
    lines.append("Per-frame-source breakdown (this is the FPS that matters):")
    if report.by_source:
        for s in report.by_source:
            lines.append(
                f"  {s.source:<24} fps={s.fps:6.1f} frames={s.frame_count} "
                f"presented={s.presented} dropped={s.dropped} janky={s.janky}"
            )
    else:
        lines.append("  (single source)")
    lines.append("")
    lines.append("Per-fling-window breakdown:")
    for w in report.windows:
        lines.append(
            f"  window {w.index}: frames={w.frame_count} "
            f"dropped={w.dropped} janky={w.janky} fps={w.fps:.1f}"
        )
    return "\n".join(lines)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Compute FPS/jank from a Perfetto trace.")
    p.add_argument("trace", help="Path to .perfetto-trace")
    p.add_argument("--swipe-log", help="Optional file of 'start_ns end_ns' per line")
    args = p.parse_args(argv)

    swipe_log = None
    if args.swipe_log:
        with open(args.swipe_log) as f:
            swipe_log = [
                tuple(int(x) for x in line.split())
                for line in f if line.strip()
            ]

    report = analyze_trace(args.trace, swipe_log=swipe_log)
    out = format_report(report, args.trace)
    print(out)
    report_path = args.trace + ".fps_report.txt"
    with open(report_path, "w") as f:
        f.write(out + "\n")
    print(f"\nSaved: {report_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the math tests to verify they pass**

```bash
python3 -m pytest tests/test_compute_fps_math.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add fps-test/compute_fps.py tests/test_compute_fps_math.py
git commit -m "feat(fps-test): add compute_fps math (TDD) + trace_processor integration"
```

---

## Task 8: Block 5 — Swipe pattern + orchestrator

**Files:**
- Create: `fps-test/swipe_pattern.txt`
- Create: `fps-test/run_fps_test.sh`
- Create: `fps-test/README.md`

- [ ] **Step 1: Write `fps-test/swipe_pattern.txt`**

This is a simple config file the shell script reads. Format: `direction x1 y1 x2 y2 duration_ms gap_ms`.

```
# Swipe pattern for FPS testing.
# Format per line: <direction> <x1> <y1> <x2> <y2> <duration_ms> <gap_ms>
# direction: up | down
# Coordinates are absolute pixels; tune for your device resolution.
# Pattern: 3 up-swipes then 3 down-swipes. Total ~6s of swiping.
up    540 1600 540 400 400 600
up    540 1600 540 400 400 600
up    540 1600 540 400 400 600
down  540 400 540 1600 400 600
down  540 400 540 1600 400 600
down  540 400 540 1600 400 600
```

- [ ] **Step 2: Write `fps-test/run_fps_test.sh`**

```bash
#!/usr/bin/env bash
# Automated swipe-based FPS test.
#
# Flow:
#   1. (user has already navigated the app to the target screen)
#   2. Start a Perfetto trace in the background (config 02_jank_frame, ~12s).
#   3. Run the swipe pattern: 3 up, then 3 down (from swipe_pattern.txt).
#   4. Wait for the trace to finish and pull it.
#   5. Compute FPS / dropped frames with compute_fps.py.
#
# Usage: run_fps_test.sh [duration_sec] [package_for_gfxinfo]
#   duration_sec default 12. The swipe pattern alone takes ~7s (1 settle + 6
#   swipes) plus adb round-trips; the trace must outlast it with margin, so the
#   default is generous. Lower it only on a fast, USB-attached device.
#   package_for_gfxinfo (optional): if given, also runs the auxiliary
#   dump_gfxinfo.sh cross-check (resets counters before, dumps framestats +
#   SurfaceFlinger latency after). Independent of the trace.
set -euo pipefail

DURATION="${1:-12}"
GFXINFO_PKG="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CAPTURE="${REPO_ROOT}/capture/capture.sh"
COMPUTE="${SCRIPT_DIR}/compute_fps.py"
PATTERN="${SCRIPT_DIR}/swipe_pattern.txt"
GFXDUMP="${SCRIPT_DIR}/dump_gfxinfo.sh"
OUT_DIR="${REPO_ROOT}/traces"
mkdir -p "${OUT_DIR}"

TS="$(date +%Y%m%d_%H%M%S)"
TRACE="${OUT_DIR}/${TS}_fps.perfetto-trace"

echo "[fps-test] duration: ${DURATION}s"
echo "[fps-test] output  : ${TRACE}"

# 1. Start the trace in the background. --no-open so capture returns when done.
echo "[fps-test] starting trace (background)..."
"${CAPTURE}" --config jank --time "${DURATION}" --output "${TRACE}" --no-open &
CAPTURE_PID=$!

# Optional auxiliary: reset gfxinfo / SurfaceFlinger latency counters before swipes.
if [[ -n "${GFXINFO_PKG}" ]]; then
  "${GFXDUMP}" reset "${GFXINFO_PKG}" || echo "[fps-test] gfxinfo reset failed (non-fatal)" >&2
fi

# Give the tracer time to actually start before swiping. record_android_trace
# may push/sideload tracebox on first run, so 1s is not always enough; 2s is a
# safer floor. (A precise barrier would parse the official script's "Tracing
# started" line — left as a future refinement.)
sleep 2

# 2. Run the swipe pattern, recording per-fling timestamps for fallback windowing.
SWIPE_LOG="${OUT_DIR}/${TS}_swipe.log"
: > "${SWIPE_LOG}"

# Read DEVICE realtime nanoseconds (toybox `date +%s%N` on the device, NOT the
# host — this avoids the host/device clock-domain mismatch AND macOS BSD `date`,
# which has no %N. compute_fps.py converts frame ts to realtime to match these.)
device_now_ns() { adb shell date +%s%N | tr -d '\r'; }

run_swipes() {
  while read -r dir x1 y1 x2 y2 dur gap _rest; do
    # Skip comments / blanks.
    [[ -z "${dir}" || "${dir}" == "#"* ]] && continue
    echo "[fps-test] swipe ${dir} ..."
    adb shell input swipe "${x1}" "${y1}" "${x2}" "${y2}" "${dur}"
    # Record the post-up (fling) window: device-now .. device-now+gap.
    start_ns="$(device_now_ns)"
    sleep "$(python3 -c "print(${gap}/1000.0)")"
    end_ns="$(device_now_ns)"
    echo "${start_ns} ${end_ns}" >> "${SWIPE_LOG}"
  done < "${PATTERN}"
}
run_swipes

# 3. Wait for the trace to complete.
echo "[fps-test] waiting for trace to finish..."
wait "${CAPTURE_PID}"

# 4. Compute FPS.
echo "[fps-test] computing FPS..."
python3 "${COMPUTE}" "${TRACE}" --swipe-log "${SWIPE_LOG}" || {
  echo ""
  echo "compute_fps.py failed. Common causes:" >&2
  echo "  - 'perfetto' python package not installed: pip install perfetto" >&2
  echo "  - no FrameTimeline data (needs Android 12+ and the" >&2
  echo "    android.surfaceflinger.frametimeline data source in 02_jank_frame.pbtx)" >&2
  echo "  - structured input only on debuggable builds; otherwise the device-clock" >&2
  echo "    swipe-marker fallback is used automatically" >&2
  exit 1
}

# 5. Optional auxiliary cross-check: dump gfxinfo framestats + SF latency.
if [[ -n "${GFXINFO_PKG}" ]]; then
  echo "[fps-test] dumping gfxinfo / SurfaceFlinger cross-check..."
  "${GFXDUMP}" dump "${GFXINFO_PKG}" "${OUT_DIR}" || echo "[fps-test] gfxinfo dump failed (non-fatal)" >&2
fi

echo ""
echo "[fps-test] done. Report next to trace: ${TRACE}.fps_report.txt"
```

- [ ] **Step 2b: Write `fps-test/dump_gfxinfo.sh`** (auxiliary cross-check)

Independent of the Perfetto trace — corroborates the trace's per-source FPS with
the system's own counters. `gfxinfo framestats` is whole-process; SurfaceFlinger
`--latency` is per-layer. Neither replaces the trace; they're a sanity check.

```bash
#!/usr/bin/env bash
# Auxiliary FPS cross-check via dumpsys. Independent of the Perfetto trace.
#
# Usage:
#   dump_gfxinfo.sh reset <package>             # before the test
#   dump_gfxinfo.sh dump  <package> [out_dir]   # after the test
#
# 'reset' zeroes gfxinfo + SurfaceFlinger latency counters.
# 'dump'  writes gfxinfo framestats and per-layer SurfaceFlinger latency to files.
set -euo pipefail

MODE="${1:?Usage: $0 reset|dump <package> [out_dir]}"
PKG="${2:?package name required}"
OUT_DIR="${3:-./traces}"

case "${MODE}" in
  reset)
    # gfxinfo per-app reset; SF latency is global clear.
    adb shell dumpsys gfxinfo "${PKG}" reset >/dev/null 2>&1 || true
    adb shell dumpsys SurfaceFlinger --latency-clear >/dev/null 2>&1 || true
    echo "[gfxinfo] reset counters for ${PKG}"
    ;;
  dump)
    mkdir -p "${OUT_DIR}"
    TS="$(date +%Y%m%d_%H%M%S)"
    GFX="${OUT_DIR}/gfxinfo_${PKG}_${TS}.txt"
    SF="${OUT_DIR}/sflatency_${PKG}_${TS}.txt"

    # 1. Whole-process frame stats (Total frames, Janky frames, percentiles, CSV).
    adb shell dumpsys gfxinfo "${PKG}" framestats > "${GFX}"
    echo "[gfxinfo] framestats -> ${GFX}"

    # 2. Per-layer present timestamps. Pick the app's visible layers. Layer names
    #    look like 'SurfaceView[pkg/Activity]#0' or 'pkg/Activity#0'. dumpsys
    #    SurfaceFlinger --list enumerates them; --latency <layer> prints 3-column
    #    (desired, actual-present, frame-ready) ns rows for that layer.
    LAYERS="$(adb shell dumpsys SurfaceFlinger --list | tr -d '\r' | grep -F "${PKG}" || true)"
    if [[ -z "${LAYERS}" ]]; then
      echo "[gfxinfo] no SurfaceFlinger layers matched ${PKG}; skipping --latency" >&2
    else
      : > "${SF}"
      while IFS= read -r layer; do
        [[ -z "${layer}" ]] && continue
        {
          echo "=== layer: ${layer} ==="
          adb shell dumpsys SurfaceFlinger --latency "${layer}"
          echo ""
        } >> "${SF}"
      done <<< "${LAYERS}"
      echo "[gfxinfo] SurfaceFlinger latency -> ${SF}"
    fi
    ;;
  *)
    echo "ERROR: unknown mode '${MODE}' (use reset|dump)" >&2
    exit 2
    ;;
esac
```

- [ ] **Step 3: Write `fps-test/README.md`**

```markdown
# FPS Test (swipe-based)

Automated scroll-smoothness test: captures a Perfetto trace while running a
fixed swipe pattern (3 up, 3 down), then computes FPS and dropped frames over
the fling (finger-up) phases only.

## Usage

1. Connect a device, launch your app, navigate to the screen you want to test.
2. Run:

```bash
./fps-test/run_fps_test.sh        # 12s default
./fps-test/run_fps_test.sh 16     # 16s (slow device / longer swipe pattern)
./fps-test/run_fps_test.sh 12 com.example.app   # + gfxinfo/SurfaceFlinger cross-check
```

3. Output:
   - `traces/<ts>_fps.perfetto-trace` — the raw trace (open at ui.perfetto.dev)
   - `traces/<ts>_fps.perfetto-trace.fps_report.txt` — the computed report

## Auxiliary cross-check: `dump_gfxinfo.sh`

An INDEPENDENT sanity check that doesn't touch the trace. Pass a package to
`run_fps_test.sh` (above) to run it automatically, or use it standalone:

```bash
./fps-test/dump_gfxinfo.sh reset com.example.app    # before the test
# ... do the scroll ...
./fps-test/dump_gfxinfo.sh dump  com.example.app    # after the test
```

It writes:
- `traces/gfxinfo_<pkg>_<ts>.txt` — `dumpsys gfxinfo <pkg> framestats`: whole-process
  Total/Janky frames, 50/90/95/99th percentiles, and a per-frame CSV.
- `traces/sflatency_<pkg>_<ts>.txt` — `dumpsys SurfaceFlinger --latency <layer>` per
  app layer: 3-column (desired / actual-present / frame-ready) ns rows you can turn
  into a per-layer FPS.

These are different measurement vantage points (process-level and per-layer) than
the trace's per-source FPS — use them to corroborate, not replace, the trace numbers.

## Report format

```
=== FPS Report: traces/...perfetto-trace ===
Produced frames/s (all sources, NOT screen FPS): 89.1
Total frames      : 480
  presented       : 462
  dropped         : 12  (never on screen)
  janky           : 18  (presented late)
Drop rate         : 2.50%

Per-frame-source breakdown (this is the FPS that matters):
  app-pipeline             fps=  59.1 frames=300 presented=294 dropped=2  janky=14
  SurfaceView[video]       fps=  29.8 frames=150 presented=148 dropped=2  janky=4
  TextureView[com.x/...]   fps=  ...  frames=30  presented=22  dropped=8  janky=0   # 8 single-buffer overwrites

Per-fling-window breakdown:
  window 0: frames=80 dropped=2 fps=58.8
  ...
```

Read the **per-source** FPS — each production source is listed separately. A 60fps
UI list scrolling over a 30fps video is two numbers, never one blended "89fps". Note
the distinction: **dropped** = never reached the screen (incl. TextureView single-
buffer overwrites); **janky** = presented but late. Only dropped frames reduce FPS;
jank is a separate quality signal.

## How fling windows are determined

`compute_fps.py` extracts `ACTION_UP` → next `ACTION_DOWN` intervals from the
trace's **structured input events** (the `android.input.inputevent` data source
in `02_jank_frame.pbtx`). Only frames within these fling windows count toward
FPS — the press/contact phase is excluded.

> Structured input only records on **debuggable / userdebug / eng** builds.
> On a production `user` build there are no input events in the trace, so
> `compute_fps.py` falls back to the swipe markers recorded by this script
> (device-clock timestamps, less precise) and prints a warning. The fallback is
> clock-coherent because both the markers and the converted frame timestamps use
> the device clock — it does not mix host and device time.

## Tuning the swipe pattern

Edit `swipe_pattern.txt`. Coordinates are absolute pixels — adjust for your
device resolution (the defaults 540×1600 → 540×400 assume a ~1080p screen).
Format per line:

```
<direction> <x1> <y1> <x2> <y2> <duration_ms> <gap_ms>
```

## Requirements

- `adb`, one connected device.
- Python 3.9+ with `pip install perfetto` (trace_processor).
- The archived official script at `../official/` (included).
```

- [ ] **Step 4: Lint + chmod**

```bash
shellcheck fps-test/run_fps_test.sh fps-test/dump_gfxinfo.sh
chmod +x fps-test/run_fps_test.sh fps-test/dump_gfxinfo.sh
```

Expected: no shellcheck output.

- [ ] **Step 5: Commit**

```bash
git add fps-test/run_fps_test.sh fps-test/dump_gfxinfo.sh fps-test/swipe_pattern.txt fps-test/README.md
git commit -m "feat(fps-test): add swipe orchestrator + tunable pattern + gfxinfo cross-check"
```

---

## Task 9: Integration test glue + final README polish

**Files:**
- Create: `tests/test_swipe_pattern.py`
- Modify: top-level `README.md` (add test/CI section)

- [ ] **Step 1: Write test for swipe pattern parsing**

`tests/test_swipe_pattern.py`:

```python
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fps-test'))
from compute_fps import FlingWindow, _fallback_windows_from_log


PATTERN = os.path.join(os.path.dirname(__file__), '..', 'fps-test', 'swipe_pattern.txt')


def test_swipe_pattern_has_six_lines():
    lines = [l for l in open(PATTERN)
             if l.strip() and not l.strip().startswith("#")]
    assert len(lines) == 6, "swipe_pattern.txt should have 6 swipes (3 up + 3 down)"


def test_swipe_pattern_three_up_three_down():
    lines = [l for l in open(PATTERN)
             if l.strip() and not l.strip().startswith("#")]
    dirs = [l.split()[0] for l in lines]
    assert dirs.count("up") == 3
    assert dirs.count("down") == 3
    assert dirs == ["up"] * 3 + ["down"] * 3


def test_fallback_windows_from_log():
    log = [(1000, 2000), (3000, 4000)]
    ws = _fallback_windows_from_log(log)
    assert len(ws) == 2
    assert isinstance(ws[0], FlingWindow)
    assert ws[0].start_ns == 1000
    assert ws[0].end_ns == 2000
```

- [ ] **Step 2: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass (config resolver: 13, fps math: 12, swipe pattern: 3 = 28 total).

- [ ] **Step 3: Add a Testing section to top-level README**

Append to `README.md` (before the Design section):

```markdown
## Testing

Pure-logic unit tests (config name resolution, FPS math, swipe pattern parsing):

```bash
python3 -m pytest tests/ -v
```

Device-dependent flows (capture, simpleperf, end-to-end fps-test) are verified
manually against a real Android device — see each subdirectory's README.
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_swipe_pattern.py README.md
git commit -m "test: add swipe pattern tests + document test strategy in README"
```

---

## Task 10: Manual acceptance (device-dependent)

These cannot be automated in this environment. They are the acceptance criteria
for the device-dependent blocks. Run them on a real Android device and record
pass/fail.

- [ ] **Step 1: Block 2 acceptance — capture a real trace**

```bash
./capture/capture.sh --config general --time 5
```
Expected: a `traces/<ts>_general.perfetto-trace` file appears, browser opens the
trace at ui.perfetto.dev.

- [ ] **Step 2: Block 4 acceptance — simpleperf only**

```bash
./simpleperf/simpleperf_only.sh com.android.chrome 5
```
Expected: `traces/simpleperf_<ts>.data` pulled successfully. (Skip / note if app
isn't debuggable — that's an environment issue, not a code defect.)

- [ ] **Step 3: Block 4 acceptance — simpleperf + trace**

```bash
./simpleperf/simpleperf_with_trace.sh com.android.chrome 5
```
Expected: both `simpleperf_<ts>.data` and `<ts>_cpu.perfetto-trace` produced.

- [ ] **Step 4: Block 5 acceptance — end-to-end FPS test**

Prereq: `pip install perfetto`. Target: the installed Friends-Circle demo app on
its scrolling feed (see Task 0).
```bash
# Demo app already on its scrollable feed. Pass the package to also run the
# gfxinfo / SurfaceFlinger cross-check.
./fps-test/run_fps_test.sh 12 <demo.package.name>
```
Expected:
- `traces/<ts>_fps.perfetto-trace` produced.
- `<trace>.fps_report.txt` produced with a non-zero **per-source** FPS (the headline
  "produced frames/s" is throughput, not screen FPS), distinct dropped vs janky
  counts, and a per-window breakdown.
- For a screen with a video / SurfaceView / TextureView, verify EACH source shows
  up as its own line (not merged), and that TextureView single-buffer overwrites
  register as drops. If only `app-pipeline` appears on such a screen, the
  BufferQueue query (Task 0 Step 3b) needs fixing.
- `traces/gfxinfo_*.txt` and `traces/sflatency_*.txt` produced; sanity-check that
  the gfxinfo Janky-frame count and SF-latency-derived FPS are in the same ballpark
  as the trace's per-source numbers.

- [ ] **Step 5: Record results**

Append a short pass/fail summary to the commit message of the final integration
commit, or to a `docs/acceptance-<date>.md` note. Do NOT claim success if any
step failed — report the actual output.

---

## Self-Review (completed by plan author)

**1. Spec coverage:**
- De-risking spike (config + schema): Task 0. ✓
- Block 1 (official archive): Task 3. ✓
- Block 2 (cross-platform capture): Tasks 4, 5. ✓
- Block 3 (configs): Task 2. ✓
- Block 4 (simpleperf): Task 6. ✓
- Block 5 (fps-test): Tasks 7, 8. ✓
- Risk mitigations (trace_processor missing → pip install hint; input fallback
  windows → device-clock swipe_log): covered in Task 7 compute_fps.py + Task 8 README. ✓
- Error handling (adb missing, no device, ambiguous config, non-debuggable
  app): covered in perfetto_capture.py and simpleperf scripts. ✓
- Testing strategy (unit tests + manual acceptance): Tasks 4/7/9 + Task 10. ✓

**2. Placeholder scan:** One DELIBERATE placeholder remains:
`_bufferqueue_events_PLACEHOLDER` in `_query_raw_buffer_events` (used only for
TextureView single-buffer overwrite detection; the main multi-source path uses the
real stdlib `frame_slice`). It is NOT an oversight — the raw BufferQueue slice
schema can only be confirmed on-device (Task 0 Step 3b), and the code comment +
spike step say so explicitly. Every other step has concrete code or an exact
command.

**3. Type/name consistency:**
- `resolve_config(name, configs_dir=None)` — same signature in test + impl. ✓
- `list_configs(configs_dir=None)` — same. ✓
- `ConfigError` — defined once, imported in test. ✓
- `Frame(ts, dur, dropped=False, janky=False, source="app-pipeline")`,
  `BufferEvent(ts, layer, kind)`, `FlingWindow(start_ns, end_ns)`,
  `compute_fps_from_frames(frames, windows)`, `summarize_windows` /
  `summarize_by_source` / `detect_overwrite_drops` / `buffer_events_to_frames`,
  `FpsReport`(+`janky_frames`)/`SourceStat`(+`janky`)/`WindowStat`(+`janky`) — used
  consistently across
  test_compute_fps_math.py, compute_fps.py, and test_swipe_pattern.py. The added
  `source` field defaults so older `Frame(ts, dur, dropped)` call sites still work. ✓
- Capture flag `--no-open` matches record_android_trace's actual `--no-open`. ✓
- `--time` is NOT passed as `-t` to record_android_trace (which ignores it under
  `-c`); it rewrites the config's `duration_ms` via `apply_duration()`. ✓
- `apply_duration(config_text, seconds)` — same signature in test + impl. ✓
- Config short-name `cpu_sched` resolves to `03_cpu_sched.pbtx` (substring
  "cpu_sched" in "03_cpu_sched"). ✓

**4. Perfetto-correctness fixes folded in (from review):**
- ATrace via `linux.ftrace.atrace_categories/atrace_apps`, not a standalone
  `android.atrace` data source. ✓
- FrameTimeline via the `android.surfaceflinger.frametimeline` data source
  (not the `gfx` atrace category). ✓
- Frames from `actual_frame_timeline_slice`; jank via `jank_type` (PROVISIONAL —
  reconcile with Task 0 spike). ✓
- Input from `android.input` structured events (debuggable builds), not
  `raw LIKE '%ACTION_UP%'`; user-build fallback uses device-clock markers, not
  host `date +%s%N`. ✓
- `proc_stats_poll_ms` (not `scan_period_ms`) in `04_memory.pbtx`. ✓
- Python floor stated as 3.9+ consistently. ✓

**5. FPS multi-source + single-buffer (from user requirement):**
- FrameTimeline covers the app surfaces it tracks, per layer. fps-test additionally
  reads per-layer BufferQueue (`frame_slice`), EXCLUDING layers FrameTimeline
  already covered, so SurfaceView / TextureView / ImageReader / WebView / Flutter /
  video are counted, and reports **per source** (never merged). ✓
- `dropped` (never on screen, incl. TextureView single-buffer overwrite) is kept
  DISTINCT from `janky` (presented late). Only dropped reduces FPS. ✓
- TextureView single-buffer overwrite is detected (`detect_overwrite_drops`) only on
  single-buffer layers (`_looks_single_buffer` hint / spike list), not blindly. ✓

**6. Remaining provisional items (must be confirmed in Task 0 before "done"):**
the exact frame/input/BufferQueue table+column+slice names, how a layer name is
carried, which layer is the app-pipeline (to skip), and `TO_REALTIME()` behavior.
The integration SQL — especially `_query_raw_buffer_events`'s placeholder table —
is a best-effort starting point with explicit spike-verification notes, NOT a claim
that the SQL is final. The pure-math layer (FPS, per-source, overwrite detection)
is fully tested and final.
