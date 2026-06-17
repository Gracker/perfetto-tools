#!/usr/bin/env bash
# Automated swipe-based FPS test.
#
# Flow:
#   1. (user has already navigated the app to the target screen)
#   2. Start a Perfetto trace in the background (config 02_jank_frame, ~12s).
#   3. Run the swipe pattern: 3 up, then 3 down (from swipe_pattern.txt).
#   4. Wait for the trace to finish and pull it.
#   5. Compute per-source FPS / dropped frames with compute_fps.py.
#
# Usage: run_fps_test.sh [duration_sec] [package_for_gfxinfo]
#   duration_sec default 12. The swipe pattern alone takes ~7s (1 settle + 6
#   swipes) plus adb round-trips; the trace must outlast it with margin, so the
#   default is generous.
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
TRACE="${OUT_DIR}/${TS}_fps.perftrace"
CAPTURE_LOG="${OUT_DIR}/${TS}_capture.log"

echo "[fps-test] duration: ${DURATION}s"
echo "[fps-test] output  : ${TRACE}"
echo "[fps-test] capture log: ${CAPTURE_LOG}"

# 1. Start the trace in the background. --no-open so capture returns when done.
#    Redirect the background capture's stdout+stderr to a log file so its
#    perfetto output cannot interleave with this script's foreground I/O (which
#    previously truncated the swipe loop to a single iteration).
echo "[fps-test] starting trace (background)..."
"${CAPTURE}" --config jank --time "${DURATION}" --output "${TRACE}" --no-open \
  >"${CAPTURE_LOG}" 2>&1 &
CAPTURE_PID=$!

# Optional auxiliary: reset gfxinfo / SurfaceFlinger latency counters before swipes.
if [[ -n "${GFXINFO_PKG}" ]]; then
  "${GFXDUMP}" reset "${GFXINFO_PKG}" || echo "[fps-test] gfxinfo reset failed (non-fatal)" >&2
fi

# Give the tracer time to actually start before swiping. record_android_trace
# may push/sideload tracebox on first run, so 1s is not always enough; 2s is a
# safer floor.
sleep 2

# 2. Run the swipe pattern, recording per-fling timestamps for the tier-3
#    device-clock fallback. These use DEVICE realtime ns (adb shell date +%s%N),
#    NOT host time — avoids host/device clock skew and macOS BSD date (no %N).
SWIPE_LOG="${OUT_DIR}/${TS}_swipe.log"
: > "${SWIPE_LOG}"

# </dev/null on every adb call below: adb reads stdin interactively, and inside
# a `while read` loop it would otherwise consume the swipe-pattern lines feeding
# the loop, truncating it to a single iteration. Pinning adb's stdin to
# /dev/null breaks that leak.
device_now_ns() { adb shell date +%s%N </dev/null | tr -d '\r'; }

run_swipes() {
  while read -r dir x1 y1 x2 y2 dur gap _rest; do
    # Skip comments / blanks.
    [[ -z "${dir}" || "${dir}" == "#"* ]] && continue
    echo "[fps-test] swipe ${dir} ..."
    adb shell input swipe "${x1}" "${y1}" "${x2}" "${y2}" "${dur}" </dev/null
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
  echo "  - on macOS Python, trace_processor_shell may need a CA bundle:" >&2
  echo "    export SSL_CERT_FILE=\"\$(python3 -c 'import certifi;print(certifi.where())')\"" >&2
  exit 1
}

# 5. Optional auxiliary cross-check: dump gfxinfo framestats + SF latency.
if [[ -n "${GFXINFO_PKG}" ]]; then
  echo "[fps-test] dumping gfxinfo / SurfaceFlinger cross-check..."
  "${GFXDUMP}" dump "${GFXINFO_PKG}" "${OUT_DIR}" || echo "[fps-test] gfxinfo dump failed (non-fatal)" >&2
fi

echo ""
echo "[fps-test] done. Report next to trace: ${TRACE}.fps_report.txt"
