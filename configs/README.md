# Prebuilt Trace Configs

Each `.pbtx` is a text-protobuf Perfetto config for a common Android performance
scenario. Pass its name (or number/keyword) to the capture script.

| File | Scenario | Key datasources | Approx. size (10s) |
|---|---|---|---|
| `00_general.pbtx` | General default | sched, freq, atrace(am/wm/gfx/view), mem | ~5–8 MB |
| `01_app_startup.pbtx` | App cold launch | + detailed am/wm, input, ss | ~10–12 MB |
| `02_jank_frame.pbtx` | Scroll / jank | **frametimeline**, **input** (debuggable), gfx, view | ~8 MB |
| `03_cpu_sched.pbtx` | CPU / scheduling | detailed sched, freq, idle | ~2 MB |
| `04_memory.pbtx` | Memory | mem counters, lmk, page alloc | <1 MB |
| `05_full.pbtx` | Full debug | everything above, large buffer | ~8–10 MB |

## Notes

- **ATrace is configured inside `linux.ftrace`** via `atrace_categories` /
  `atrace_apps`. There is no standalone `android.atrace` data source — using one
  makes the config fail to parse.
- `02_jank_frame.pbtx` is also used by `fps-test/`. It adds the
  `android.surfaceflinger.frametimeline` data source (authoritative per-layer frame
  timing, Android 12+) and `android.input.inputevent`. Structured input actions
  only populate on **debuggable / userdebug / eng** builds; on `user` builds fps-test
  derives fling windows from the `input` atrace category's `ACTION_DOWN/UP` slices
  (see [`../docs/spike-notes.md`](../docs/spike-notes.md)).
- `general`, `startup`, `cpu`, and `memory` are supported on API 29+ and run in
  best-effort legacy mode on API 23–28. The wrapper selects one of the four
  bundled v57.2 tracebox binaries, so legacy capture does not download a tool at
  run time. OEM kernels may still omit requested ftrace events.
- API 29 uses its system tracing service when running and bundled tracebox when
  that service is stopped. API 30+ uses system Perfetto.
- `jank` and automated FPS require FrameTimeline from Android 12 / API 31. The
  wrapper rejects them earlier instead of returning a trace with no valid FPS
  source. `full` warns and produces a partial trace before API 31.

## Validating a config

All configs in this repo have API 36 real-device evidence. To run the
capability-aware physical-device smoke plan after editing:

```bash
.venv/bin/python tools/device_smoke.py --require-device
```

The plan covers `general` on API 23+, adds `cpu` on API 29+, and adds `jank` on
API 31+. Every item requires a non-empty trace. See
[`../docs/compatibility.md`](../docs/compatibility.md).
