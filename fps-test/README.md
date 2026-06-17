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

```
=== FPS Report: traces/...perfetto-trace ===
Screen FPS (display/SF output over fling windows): 120.1
Total frames (all sources): 493
  presented       : 493
  dropped         : 0  (never on screen)
  janky           : 128  (presented late)
Drop rate         : 0.00%

Per-frame-source breakdown (this is the FPS that matters):
  TX - com.example/...Activity#292           fps= 119.2 frames=89 ...
  TX - StatusBar#92                          fps=   8.6 frames=26 ...
  display                                    fps=  65.3 frames=197 ...

Per-fling-window breakdown:
  window 0: frames=217 dropped=0 janky=127 fps=120.5
  ...
```

- **Screen FPS** = SurfaceFlinger's composited output (the `display` source) over
  the fling windows. That is the refresh rate the user actually saw.
- **Per-source** lists each producing surface (app surface, SurfaceView,
  TextureView, video, StatusBar, InputMethod...) separately. Summing them is NOT
  a screen FPS — a 120Hz screen with a 60fps list + a 60fps video is still 120Hz.
- **dropped** = `present_type='Dropped Frame'` (never reached the screen).
- **janky** = `jank_type != None` AND not dropped (presented but late).

## How fling windows are determined (3-tier)

`compute_fps.py` derives the fling (finger-up → next finger-down) windows:

1. **Structured input** `android_input_events.event_action` — debuggable /
   userdebug / eng builds only.
2. **atrace `input` category slices** — `dispatchInputEvent MotionEvent
   ACTION_UP` → next `ACTION_DOWN`. **Works on production `user` builds**
   (confirmed on API 36). Multiple slices fire per gesture (one per input
   channel); they're de-duped (50ms) before pairing.
3. **Device-clock swipe markers** from `run_fps_test.sh` (`adb shell date +%s%N`)
   — coarse, last resort. Frame timestamps are converted with `TO_REALTIME()` so
   both sides share the device-realtime clock.

Only frames within these fling windows count toward FPS — the press/contact
phase is excluded.

## macOS / trace_processor note

On macOS Python 3.12 the `perfetto` package's first run can hit an SSL cert
error when fetching `trace_processor_shell`. Fix:

```bash
pip install certifi
export SSL_CERT_FILE="$(python3 -c 'import certifi; print(certifi.where())')"
```

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
