"""
main.py — FastAPI entry point
Serve Web UI + REST API + khởi động bots + scheduler
"""
import asyncio
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import aiofiles
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from state import (
    init_db, log, get_logs, clear_logs,
    get_all_snapshots, get_snapshot,
    get_daily_stats, get_daily_stats_range, get_billing_cache_range,
    get_meeting_history, add_meeting_history, delete_meeting_history,
    set_bot_status, get_bot_status
)
from scheduler import (
    create_scheduler, set_telegram_sender, set_paused, is_paused,
    backfill_billing, billing_backfill_status
)
from modules.nospam import query_nospam
from modules.meeting import (
    list_meeting_files, test_gemini_connection, summarize_audio, resummarize,
    normalize_model, normalize_language, resolve_api_key, delete_note_files,
    LANGUAGES, DEFAULT_STYLE, NOTES_DIR,
)
from utils import load_config, save_config

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

MAX_UPLOAD_MB = 500  # trần dung lượng file tóm tắt qua web (chống đầy đĩa)

# ===== PROCESS REGISTRY =====
_bot_procs: dict[str, Optional[subprocess.Popen]] = {
    "nospam":  None,
    "meeting": None,
    "mpt":     None,
}


def _pid_alive(pid: Optional[int]) -> bool:
    """Kiểm tra một PID có còn sống không (tránh spawn bot trùng khi main restart)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


async def _bot_already_running(name: str) -> bool:
    """True nếu bot `name` đang chạy ở tiến trình này hoặc theo PID lưu trong DB."""
    proc = _bot_procs.get(name)
    if proc and proc.poll() is None:
        return True
    status = await get_bot_status(name)
    return _pid_alive(status.get("pid"))


_BOT_SCRIPTS = {
    "mpt":     "bot_mpt.py",
    "nospam":  "bot_nospam.py",
    "meeting": "bot_meeting.py",
}


async def _start_bot(name: str) -> Optional[int]:
    """Spawn 1 bot nếu chưa chạy. Trả về PID mới, hoặc None nếu đã chạy sẵn."""
    if await _bot_already_running(name):
        return None
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "bots", _BOT_SCRIPTS[name])],
        cwd=BASE_DIR,
    )
    _bot_procs[name] = proc
    await set_bot_status(name, True, proc.pid)
    return proc.pid


async def _stop_bot(name: str) -> None:
    """Dừng bot `name` nếu đang chạy."""
    proc = _bot_procs.get(name)
    if proc and proc.poll() is None:
        proc.terminate()
    _bot_procs[name] = None
    await set_bot_status(name, False)

# ===== TELEGRAM SENDER =====

async def _telegram_sender(bot_config: dict, text: str):
    """Gửi Telegram message từ scheduler."""
    import httpx
    token   = bot_config.get("token", "")
    chat_id = bot_config.get("chatid", "")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        })

# load_config / save_config nay dùng chung từ utils.py

# ===== LIFESPAN =====

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log("SuperBot khởi động", "BOOT")

    set_telegram_sender(_telegram_sender)
    scheduler = create_scheduler()
    scheduler.start()
    log("Scheduler đã khởi động", "BOOT")

    # Tự động khởi động các bot đã bật trong config (bỏ qua nếu PID cũ còn sống)
    cfg = load_config()

    mpt_bots = cfg.get("mpt", {}).get("bots", [])
    autostart = []
    if any(b.get("enabled") for b in mpt_bots):
        autostart.append("mpt")
    if cfg.get("nospam", {}).get("enabled") and cfg.get("nospam", {}).get("bot_token"):
        autostart.append("nospam")
    if cfg.get("meeting", {}).get("enabled") and cfg.get("meeting", {}).get("bot_token"):
        autostart.append("meeting")

    for name in autostart:
        pid = await _start_bot(name)
        if pid:
            log(f"Auto-start {name} Bot (PID={pid})", "BOOT")
        else:
            log(f"{name} Bot đã chạy sẵn — bỏ qua auto-start", "BOOT")

    yield

    scheduler.shutdown(wait=False)
    for name, proc in _bot_procs.items():
        if proc and proc.poll() is None:
            proc.terminate()
            log(f"Đã dừng {name} Bot", "SHUTDOWN")
    log("SuperBot đã tắt", "SHUTDOWN")

# ===== APP =====

app = FastAPI(title="MPT SuperBot", lifespan=lifespan)

# ===== WebSocket LOG STREAM =====

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

ws_manager = ConnectionManager()

@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(2)
            logs = await get_logs(50)
            await websocket.send_json({"logs": logs})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# ===== PYDANTIC MODELS =====

class ConfigPayload(BaseModel):
    config: dict

class PhoneQuery(BaseModel):
    phone: str
    target_id: str

class NospamQuery(BaseModel):
    phone: str

class BotControl(BaseModel):
    action: str  # "start" | "stop"

class PromptPayload(BaseModel):
    prompt: str

class MeetingSummarize(BaseModel):
    text: str

class MeetingResummarize(BaseModel):
    filename: str
    style: Optional[str] = None
    model: Optional[str] = None
    language: Optional[str] = None

# ===== API: CONFIG =====

@app.get("/api/config")
async def api_get_config():
    return load_config()

@app.post("/api/config")
async def api_save_config(payload: ConfigPayload):
    save_config(payload.config)
    log("Config đã được lưu", "CFG")
    return {"status": "ok"}

# ===== API: MPT ROUTING =====

@app.get("/api/mpt/snapshots")
async def api_mpt_snapshots():
    return await get_all_snapshots()

@app.get("/api/mpt/snapshot/{target_id}")
async def api_mpt_snapshot(target_id: str):
    snap = await get_snapshot(target_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap

@app.get("/api/mpt/stats")
async def api_mpt_stats(date: str = None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    return await get_daily_stats(date)

@app.get("/api/mpt/stats/range")
async def api_mpt_stats_range(days: int = 14):
    """Cảnh báo (drops/bads) theo ngày, `days` ngày gần nhất — phục vụ biểu đồ Dashboard."""
    return await get_daily_stats_range(max(1, min(days, 90)))

@app.get("/api/mpt/billing/history")
async def api_mpt_billing_history(days: int = 7):
    """Sản lượng billing đã cache theo ngày — phục vụ biểu đồ xu hướng."""
    return await get_billing_cache_range(max(1, min(days, 30)))

@app.post("/api/mpt/billing/backfill")
async def api_mpt_billing_backfill(payload: dict = None):
    """Cache billing `days` ngày gần nhất (chạy nền). Dùng cho nút Backfill / Cache hôm nay."""
    days = max(1, min(int((payload or {}).get("days", 15)), 30))
    if billing_backfill_status()["running"]:
        return {"status": "already_running", **billing_backfill_status()}
    asyncio.create_task(backfill_billing(days))
    return {"status": "started", "days": days}

@app.get("/api/mpt/billing/cache-status")
async def api_mpt_billing_cache_status():
    return billing_backfill_status()

@app.post("/api/mpt/pause")
async def api_mpt_pause(payload: dict = None):
    paused = True if not payload else payload.get("paused", True)
    await set_paused(paused)
    return {"paused": paused}

@app.get("/api/mpt/status")
async def api_mpt_running_status():
    return {"paused": await is_paused()}

@app.get("/api/mpt/billing")
async def api_mpt_billing(target_id: str, day: str):
    """Lấy báo cáo billing cho 1 target, 1 ngày (chạy qua Playwright session)."""
    config = load_config()
    targets = config.get("mpt", {}).get("targets", [])
    target = next((t for t in targets if t["id"] == target_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    if not target.get("enabled"):
        return {"error": "Target không được bật"}
    log(f"Fetch billing {target_id} ngày {day}", "BILLING")
    from modules.mpt_http import get_session
    session = get_session(target)
    result = await session.fetch_billing(day)
    return result

@app.post("/api/mpt/find")
async def api_mpt_find(payload: PhoneQuery):
    config = load_config()
    targets = config.get("mpt", {}).get("targets", [])
    
    # Tra cứu độc lập, không phụ thuộc vào trạng thái bật/tắt (enabled)
    active_targets = targets
    log(f"Tra cứu SĐT: {payload.phone}, target_id: {payload.target_id}, targets: {[t['id'] for t in active_targets]}", "FIND")
    
    if payload.target_id != "all":
        active_targets = [t for t in active_targets if str(t.get("id")).lower() == str(payload.target_id).lower()]
        log(f"Sau khi filter: {[t['id'] for t in active_targets]}", "FIND")
        
    if not active_targets:
        log("Lỗi: Không có target nào hợp lệ để tra cứu", "FIND_ERR")
        return {"data": []}

    from modules.mpt_http import get_session
    
    async def _search(t):
        sess = get_session(t)
        res = await sess.fetch_call_history(payload.phone)
        return {"target": t.get("alertName", t["id"]), "result": res}
        
    tasks = [_search(t) for t in active_targets]
    outcomes = await asyncio.gather(*tasks)
    return {"data": outcomes}

@app.post("/api/mpt/bot")
async def api_mpt_bot_control(payload: BotControl):
    if payload.action == "start":
        pid = await _start_bot("mpt")
        if pid is None:
            return {"status": "already_running"}
        log(f"MPT Bot khởi động (PID={pid})", "BOT")
        return {"status": "started", "pid": pid}

    elif payload.action == "stop":
        await _stop_bot("mpt")
        log("MPT Bot đã dừng", "BOT")
        return {"status": "stopped"}

@app.get("/api/mpt/bot/status")
async def api_mpt_bot_status():
    proc = _bot_procs.get("mpt")
    running = proc is not None and proc.poll() is None
    return {"running": running, "pid": proc.pid if running else None}

# ===== API: NOSPAM =====

@app.post("/api/nospam/query")
async def api_nospam_query(payload: NospamQuery):
    log(f"Tra cứu Nospam: {payload.phone}", "NOSPAM")
    result = await query_nospam(payload.phone)
    return result

@app.post("/api/nospam/bot")
async def api_nospam_bot_control(payload: BotControl):
    if payload.action == "start":
        pid = await _start_bot("nospam")
        if pid is None:
            return {"status": "already_running"}
        log(f"Nospam Bot khởi động (PID={pid})", "BOT")
        return {"status": "started", "pid": pid}

    elif payload.action == "stop":
        await _stop_bot("nospam")
        log("Nospam Bot đã dừng", "BOT")
        return {"status": "stopped"}

@app.get("/api/nospam/bot/status")
async def api_nospam_bot_status():
    proc = _bot_procs.get("nospam")
    running = proc is not None and proc.poll() is None
    return {"running": running, "pid": proc.pid if running else None}

def _markdown_to_print_html(title: str, md: str) -> str:
    """Chuyển markdown → HTML in được tối giản (không phụ thuộc thư viện ngoài).
    Hỗ trợ: tiêu đề #, **đậm**, gạch ngang ---, bảng |...|, danh sách -/*."""
    import html as _html

    def inline(s: str) -> str:
        s = _html.escape(s)
        # **bold**
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        return s

    lines = (md or "").split("\n")
    out, i, in_table = [], 0, False
    while i < len(lines):
        ln = lines[i].rstrip()
        # Bảng markdown: nhóm các dòng bắt đầu bằng |
        if ln.startswith("|") and ln.endswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            out.append("<table>")
            for ri, row in enumerate(rows):
                cells = [c.strip() for c in row.strip("|").split("|")]
                if ri == 1 and all(set(c) <= set("-: ") for c in cells):
                    continue  # dòng phân cách header
                tag = "th" if ri == 0 else "td"
                out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
            out.append("</table>")
            continue
        if ln.startswith("### "):
            out.append(f"<h3>{inline(ln[4:])}</h3>")
        elif ln.startswith("## "):
            out.append(f"<h2>{inline(ln[3:])}</h2>")
        elif ln.startswith("# "):
            out.append(f"<h1>{inline(ln[2:])}</h1>")
        elif ln.strip() == "---":
            out.append("<hr/>")
        elif ln.startswith(("- ", "* ")):
            out.append(f"<li>{inline(ln[2:])}</li>")
        elif ln.strip() == "":
            out.append("<br/>")
        else:
            out.append(f"<p>{inline(ln)}</p>")
        i += 1
    body = "\n".join(out)
    style = ("body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;"
             "margin:32px auto;padding:0 20px;color:#1a1a1a;line-height:1.6}"
             "table{border-collapse:collapse;width:100%;margin:12px 0}"
             "th,td{border:1px solid #ccc;padding:6px 10px;text-align:left}"
             "th{background:#f3f4f6}h1{font-size:24px}h2{font-size:19px}hr{border:none;border-top:1px solid #ddd}"
             "@media print{body{margin:0}}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{_html.escape(title)}</title>"
            f"<style>{style}</style></head><body>{body}</body></html>")


# ===== API: MEETING =====

@app.post("/api/meeting/bot")
async def api_meeting_bot_control(payload: BotControl):
    if payload.action == "start":
        pid = await _start_bot("meeting")
        if pid is None:
            return {"status": "already_running"}
        log(f"Meeting Bot khởi động (PID={pid})", "BOT")
        return {"status": "started", "pid": pid}

    elif payload.action == "stop":
        await _stop_bot("meeting")
        log("Meeting Bot đã dừng", "BOT")
        return {"status": "stopped"}

@app.get("/api/meeting/bot/status")
async def api_meeting_bot_status():
    proc = _bot_procs.get("meeting")
    running = proc is not None and proc.poll() is None
    return {"running": running, "pid": proc.pid if running else None}

@app.get("/api/meeting/languages")
async def api_meeting_languages():
    return {"languages": LANGUAGES, "default": "vi"}

@app.get("/api/meeting/history")
async def api_meeting_history(q: str = ""):
    # Ưu tiên lịch sử từ DB; lọc các file còn tồn tại trên đĩa, fallback quét thư mục notes/
    rows = await get_meeting_history(200)
    files, seen = [], set()
    for r in rows:
        fn = r["filename"]
        if fn in seen:
            continue
        seen.add(fn)
        if os.path.exists(os.path.join(NOTES_DIR, fn)):
            files.append(fn)
    if not files:
        files = list_meeting_files(50)
    if q:
        ql = q.strip().lower()
        files = [f for f in files if ql in f.lower()]
    return {"files": files}

@app.get("/api/meeting/download/{filename}")
async def api_meeting_download(filename: str):
    # Chống path traversal: chỉ nhận basename, đúng pattern, và nằm trong NOTES_DIR
    safe_name = os.path.basename(filename)
    if safe_name != filename or not re.fullmatch(r"Meeting_(Note|Transcript)_.+\.md", safe_name):
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = os.path.realpath(os.path.join(NOTES_DIR, safe_name))
    if os.path.commonpath([filepath, os.path.realpath(NOTES_DIR)]) != os.path.realpath(NOTES_DIR):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath, filename=safe_name, media_type="text/markdown")

@app.post("/api/meeting/upload")
async def api_meeting_upload(file: UploadFile = File(...)):
    """Tóm tắt audio/video upload trực tiếp qua web — không vướng giới hạn 20MB của Telegram."""
    cfg = load_config().get("meeting", {})
    gemini_key = resolve_api_key(cfg.get("gemini_api_key", ""))
    if not gemini_key:
        return {"ok": False, "error": "Chưa cấu hình Gemini API Key."}

    safe = os.path.basename(file.filename or "upload.bin")
    tmp_path = os.path.join(TEMP_DIR, f"web_{int(datetime.now().timestamp())}_{safe}")
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    try:
        written = 0
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    return {"ok": False,
                            "error": f"File vượt giới hạn {MAX_UPLOAD_MB}MB qua web."}
                f.write(chunk)
        result = await summarize_audio(
            tmp_path, cfg.get("summary_prompt", ""), gemini_key,
            model=normalize_model(cfg.get("model")),
            style=cfg.get("summary_style", DEFAULT_STYLE),
            language=normalize_language(cfg.get("language")),
            include_transcript=bool(cfg.get("include_transcript")),
        )
        if result.get("error"):
            return {"ok": False, "error": result["error"]}
        await add_meeting_history(result["filename"])
        if result.get("transcript_name"):
            await add_meeting_history(result["transcript_name"])
        log(f"Web tóm tắt: {result['filename']}", "MEETING")
        return {"ok": True, "filename": result["filename"],
                "transcript_name": result.get("transcript_name"),
                "content": result["content"], "usage": result.get("usage")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

@app.post("/api/meeting/resummarize")
async def api_meeting_resummarize(payload: MeetingResummarize):
    """Tạo biên bản mới từ note cũ với style/model/ngôn ngữ khác — dùng nguồn đã lưu, không upload lại."""
    cfg = load_config().get("meeting", {})
    gemini_key = resolve_api_key(cfg.get("gemini_api_key", ""))
    if not gemini_key:
        return {"ok": False, "error": "Chưa cấu hình Gemini API Key."}
    safe = os.path.basename(payload.filename or "")
    if not re.fullmatch(r"Meeting_(Note|Transcript)_.+\.md", safe):
        return {"ok": False, "error": "Tên file không hợp lệ."}
    result = await resummarize(
        safe, cfg.get("summary_prompt", ""), gemini_key,
        model=normalize_model(payload.model or cfg.get("model")),
        style=payload.style or cfg.get("summary_style", DEFAULT_STYLE),
        language=normalize_language(payload.language or cfg.get("language")),
    )
    if result.get("error"):
        return {"ok": False, "error": result["error"]}
    await add_meeting_history(result["filename"])
    log(f"Tóm tắt lại từ {safe} → {result['filename']}", "MEETING")
    return {"ok": True, "filename": result["filename"], "content": result["content"]}

@app.delete("/api/meeting/note/{filename}")
async def api_meeting_delete(filename: str):
    safe = os.path.basename(filename)
    if safe != filename or not re.fullmatch(r"Meeting_(Note|Transcript)_.+\.md", safe):
        raise HTTPException(status_code=400, detail="Invalid filename")
    removed = delete_note_files(safe)
    await delete_meeting_history(safe)
    log(f"Đã xoá note: {safe}", "MEETING")
    return {"ok": True, "removed": removed}

@app.get("/api/meeting/export/{filename}")
async def api_meeting_export(filename: str):
    """Xuất note ra HTML in được (Print → Save as PDF). Không cần thêm thư viện."""
    safe = os.path.basename(filename)
    if safe != filename or not re.fullmatch(r"Meeting_(Note|Transcript)_.+\.md", safe):
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = os.path.realpath(os.path.join(NOTES_DIR, safe))
    if os.path.commonpath([filepath, os.path.realpath(NOTES_DIR)]) != os.path.realpath(NOTES_DIR):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
        md = await f.read()
    html = _markdown_to_print_html(safe, md)
    return HTMLResponse(content=html)

@app.post("/api/meeting/prompt")
async def api_meeting_prompt(payload: PromptPayload):
    cfg = load_config()
    if "meeting" not in cfg:
        cfg["meeting"] = {}
    cfg["meeting"]["summary_prompt"] = payload.prompt
    save_config(cfg)
    log("Meeting prompt đã cập nhật", "CFG")
    return {"status": "ok"}

@app.post("/api/meeting/test-gemini")
async def api_test_gemini(payload: dict):
    # Key gửi từ form, hoặc fallback config/env (cho phép test khi key lưu ở môi trường)
    api_key = resolve_api_key(payload.get("api_key", "")
                              or load_config().get("meeting", {}).get("gemini_api_key", ""))
    if not api_key:
        return {"ok": False, "msg": "Chưa có Gemini API Key (form, config hoặc env GEMINI_API_KEY)."}
    result = await test_gemini_connection(api_key)
    return result

# ===== API: LOGS =====

@app.get("/api/logs")
async def api_get_logs(limit: int = 200):
    return {"logs": await get_logs(limit)}

@app.delete("/api/logs")
async def api_clear_logs():
    await clear_logs()
    return {"status": "ok"}

# ===== API: TELEGRAM TEST =====

@app.post("/api/telegram/test")
async def api_test_telegram(payload: dict):
    import httpx
    token = payload.get("token", "")
    if not token:
        return {"ok": False, "msg": "Token trống"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
            if data.get("ok"):
                username = data["result"]["username"]
                return {"ok": True, "msg": f"Kết nối OK — Bot: @{username}"}
            return {"ok": False, "msg": data.get("description", "Token không hợp lệ")}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ===== BILLING MANAGER API =====

from state import (
    create_billing_group, get_billing_groups, get_billing_group,
    update_billing_group, delete_billing_group,
    set_group_clusters, topup_group, get_topup_history,
    get_billing_usage, get_billing_usage_range, get_billing_cache_range
)


class BillingGroupCreate(BaseModel):
    name: str
    target_id: str
    price_per_min: float
    block_factor: float = 1.02
    warn_low: float = 0
    warn_neg: float = 0
    notes: str = ""
    clusters: list = []


class BillingGroupUpdate(BaseModel):
    name: Optional[str] = None
    price_per_min: Optional[float] = None
    block_factor: Optional[float] = None
    warn_low: Optional[float] = None
    warn_neg: Optional[float] = None
    enabled: Optional[int] = None
    notes: Optional[str] = None
    clusters: Optional[list] = None


class TopupRequest(BaseModel):
    amount: float
    note: str = ""


@app.get("/api/billing/groups")
async def api_get_billing_groups():
    """Danh sách nhóm billing kèm số dư và clusters."""
    groups = await get_billing_groups()
    return {"groups": groups}


@app.post("/api/billing/groups")
async def api_create_billing_group(body: BillingGroupCreate):
    """Tạo nhóm billing mới."""
    try:
        gid = await create_billing_group(
            name=body.name, target_id=body.target_id,
            price_per_min=body.price_per_min, block_factor=body.block_factor,
            warn_low=body.warn_low, warn_neg=body.warn_neg, notes=body.notes
        )
        if body.clusters:
            await set_group_clusters(gid, body.clusters)
        g = await get_billing_group(gid)
        return {"ok": True, "group": g}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.put("/api/billing/groups/{group_id}")
async def api_update_billing_group(group_id: int, body: BillingGroupUpdate):
    """Sửa thông tin nhóm billing."""
    updates = {k: v for k, v in body.dict().items() if v is not None and k != "clusters"}
    if updates:
        await update_billing_group(group_id, **updates)
    if body.clusters is not None:
        await set_group_clusters(group_id, body.clusters)
    g = await get_billing_group(group_id)
    if not g:
        raise HTTPException(404, "Group not found")
    return {"ok": True, "group": g}


@app.delete("/api/billing/groups/{group_id}")
async def api_delete_billing_group(group_id: int):
    g = await get_billing_group(group_id)
    if not g:
        raise HTTPException(404, "Group not found")
    await delete_billing_group(group_id)
    return {"ok": True}


@app.post("/api/billing/groups/{group_id}/topup")
async def api_topup_group(group_id: int, body: TopupRequest):
    """Nạp tiền vào nhóm (cộng vào số dư)."""
    if body.amount <= 0:
        raise HTTPException(400, "amount phai > 0")
    g = await get_billing_group(group_id)
    if not g:
        raise HTTPException(404, "Group not found")
    await topup_group(group_id, body.amount, body.note)
    updated = await get_billing_group(group_id)
    return {"ok": True, "new_balance": updated["balance"], "group": updated}


@app.get("/api/billing/groups/{group_id}/summary")
async def api_group_summary(group_id: int, period: str = "today"):
    """
    Tổng hợp cước nhóm theo period: today | week | month
    """
    from datetime import timedelta
    g = await get_billing_group(group_id)
    if not g:
        raise HTTPException(404, "Group not found")

    today = datetime.now().strftime("%Y-%m-%d")
    if period == "today":
        start = end = today
    elif period == "week":
        start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        end = today
    elif period == "month":
        start = datetime.now().strftime("%Y-%m-01")
        end = today
    else:
        start = end = today

    usages = await get_billing_usage_range(group_id, start, end)
    total_raw = sum(u["minutes_raw"] for u in usages)
    total_billed = sum(u["minutes_billed"] for u in usages)
    total_cost = sum(u["cost"] for u in usages)

    topups = await get_topup_history(group_id, 10)

    return {
        "group": g,
        "period": {"start": start, "end": end},
        "summary": {
            "total_minutes_raw": round(total_raw, 2),
            "total_minutes_billed": round(total_billed, 2),
            "total_cost": round(total_cost, 2),
            "balance": g["balance"],
        },
        "daily": usages,
        "topups": topups,
    }


@app.get("/api/billing/clusters/available")
async def api_available_clusters(target_id: str = None):
    """
    Danh sách cluster có thể chọn (từ billing_cache 7 ngày gần nhất).
    Dùng cho dropdown khi tạo/sửa nhóm.
    """
    from datetime import timedelta
    from state import _connect
    import aiosqlite

    result = {}
    for tid in (["ps3", "ps4", "ps50"] if not target_id else [target_id]):
        # Lấy cache mới nhất của target
        from state import get_billing_cache
        today = datetime.now().strftime("%Y-%m-%d")
        cached = await get_billing_cache(today, tid)
        if not cached:
            # Thử ngày hôm qua
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            cached = await get_billing_cache(yesterday, tid)
        if cached and cached.get("rows"):
            result[tid] = [r["name"] for r in cached["rows"]]
        else:
            result[tid] = []

    return {"clusters": result}


# ===== SERVE STATIC (Web UI) =====

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(BASE_DIR, "static", "index.html")
    async with aiofiles.open(index_path, "r", encoding="utf-8") as f:
        return await f.read()

# ===== ENTRY POINT =====

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
