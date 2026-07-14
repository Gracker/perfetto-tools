import ast
import hashlib
import platform
import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PERFETTO_VERSION = "57.2"
PERFETTO_PACKAGE_VERSION = "0.57.2"
PERFETTO_COMMIT = "96c76d5ad9a352a216577082de11fed9fa68e561"
RECORD_HELPER_SHA256 = "377178a17bb87272e46616ad0e7b5814ea09037ba0a2ee55bf9c242d592b1559"
PLATFORM_TOOLS_VERSION = "37.0.0"
PLATFORM_TOOLS_HASHES = {
    "darwin": "094a1395683c509fd4d48667da0d8b5ef4d42b2abfcd29f2e8149e2f989357c7",
    "linux": "198ae156ab285fa555987219af237b31102fefe8b9d2bc274708a8d4f2865a07",
    "windows": "4fe305812db074cea32903a489d061eb4454cbc90a49e8fea677f4b7af764918",
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


def _tool_versions():
    values = {}
    for line in (REPO_ROOT / "tools" / "tool-versions.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_official_snapshot_is_pinned_to_latest_inspected_main_commit():
    metadata = _version_metadata()

    assert metadata["source"].endswith("/google/perfetto/main/tools/record_android_trace")
    assert metadata["commit"] == PERFETTO_COMMIT
    assert metadata["tool_version"] == f"v{PERFETTO_VERSION}"
    assert metadata["snapshot_date"] == "2026-07-14"
    assert metadata["sha256"] == RECORD_HELPER_SHA256
    assert _sha256(REPO_ROOT / "official" / "record_android_trace") == RECORD_HELPER_SHA256


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
        assert path.is_file()
        assert _sha256(path) == expected
        if relative_path.startswith("tools/trace_processor_shell/"):
            entries.append(path)

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
    versions = _tool_versions()
    setup_runtime = (REPO_ROOT / "tools" / "setup_runtime.py").read_text()

    assert versions["PLATFORM_TOOLS_VERSION"] == PLATFORM_TOOLS_VERSION
    for expected in PLATFORM_TOOLS_HASHES.values():
        assert expected in versions.values()
    assert "Proceeding anyway" not in setup_runtime
    assert "checksum mismatch" in setup_runtime


def test_setup_delegates_host_tool_installation_to_shared_runtime():
    setup = (REPO_ROOT / "tools" / "setup.sh").read_text()
    setup_runtime = (REPO_ROOT / "tools" / "setup_runtime.py").read_text()

    assert 'setup_runtime.py" "$@"' in setup
    assert "ensure_platform_tools" in setup_runtime
    assert 'shutil.which("adb")' not in setup_runtime


def test_every_android_tracebox_matches_upstream_manifest_and_checksum_file():
    official_source = (REPO_ROOT / "official" / "record_android_trace").read_text()
    tree = ast.parse(official_source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "TRACEBOX_MANIFEST"
            for target in node.targets
        )
    )
    manifest = ast.literal_eval(assignment.value)
    upstream = {
        entry["arch"]: entry for entry in manifest if entry["arch"].startswith("android-")
    }
    tracebox_dir = REPO_ROOT / "tools" / "tracebox"
    local = {path.name: path for path in tracebox_dir.glob("android-*")}

    checksum_entries = {}
    for line in (REPO_ROOT / "tools" / "sha256.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            sha256, relative = line.split(maxsplit=1)
            checksum_entries[relative] = sha256

    assert set(local) == {"android-arm", "android-arm64", "android-x86", "android-x64"}
    assert set(upstream) == set(local)
    for name, path in local.items():
        entry = upstream[name]
        assert path.stat().st_size == entry["file_size"]
        assert _sha256(path) == entry["sha256"]
        assert checksum_entries[f"tools/tracebox/{name}"] == entry["sha256"]


def test_verify_workflow_covers_supported_host_families_and_native_bootstraps():
    workflow = (REPO_ROOT / ".github" / "workflows" / "verify.yml").read_text()

    for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert runner in workflow
    assert "./tools/setup.sh" in workflow
    assert r".\tools\setup.ps1" in workflow
    assert "tools/doctor.py" in workflow
    assert "capture/capture.sh --list-configs" in workflow
    assert r"capture\capture.bat --list-configs" in workflow
    assert "shellcheck" in workflow
    assert "sha256sum --check" in workflow


def test_tool_drift_workflow_is_scheduled_manual_and_isolated():
    workflow = (REPO_ROOT / ".github" / "workflows" / "tool-drift.yml").read_text()

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "tools/check_updates.py --check" in workflow
    assert "pull_request:" not in workflow
