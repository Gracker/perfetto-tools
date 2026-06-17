# FPS Test (swipe-based)

Automated scroll-smoothness test: captures a Perfetto trace while running a
fixed swipe pattern (3 up, 3 down), then computes FPS and dropped frames over
the fling (finger-up) phases only.

## Usage

1. Connect a device, launch your app, navigate to the screen you want to test.
2. Run:

```bash
pip install perfetto                       # trace_processor, needed for the compute step
./fps-test/run_fps_test.sh                 # 12s default
./fps-test/run_fps_test.sh 16              # 16s (slow device / longer swipe pattern)
./fps-test/run_fps_test.sh 12 com.example.app   # + gfxinfo/SurfaceFlinger cross-check
```

3. Output (in `traces/`):
   - `<ts>_fps.perfetto-trace` — the raw trace (open at ui.perfetto.dev)
   - `<ts>_fps.perfetto-trace.fps_report.txt` — the computed report
   - `<ts>_swipe.log` — device-clock fling markers (tier-3 fallback)
   - (if package given) `gfxinfo_<pkg>_<ts>.txt`, `sflatency_<pkg>_<ts>.txt`

## Report format

Each run reports **three FPS phases per gesture** — overall, press, and fling —
so a single capture diagnoses drag responsiveness, fling smoothness, and overall
smoothness together:

```
=== FPS Report: traces/...perfetto-trace ===
[window source: structured input, 2 gesture(s)]

-- Overall (press + fling: DOWN -> next DOWN) --
Screen FPS (display/SF output): 76.4
Total frames (all sources): 696
  presented       : 696
  dropped         : 0  (never on screen)
  janky           : 21  (presented late)
Drop rate         : 0.00%
Per-frame-source breakdown:
  TX - com.example/...Activity#48506        fps= 76.6 frames=347 presented=347 dropped=0 janky=21
  display                                    fps= 76.4 frames=346 ...

-- Press phase (finger down -> up) --
Screen FPS (display/SF output): 56.9
...

-- Fling phase (finger up -> next press) --
Screen FPS (display/SF output): 77.8
...

Per-gesture Screen-FPS (overall / press / fling):
  gesture 0: overall=62.0  press=61.0  fling=62.1
  gesture 1: overall=102.2  press=51.4  fling=106.9
```

- **Screen FPS** = SurfaceFlinger's composited output (the `display` source) over
  the phase's windows. That is the refresh rate the user actually saw.
- **Per-source** lists each producing surface (app surface, SurfaceView,
  TextureView, video, StatusBar, InputMethod...) separately. Summing them is NOT
  a screen FPS — a 120Hz screen with a 60fps list + a 60fps video is still 120Hz.
- **dropped** = `present_type='Dropped Frame'` (never reached the screen).
- **janky** = `jank_type != None` AND not dropped (presented but late).
- **press phase = 0 FPS / N/A** is legitimate: it means no frame was produced
  during the (possibly very short) finger-contact period (e.g. a launch trace
  where the app hadn't started rendering yet), or the source had no UP marker
  (tier-3 device-clock markers).

## How gestures & the three phases are determined

`compute_fps.py` derives **gestures** from touch events. A gesture is one
physical touch: `DOWN` (press start) → `UP` (press end / fling start) → next
`DOWN` or trace end (fling end). Each gesture splits into three phases:

| phase | window | measures |
|-------|--------|----------|
| **overall** | DOWN → next DOWN | the whole interaction (press + fling) |
| **press** | DOWN → UP | finger-contact period (drag / press responsiveness) |
| **fling** | UP → next DOWN | finger-up inertia period (fling smoothness) |

A single-gesture trace still yields one overall + one fling window (the old
fling-only definition produced ZERO windows for single-gesture traces).

Gesture sources, 3-tier fallback (first non-empty wins):

1. **Structured input** `android_input_events.event_action`, timestamped with
   `COALESCE(event_time, dispatch_ts)` — `event_time` is NULL on API 36 user
   builds, so `dispatch_ts` is the reliable fallback.
2. **atrace `input` category slices** — `dispatchInputEvent` / `publishMotionEvent`
   ACTION_DOWN/UP. **Works on production `user` builds** (confirmed on 6
   real-device traces). Multiple slices fire per gesture (one per input channel);
   they're de-duped (50ms) before pairing.
3. **Device-clock swipe markers** from `run_fps_test.sh` (`adb shell date +%s%N`)
   — coarse, last resort. No UP marker → press phase unavailable; frame
   timestamps are converted with `TO_REALTIME()` so both sides share the
   device-realtime clock.

## trace_processor_shell (no run-time download)

The native `trace_processor_shell` binary ships in [`../tools/trace_processor_shell/`](../tools/trace_processor_shell/)
(5 platforms, ~50MB). `run_fps_test.sh` sets `PYTHONPATH` so a preload hook
(`_tp_shell_patch.py`) patches the `perfetto` pip package to use that local
binary instead of downloading it. Run `./tools/setup.sh` once to verify the
binaries' checksums.

You still need `pip install perfetto` (the Python SQL client) - but the ~12MB
native binary no longer comes from the network, which avoids the macOS Python
3.12 SSL-cert error that the pip package's download path hits.

## Auxiliary cross-check: `dump_gfxinfo.sh`

An INDEPENDENT sanity check that doesn't touch the trace. Pass a package to
`run_fps_test.sh` (above) to run it automatically, or standalone:

```bash
./fps-test/dump_gfxinfo.sh reset com.example.app    # before the test
# ... do the scroll ...
./fps-test/dump_gfxinfo.sh dump  com.example.app    # after the test
```

- `gfxinfo_<pkg>_<ts>.txt` — `dumpsys gfxinfo <pkg> framestats`: whole-process
  Total/Janky frames, 50/90/95/99th percentiles, per-frame CSV.
- `sflatency_<pkg>_<ts>.txt` — `dumpsys SurfaceFlinger --latency <layer>` per app
  layer: 3-column (desired / actual-present / frame-ready) ns rows.

Different vantage points (process-level, per-layer) than the trace's per-source
FPS — use them to corroborate, not replace, the trace numbers.

## Tuning the swipe pattern

Edit `swipe_pattern.txt`. Coordinates are absolute pixels — adjust for your
device resolution (defaults target ~1264×2800). Format per line:

```
<direction> <x1> <y1> <x2> <y2> <duration_ms> <gap_ms>
```

## Requirements

- `adb`, one connected device.
- Python 3.9+ with `pip install perfetto` (trace_processor).
- The archived official script at `../official/` (included).
- FrameTimeline needs Android 12 (API 31)+.

## Known limitations / implementation status

- **FPS is sourced from FrameTimeline only.** `compute_fps.py` groups
  `actual_frame_timeline_slice` rows by `layer_name` to produce per-source FPS.
  The code also contains `detect_overwrite_drops()` /
  `buffer_events_to_frames()` — a *tested library* for per-layer BufferQueue
  (`frame_slice`) analysis, including TextureView single-buffer overwrite
  detection — but **it is not wired into `analyze_trace`**, because every device
  tested (6 real-device traces across TextureView/SurfaceView/AOSP/Flutter
  scenarios) has an **empty `frame_slice` table**. Those surfaces still get
  per-source FPS via their FrameTimeline `layer_name` attribution; only the
  BufferQueue-specific overwrite signal is unavailable.
- **No single-buffer overwrite reporting** as a consequence of the above. The
  drop/jank counts come from FrameTimeline's `present_type`/`jank_type`, which
  already capture most cases; the BufferQueue path would only add coverage for
  surfaces that FrameTimeline doesn't attribute a layer to.
