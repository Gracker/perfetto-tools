import hashlib
import platform
import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PERFETTO_VERSION = "57.2"
PERFETTO_PACKAGE_VERSION = "0.57.2"
PERFETTO_COMMIT = "4f2c163974d699295f4b12ab50139c7a1d69f7f6"
PLATFORM_TOOLS_VERSION = "37.0.0"
PLATFORM_TOOLS_HASHES = {
    "darwin": "094a1395683c509fd4d48667da0d8b5ef4d42b2abfcd29f2e8149e2f989357c7",
    "linux": "198ae156ab285fa555987219af237b31102fefe8b9d2bc274708a8d4f2865a07",
}


def _version_metadata():
    metadata = {}
    for line in (REPO_ROOT / "official" / "VERSION").read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    return metadata


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_official_snapshot_is_pinned_to_latest_inspected_main_commit():
    metadata = _version_metadata()

    assert metadata["source"].endswith("/google/perfetto/main/tools/record_android_trace")
    assert metadata["commit"] == PERFETTO_COMMIT
    assert metadata["tool_version"] == f"v{PERFETTO_VERSION}"
    assert metadata["snapshot_date"] == "2026-07-13"


def test_official_script_embeds_matching_perfetto_prebuilts():
    script = (REPO_ROOT / "official" / "record_android_trace").read_text()

    assert f"tools/release/roll-prebuilts v{PERFETTO_VERSION}" in script
    embedded_versions = set(re.findall(r"perfetto-luci-artifacts/(v[0-9.]+)/", script))
    assert embedded_versions == {f"v{PERFETTO_VERSION}"}


def test_python_requirements_match_bundled_perfetto_version():
    runtime = (REPO_ROOT / "requirements.txt").read_text().splitlines()
    development = (REPO_ROOT / "requirements-dev.txt").read_text().splitlines()

    assert f"perfetto=={PERFETTO_PACKAGE_VERSION}" in runtime
    assert "-r requirements.txt" in development
    assert "pytest==8.4.2" in development


def test_every_bundled_trace_processor_matches_recorded_sha256():
    checksum_file = REPO_ROOT / "tools" / "sha256.txt"
    assert f"trace_processor_shell v{PERFETTO_VERSION}" in checksum_file.read_text()

    entries = []
    for line in checksum_file.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        expected, relative_path = line.split(maxsplit=1)
        path = REPO_ROOT / relative_path
        entries.append(path)
        assert path.is_file()
        assert _sha256(path) == expected

    assert {path.name for path in entries} == {
        "mac-amd64",
        "mac-arm64",
        "linux-amd64",
        "linux-arm64",
        "windows-amd64.exe",
    }


def test_host_trace_processor_reports_pinned_version():
    host = (platform.system(), platform.machine().lower())
    names = {
        ("Darwin", "arm64"): "mac-arm64",
        ("Darwin", "x86_64"): "mac-amd64",
        ("Linux", "aarch64"): "linux-arm64",
        ("Linux", "x86_64"): "linux-amd64",
        ("Windows", "amd64"): "windows-amd64.exe",
        ("Windows", "x86_64"): "windows-amd64.exe",
    }
    binary = REPO_ROOT / "tools" / "trace_processor_shell" / names[host]
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True)

    assert f"Perfetto v{PERFETTO_VERSION}-" in result.stdout


def test_platform_tools_pin_uses_verified_stable_archives_and_fails_closed():
    setup = (REPO_ROOT / "tools" / "setup.sh").read_text()

    assert f'PT_VERSION="{PLATFORM_TOOLS_VERSION}"' in setup
    for expected in PLATFORM_TOOLS_HASHES.values():
        assert expected in setup
    assert "Proceeding anyway" not in setup
    assert 'rm -f "${TMP_ZIP}"' in setup


def test_setup_honors_the_shared_adb_resolver_before_downloading():
    setup = (REPO_ROOT / "tools" / "setup.sh").read_text()

    assert 'if ADB="$("${SCRIPT_DIR}/resolve.sh" adb 2>/dev/null)"; then' in setup
    assert "if command -v adb" not in setup
