#!/usr/bin/env bash
# Bootstrap a repository-owned Python environment and verified host tools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BIN_DIR="${REPO_ROOT}/.bin"
# The manifest path is resolved relative to this script.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/tool-versions.env"

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "ERROR: shasum or sha256sum is required to bootstrap uv." >&2
    return 1
  fi
}

download_file() {
  local url="$1" destination="$2"
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -fL "${url}" -o "${destination}"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only -O "${destination}" "${url}"
  else
    echo "ERROR: curl or wget is required for the first setup." >&2
    return 1
  fi
}

host_key() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) echo "MAC_ARM64" ;;
    Darwin-x86_64) echo "MAC_AMD64" ;;
    Linux-aarch64|Linux-arm64) echo "LINUX_ARM64" ;;
    Linux-x86_64) echo "LINUX_AMD64" ;;
    *)
      echo "ERROR: unsupported Unix host: $(uname -s)/$(uname -m)" >&2
      return 1
      ;;
  esac
}

ensure_uv() {
  local key asset_var sha_var asset expected archive temp_dir extracted uv_bin
  key="$(host_key)"
  asset_var="UV_${key}_ASSET"
  sha_var="UV_${key}_SHA256"
  asset="${!asset_var}"
  expected="${!sha_var}"
  uv_bin="${BIN_DIR}/uv/uv"
  if [[ -x "${uv_bin}" ]] && [[ "$("${uv_bin}" --version)" == "uv ${UV_VERSION}"* ]]; then
    echo "${uv_bin}"
    return 0
  fi
  command -v tar >/dev/null 2>&1 || {
    echo "ERROR: tar is required for the first setup." >&2
    return 1
  }
  mkdir -p "${BIN_DIR}/downloads"
  archive="${BIN_DIR}/downloads/${asset}"
  download_file "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${asset}" "${archive}"
  actual="$(sha256_file "${archive}")"
  if [[ "${actual}" != "${expected}" ]]; then
    rm -f "${archive}"
    echo "ERROR: uv archive checksum mismatch (expected ${expected}, got ${actual})." >&2
    return 1
  fi
  temp_dir="$(mktemp -d "${BIN_DIR}/uv.XXXXXX")"
  tar -xzf "${archive}" -C "${temp_dir}"
  extracted="${temp_dir}/${asset%.tar.gz}/uv"
  if [[ ! -x "${extracted}" ]]; then
    rm -rf "${temp_dir}"
    echo "ERROR: uv archive layout is invalid: ${asset}" >&2
    return 1
  fi
  rm -rf "${BIN_DIR}/uv"
  mkdir -p "${BIN_DIR}/uv"
  mv "${extracted}" "${uv_bin}"
  chmod +x "${uv_bin}"
  rm -rf "${temp_dir}"
  echo "${uv_bin}"
}

UV_BIN="$(ensure_uv)"
export UV_CACHE_DIR="${BIN_DIR}/uv-cache"
export UV_PYTHON_INSTALL_DIR="${BIN_DIR}/python"
export UV_MANAGED_PYTHON=1
export UV_NO_CONFIG=1
export UV_PROJECT_ENVIRONMENT="${REPO_ROOT}/.venv"

cd "${REPO_ROOT}"
"${UV_BIN}" sync --frozen --python "${PYTHON_VERSION}"
exec "${REPO_ROOT}/.venv/bin/python" "${SCRIPT_DIR}/setup_runtime.py" "$@"
