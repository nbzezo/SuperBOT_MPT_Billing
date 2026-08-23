"""
utils.py — Tiện ích dùng chung cho toàn dự án
Gom các hàm vốn lặp ở nhiều file: tìm Chrome, padding bảng, đọc config.
"""
import json
import os
import shutil
import tempfile
import threading
from typing import Optional

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Khóa trong-tiến-trình: chống đua giữa các coroutine/thread cùng process.
# Ghi atomic (tmp + os.replace) chống đọc phải JSON ghi-dở giữa các tiến trình (main + bots).
_config_lock = threading.RLock()


# ===== CONFIG =====

def load_config() -> dict:
    """Đọc config.json. Trả về {} nếu chưa có file (hoặc file hỏng)."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    with _config_lock:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # File ghi dở / hỏng — trả {} thay vì làm sập app gọi
            return {}


def save_config(cfg: dict) -> None:
    """Ghi config.json atomic (giữ Unicode, indent 4).
    Ghi ra file tạm cùng thư mục rồi os.replace → người đọc luôn thấy file nguyên vẹn."""
    with _config_lock:
        dir_ = os.path.dirname(CONFIG_FILE) or "."
        fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_FILE)  # atomic trên cùng filesystem
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise


def update_config_field(section: str, key: str, value) -> None:
    """Đọc-sửa-ghi một field con dưới khóa lock, giảm cửa sổ lost-update giữa các lệnh bot."""
    with _config_lock:
        cfg = load_config()
        cfg.setdefault(section, {})[key] = value
        save_config(cfg)


# ===== CHROME / CHROMIUM =====

def find_chrome() -> Optional[str]:
    """Tự động tìm Chrome/Chromium trên system. Trả về path hoặc None."""
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/usr/bin/google-chrome",
        "/snap/bin/chromium",
    ]
    return next((c for c in candidates if c and os.path.exists(c)), None)


# ===== TABLE PADDING (cho bảng text trong Telegram) =====

def pad_right(s, n: int, ellipsis: bool = False) -> str:
    """
    Căn trái, độ rộng cố định n.
    ellipsis=True: nếu dài hơn n thì cắt và thêm '..' (dùng cho bảng Telegram).
    ellipsis=False: cắt cụt đơn giản.
    """
    s = str(s)
    if len(s) > n:
        return (s[: n - 2] + "..") if ellipsis else s[:n]
    return s + " " * (n - len(s))


def pad_left(s, n: int) -> str:
    """Căn phải tối thiểu rộng n. Không cắt nếu dài hơn (tránh hỏng số liệu)."""
    s = str(s)
    return s if len(s) > n else " " * (n - len(s)) + s


def build_dauso_table(alert_name, rows, ts_str) -> str:
    """Bảng số lượng đầu số VT/MB/VN theo account — dùng cho /report và báo cáo tự động."""
    name = str(alert_name)
    for ch in ("_", "*", "`", "["):      # md-escape phần tiêu đề (ngoài code block)
        name = name.replace(ch, "\\" + ch)
    text = f"◇ *{name} · Đầu số theo account* · {ts_str}\n```\n"
    text += pad_right("Tên Routing", 22) + " |   VT |   MB |   VN\n"
    text += "-" * 22 + " |------|------|------\n"
    for r in rows:
        cl = str(r["cluster"]).replace("`", "'")   # code-safe trong code block
        text += (pad_right(cl, 22) + " | " +
                 pad_left(str(r["viettel"]), 4) + " | " +
                 pad_left(str(r["mobi"]), 4) + " | " +
                 pad_left(str(r["vina"]), 4) + "\n")
    text += "```"
    return text
