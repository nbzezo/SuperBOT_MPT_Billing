"""
scheduler.py — APScheduler thay Chrome Alarms
Quét MPT routing định kỳ + gửi Telegram alerts
"""
import asyncio
import time
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from modules.mpt_http import get_session
from modules.alert_helpers import (
    build_bad_alert, build_bad_alert_batch, build_drop_alert,
    build_drop_alert_batch, build_recovery_alert,
)
from state import (
    log, save_snapshot, get_snapshot, get_persist, save_persist,
    increment_daily_stat, get_daily_stats, set_billing_cache,
    get_config_value, set_config_value, get_billing_cache
)
from utils import load_config, build_dauso_table

# Module-level Telegram sender (set bởi main.py)
_send_telegram_fn = None

DEDUP_WINDOW_MS = 6 * 3600 * 1000   # 6h

# ===== SETUP =====

def set_telegram_sender(fn):
    global _send_telegram_fn
    _send_telegram_fn = fn

async def set_paused(paused: bool):
    """Lưu trạng thái pause vào DB (bảng config) để mọi tiến trình dùng chung."""
    await set_config_value("paused", bool(paused))
    log(f"Hệ thống {'⏸ Tạm dừng' if paused else '▶ Tiếp tục chạy'}", "PAUSE" if paused else "RESUME")

async def is_paused() -> bool:
    return bool(await get_config_value("paused", False))

def is_within_work_hours(global_cfg: dict) -> bool:
    h = datetime.now().hour
    start = global_cfg.get("work_start_hour", 7)
    end   = global_cfg.get("work_end_hour", 21)
    return start <= h < end

async def send_telegram(bot_config: dict, text: str):
    """Gửi message tới Telegram bot."""
    if not _send_telegram_fn:
        return
    try:
        await _send_telegram_fn(bot_config, text)
    except Exception as e:
        log(f"Telegram send error: {e}", "TG_ERR")

# ===== CORE CHECK CYCLE =====

async def check_target(target: dict, global_cfg: dict, mpt_bots: list):
    """Quét 1 target MPT và xử lý cảnh báo."""
    tid = target["id"]
    alert_name = target.get("alertName", tid)

    if await is_paused():
        return
    if not is_within_work_hours(global_cfg):
        log(f"Ngoài giờ làm việc – bỏ qua", "SKIP_TIME", tid)
        return
    if not target.get("enabled"):
        return

    # Kiểm tra interval
    p = await get_persist(tid)
    now = int(time.time() * 1000)
    interval_ms = target.get("intervalMs", 5 * 60 * 1000)
    if p.get("lastRunAt") and now - p["lastRunAt"] < interval_ms:
        return

    # Kiểm tra cooldown
    if p.get("loginCooldownUntil") and now < p["loginCooldownUntil"]:
        remain_min = round((p["loginCooldownUntil"] - now) / 60000)
        log(f"Cooldown còn {remain_min} phút – bỏ qua", "COOLDOWN", tid)
        return

    log(f"Bắt đầu quét target: {alert_name}", "START", tid)
    await save_persist(tid, {"lastRunAt": now})

    session = get_session(target)
    rows = await session.scrape_routing()

    if rows is None:
        log(f"Quét thất bại – không lấy được dữ liệu", "FAIL", tid)
        fail_count = p.get("loginFailCount", 0) + 1
        await save_persist(tid, {"loginFailCount": fail_count})

        if fail_count >= 3:
            await save_persist(tid, {
                "loginFailCount": 0,
                "loginCooldownUntil": now + 15 * 60 * 1000
            })
            msg = (
                f"◆ *{alert_name} - Lỗi quét liên tiếp*\n"
                f"3 lần quét không thành công. Tạm dừng 15 phút.\n"
                f"Thời gian: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
            for bot in mpt_bots:
                if bot.get("enabled"):
                    await send_telegram(bot, msg)
        return

    await save_persist(tid, {"loginFailCount": 0})

    if not rows:
        log(f"Không có dòng dữ liệu routing", "WARN", tid)
        return

    log(f"Quét OK: {len(rows)} dòng routing", "DATA", tid)

    # Lấy snapshot cũ để so sánh
    old_snap = await get_snapshot(tid)
    old_rows = old_snap["rows"] if old_snap else []
    old_map = {r["cluster"]: r for r in old_rows}

    # Lưu snapshot mới
    await save_snapshot(tid, rows)

    # Đọc config ngưỡng
    threshold = int(target.get("threshold", 0) or 0)
    vt_limit = target.get("threshold_viettel")
    mb_limit = target.get("threshold_mobi")
    vn_limit = target.get("threshold_vina")
    vt_limit = int(vt_limit) if vt_limit is not None else threshold
    mb_limit = int(mb_limit) if mb_limit is not None else threshold
    vn_limit = int(vn_limit) if vn_limit is not None else threshold

    only_on_change  = global_cfg.get("only_on_change", True)
    drop_threshold  = global_cfg.get("drop_threshold", 70)
    remain_ratio    = 1 - (drop_threshold / 100)

    last_status = p.get("lastStatus", {})
    cluster_alert_times = p.get("clusterAlertTimes", {})
    cluster_drop_times  = p.get("clusterDropAlertTimes", {})
    cluster_bad_since   = p.get("clusterBadSince", {})  # cluster → ts_ms bắt đầu cạn

    changed_bad = []
    drop_alerts = []
    recovered   = []  # cluster vừa phục hồi (bad → good)

    for r in rows:
        cluster = r["cluster"]

        # 1. Drop alert
        old_r = old_map.get(cluster)
        if old_r:
            drop_details = []
            if old_r["viettel"] > 0 and r["viettel"] <= old_r["viettel"] * remain_ratio:
                drop_details.append(f"VT:{old_r['viettel']}→{r['viettel']}")
            if old_r["mobi"] > 0 and r["mobi"] <= old_r["mobi"] * remain_ratio:
                drop_details.append(f"MB:{old_r['mobi']}→{r['mobi']}")
            if old_r["vina"] > 0 and r["vina"] <= old_r["vina"] * remain_ratio:
                drop_details.append(f"VN:{old_r['vina']}→{r['vina']}")
            if drop_details:
                drop_alerts.append({"row": r, "details": ", ".join(drop_details)})

        # 2. Bad alert
        is_bad = r["viettel"] <= vt_limit or r["mobi"] <= mb_limit or r["vina"] <= vn_limit
        prev = last_status.get(cluster, "good")
        if not is_bad:
            if prev == "bad":
                # Phục hồi: ghi nhận thời điểm bắt đầu cạn để gửi tin ✓
                recovered.append({
                    "cluster": cluster,
                    "viettel": r["viettel"],
                    "mobi": r["mobi"],
                    "vina": r["vina"],
                    "since_ms": cluster_bad_since.get(cluster, 0),
                })
                cluster_bad_since.pop(cluster, None)
            last_status[cluster] = "good"
            continue

        if prev != "bad":
            cluster_bad_since[cluster] = now  # bắt đầu cạn
        last_status[cluster] = "bad"
        if only_on_change and prev == "bad":
            continue
        changed_bad.append(r)

    await save_persist(tid, {
        "lastStatus": last_status,
        "clusterAlertTimes": cluster_alert_times,
        "clusterDropAlertTimes": cluster_drop_times,
        "clusterBadSince": cluster_bad_since,
    })

    active_bots = [b for b in mpt_bots if b.get("enabled")]

    # Gửi DROP alerts (dedup 6h) — gộp 1 tin nếu nhiều cluster
    pending_drops = []
    for drop in drop_alerts:
        last_drop = cluster_drop_times.get(drop["row"]["cluster"], 0)
        if now - last_drop < DEDUP_WINDOW_MS:
            continue
        cluster_drop_times[drop["row"]["cluster"]] = now
        pending_drops.append(drop)
    if pending_drops:
        text = build_drop_alert_batch(alert_name, pending_drops, drop_threshold)
        for bot in active_bots:
            await send_telegram(bot, text)
        await increment_daily_stat(tid, "drop")

    # Gửi BAD alerts (dedup 6h) — gộp 1 tin nếu nhiều cluster
    pending_bads = []
    for r in changed_bad:
        last_bad = cluster_alert_times.get(r["cluster"], 0)
        if now - last_bad < DEDUP_WINDOW_MS:
            continue
        cluster_alert_times[r["cluster"]] = now
        pending_bads.append(r)
    if pending_bads:
        text = build_bad_alert_batch(alert_name, pending_bads, cluster_bad_since)
        for bot in active_bots:
            await send_telegram(bot, text)
        await increment_daily_stat(tid, "bad")

    # Gửi RECOVERY alerts (không dedup — sự kiện hiếm, gộp 1 tin)
    if recovered:
        text = build_recovery_alert(alert_name, recovered)
        for bot in active_bots:
            await send_telegram(bot, text)

    # Lưu lại alert times đã update
    await save_persist(tid, {
        "clusterAlertTimes": cluster_alert_times,
        "clusterDropAlertTimes": cluster_drop_times,
        "clusterBadSince": cluster_bad_since,
    })


async def run_all_checks():
    """Job chạy định kỳ — quét tất cả target đang bật."""
    if await is_paused():
        return
    try:
        config = load_config()
        global_cfg = config.get("global", {})
        mpt_cfg    = config.get("mpt", {})
        targets    = mpt_cfg.get("targets", [])
        mpt_bots   = mpt_cfg.get("bots", [])

        if not targets:
            return

        tasks = [
            check_target(t, global_cfg, mpt_bots)
            for t in targets if t.get("enabled")
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        log(f"run_all_checks error: {e}", "SCHED_ERR")


async def send_daily_summary(time_label: str):
    """Gửi báo cáo tóm tắt đầu/cuối ngày."""
    config = load_config()
    mpt_cfg = config.get("mpt", {})
    mpt_bots = mpt_cfg.get("bots", [])
    active_bots = [b for b in mpt_bots if b.get("enabled")]
    if not active_bots:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    stats = await get_daily_stats(today)
    targets = [t for t in mpt_cfg.get("targets", []) if t.get("enabled")]

    if time_label == "morning":
        msg = "◇ *BÁO CÁO ĐẦU NGÀY*\n"
        msg += f"Hệ thống MPT đã sẵn sàng.\n"
        msg += f"Số lượng Target đang quét: {len(targets)}\n"
        msg += "Chúc sếp và team một ngày làm việc hiệu quả!"
    else:
        msg = "◇ *BÁO CÁO CUỐI NGÀY*\n"
        total_drops = total_bads = 0
        for t in targets:
            d = stats.get(t["id"], {}).get("drops", 0)
            b = stats.get(t["id"], {}).get("bads", 0)
            total_drops += d
            total_bads += b
        msg += f"Tổng kết hôm nay:\n"
        msg += f"- Cảnh báo giảm sâu: {total_drops}\n"
        msg += f"- Cảnh báo cạn số: {total_bads}\n"
        if total_drops == 0 and total_bads == 0:
            msg += "✓ Hệ thống chạy ổn định, không có sự cố nào.\n"
        msg += "Chúc mọi người buổi tối vui vẻ!"

    for bot in active_bots:
        await send_telegram(bot, msg)

    # Kèm bảng số lượng đầu số (VT/MB/VN) theo từng target — lấy từ snapshot mới nhất
    for t in targets:
        snap = await get_snapshot(t["id"])
        rows = snap.get("rows") if snap else None
        if not rows:
            continue
        ts = datetime.fromtimestamp(snap["ts"] / 1000).strftime("%H:%M")
        table = build_dauso_table(t.get("alertName", t["id"]), rows, ts)
        for bot in active_bots:
            await send_telegram(bot, table)
        await asyncio.sleep(0.4)


# Tiến trình backfill/cache billing (dùng chung cho cron 22:00, nút Backfill, nút Cache hôm nay)
_billing_backfill = {"running": False, "done": 0, "total": 0}


async def backfill_billing(days: int):
    """Cache sản lượng billing cho `days` ngày gần nhất × mọi target enabled.
    Tích luỹ lịch sử cho biểu đồ xu hướng. Chống chạy trùng qua cờ _billing_backfill."""
    if _billing_backfill["running"]:
        return
    config = load_config()
    targets = [t for t in config.get("mpt", {}).get("targets", []) if t.get("enabled")]
    if not targets:
        return

    from datetime import timedelta
    daylist = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    _billing_backfill.update(running=True, done=0, total=len(daylist) * len(targets))
    log(f"Bắt đầu backfill billing {days} ngày × {len(targets)} target", "BILLING_CACHE")
    try:
        for day in daylist:
            for t in targets:
                try:
                    result = await get_session(t).fetch_billing(day)
                    if result and not result.get("error") and result.get("rows"):
                        await set_billing_cache(day, t["id"], result)
                        log(f"Cache {day}: {len(result['rows'])} account", "BILLING_CACHE", t["id"])
                except Exception as e:
                    log(f"backfill_billing {day} lỗi: {e}", "BILLING_CACHE_ERR", t["id"])
                _billing_backfill["done"] += 1
                await asyncio.sleep(0.3)
    finally:
        _billing_backfill["running"] = False
        log("Hoàn tất backfill billing", "BILLING_CACHE")


def billing_backfill_status() -> dict:
    return dict(_billing_backfill)


async def cache_billing_daily():
    """Cron 22:00 — cache billing hôm nay (1 ngày) cho mọi target enabled."""
    await backfill_billing(1)


# ===== SCHEDULER SETUP =====

_scheduler: Optional[AsyncIOScheduler] = None

async def prefetch_billing_today():
    """Cron 23:50 — pre-fetch billing hom nay vao cache truoc khi het ngay.
    Dam bao sang hom sau AI co data ngay ma khong can fetch portal."""
    config = load_config()
    targets = [t for t in config.get("mpt", {}).get("targets", []) if t.get("enabled")]
    today = datetime.now().strftime("%Y-%m-%d")
    log(f"Pre-fetch billing ngay {today} cho {len(targets)} target", "BILLING_PREFETCH")
    for t in targets:
        try:
            existing = await get_billing_cache(today, t["id"])
            if existing and existing.get("rows"):
                log(f"Da co cache ngay {today}, bo qua", "BILLING_PREFETCH", t["id"])
                continue
            result = await get_session(t).fetch_billing(today)
            if result and not result.get("error") and result.get("rows"):
                await set_billing_cache(today, t["id"], result)
                log(f"Pre-fetch OK: {len(result['rows'])} account", "BILLING_PREFETCH", t["id"])
        except Exception as e:
            log(f"Pre-fetch billing loi: {e}", "BILLING_PREFETCH_ERR", t["id"])
        await asyncio.sleep(1)


async def refresh_billing_today(force: bool = False):
    """Fetch billing hom nay va luu cache. force=True se cap nhat du lieu moi du da co cache cu."""
    config = load_config()
    targets = [t for t in config.get("mpt", {}).get("targets", []) if t.get("enabled")]
    mpt_bots = [b for b in config.get("mpt", {}).get("bots", []) if b.get("enabled")]
    today = datetime.now().strftime("%Y-%m-%d")
    log(f"Refresh billing ngay {today} cho {len(targets)} target (force={force})", "BILLING_REFRESH")
    for t in targets:
        try:
            if not force:
                existing = await get_billing_cache(today, t["id"])
                if existing and existing.get("rows"):
                    log(f"Da co cache, bo qua (dung force=True de cap nhat)", "BILLING_REFRESH", t["id"])
                    continue
            result = await get_session(t).fetch_billing(today)
            if result and not result.get("error") and result.get("rows"):
                await set_billing_cache(today, t["id"], result)
                total = sum(r["duration"] for r in result["rows"])
                log(f"Cache OK: {len(result['rows'])} accounts, tong {total:.0f} phut", "BILLING_REFRESH", t["id"])
        except Exception as e:
            log(f"Refresh billing loi: {e}", "BILLING_REFRESH_ERR", t["id"])
        await asyncio.sleep(2)

    # --- Chạy billing cycle: tính cước và kiểm tra cảnh báo ---
    try:
        from modules.billing_manager import run_billing_cycle
        alerts = await run_billing_cycle(today)
        for alert in alerts:
            msg = alert["message"]
            for bot in mpt_bots:
                await send_telegram(bot, msg)
    except Exception as e:
        log(f"Billing cycle error: {e}", "BILLING_CYCLE_ERR")


def create_scheduler() -> AsyncIOScheduler:
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")

    # Quet routing moi 1 phut (target tu kiem tra interval rieng)
    _scheduler.add_job(run_all_checks, "interval", minutes=1, id="mpt_check")

    # Bao cao dau/cuoi ngay
    _scheduler.add_job(send_daily_summary, "cron", hour=8, minute=0, args=["morning"], id="report_morning")
    _scheduler.add_job(send_daily_summary, "cron", hour=20, minute=0, args=["evening"], id="report_evening")

    # --- Billing cache refresh: 10h, 12h, 15h, 18h, 22h ---
    _scheduler.add_job(refresh_billing_today, "cron", hour=10, minute=0, kwargs={"force": True}, id="billing_10h")
    _scheduler.add_job(refresh_billing_today, "cron", hour=12, minute=0, kwargs={"force": True}, id="billing_noon")
    _scheduler.add_job(refresh_billing_today, "cron", hour=15, minute=0, kwargs={"force": True}, id="billing_15h")
    _scheduler.add_job(refresh_billing_today, "cron", hour=18, minute=0, kwargs={"force": True}, id="billing_18h")
    _scheduler.add_job(refresh_billing_today, "cron", hour=22, minute=0, kwargs={"force": True}, id="billing_cache")
    # 23:50 -- pre-fetch lan cuoi truoc khi het ngay
    _scheduler.add_job(prefetch_billing_today, "cron", hour=23, minute=50, id="billing_prefetch")

    return _scheduler



def get_scheduler() -> Optional[AsyncIOScheduler]:
    return _scheduler
