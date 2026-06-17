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
