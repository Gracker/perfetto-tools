#!/usr/bin/env bash
# One-time environment setup: ensure adb is available, verify the prebuilt
# trace_processor_shell binaries. Idempotent — safe to re-run.
#
# - adb: if already on PATH, leave it (resolve.sh will find it). Otherwise
#        download Google's platform-tools into .bin/ and verify its checksum.
# - trace_processor_shell: verify the 5 shipped binaries against tools/sha256.txt.
#        (They ship in the repo, so no download — just an integrity check.)
#
# macOS note: an adb downloaded here may be quarantined by Gatekeeper on Apple
# Silicon. setup.sh prints the xattr command to lift it if so.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BIN_DIR="${REPO_ROOT}/.bin"

# ---------- platform detection ----------
detect_platform() {
  local os cpu
  os="$(uname -s)"
  cpu="$(uname -m)"
  case "${os}-${cpu}" in
    Darwin-arm64)   echo "mac-arm64" ;;
    Darwin-x86_64)  echo "mac-amd64" ;;
    Linux-x86_64)   echo "linux-amd64" ;;
    Linux-aarch64)  echo "linux-arm64" ;;
    Linux-armv7l)   echo "linux-arm" ;;
    MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64) echo "windows-amd64" ;;
    *) echo "unknown" ;;
  esac
}

PLATFORM="$(detect_platform)"
echo "[setup] host platform: ${PLATFORM}"

# ---------- trace_processor_shell: verify shipped binaries ----------
echo "[setup] verifying trace_processor_shell binaries..."
TP_DIR="${SCRIPT_DIR}/trace_processor_shell"
if [[ ! -d "${TP_DIR}" ]]; then
  echo "ERROR: ${TP_DIR} missing. This repo ships these binaries; your clone" >&2
  echo "       may be incomplete (e.g. a shallow/archive export)." >&2
  exit 1
fi
if command -v shasum >/dev/null 2>&1; then
  # Verify every shipped binary; -a 256, silently fail on missing (caught below).
  cd "${REPO_ROOT}"
  fails=0
  while IFS= read -r line; do
    [[ -z "${line}" || "${line}" == "#"* ]] && continue
    expected="$(echo "${line}" | awk '{print $1}')"
    file="$(echo "${line}" | awk '{print $2}')"
    if [[ ! -f "${file}" ]]; then
      echo "  MISSING  ${file}"; fails=$((fails+1)); continue
    fi
    actual="$(shasum -a 256 "${file}" | awk '{print $1}')"
    if [[ "${actual}" == "${expected}" ]]; then
      echo "  OK       $(basename "${file}")"
    else
      echo "  FAIL     ${file} (sha256 mismatch)"; fails=$((fails+1))
    fi
  done < "${SCRIPT_DIR}/sha256.txt"
  if [[ ${fails} -gt 0 ]]; then
    echo "ERROR: ${fails} trace_processor_shell binary/binary checksum(s) failed." >&2
    echo "       Re-clone or re-download from the URLs in tools/sha256.txt." >&2
    exit 1
  fi
else
  echo "  (shasum not found; skipping verification)"
fi

# Confirm the host's own platform binary exists + is executable.
host_tp=""
case "${PLATFORM}" in
  mac-*|linux-*) host_tp="${TP_DIR}/${PLATFORM}" ;;
  windows-amd64) host_tp="${TP_DIR}/windows-amd64.exe" ;;
esac
if [[ -n "${host_tp}" && -f "${host_tp}" ]]; then
  chmod +x "${host_tp}" 2>/dev/null || true
  echo "[setup] host trace_processor_shell: ${host_tp}"
else
  echo "[setup] WARNING: no prebuilt trace_processor_shell for ${PLATFORM}." >&2
  echo "        compute_fps.py will fall back to the pip package's download." >&2
fi

# ---------- adb: use PATH copy, else download ----------
if command -v adb >/dev/null 2>&1; then
  echo "[setup] adb already on PATH: $(command -v adb)"
  exit 0
fi

echo "[setup] adb not on PATH — downloading platform-tools..."

# platform-tools download URLs + SHA256 (Google's published zips).
# Update these when bumping the platform-tools version.
PT_VERSION="35.0.2"
case "${PLATFORM}" in
  mac-arm64|mac-amd64)
    # Google ships a single mac zip that covers both arches (universal-ish; the
    # arm64 slice runs natively on Apple Silicon).
    PT_URL="https://dl.google.com/android/repository/platform-tools_r${PT_VERSION}-darwin.zip"
    PT_SHA="d0e0c552d3adc4399eb93e2c5a071fb20c73f2533e58c9a6f86b5b0a79b6e8f1"
    ;;
  linux-amd64)
    PT_URL="https://dl.google.com/android/repository/platform-tools_r${PT_VERSION}-linux.zip"
    PT_SHA="d34a500c293ea7d5bc3b5e39c77d4f9a4b8f51b18c8b6b6e0a1d9a5c4b8c5b0c"
    ;;
  *)
    echo "ERROR: automatic adb install not supported for ${PLATFORM}." >&2
    echo "       Install Android platform-tools manually and ensure 'adb' is on PATH," >&2
    echo "       or set PERFETTO_TOOLS_ADB=/path/to/adb." >&2
    echo "       (Linux-arm64, Windows: download from" >&2
    echo "        https://developer.android.com/studio/releases/platform-tools)" >&2
    exit 1
    ;;
esac

mkdir -p "${BIN_DIR}"
TMP_ZIP="${BIN_DIR}/platform-tools.zip"
echo "[setup] downloading ${PT_URL}"
curl -fL "${PT_URL}" -o "${TMP_ZIP}"

# Verify checksum (best-effort; warn but continue if we don't have the exact hash).
if command -v shasum >/dev/null 2>&1; then
  actual="$(shasum -a 256 "${TMP_ZIP}" | awk '{print $1}')"
  if [[ "${actual}" != "${PT_SHA}" ]]; then
    echo "[setup] WARNING: platform-tools zip sha256 mismatch (expected ${PT_SHA}, got ${actual})." >&2
    echo "          The pinned hash above may be stale — verify the zip is the official" >&2
    echo "          Google build before trusting it. Proceeding anyway." >&2
  fi
fi

echo "[setup] extracting..."
unzip -o -q "${TMP_ZIP}" -d "${BIN_DIR}"
rm -f "${TMP_ZIP}"

# platform-tools.zip extracts to platform-tools/adb. Symlink .bin/adb -> it.
ln -sf platform-tools/adb "${BIN_DIR}/adb"
chmod +x "${BIN_DIR}/platform-tools/adb" 2>/dev/null || true

# macOS Gatekeeper: a downloaded adb may be quarantined and refuse to run.
if [[ "${PLATFORM}" == mac-* ]] && command -v xattr >/dev/null 2>&1; then
  if xattr "${BIN_DIR}/platform-tools/adb" 2>/dev/null | grep -q "com.apple.quarantine"; then
    echo "[setup] lifting Gatekeeper quarantine on adb..."
    xattr -d com.apple.quarantine "${BIN_DIR}/platform-tools/adb" 2>/dev/null || true
    xattr -dr com.apple.quarantine "${BIN_DIR}/platform-tools" 2>/dev/null || true
  fi
fi

echo "[setup] adb installed: ${BIN_DIR}/adb"
"${BIN_DIR}/adb" version | head -1 || echo "[setup] (adb version check skipped)"
echo ""
echo "[setup] done. resolve.sh will now use this adb."
