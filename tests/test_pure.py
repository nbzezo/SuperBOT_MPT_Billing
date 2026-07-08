"""
tests/test_pure.py — Unit test cho các hàm thuần (không cần browser/mạng).
Chạy: .venv/bin/python -m pytest tests/ -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils import pad_right, pad_left
from modules.nospam import normalize_vn_phone
from modules.mpt_routing import build_bad_alert, build_drop_alert, _md_escape, _code_safe


# ===== normalize_vn_phone =====

def test_normalize_basic_0():
    assert normalize_vn_phone("0916705392") == "0916705392"

def test_normalize_84_prefix():
    assert normalize_vn_phone("84916705392") == "0916705392"

def test_normalize_plus84():
    assert normalize_vn_phone("+84916705392") == "0916705392"

def test_normalize_9_digits():
    assert normalize_vn_phone("916705392") == "0916705392"

def test_normalize_with_separators():
    assert normalize_vn_phone("091-670-5392") == "0916705392"

def test_normalize_invalid():
    assert normalize_vn_phone("") is None
    assert normalize_vn_phone("abc") is None


# ===== pad_right / pad_left =====

def test_pad_right_fills():
    assert pad_right("ab", 5) == "ab   "

def test_pad_right_truncate_plain():
    assert pad_right("abcdef", 4) == "abcd"

def test_pad_right_truncate_ellipsis():
    assert pad_right("abcdef", 4, ellipsis=True) == "ab.."

def test_pad_left_fills():
    assert pad_left("7", 3) == "  7"

def test_pad_left_no_truncate():
    # Không được cắt số dài (giữ đúng số liệu billsec/duration)
    assert pad_left("1000", 3) == "1000"


# ===== escape helpers =====

def test_md_escape():
    assert _md_escape("a*b_c") == "a\\*b\\_c"

def test_code_safe_strips_backtick():
    assert "`" not in _code_safe("rou`ting")


# ===== alert builders =====

ROW = {"cluster": "CLUSTER_A", "viettel": 0, "mobi": 5, "vina": 3, "other": 1}

def test_build_bad_alert_contains_name_and_cluster():
    text = build_bad_alert("PS3", ROW)
    assert "PS3" in text
    assert "CLUSTER_A" in text
    assert text.count("```") == 2  # mở + đóng code block

def test_build_drop_alert_contains_details_and_threshold():
    text = build_drop_alert("PS3", ROW, "VT:10→2", 70)
    assert "70%" in text
    assert "VT:10" in text and "2" in text

def test_build_alert_escapes_special_name():
    text = build_bad_alert("PS*3_x", ROW)
    assert "PS\\*3\\_x" in text
