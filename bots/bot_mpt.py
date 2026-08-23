"""
bots/bot_mpt.py — Telegram Bot cho MPT Routing
Nhận lệnh: /status /report /billing /pause /resume /find <số>
AI Agent: tin nhắn text tự do → Gemini (nhớ ngữ cảnh, đọc DB, gọi API)
"""
import asyncio
import json
import logging
import os
import sys
import time
import traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
import httpx
from telegram import Update, ReplyKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from state import (
    get_all_snapshots, get_snapshot, get_daily_stats, get_daily_stats_range, get_billing_cache,
    set_billing_cache, get_logs,
    get_ai_history, clear_ai_history, save_ai_history,
    get_bot_status,
    get_billing_groups, get_billing_group, topup_group, get_billing_usage_range
)
from scheduler import set_paused
from utils import load_config, pad_right, pad_left, build_dauso_table

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Bot gọi API của tiến trình main (dùng chung session Playwright đã đăng nhập,
# tránh mở browser trùng và rủi ro ping-pong session).
MAIN_API = "http://127.0.0.1:8765"


def get_enabled_targets(config: dict) -> list:
    return [t for t in config.get("mpt", {}).get("targets", []) if t.get("enabled")]


def _code_safe(s) -> str:
    """Tránh backtick làm vỡ khối ``` trong message Telegram."""
    return str(s).replace("`", "'")


async def _api_get(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(f"{MAIN_API}{path}", params=params)
        return r.json()


async def _api_post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{MAIN_API}{path}", json=payload)
        return r.json()


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    targets = get_enabled_targets(config)
    if not targets:
        await update.message.reply_text("◆ Chưa có target nào được cấu hình.")
        return

    msg = "◇ *TRẠNG THÁI HỆ THỐNG MPT*\n\n"
    snapshots = await get_all_snapshots()
    for t in targets:
        snap = snapshots.get(t["id"])
        if snap:
            ts = datetime.fromtimestamp(snap["ts"] / 1000).strftime("%H:%M:%S")
            rows = snap["rows"]
            bad_count = sum(1 for r in rows if r["viettel"] == 0 or r["mobi"] == 0 or r["vina"] == 0)
            msg += f"▸ *{t.get('alertName', t['id'])}*: {len(rows)} routing"
            if bad_count > 0:
                msg += f" | ◆ {bad_count} cần chú ý"
            msg += f"\n  Cập nhật: {ts}\n"
        else:
            msg += f"▸ *{t.get('alertName', t['id'])}*: Chưa có dữ liệu\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().strftime("%Y-%m-%d")
    stats = await get_daily_stats(today)
    config = load_config()
    targets = get_enabled_targets(config)
    snapshots = await get_all_snapshots()

    date_str = datetime.now().strftime("%d/%m/%Y")
    msg = f"◇ *BÁO CÁO ROUTING NGÀY {date_str}*\n\n"
    total_drops = total_bads = 0

    for t in targets:
        tid = t["id"]
        d = stats.get(tid, {}).get("drops", 0)
        b = stats.get(tid, {}).get("bads", 0)
        msg += f"▸ *{escape_markdown(str(t.get('alertName', tid)), version=1)}*\n"
        if d > 0:
            msg += f"- Số lần báo giảm sâu: {d}\n"
        if b > 0:
            msg += f"- Số lần báo cạn số: {b}\n"
        if d == 0 and b == 0:
            msg += "- Routing ổn định ✓\n"
        msg += "\n"
        total_drops += d
        total_bads += b

    if total_drops == 0 and total_bads == 0:
        msg += "✓ Không có sự cố routing nào hôm nay."
    else:
        msg += f"◆ Tổng cảnh báo: {total_drops + total_bads}"

    await update.message.reply_text(msg, parse_mode="Markdown")

    # Bảng số lượng đầu số (VT/MB/VN) theo từng account — lấy từ snapshot mới nhất
    for t in targets:
        snap = snapshots.get(t["id"])
        rows = snap.get("rows") if snap else None
        if not rows:
            continue
        ts = datetime.fromtimestamp(snap["ts"] / 1000).strftime("%H:%M")
        m = build_dauso_table(t.get("alertName", t["id"]), rows, ts)
        await update.message.reply_text(m, parse_mode="Markdown")
        await asyncio.sleep(0.4)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_paused(True)
    await update.message.reply_text("◇ Đã TẠM DỪNG hệ thống quét MPT. Sẽ không gửi cảnh báo nữa.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_paused(False)
    await update.message.reply_text("◇ Đã TIẾP TỤC hệ thống quét MPT. Cảnh báo hoạt động trở lại.")


async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Vui lòng nhập SĐT. VD: `/find 0987654321`", parse_mode="Markdown")
        return
    phone = context.args[0]
    phone_md = escape_markdown(phone, version=1)
    await update.message.reply_text(f"◇ Đang tra cứu số `{phone}` trên Nospam & MPT Portal...", parse_mode="Markdown")

    # 1. Tra cứu nospam (qua API main)
    nr = await _api_post("/api/nospam/query", {"phone": phone})
    msg = f"◇ *KẾT QUẢ TRA CỨU SỐ {phone_md}*\n\n"
    msg += "*▸ NOSPAM (VNCERT):*\n"
    if nr and not nr.get("error"):
        msg += f"{nr.get('result') or '(Không có dữ liệu)'}\n\n"
    else:
        msg += f"✕ Lỗi: {escape_markdown(str(nr.get('error', 'Không thể kết nối')), version=1)}\n\n"

    # 2. Tra cứu MPT (qua API main — dùng chung session, không mở browser ở bot)
    msg += "*▸ LỊCH SỬ MPT PORTAL:*\n\n"
    config = load_config()
    targets = get_enabled_targets(config)
    if not targets:
        msg += "Không có target nào đang bật."
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    await update.message.reply_text(msg, parse_mode="Markdown")

    async def _search(t):
        res = await _api_post("/api/mpt/find", {"phone": phone, "target_id": t["id"]})
        data = res.get("data") or []
        entry = data[0] if data else {"target": t.get("alertName", t["id"]), "result": {"error": "no_data"}}
        return entry

    outcomes = await asyncio.gather(*[_search(t) for t in targets])

    for d in outcomes:
        t_msg = f"◇ *KẾT QUẢ TRA CỨU SỐ {phone_md}*\n▸ Hệ thống: {escape_markdown(str(d['target']), version=1)}\n\n"
        if d['result'].get('error'):
            t_msg += f"✕ Lỗi: {escape_markdown(str(d['result']['error']), version=1)}\n"
        elif not d['result'].get('rows'):
            t_msg += "Không tìm thấy cuộc gọi nào.\n"
        else:
            rows = d['result']['rows']
            max_rows = min(len(rows), 5)
            t_msg += "```\n"
            t_msg += pad_right("Account", 12) + " | DD/MM/YY | HH:MM | Sec | Cause\n"
            t_msg += "-" * 12 + " |----------|-------|-----|----------\n"
            for i in range(max_rows):
                r = rows[i]
                start_time = str(r['startTime'])
                if " " in start_time:
                    dt_part, tm_part = start_time.split(" ", 1)
                    if len(dt_part) >= 10:
                        date_short = f"{dt_part[8:10]}/{dt_part[5:7]}/{dt_part[2:4]}"
                    else:
                        date_short = dt_part[:8]
                    time_short = tm_part[:5]
                else:
                    date_short = "N/A"
                    time_short = start_time[:5]

                cause_short = _code_safe(r['cause'])[:10]
                t_msg += pad_right(_code_safe(r['account']), 12, ellipsis=True) + f" | {pad_right(date_short, 8)} | {pad_left(time_short, 5)} | {pad_left(str(r['billsec']), 3)} | {cause_short}\n"
            t_msg += "```\n"
            if len(rows) > max_rows:
                t_msg += f"_... và {len(rows) - max_rows} cuộc gọi khác_\n"
        await update.message.reply_text(t_msg, parse_mode="Markdown")
        await asyncio.sleep(0.5)


async def cmd_billing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = context.args[0] if context.args else datetime.now().strftime("%Y-%m-%d")
    await update.message.reply_text(f"◇ Đang lấy báo cáo cước ngày {day}...", parse_mode="Markdown")

    config = load_config()
    targets = get_enabled_targets(config)
    if not targets:
        await update.message.reply_text("Không có target nào đang bật.")
        return

    for t in targets:
        # Lấy billing qua API main (dùng chung session Playwright)
        res = await _api_get("/api/mpt/billing", {"target_id": t["id"], "day": day})
        label = escape_markdown(str(t.get('alertName', t['id'])), version=1)

        if res.get("error"):
            msg = f"✕ *{label} · Billing lỗi*\nLỗi: {escape_markdown(str(res['error']), version=1)}"
        else:
            rows = res.get("rows", [])
            if not rows:
                msg = f"◇ *{label} · Billing {day}*\nKhông có dữ liệu."
            else:
                total = sum(r["duration"] for r in rows)
                msg = f"◇ *{label} · Billing {day}*\n"
                msg += "```\n"
                for r in rows:
                    msg += pad_right(_code_safe(r['name']), 24, ellipsis=True) + pad_left(f"{r['duration']:.2f}", 10) + "\n"
                msg += pad_right("Tổng", 24) + pad_left(f"{total:.2f}", 10) + "\n"
                msg += "```"
        await update.message.reply_text(msg, parse_mode="Markdown")
        await asyncio.sleep(0.5)


def _main_keyboard() -> ReplyKeyboardMarkup:
    """Bàn phím nút bấm nhanh cho các lệnh chính."""
    return ReplyKeyboardMarkup(
        [
            ["/status", "/report"],
            ["/billing", "/find"],
            ["/pause", "/resume"],
            ["/help"],
        ],
        resize_keyboard=True,
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chào mừng + hiện bàn phím nút bấm."""
    await update.message.reply_text(
        "◇ *MPT Routing Bot*\n"
        "Dùng nút bên dưới hoặc gõ lệnh /help để xem danh sách lệnh.",
        parse_mode="Markdown",
        reply_markup=_main_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "◇ *MPT Routing Bot — Lệnh hỗ trợ*\n\n"
        "/status — Xem trạng thái tất cả target\n"
        "/report — Báo cáo routing hôm nay\n"
        "/find <sđt> — Tra cứu SĐT trên Nospam & MPT\n"
        "/billing [ngày] — Xuất báo cáo cước\n"
        "/pause — Tạm dừng gửi cảnh báo MPT\n"
        "/resume — Tiếp tục quét và gửi cảnh báo\n"
        "/ai_clear — Xóa lịch sử hội thoại AI\n"
        "/help — Danh sách lệnh\n\n"
        "🤖 *AI Agent*: Gửi bất kỳ câu hỏi nào (không cần lệnh /) — AI sẽ trả lời dựa trên dữ liệu hệ thống."
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=_main_keyboard())


# ===== AI AGENT =====

MAIN_API_AI = "http://127.0.0.1:8765"
DB_PATH_AI  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "superbot.db")


def _get_ai_config():
    cfg = load_config()
    return cfg.get("ai", {})


def _is_allowed(chat_id: int) -> bool:
    """Kiểm tra chat_id có trong whitelist không. Nếu whitelist rỗng → cho phép tất cả."""
    ai_cfg = _get_ai_config()
    allowed = ai_cfg.get("allowed_chat_ids", [])
    if not allowed:
        return True
    return chat_id in allowed or str(chat_id) in [str(x) for x in allowed]


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Chia message dài thành nhiều phần ≤ max_len ký tự."""
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # Cắt tại dòng gần nhất
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


# --- Gemini Tools ---

async def _tool_get_bot_status(name: str = None) -> str:
    """Lấy trạng thái các bot (mpt, nospam, meeting)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if name and name in ("mpt", "nospam", "meeting"):
                r = await client.get(f"{MAIN_API_AI}/api/{name}/bot/status")
                return json.dumps({name: r.json()}, ensure_ascii=False)
            # Lấy cả 3
            results = {}
            for n in ["mpt", "nospam"]:
                try:
                    r = await client.get(f"{MAIN_API_AI}/api/{n}/bot/status")
                    results[n] = r.json()
                except Exception:
                    results[n] = {"error": "không kết nối được"}
            return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_get_mpt_snapshots(target_id: str = None) -> str:
    """Lấy dữ liệu routing snapshot hiện tại."""
    try:
        snaps = await get_all_snapshots()
        if target_id and target_id in snaps:
            s = snaps[target_id]
            ts = datetime.fromtimestamp(s["ts"] / 1000).strftime("%H:%M:%S")
            rows = s["rows"]
            bad = [r for r in rows if r.get("viettel", 1) == 0 or r.get("mobi", 1) == 0 or r.get("vina", 1) == 0]
            return json.dumps({"target_id": target_id, "updated": ts, "total_routes": len(rows), "bad_routes": len(bad), "rows": rows[:20]}, ensure_ascii=False)
        # Tất cả
        out = {}
        for tid, s in snaps.items():
            ts = datetime.fromtimestamp(s["ts"] / 1000).strftime("%H:%M:%S")
            rows = s["rows"]
            bad = [r for r in rows if r.get("viettel", 1) == 0 or r.get("mobi", 1) == 0 or r.get("vina", 1) == 0]
            out[tid] = {"updated": ts, "total_routes": len(rows), "bad_routes": len(bad)}
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_get_daily_stats(date: str = None) -> str:
    """Lấy số lần drop/bad hôm nay hoặc ngày cụ thể (YYYY-MM-DD)."""
    try:
        d = date or datetime.now().strftime("%Y-%m-%d")
        stats = await get_daily_stats(d)
        return json.dumps({"date": d, "stats": stats}, ensure_ascii=False)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_get_logs(limit: int = 30) -> str:
    """Lấy logs hệ thống gần nhất."""
    try:
        import aiosqlite
        async with aiosqlite.connect(DB_PATH_AI, timeout=5.0) as db:
            async with db.execute(
                "SELECT ts, level, code, target_id, msg FROM logs ORDER BY id DESC LIMIT ?", (min(limit, 100),)
            ) as cur:
                rows = await cur.fetchall()
        logs = []
        for r in reversed(rows):
            ts = datetime.fromtimestamp(r[0] / 1000).strftime("%H:%M:%S")
            logs.append(f"[{ts}][{r[1]}][{r[2]}] {r[4]}")
        return "\n".join(logs) if logs else "(không có log nào)"
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_get_billing(target_id: str, day: str = None, realtime: bool = False) -> str:
    """Lay bao cao cuoc cua mot target. Mac dinh doc tu cache nhanh (~5ms).
    Neu realtime=True thi fetch thang tu portal MPT du co cache (mat ~60s nhung data moi nhat)."""
    try:
        d = day or datetime.now().strftime("%Y-%m-%d")

        # Uu tien cache tru khi yeu cau realtime
        if not realtime:
            cached = await get_billing_cache(d, target_id)
            if cached and cached.get("rows"):
                rows = cached["rows"]
                total = sum(r["duration"] for r in rows)
                lines = [f"{r['name']}: {r['duration']:.1f} phut" for r in rows]
                lines.append(f"Tong: {total:.1f} phut (~{total/60:.1f} gio)")
                return f"Billing {target_id} ngay {d} (cache):\n" + "\n".join(lines)

        # Fetch truc tiep tu portal (realtime hoac cache miss)
        source = "realtime" if realtime else "moi fetch"
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.get(f"{MAIN_API_AI}/api/mpt/billing", params={"target_id": target_id, "day": d})
            data = r.json()
        if data.get("error"):
            return f"Loi: {data['error']}"
        rows = data.get("rows", [])
        if not rows:
            return f"Khong co du lieu billing {target_id} ngay {d}"
        # Luu vao cache sau khi fetch
        await set_billing_cache(d, target_id, data)
        total = sum(r["duration"] for r in rows)
        lines = [f"{r['name']}: {r['duration']:.1f} phut" for r in rows]
        lines.append(f"Tong: {total:.1f} phut (~{total/60:.1f} gio)")
        return f"Billing {target_id} ngay {d} ({source}):\n" + "\n".join(lines)
    except Exception as e:
        return f"Loi: {e}"



async def _tool_query_phone(phone: str, target_id: str = "all") -> str:
    """Tra cứu lịch sử cuộc gọi của số điện thoại."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{MAIN_API_AI}/api/mpt/find", json={"phone": phone, "target_id": target_id})
            data = r.json().get("data", [])
        if not data:
            return "Không tìm thấy dữ liệu."
        out = []
        for item in data:
            target = item.get("target", "?")
            result = item.get("result", {})
            if result.get("error"):
                out.append(f"{target}: Lỗi - {result['error']}")
            else:
                rows = result.get("rows", [])
                out.append(f"{target}: {len(rows)} cuộc gọi")
                for row in rows[:5]:
                    out.append(f"  {row.get('startTime','')} | {row.get('billsec','')}s | {row.get('cause','')}")
        return "\n".join(out)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_control_bot(name: str, action: str) -> str:
    """Khởi động hoặc dừng một bot (name: mpt/nospam/meeting, action: start/stop)."""
    if name not in ("mpt", "nospam", "meeting"):
        return f"Bot không hợp lệ: {name}. Chọn: mpt, nospam, meeting."
    if action not in ("start", "stop"):
        return f"Action không hợp lệ: {action}. Chọn: start, stop."
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{MAIN_API_AI}/api/{name}/bot", json={"action": action})
            return json.dumps(r.json(), ensure_ascii=False)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_set_mpt_paused(paused: bool) -> str:
    """Tạm dừng (paused=true) hoặc tiếp tục (paused=false) giám sát MPT."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{MAIN_API_AI}/api/mpt/pause", json={"paused": paused})
            return json.dumps(r.json(), ensure_ascii=False)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_run_sql_readonly(query: str) -> str:
    """Chạy câu lệnh SQL SELECT trực tiếp trên database superbot.db. Chỉ cho phép SELECT."""
    q = query.strip().upper()
    if not q.startswith("SELECT"):
        return "Chỉ chấp nhận câu lệnh SELECT."
    try:
        import aiosqlite
        async with aiosqlite.connect(DB_PATH_AI, timeout=5.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query) as cur:
                rows = await cur.fetchmany(50)
                if not rows:
                    return "(Không có kết quả)"
                cols = list(rows[0].keys())
                lines = [" | ".join(cols)]
                lines.append("-" * len(lines[0]))
                for r in rows:
                    lines.append(" | ".join(str(r[c]) for c in cols))
                return "\n".join(lines)
    except Exception as e:
        return f"Lỗi SQL: {e}"


async def _tool_get_billing_groups() -> str:
    """Liệt kê các nhóm billing và số dư hiện tại."""
    try:
        groups = await get_billing_groups()
        if not groups:
            return "Chưa có nhóm billing nào được tạo."
        out = ["Danh sách nhóm billing:"]
        for g in groups:
            en = "Đang bật" if g["enabled"] else "Đã tắt"
            bal = g["balance"]
            price = g["price_per_min"]
            cls = ", ".join(g["clusters"]) if g["clusters"] else "(Chưa có)"
            out.append(f"- ID: {g['id']} | Tên: {g['name']} | Trạng thái: {en}")
            out.append(f"  Target: {g['target_id']} | Giá: {price:,.0f}đ/phút | Số dư: {bal:,.0f}đ")
            out.append(f"  Clusters: {cls}")
        return "\n".join(out)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_get_group_summary(group_id: int, period: str = "today") -> str:
    """Tổng hợp cước của một nhóm billing."""
    try:
        from datetime import datetime, timedelta
        g = await get_billing_group(group_id)
        if not g:
            return f"Không tìm thấy nhóm ID {group_id}"

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

        out = [
            f"Báo cáo cước nhóm '{g['name']}' ({period}: {start} -> {end})",
            f"Số dư hiện tại: {g['balance']:,.0f} đ",
            f"Tổng phút gọi (raw): {total_raw:,.1f}",
            f"Tổng phút tính tiền (billed): {total_billed:,.1f}",
            f"Tổng chi phí: {total_cost:,.0f} đ"
        ]
        if usages:
            out.append("\nChi tiết theo ngày:")
            for u in usages:
                out.append(f"- {u['date']}: {u['minutes_billed']:,.1f} phút -> {u['cost']:,.0f} đ")
        return "\n".join(out)
    except Exception as e:
        return f"Lỗi: {e}"


async def _tool_topup_group(group_id: int, amount: float, note: str = "") -> str:
    """Nạp tiền vào nhóm."""
    try:
        if amount <= 0:
            return "Số tiền nạp phải > 0."
        g = await get_billing_group(group_id)
        if not g:
            return f"Không tìm thấy nhóm ID {group_id}."
        await topup_group(group_id, amount, note)
        new_g = await get_billing_group(group_id)
        return f"Nạp thành công {amount:,.0f} đ vào nhóm '{new_g['name']}'. Số dư mới: {new_g['balance']:,.0f} đ."
    except Exception as e:
        return f"Lỗi nạp tiền: {e}"


_TOOL_MAP = {
    "get_bot_status":   _tool_get_bot_status,
    "get_mpt_snapshots": _tool_get_mpt_snapshots,
    "get_daily_stats":  _tool_get_daily_stats,
    "get_logs":         _tool_get_logs,
    "get_billing":      _tool_get_billing,
    "query_phone":      _tool_query_phone,
    "control_bot":      _tool_control_bot,
    "set_mpt_paused":   _tool_set_mpt_paused,
    "run_sql_readonly": _tool_run_sql_readonly,
    "get_billing_groups": _tool_get_billing_groups,
    "get_group_summary": _tool_get_group_summary,
    "topup_group":      _tool_topup_group,
}

_GEMINI_TOOLS_DECL = [
    {
        "name": "get_bot_status",
        "description": "Kiểm tra trạng thái đang chạy của các bot. name có thể là mpt, nospam, meeting hoặc để trống để lấy tất cả.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Tên bot: mpt, nospam, meeting (tùy chọn)"}}},
    },
    {
        "name": "get_mpt_snapshots",
        "description": "Lấy dữ liệu routing hiện tại. target_id có thể là ps3, ps4, ps50 hoặc để trống để lấy tất cả.",
        "parameters": {"type": "object", "properties": {"target_id": {"type": "string", "description": "ID target: ps3, ps4, ps50 (tùy chọn)"}}},
    },
    {
        "name": "get_daily_stats",
        "description": "Lấy số lần cảnh báo drop/bad trong ngày.",
        "parameters": {"type": "object", "properties": {"date": {"type": "string", "description": "Ngày YYYY-MM-DD, mặc định hôm nay"}}},
    },
    {
        "name": "get_logs",
        "description": "Lấy logs hệ thống gần nhất.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Số dòng log, mặc định 30"}}},
    },
    {
        "name": "get_billing",
        "description": "Lay bao cao cuoc (billing) cua mot target theo ngay. Mac dinh doc tu cache nhanh. Neu nguoi dung noi 'realtime', 'du lieu that', 'du lieu moi nhat', 'cap nhat moi' thi dat realtime=true de fetch thang tu portal MPT.",
        "parameters": {
            "type": "object",
            "required": ["target_id"],
            "properties": {
                "target_id": {"type": "string", "description": "ID target: ps3, ps4, ps50"},
                "day":       {"type": "string", "description": "Ngay YYYY-MM-DD, mac dinh hom nay"},
                "realtime":  {"type": "boolean", "description": "true = bo qua cache, fetch thang tu portal de co du lieu moi nhat (mat ~60s). Dat khi user yeu cau realtime/du lieu that/cap nhat moi."}
            }
        },
    },
    {
        "name": "query_phone",
        "description": "Tra cứu lịch sử cuộc gọi của một số điện thoại.",
        "parameters": {"type": "object", "required": ["phone"], "properties": {"phone": {"type": "string", "description": "Số điện thoại cần tra cứu"}, "target_id": {"type": "string", "description": "ID target hoặc 'all' để tra tất cả"}}},
    },
    {
        "name": "control_bot",
        "description": "Khởi động hoặc dừng một bot Telegram.",
        "parameters": {"type": "object", "required": ["name", "action"], "properties": {"name": {"type": "string", "description": "Tên bot: mpt, nospam, meeting"}, "action": {"type": "string", "description": "start hoặc stop"}}},
    },
    {
        "name": "set_mpt_paused",
        "description": "Tạm dừng hoặc tiếp tục giám sát và cảnh báo MPT Routing.",
        "parameters": {"type": "object", "required": ["paused"], "properties": {"paused": {"type": "boolean", "description": "true=tạm dừng, false=tiếp tục"}}},
    },
    {
        "name": "run_sql_readonly",
        "description": "Chạy câu lệnh SQL SELECT trực tiếp trên database superbot.db để phân tích dữ liệu tùy ý.",
        "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string", "description": "Câu lệnh SQL SELECT"}}},
    },
    {
        "name": "get_billing_groups",
        "description": "Lấy danh sách các nhóm quản lý cước và số dư hiện tại của chúng.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_group_summary",
        "description": "Lấy báo cáo tổng hợp cước của một nhóm cụ thể.",
        "parameters": {
            "type": "object",
            "required": ["group_id"],
            "properties": {
                "group_id": {"type": "integer", "description": "ID của nhóm billing"},
                "period": {"type": "string", "description": "today, week, hoặc month", "enum": ["today", "week", "month"]}
            }
        },
    },
    {
        "name": "topup_group",
        "description": "Nạp tiền vào một nhóm quản lý cước (tăng số dư). Dùng khi user yêu cầu nạp thêm tiền.",
        "parameters": {
            "type": "object",
            "required": ["group_id", "amount"],
            "properties": {
                "group_id": {"type": "integer", "description": "ID của nhóm billing"},
                "amount": {"type": "number", "description": "Số tiền cần nạp (VNĐ)"},
                "note": {"type": "string", "description": "Ghi chú nạp tiền (tùy chọn)"}
            }
        },
    },
]


async def _call_deepseek_with_tools(chat_id: int, user_text: str, ai_cfg: dict) -> str:
    """Gọi DeepSeek (OpenAI-compatible) với function calling và conversation memory."""
    from openai import AsyncOpenAI

    api_key    = ai_cfg.get("deepseek_api_key", "")
    base_url   = ai_cfg.get("deepseek_base_url", "https://api.deepseek.com")
    model_name = ai_cfg.get("deepseek_model", "deepseek-chat")
    sys_prompt = ai_cfg.get("system_prompt", "Bạn là AI Assistant của SuperBot.")
    hist_limit = ai_cfg.get("history_limit", 20)

    if not api_key or api_key == "DEEPSEEK_API_KEY_HERE":
        return "❌ Chưa cấu hình deepseek_api_key trong config.json."

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    # Build messages từ history
    history = await get_ai_history(chat_id, limit=hist_limit)
    messages = [{"role": "system", "content": sys_prompt}]
    for h in history:
        # "model" là role của Gemini, DeepSeek/OpenAI dùng "assistant"
        role = "assistant" if h["role"] == "model" else h["role"]
        messages.append({"role": role, "content": h["content"]})
    messages.append({"role": "user", "content": user_text})

    # Build tools theo OpenAI format
    tools = [
        {
            "type": "function",
            "function": {
                "name": decl["name"],
                "description": decl["description"],
                "parameters": decl.get("parameters", {"type": "object", "properties": {}}),
            }
        }
        for decl in _GEMINI_TOOLS_DECL
    ]

    # Vòng lặp agentic
    max_rounds = 5
    for _ in range(max_rounds):
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.2,
        )

        msg = response.choices[0].message

        # Không có tool call → trả kết quả
        if not msg.tool_calls:
            final_text = msg.content or "(Không có phản hồi)"
            await save_ai_history(chat_id, "user", user_text)
            await save_ai_history(chat_id, "assistant", final_text)  # DeepSeek dùng "assistant"
            return final_text

        # Có tool calls → thực hiện từng tool
        messages.append(msg)  # assistant message với tool_calls
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                fn_args = {}

            tool_fn = _TOOL_MAP.get(fn_name)
            if tool_fn:
                try:
                    tool_result = await tool_fn(**fn_args)
                except Exception as e:
                    tool_result = f"Lỗi khi gọi {fn_name}: {e}"
            else:
                tool_result = f"Tool '{fn_name}' không tồn tại."

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(tool_result),
            })

    return "(AI đã thực hiện nhiều bước nhưng chưa có kết quả cuối cùng.)"


async def _call_gemini_with_tools(chat_id: int, user_text: str, ai_cfg: dict) -> str:
    """Gọi Gemini với lịch sử hội thoại và function calling tools."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "❌ Thiếu thư viện google-genai. Chạy: pip install google-genai"

    api_key = ai_cfg.get("gemini_api_key", "")
    if not api_key:
        return "❌ Chưa cấu hình gemini_api_key trong config.json."

    model_name = ai_cfg.get("gemini_model", ai_cfg.get("model", "gemini-2.0-flash"))
    sys_prompt = ai_cfg.get("system_prompt", "Bạn là AI Assistant của SuperBot.")
    hist_limit = ai_cfg.get("history_limit", 20)

    history = await get_ai_history(chat_id, limit=hist_limit)
    client  = genai.Client(api_key=api_key)

    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=h["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))

    tools  = [types.Tool(function_declarations=[types.FunctionDeclaration(**decl) for decl in _GEMINI_TOOLS_DECL])]
    config = types.GenerateContentConfig(system_instruction=sys_prompt, tools=tools, temperature=0.2)

    max_rounds = 5
    for _ in range(max_rounds):
        response  = client.models.generate_content(model=model_name, contents=contents, config=config)
        candidate = response.candidates[0]
        part      = candidate.content.parts[0] if candidate.content.parts else None

        if part is None or not hasattr(part, "function_call") or part.function_call is None:
            final_text = response.text or "(Không có phản hồi)"
            await save_ai_history(chat_id, "user", user_text)
            await save_ai_history(chat_id, "model", final_text)
            return final_text

        fc       = part.function_call
        fn_name  = fc.name
        fn_args  = dict(fc.args) if fc.args else {}
        tool_fn  = _TOOL_MAP.get(fn_name)
        if tool_fn:
            try:
                tool_result = await tool_fn(**fn_args)
            except Exception as e:
                tool_result = f"Lỗi khi gọi {fn_name}: {e}"
        else:
            tool_result = f"Tool '{fn_name}' không tồn tại."

        contents.append(candidate.content)
        contents.append(types.Content(role="tool", parts=[types.Part(
            function_response=types.FunctionResponse(name=fn_name, response={"result": tool_result})
        )]))

    return "(AI đã thực hiện nhiều bước nhưng chưa có kết quả cuối cùng.)"


async def _call_ai_with_tools(chat_id: int, user_text: str) -> str:
    """Dispatcher: chọn provider từ config rồi gọi tương ứng."""
    ai_cfg   = _get_ai_config()
    provider = ai_cfg.get("provider", "gemini").lower()
    if provider == "deepseek":
        return await _call_deepseek_with_tools(chat_id, user_text, ai_cfg)
    else:
        return await _call_gemini_with_tools(chat_id, user_text, ai_cfg)



async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý mọi tin nhắn text không phải lệnh → gửi vào AI Agent."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    user_text = update.message.text.strip()

    if not _is_allowed(chat_id):
        await update.message.reply_text("❌ Bạn không có quyền sử dụng AI Agent.")
        return

    if not _get_ai_config().get("enabled", False):
        return  # AI bị tắt trong config, bỏ qua

    # Hiển thị "đang suy nghĩ..." trong khi chờ Gemini
    thinking_msg = await update.message.reply_text("🤖 Đang xử lý...")

    try:
        answer = await _call_ai_with_tools(chat_id, user_text)
    except Exception as e:
        answer = f"❌ Lỗi AI Agent: {e}"

    # Xóa tin nhắn "đang xử lý"
    try:
        await thinking_msg.delete()
    except Exception:
        pass

    # Gửi kết quả (chia nhỏ nếu quá dài, fallback plain text nếu Markdown lỗi)
    for part in _split_message(answer):
        try:
            await update.message.reply_text(part, parse_mode="Markdown")
        except Exception:
            # Fallback: gửi plain text nếu Markdown có ký tự lỗi
            try:
                await update.message.reply_text(part)
            except Exception:
                pass
        if len(answer) > 4000:
            await asyncio.sleep(0.3)


async def cmd_ai_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xóa lịch sử hội thoại AI của user hiện tại."""
    chat_id = update.message.chat_id
    await clear_ai_history(chat_id)
    await update.message.reply_text("✅ Đã xóa lịch sử hội thoại AI. Cuộc trò chuyện mới bắt đầu.")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bắt lỗi toàn cục của bot và báo lại cho người dùng."""
    logging.error("Exception while handling an update:", exc_info=context.error)
    traceback.print_exc()

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Hệ thống đang gặp lỗi khi xử lý lệnh. Vui lòng thử lại sau hoặc báo Admin kiểm tra log."
            )
        except Exception:
            pass


def main() -> None:
    config = load_config()
    bots = config.get("mpt", {}).get("bots", [])
    if not bots:
        print("❌ Chưa cấu hình mpt.bots trong config.json")
        return

    # Chạy bot đầu tiên được bật
    active_bot = next((b for b in bots if b.get("enabled")), None)
    if not active_bot:
        print("❌ Không có MPT bot nào được bật.")
        return

    token = active_bot.get("token", "")
    if not token:
        print("❌ Bot token trống.")
        return

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("billing", cmd_billing))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ai_clear", cmd_ai_clear))
    
    # Bắt lỗi toàn cục
    app.add_error_handler(global_error_handler)
    
    # MessageHandler phải thêm sau cùng — bắt text không phải lệnh /
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))
    ai_enabled = config.get("ai", {}).get("enabled", False)
    print(f"🤖 MPT Bot đang chạy... AI Agent: {'Bật' if ai_enabled else 'Tắt'} (Ctrl+C để dừng)")
    app.run_polling()


if __name__ == "__main__":
    main()
