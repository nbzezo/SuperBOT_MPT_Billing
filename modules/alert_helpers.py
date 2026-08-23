"""
modules/alert_helpers.py — Hàm dựng nội dung cảnh báo Telegram (không phụ thuộc playwright)
Tách từ mpt_routing.py để scheduler không phải import playwright.
"""
from datetime import datetime


def _md_escape(s) -> str:
    """Escape ký tự đặc biệt của Telegram legacy Markdown (dùng cho phần NGOÀI code block)."""
    s = str(s)
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def _code_safe(s) -> str:
    """Trong khối ``` chỉ cần tránh backtick làm vỡ khối; giữ nguyên độ dài để bảng thẳng hàng."""
    return str(s).replace("`", "'")


def build_bad_alert(alert_name: str, row: dict) -> str:
    now_str = datetime.now().strftime("%H:%M")
    text = f"◆ *{_md_escape(alert_name)} · Cạn số gọi ra* · {now_str}\n"
    text += "```\n"
    text += _code_safe(row["cluster"]) + f"  VT {row['viettel']} · MB {row['mobi']} · VN {row['vina']}\n"
    text += "```"
    return text


def build_bad_alert_batch(alert_name: str, rows: list, bad_since: dict = None) -> str:
    """Gộp nhiều cluster cạn số thành 1 tin, kèm thời điểm bắt đầu cạn.
    rows: list dict {cluster, viettel, mobi, vina} | bad_since: {cluster: ts_ms}"""
    now_str = datetime.now().strftime("%H:%M")
    text = f"◆ *{_md_escape(alert_name)} · Cạn số gọi ra ({len(rows)})* · {now_str}\n"
    text += "```\n"
    for row in rows:
        line = _code_safe(row["cluster"])
        line += f"  VT {row['viettel']} · MB {row['mobi']} · VN {row['vina']}"
        since = (bad_since or {}).get(row["cluster"])
        if since:
            line += f"  (từ {datetime.fromtimestamp(since / 1000).strftime('%H:%M')})"
        text += line + "\n"
    text += "```"
    return text


def build_drop_alert(alert_name: str, row: dict, details: str, drop_threshold: int) -> str:
    now_str = datetime.now().strftime("%H:%M")
    text = f"◆ *{_md_escape(alert_name)} · Giảm sâu >{drop_threshold}%* · {now_str}\n"
    text += "```\n"
    text += _code_safe(row["cluster"]) + "  " + _code_safe(details) + "\n"
    text += "```"
    return text


def build_drop_alert_batch(alert_name: str, drops: list, drop_threshold: int) -> str:
    """Gộp nhiều drop thành 1 tin.
    drops: list dict {row: {cluster,...}, details: str}"""
    now_str = datetime.now().strftime("%H:%M")
    text = f"◆ *{_md_escape(alert_name)} · Giảm sâu >{drop_threshold}% ({len(drops)})* · {now_str}\n"
    text += "```\n"
    for d in drops:
        text += _code_safe(d["row"]["cluster"]) + "  " + _code_safe(d["details"]) + "\n"
    text += "```"
    return text


def build_recovery_alert(alert_name: str, rows: list) -> str:
    """Thông báo cluster đã phục hồi sau khi cạn.
    rows: list dict {cluster, viettel, mobi, vina, since_ms}"""
    now_str = datetime.now().strftime("%H:%M")
    text = f"✓ *{_md_escape(alert_name)} · Đã phục hồi ({len(rows)})* · {now_str}\n"
    text += "```\n"
    for row in rows:
        line = _code_safe(row["cluster"])
        if row.get("since_ms"):
            line += f"  (cạn từ {datetime.fromtimestamp(row['since_ms'] / 1000).strftime('%H:%M')})"
        text += line + "\n"
    text += "```"
    return text
