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
# -g: callchain based. (Add --trace-offcpu for off-cpu time if desired.)
# Captures both stdout+stderr so we can match simpleperf's own diagnostic line
# and give a precise reason instead of a generic "is the app debuggable?".
if ! SP_OUT=$(adb shell simpleperf record -p "${PID}" -g --duration "${DURATION}" -o "${REMOTE}" 2>&1); then
  echo "${SP_OUT}" | tr -d '\r' >&2
  echo "" >&2
  echo "ERROR: simpleperf record failed." >&2
  if echo "${SP_OUT}" | grep -q "not supported on the device"; then
    echo "" >&2
    echo "This is typically a SELinux / build-type limit, NOT a 'not debuggable' problem:" >&2
    echo "  - 'user' builds block perf_event_open for untrusted apps even when the" >&2
    echo "    app is debuggable and perf_event_paranoid is -1." >&2
    echo "  - Fix: use a 'userdebug'/'eng' build, or 'adb root' on an engineering device." >&2
    echo "  - Alternative: Perfetto can capture CPU sampling in one trace via the" >&2
    echo "    'linux.perf' datasource (no separate perf_event_open from simpleperf)." >&2
  fi
  exit 1
fi

echo "[simpleperf] pulling -> ${LOCAL}"
adb pull "${REMOTE}" "${LOCAL}"
adb shell rm -f "${REMOTE}"

echo ""
echo "Done: ${LOCAL}"
echo "View with simpleperf's report_html.py (ships with the Android NDK):"
echo "  python3 report_html.py -i ${LOCAL}"
