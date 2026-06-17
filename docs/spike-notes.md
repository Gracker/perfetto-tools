# Task 0 Spike Notes — Perfetto schema on real device

- **Date:** 2026-06-17
- **Device:** OPPO P0110, arm64-v8a, **API 36** (Android 16 preview)
- **Build type:** `user` (NOT debuggable/userdebug/eng) — important for input below.
- **trace_processor:** perfetto py 0.56.0 + `trace_processor_shell` v56.1 mac-arm64
  (downloaded directly from `commondatastorage.googleapis.com/perfetto-luci-artifacts/v56.1/mac-arm64/`
  — the Python package's bootstrap at `get.perfetto.dev/trace_processor` hit an SSL
  cert-verify failure on macOS Python 3.12; the direct-artifact curl bypasses it).
- **Test trace:** `/tmp/spike2.pftrace` — 8s with manual `adb input swipe` activity,
  17.7 MB. Captured via `official/record_android_trace -c <min.pbtx> --no-open`.

## Confirmed facts

### Config schema
- The minimal config with `linux.ftrace` + `atrace_categories`/`atrace_apps` (NOT a
  standalone `android.atrace` data source) parses and captures cleanly. ✅
- `android.surfaceflinger.frametimeline` is accepted as a data source. ✅
- `android.input.inputevent` is accepted as a data source. ✅

### Frame data — `actual_frame_timeline_slice` (PRIMARY source)
- **792 rows** in the 8s swiping trace. Columns (confirmed):
  `id, ts, dur, track_id, category, name, depth, parent_id, arg_set_id,
   display_frame_token, surface_frame_token, upid, layer_name, present_type,
   on_time_finish, gpu_composition, jank_type, jank_severity_type, prediction_type,
   jank_tag, jank_tag_experimental, jank_score, latched_unsignaled_count,
   addressable_unsignaled_latch_count, latched_fence_state`
- **`present_type` values:** `Dropped Frame`, `Early Present`, `Late Present`,
  `On-time Present`, `Unspecified Present`.
- **`jank_type` values:** `None`, `App Deadline Missed`, `Buffer Stuffing`,
  `Display HAL`, `Dropped Frame`, `Prediction Error`, `SurfaceFlinger Scheduling`,
  `Unknown Jank`. **Can be comma-separated** (e.g. `App Deadline Missed, Buffer Stuffing`).
- **Multi-source is BUILT IN via `layer_name`:** each producing surface is its own
  layer (`TX - com.obric.quicksearch/...SearchActivity#292`, `TX - ...Launcher#264`,
  `TX - StatusBar#92`, `TX - InputMethod#281`, `TX - Task=905#272`, ...). SurfaceView /
  TextureView surfaces would appear here too.
- **`layer_name IS NULL` = display (SF composited) frames** — 327 rows, distinct from
  the 465 per-surface frames. These represent screen refreshes, not per-source production.

### `frame_slice` (stdlib `android.frames.timeline`) — **EMPTY (0 rows)**
- Useless on this device/version. The plan's multi-source BufferQueue path via
  `frame_slice` does NOT work here.

### Input events
- `android_input_events` / `android_motion_events` tables **exist** but require
  `INCLUDE PERFETTO MODULE android.input;` first.
- On this **user build**: `android_input_events` has 533 rows but **`event_action`
  is NULL for ALL of them**. `android_motion_events` has **0 rows**.
  → Structured input action is only populated on debuggable/userdebug/eng builds.
- **BUT:** atrace slices DO carry the action with timestamps (from the `input`
  atrace category):
  - `dispatchInputEvent MotionEvent ACTION_DOWN deviceId=-1 source=0x1002 historySize=0` — 15 occurrences, with `ts`.
  - `dispatchInputEvent MotionEvent ACTION_UP deviceId=-1 source=0x1002 historySize=0` — 13 occurrences, with `ts`.
  - `publishMotionEvent(inputChannel=..., action=DOWN|UP|MOVE)` also present.
  → ACTION_DOWN/UP **can be extracted from atrace slices even on a user build**,
    giving precise fling windows without the coarse device-clock fallback.

### BufferQueue raw slices
- `queueBuffer` (1232), `dequeueBuffer` (869), `acquireBuffer` (713) all present in
  the `gfx` atrace category.
- **BUT their `track.name` is NULL** — there is no layer association on these slices.
  → TextureView single-buffer overwrite detection via raw BufferQueue slices is NOT
    feasible here (can't attribute a queue/acquire to a layer). Dropped frames are
    still caught via `present_type='Dropped Frame'` in FrameTimeline.

### `TO_REALTIME()`
- Available. `TO_REALTIME(0)` = `1781656624538267536` (a unix-realtime nanosecond value).
- Used to align frame `ts` (trace/boottime) with device-clock swipe markers.

## Architecture decisions (deviations from plan, based on this spike)

### Decision 1 — Fling-window extraction: THREE-tier fallback (was two-tier)
1. **Structured** `android_input_events.event_action` (non-NULL) — debuggable builds.
2. **atrace slice** `dispatchInputEvent ... ACTION_UP` → next `ACTION_DOWN` — **works on
   user builds** (confirmed here). Preferred over device-clock when structured is empty.
3. **Device-clock** swipe markers from `run_fps_test.sh` (coarse, last resort).

### Decision 2 — Multi-source FPS = FrameTimeline by `layer_name` (was frame_slice + BufferQueue)
- Drop the `frame_slice` / per-layer BufferQueue path (empty / no layer attribution).
- FPS is computed per `layer_name` from `actual_frame_timeline_slice`.
- `layer_name IS NULL` rows are bucketed as source `"display"` (SF output refreshes);
  non-NULL rows keep their layer name as the source. SurfaceView/TextureView/video
  surfaces appear as their own `layer_name` automatically.

### Decision 3 — Drops = `present_type='Dropped Frame'`; remove single-buffer overwrite detector
- Drop the `detect_overwrite_drops` / `_query_raw_buffer_events` integration path
  (no layer attribution available). Dropped frames are caught by FrameTimeline's
  `present_type`. `jank_type='Dropped Frame'` mirrors it.
- The **pure-math** `detect_overwrite_drops`/`buffer_events_to_frames` functions and
  their unit tests are KEPT (they are correct sequence logic and cheap), but
  `analyze_trace` no longer calls into the raw-BufferQueue path. They remain as a
  tested library for future use when a trace exposes layer-attributed buffer events.

### jank vs drop semantics (confirmed, kept from plan)
- **dropped** = `present_type='Dropped Frame'` → never on screen → excluded from FPS.
- **janky** = `jank_type IS NOT NULL AND jank_type != 'None'` → presented but late →
  counts toward FPS, reported separately. (Note: when present_type is Dropped,
  jank_type is also 'Dropped Frame' — already excluded as dropped, don't double-count
  it as janky. So janky requires NOT dropped.)

## Config 02 consequences
- `02_jank_frame.pbtx` keeps `android.surfaceflinger.frametimeline` (primary frame source)
  and `android.input.inputevent` (for structured input when available). The `input`
  atrace category is ALSO required — it's what populates the ACTION_UP/DOWN slices
  used on user builds. Both must be present.
