#!/usr/bin/env bash
# Automated swipe-based FPS test with deterministic capture readiness.
# Usage: run_fps_test.sh [duration_sec] [package_for_gfxinfo]
set -euo pipefail

if (( $# > 2 )); then
  echo "ERROR: usage: run_fps_test.sh [duration_sec] [package_for_gfxinfo]" >&2
  exit 2
fi

DURATION="${1:-12}"
GFXINFO_PKG="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CAPTURE="${REPO_ROOT}/capture/capture.sh"
COMPUTE="${SCRIPT_DIR}/compute_fps.py"
PATTERN="${SCRIPT_DIR}/swipe_pattern.txt"
GFXDUMP="${SCRIPT_DIR}/dump_gfxinfo.sh"
DOCTOR="${REPO_ROOT}/tools/doctor.py"
OUT_DIR="${REPO_ROOT}/traces"
STARTUP_TIMEOUT=20

PYTHON="$("${REPO_ROOT}/tools/resolve.sh" python)"

if ! "${PYTHON}" -c \
  'import math, sys; value=float(sys.argv[1]); raise SystemExit(not (math.isfinite(value) and value > 0))' \
  "${DURATION}" 2>/dev/null; then
  echo "ERROR: duration must be a finite positive number of seconds: ${DURATION}" >&2
  exit 2
fi

if [[ -n "${GFXINFO_PKG}" ]] &&
   [[ ! "${GFXINFO_PKG}" =~ ^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$ ]]; then
  echo "ERROR: package_for_gfxinfo must be an Android package identifier: ${GFXINFO_PKG}" >&2
  exit 2
fi

# This verifies exact managed package/tool versions and requires an API 31+
# authorized device before any capture process or counter reset is started.
"${PYTHON}" "${DOCTOR}" --device --feature fps
ADB="$("${REPO_ROOT}/tools/resolve.sh" adb)"

mkdir -p "${OUT_DIR}"
TS="$(date +%Y%m%d_%H%M%S)"
TRACE="${OUT_DIR}/${TS}_fps.perfetto-trace"
CAPTURE_LOG="${OUT_DIR}/${TS}_capture.log"
SWIPE_LOG="${OUT_DIR}/${TS}_swipe.log"
CAPTURE_PID=""

cleanup() {
  local exit_status=$?
  trap - EXIT INT TERM
  if [[ -n "${CAPTURE_PID}" ]] && kill -0 "${CAPTURE_PID}" 2>/dev/null; then
    echo "[fps-test] stopping background tracer..." >&2
    kill -TERM "${CAPTURE_PID}" 2>/dev/null || true
    wait "${CAPTURE_PID}" 2>/dev/null || true
  fi
  return "${exit_status}"
}
trap cleanup EXIT INT TERM

echo "[fps-test] duration   : ${DURATION}s"
echo "[fps-test] output     : ${TRACE}"
echo "[fps-test] capture log: ${CAPTURE_LOG}"
echo "[fps-test] starting trace (background)..."
PYTHONUNBUFFERED=1 "${CAPTURE}" --config jank --time "${DURATION}" \
  --output "${TRACE}" --no-open >"${CAPTURE_LOG}" 2>&1 &
CAPTURE_PID=$!

deadline=$((SECONDS + STARTUP_TIMEOUT))
while ! grep -Fq "Trace started" "${CAPTURE_LOG}"; do
  if ! kill -0 "${CAPTURE_PID}" 2>/dev/null; then
    if wait "${CAPTURE_PID}"; then
      capture_status=0
    else
      capture_status=$?
    fi
    CAPTURE_PID=""
    if (( capture_status == 0 )); then
      capture_status=1
    fi
    echo "ERROR: capture exited before it became ready (exit ${capture_status})." >&2
    sed -n '1,160p' "${CAPTURE_LOG}" >&2
    exit "${capture_status}"
  fi
  if (( SECONDS >= deadline )); then
    echo "ERROR: capture did not report readiness within ${STARTUP_TIMEOUT}s." >&2
    sed -n '1,160p' "${CAPTURE_LOG}" >&2
    exit 1
  fi
  sleep 0.2
done
echo "[fps-test] trace ready; starting swipe pattern."

if [[ -n "${GFXINFO_PKG}" ]]; then
  "${GFXDUMP}" reset "${GFXINFO_PKG}" ||
    echo "[fps-test] gfxinfo reset failed (non-fatal)" >&2
fi

: >"${SWIPE_LOG}"
device_now_ns() { "${ADB}" shell date +%s%N </dev/null | tr -d '\r'; }

while read -r dir x1 y1 x2 y2 dur gap _rest; do
  [[ -z "${dir}" || "${dir}" == "#"* ]] && continue
  if ! kill -0 "${CAPTURE_PID}" 2>/dev/null; then
    echo "ERROR: capture ended before the swipe pattern completed." >&2
    sed -n '1,160p' "${CAPTURE_LOG}" >&2
    exit 1
  fi
  echo "[fps-test] swipe ${dir} ..."
  "${ADB}" shell input swipe "${x1}" "${y1}" "${x2}" "${y2}" "${dur}" </dev/null
  start_ns="$(device_now_ns)"
  sleep "$("${PYTHON}" -c "print(${gap}/1000.0)")"
  end_ns="$(device_now_ns)"
  echo "${start_ns} ${end_ns}" >>"${SWIPE_LOG}"
done <"${PATTERN}"

echo "[fps-test] waiting for trace to finish..."
if wait "${CAPTURE_PID}"; then
  CAPTURE_PID=""
else
  capture_status=$?
  CAPTURE_PID=""
  echo "ERROR: capture failed after swipes (exit ${capture_status})." >&2
  sed -n '1,160p' "${CAPTURE_LOG}" >&2
  exit "${capture_status}"
fi

echo "[fps-test] computing FPS..."
PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON}" "${COMPUTE}" \
  "${TRACE}" --swipe-log "${SWIPE_LOG}" || {
  echo "compute_fps.py failed. Check the trace and run ./tools/setup.sh again." >&2
  exit 1
}

if [[ -n "${GFXINFO_PKG}" ]]; then
  echo "[fps-test] dumping gfxinfo / SurfaceFlinger cross-check..."
  "${GFXDUMP}" dump "${GFXINFO_PKG}" "${OUT_DIR}" ||
    echo "[fps-test] gfxinfo dump failed (non-fatal)" >&2
fi

echo "[fps-test] done. Report next to trace: ${TRACE}.fps_report.txt"
