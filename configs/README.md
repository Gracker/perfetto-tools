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
- Base atrace configs target Android 10+ (API 29+). On older devices, capture falls
  back to sideloading tracebox automatically (handled by `record_android_trace`).
  FrameTimeline specifically needs API 31+.

## Validating a config

All configs in this repo were validated by capturing a 1s trace on a real device
(API 36). To re-validate after editing:

```bash
./capture/capture.sh --config 02 --time 1 --no-open   # produces traces/<ts>_02_jank_frame.perfetto-trace
```

No error and a non-empty trace file = OK.
