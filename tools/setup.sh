#!/usr/bin/env bash
# One-time environment setup: ensure adb is available, verify the prebuilt
# trace_processor_shell binaries. Idempotent — safe to re-run.
#
# - adb: if resolve.sh already finds one, leave it. Otherwise download Google's
#        platform-tools into .bin/ and verify its checksum.
# - trace_processor_shell: verify the 5 shipped binaries against tools/sha256.txt.
#        (They ship in the repo, so no download — just an integrity check.)
#
# macOS note: an adb downloaded here may be quarantined by Gatekeeper on Apple
# Silicon. setup.sh prints the xattr command to lift it if so.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BIN_DIR="${REPO_ROOT}/.bin"

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "ERROR: shasum or sha256sum is required for tool verification." >&2
    return 1
  fi
}

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
cd "${REPO_ROOT}"
fails=0
while IFS= read -r line; do
  [[ -z "${line}" || "${line}" == "#"* ]] && continue
  expected="$(echo "${line}" | awk '{print $1}')"
  file="$(echo "${line}" | awk '{print $2}')"
  if [[ ! -f "${file}" ]]; then
    echo "  MISSING  ${file}"; fails=$((fails+1)); continue
  fi
  actual="$(sha256_file "${file}")"
  if [[ "${actual}" == "${expected}" ]]; then
    echo "  OK       $(basename "${file}")"
  else
    echo "  FAIL     ${file} (sha256 mismatch)"; fails=$((fails+1))
  fi
done < "${SCRIPT_DIR}/sha256.txt"
if [[ ${fails} -gt 0 ]]; then
  echo "ERROR: ${fails} trace_processor_shell binary checksum(s) failed." >&2
  echo "       Re-clone or re-download from the URLs in tools/sha256.txt." >&2
  exit 1
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

PYTHON="$("${SCRIPT_DIR}/resolve.sh" python)"
echo "[setup] Python 3.9+: ${PYTHON}"

# ---------- adb: use the shared resolver, else download ----------
if ADB="$("${SCRIPT_DIR}/resolve.sh" adb 2>/dev/null)"; then
  echo "[setup] adb available: ${ADB}"
  exit 0
fi

echo "[setup] adb not on PATH — downloading platform-tools..."

# platform-tools download URLs + SHA256 (Google's published zips).
# Update these when bumping the platform-tools version.
PT_VERSION="37.0.0"
case "${PLATFORM}" in
  mac-arm64|mac-amd64)
    # Google ships a single mac zip that covers both arches (universal-ish; the
    # arm64 slice runs natively on Apple Silicon).
    PT_URL="https://dl.google.com/android/repository/platform-tools_r${PT_VERSION}-darwin.zip"
    PT_SHA="094a1395683c509fd4d48667da0d8b5ef4d42b2abfcd29f2e8149e2f989357c7"
    ;;
  linux-amd64)
    PT_URL="https://dl.google.com/android/repository/platform-tools_r${PT_VERSION}-linux.zip"
    PT_SHA="198ae156ab285fa555987219af237b31102fefe8b9d2bc274708a8d4f2865a07"
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
for tool in curl unzip; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "ERROR: ${tool} is required to install platform-tools." >&2
    exit 1
  fi
done
echo "[setup] downloading ${PT_URL}"
curl -fL "${PT_URL}" -o "${TMP_ZIP}"

# Never install an archive that does not match the reviewed Google download.
actual="$(sha256_file "${TMP_ZIP}")"
if [[ "${actual}" != "${PT_SHA}" ]]; then
  echo "ERROR: platform-tools zip sha256 mismatch." >&2
  echo "       expected ${PT_SHA}" >&2
  echo "       actual   ${actual}" >&2
  rm -f "${TMP_ZIP}"
  exit 1
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
