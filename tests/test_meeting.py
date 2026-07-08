"""
tests/test_meeting.py — Unit test hàm thuần của tính năng Meeting Note (không gọi mạng).
Chạy: .venv/bin/python -m pytest tests/ -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules import meeting as M


# ===== normalize_model =====

def test_model_alias_flash():
    assert M.normalize_model("flash") == "gemini-2.5-flash"

def test_model_alias_pro():
    assert M.normalize_model("pro") == "gemini-2.5-pro"

def test_model_full_passthrough():
    assert M.normalize_model("gemini-2.5-pro") == "gemini-2.5-pro"

def test_model_invalid_defaults():
    assert M.normalize_model("gpt-4") == M.DEFAULT_MODEL
    assert M.normalize_model(None) == M.DEFAULT_MODEL
    assert M.normalize_model("") == M.DEFAULT_MODEL


# ===== normalize_language =====

def test_language_valid():
    assert M.normalize_language("en") == "en"

def test_language_invalid_defaults_vi():
    assert M.normalize_language("zz") == "vi"
    assert M.normalize_language(None) == "vi"


# ===== resolve_api_key =====

def test_resolve_key_prefers_config():
    assert M.resolve_api_key("  abc123  ") == "abc123"

def test_resolve_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "envkey")
    assert M.resolve_api_key("") == "envkey"
    assert M.resolve_api_key("  ") == "envkey"

def test_resolve_key_empty_when_nothing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert M.resolve_api_key("") == ""


# ===== _compose_prompt =====

def test_compose_includes_style_guide():
    p = M._compose_prompt("Base", "action", "vi")
    assert "Action Items" in p
    assert "tiếng Việt" in p

def test_compose_language_english():
    p = M._compose_prompt("Base", "short", "en")
    assert "English" in p

def test_compose_default_base_when_empty():
    p = M._compose_prompt("", "structured", "vi")
    assert "trợ lý" in p.lower()


# ===== save / read source + delete roundtrip =====

def test_md_and_source_roundtrip_then_delete():
    title = "Meeting_Note_TESTPYTEST_0001"
    filepath, filename = M.save_as_md(title, "noi dung tom tat", subtitle="text · test")
    try:
        assert os.path.exists(filepath)
        M.save_source(filename, "van ban nguon")
        assert M.read_source(filename) == "van ban nguon"
        assert filename in M.list_meeting_files(100)
    finally:
        assert M.delete_note_files(filename) is True
        assert not os.path.exists(filepath)
        assert M.read_source(filename) is None  # source bị xoá theo

def test_read_source_missing_returns_none():
    assert M.read_source("Meeting_Note_khong_ton_tai.md") is None

def test_save_source_ignores_empty():
    M.save_source("Meeting_Note_empty.md", "   ")
    assert M.read_source("Meeting_Note_empty.md") is None
