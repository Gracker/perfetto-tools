import ast
import importlib.util
from pathlib import Path

import pytest

from perfetto_tools.artifacts import ArtifactFailure, trace_processor_relative_path


REPO_ROOT = Path(__file__).resolve().parent.parent
PATCH_PATH = REPO_ROOT / "fps-test" / "_tp_shell_patch.py"


@pytest.mark.parametrize(
    ("system", "machine", "sys_platform", "expected"),
    [
        ("Darwin", "arm64", "darwin", "tools/trace_processor_shell/mac-arm64"),
        ("Darwin", "x86_64", "darwin", "tools/trace_processor_shell/mac-amd64"),
        ("Linux", "aarch64", "linux", "tools/trace_processor_shell/linux-arm64"),
        ("Linux", "x86_64", "linux", "tools/trace_processor_shell/linux-amd64"),
        ("Windows", "AMD64", "win32", "tools/trace_processor_shell/windows-amd64.exe"),
    ],
)
def test_trace_processor_platform_mapping(system, machine, sys_platform, expected):
    assert str(trace_processor_relative_path(system, machine, sys_platform)) == expected


def test_missing_trace_processor_fails_closed_with_setup_guidance(tmp_path):
    module_spec = importlib.util.spec_from_file_location("tp_patch_under_test", PATCH_PATH)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    with pytest.raises(ArtifactFailure, match="missing.*setup"):
        module.local_tp_shell_path(
            repo_root=tmp_path,
            system="Darwin",
            machine="arm64",
            sys_platform="darwin",
        )


def test_sitecustomize_is_only_a_delegate_import_and_install():
    source = (REPO_ROOT / "fps-test" / "sitecustomize.py").read_text()
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]

    assert len(imports) == 1
    assert imports[0].module == "_tp_shell_patch"
    assert [alias.name for alias in imports[0].names] == ["install"]
    assert len(calls) == 1
    assert isinstance(calls[0].value, ast.Call)
    assert isinstance(calls[0].value.func, ast.Name)
    assert calls[0].value.func.id == "install"


def test_compute_fps_explicitly_installs_local_delegate_before_importing_perfetto():
    source = (REPO_ROOT / "fps-test" / "compute_fps.py").read_text()
    install_index = source.index("install_local_trace_processor()")
    perfetto_index = source.index("from perfetto.trace_processor import TraceProcessor")

    assert install_index < perfetto_index
