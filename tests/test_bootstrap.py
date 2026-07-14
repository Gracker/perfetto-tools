import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS = REPO_ROOT / "tools" / "tool-versions.env"


def read_env_manifest(path):
    values = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_bootstrap_manifest_pins_managed_runtime():
    values = read_env_manifest(VERSIONS)

    assert values["PYTHON_VERSION"] == "3.13.14"
    assert values["UV_VERSION"] == "0.11.28"
    assert values["PLATFORM_TOOLS_VERSION"] == "37.0.0"


def test_bootstrap_manifest_has_verified_uv_artifacts_for_supported_hosts():
    values = read_env_manifest(VERSIONS)
    expected = {
        "MAC_ARM64": "33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232",
        "MAC_AMD64": "2ad79983127ffca7d77b77ce6a24278d7e4f7b817a1acf72fea5f8124b4aac5e",
        "LINUX_ARM64": "03e9fe0a81b0718d0bc84625de3885df6cc3f89a8b6af6121d6b9f6113fb6533",
        "LINUX_AMD64": "e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224",
        "WINDOWS_AMD64": "0a23463216d09c6a72ff80ef5dc5a795f07dc1575cb84d24596c2f124a441b7b",
    }

    for host, sha256 in expected.items():
        assert values[f"UV_{host}_ASSET"]
        assert values[f"UV_{host}_SHA256"] == sha256


def test_platform_tools_archives_are_versioned_and_verified():
    values = read_env_manifest(VERSIONS)

    assert values["PT_MAC_URL"].endswith("platform-tools_r37.0.0-darwin.zip")
    assert values["PT_LINUX_AMD64_URL"].endswith("platform-tools_r37.0.0-linux.zip")
    assert values["PT_WINDOWS_AMD64_URL"].endswith("platform-tools_r37.0.0-win.zip")
    assert values["PT_MAC_SHA256"] == "094a1395683c509fd4d48667da0d8b5ef4d42b2abfcd29f2e8149e2f989357c7"
    assert values["PT_LINUX_AMD64_SHA256"] == "198ae156ab285fa555987219af237b31102fefe8b9d2bc274708a8d4f2865a07"
    assert values["PT_WINDOWS_AMD64_SHA256"] == "4fe305812db074cea32903a489d061eb4454cbc90a49e8fea677f4b7af764918"


def test_native_bootstraps_use_the_shared_version_manifest():
    unix_setup = (REPO_ROOT / "tools" / "setup.sh").read_text()
    windows_setup = (REPO_ROOT / "tools" / "setup.ps1").read_text()

    assert 'source "${SCRIPT_DIR}/tool-versions.env"' in unix_setup
    assert "tool-versions.env" in windows_setup
    assert "ConvertFrom-EnvFile" in windows_setup
    assert "sync --frozen" in unix_setup
    assert "sync --frozen" in windows_setup
    assert os.access(REPO_ROOT / "tools" / "setup.sh", os.X_OK)


def test_default_runtime_setup_does_not_accept_path_adb():
    setup_runtime = (REPO_ROOT / "tools" / "setup_runtime.py").read_text()

    assert 'shutil.which("adb")' not in setup_runtime
    assert "PERFETTO_TOOLS_ADB" in setup_runtime
    assert "ensure_platform_tools" in setup_runtime


def test_setup_and_doctor_verify_the_pinned_official_helper():
    setup_runtime = (REPO_ROOT / "tools" / "setup_runtime.py").read_text()
    doctor = (REPO_ROOT / "tools" / "doctor.py").read_text()

    assert "verify_official_helper" in setup_runtime
    assert "verify_official_helper" in doctor


def test_python_project_and_requirements_have_matching_direct_pins():
    project = (REPO_ROOT / "pyproject.toml").read_text()
    runtime = (REPO_ROOT / "requirements.txt").read_text().splitlines()
    development = (REPO_ROOT / "requirements-dev.txt").read_text().splitlines()

    assert 'requires-python = ">=3.10,<3.15"' in project
    assert '"perfetto==0.57.2"' in project
    assert '"protobuf==7.35.1"' in project
    assert '"pytest==9.1.1"' in project
    assert runtime == ["perfetto==0.57.2", "protobuf==7.35.1"]
    assert development == ["-r requirements.txt", "pytest==9.1.1"]


def test_windows_capture_prefers_repository_virtual_environment():
    batch = (REPO_ROOT / "capture" / "capture.bat").read_text()
    managed = batch.index(r".venv\Scripts\python.exe")
    launcher = batch.index("where py")

    assert managed < launcher


def test_python_entrypoints_enforce_the_documented_upper_bound():
    capture = (REPO_ROOT / "capture" / "perfetto_capture.py").read_text()
    compute = (REPO_ROOT / "fps-test" / "compute_fps.py").read_text()

    assert "sys.version_info >= (3, 15)" in capture
    assert "sys.version_info >= (3, 15)" in compute


def test_platform_tools_activation_restores_previous_install_on_failure(
    tmp_path, monkeypatch
):
    from tools.setup_runtime import SetupError, activate_managed_directory

    managed = tmp_path / "platform-tools"
    staging = tmp_path / ".platform-tools.new"
    backup = tmp_path / ".platform-tools.old"
    managed.mkdir()
    staging.mkdir()
    (managed / "marker").write_text("old")
    (staging / "marker").write_text("new")
    original_rename = Path.rename

    def fail_staging(self, target):
        if self == staging:
            raise OSError("activation failed")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_staging)

    with pytest.raises(SetupError, match="activate"):
        activate_managed_directory(staging, managed, backup)

    assert (managed / "marker").read_text() == "old"


def test_platform_tools_activation_wraps_backup_failure_without_touching_install(
    tmp_path, monkeypatch
):
    from tools.setup_runtime import SetupError, activate_managed_directory

    managed = tmp_path / "platform-tools"
    staging = tmp_path / ".platform-tools.new"
    backup = tmp_path / ".platform-tools.old"
    managed.mkdir()
    staging.mkdir()
    (managed / "marker").write_text("old")
    (staging / "marker").write_text("new")
    original_rename = Path.rename

    def fail_managed(self, target):
        if self == managed:
            raise OSError("backup failed")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_managed)

    with pytest.raises(SetupError, match="preserve the current Platform-Tools"):
        activate_managed_directory(staging, managed, backup)

    assert (managed / "marker").read_text() == "old"
    assert (staging / "marker").read_text() == "new"


def test_first_platform_tools_activation_failure_does_not_claim_a_restore(
    tmp_path, monkeypatch
):
    from tools.setup_runtime import SetupError, activate_managed_directory

    managed = tmp_path / "platform-tools"
    staging = tmp_path / ".platform-tools.new"
    backup = tmp_path / ".platform-tools.old"
    staging.mkdir()

    def fail_staging(self, target):
        raise OSError("activation failed")

    monkeypatch.setattr(Path, "rename", fail_staging)

    with pytest.raises(SetupError, match="no previous install was changed"):
        activate_managed_directory(staging, managed, backup)


def test_setup_main_translates_host_filesystem_errors(capsys, monkeypatch):
    import tools.setup_runtime as setup_runtime

    def fail_manifest():
        raise OSError("manifest is unreadable")

    monkeypatch.setattr(setup_runtime, "read_env_manifest", fail_manifest)

    assert setup_runtime.main([]) == 3
    assert "host setup operation failed: manifest is unreadable" in capsys.readouterr().err
