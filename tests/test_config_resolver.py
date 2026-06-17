import os
import sys
import pytest

# Make capture/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'capture'))
from perfetto_capture import resolve_config, list_configs, apply_duration, ConfigError


CONFIGS_DIR = os.path.join(os.path.dirname(__file__), '..', 'configs')


def test_resolve_by_full_number_prefix():
    # "00" → 00_general.pbtx
    p = resolve_config("00", CONFIGS_DIR)
    assert os.path.basename(p) == "00_general.pbtx"


def test_resolve_by_keyword():
    # "jank" → 02_jank_frame.pbtx (keyword match in filename)
    p = resolve_config("jank", CONFIGS_DIR)
    assert os.path.basename(p) == "02_jank_frame.pbtx"


def test_resolve_case_insensitive():
    p = resolve_config("JANK", CONFIGS_DIR)
    assert os.path.basename(p) == "02_jank_frame.pbtx"


def test_resolve_returns_absolute_path():
    p = resolve_config("general", CONFIGS_DIR)
    assert os.path.isabs(p)


def test_unknown_name_raises():
    with pytest.raises(ConfigError) as exc:
        resolve_config("nonexistent", CONFIGS_DIR)
    assert "nonexistent" in str(exc.value)


def test_ambiguous_match_raises_with_candidates():
    # "0" matches all six files → ambiguous
    with pytest.raises(ConfigError):
        resolve_config("0", CONFIGS_DIR)


def test_list_configs_returns_all():
    names = list_configs(CONFIGS_DIR)
    assert len(names) == 6
    assert "00_general" in names
    assert "05_full" in names


def test_resolve_strips_pbtx_extension_in_input():
    # User passes the full filename
    p = resolve_config("02_jank_frame.pbtx", CONFIGS_DIR)
    assert os.path.basename(p) == "02_jank_frame.pbtx"


def test_apply_duration_replaces_existing():
    text = "duration_ms: 10000\nbuffers { size_kb: 1024 }\n"
    out = apply_duration(text, 8)
    assert "duration_ms: 8000" in out
    assert "duration_ms: 10000" not in out
    # Only the duration line changes; the rest is untouched.
    assert "buffers { size_kb: 1024 }" in out


def test_apply_duration_inserts_when_missing():
    text = "buffers { size_kb: 1024 }\n"
    out = apply_duration(text, 5)
    assert out.startswith("duration_ms: 5000\n")


def test_apply_duration_only_first_occurrence():
    # A top-level duration_ms is rewritten; a nested datasource one is preserved.
    text = "duration_ms: 10000\ndata_sources { config { duration_ms: 999 } }\n"
    out = apply_duration(text, 3)
    assert "duration_ms: 3000" in out
    assert "duration_ms: 999" in out  # nested one preserved


def test_apply_duration_nested_only_inserts_top_level():
    # No TOP-LEVEL duration_ms, only an INDENTED nested one on its own line
    # (the realistic multi-line shape). Must insert at top, NOT touch the nested.
    text = (
        "buffers { size_kb: 1024 }\n"
        "data_sources {\n"
        "    config {\n"
        "        duration_ms: 999\n"   # indented → not top-level
        "    }\n"
        "}\n"
    )
    out = apply_duration(text, 4)
    assert out.startswith("duration_ms: 4000\n")
    assert "        duration_ms: 999" in out  # nested line untouched


def test_apply_duration_rejects_nonpositive():
    with pytest.raises(ConfigError):
        apply_duration("duration_ms: 1000\n", 0)
    with pytest.raises(ConfigError):
        apply_duration("duration_ms: 1000\n", -5)
