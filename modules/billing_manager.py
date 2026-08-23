"""
modules/billing_manager.py
Logic tính cước, quản lý số dư và gửi cảnh báo Telegram.
"""
import time
from datetime import datetime

from state import (
    get_billing_groups, get_billing_cache, record_billing_usage,
    get_billing_group, log
)

# Dedup cảnh báo: không spam cùng loại trong 1h
ALERT_DEDUP_SEC = 3600
_alert_sent: dict = {}


def calculate_cost(minutes_raw: float, price_per_min: float,
                   block_factor: float = 1.02) -> tuple:
    """
    Tính cước với block_factor xấp xỉ block 6s+1.
    Vì portal trả tổng phút (không có từng cuộc riêng lẻ).
    Returns: (minutes_billed, cost)
    """
    minutes_billed = minutes_raw * block_factor
    cost = minutes_billed * price_per_min
    return round(minutes_billed, 4), round(cost, 2)


def check_balance_alerts(group: dict) -> list:
    """
    Kiểm tra ngưỡng cảnh báo số dư.
    Returns: list alerts [{type, message, group_id, group_name, balance}]
    """
    alerts = []
    gid = group["id"]
    name = group["name"]
    balance = group["balance"]
    warn_low = group.get("warn_low", 0)
    warn_neg = group.get("warn_neg", 0)
    now = time.time()

    def _should_send(alert_type):
        key = (gid, alert_type)
        last = _alert_sent.get(key, 0)
        if now - last > ALERT_DEDUP_SEC:
            _alert_sent[key] = now
            return True
        return False

    # Âm vượt ngưỡng (nghiêm trọng nhất)
    if warn_neg < 0 and balance < warn_neg:
        if _should_send("neg_threshold"):
            alerts.append({
                "type": "neg_threshold",
                "group_id": gid, "group_name": name, "balance": balance,
                "message": (
                    f"SOS - Nhom [{name}] am vuot nguong!\n"
                    f"So du: {balance:,.0f} VND\n"
                    f"Nguong dat: {warn_neg:,.0f} VND\n"
                    f"Can nap tien ngay!"
                )
            })
    # Âm tiền
    elif balance < 0:
        if _should_send("negative"):
            alerts.append({
                "type": "negative",
                "group_id": gid, "group_name": name, "balance": balance,
                "message": (
                    f"CANH BAO - Nhom [{name}] am tien!\n"
                    f"So du: {balance:,.0f} VND\n"
                    f"Vui long nap tien."
                )
            })
    # Gần hết tiền
    elif warn_low > 0 and balance < warn_low:
        if _should_send("low"):
            alerts.append({
                "type": "low",
                "group_id": gid, "group_name": name, "balance": balance,
                "message": (
                    f"CANH BAO - Nhom [{name}] sap het tien!\n"
                    f"So du con: {balance:,.0f} VND\n"
                    f"Nguong canh bao: {warn_low:,.0f} VND"
                )
            })

    return alerts


async def run_billing_cycle(date_str: str = None) -> list:
    """
    Tính cước cho tất cả nhóm billing enabled từ billing_cache.
    Gọi sau mỗi lần refresh_billing_today().
    Returns: list alerts cần gửi Telegram.
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    groups = await get_billing_groups(enabled_only=True)
    if not groups:
        return []

    all_alerts = []
    log(f"Billing cycle {date_str}: {len(groups)} nhom", "BILLING_CYCLE")

    for group in groups:
        gid = group["id"]
        gname = group["name"]
        target_id = group["target_id"]
        clusters = group.get("clusters", [])
        price = group["price_per_min"]
        block_factor = group.get("block_factor", 1.02)

        if not clusters or price <= 0:
            continue

        cached = await get_billing_cache(date_str, target_id)
        if not cached or not cached.get("rows"):
            log(f"Khong co billing cache {target_id} {date_str}", "BILLING_CYCLE", str(gid))
            continue

        # Tổng phút của các cluster thuộc nhóm
        total_raw = sum(
            r.get("duration", 0.0)
            for r in cached["rows"]
            if r.get("name") in clusters
        )

        minutes_billed, cost = calculate_cost(total_raw, price, block_factor)
        await record_billing_usage(gid, date_str, total_raw, minutes_billed, cost)

        log(
            f"[{gname}] {total_raw:.1f}ph raw -> {minutes_billed:.1f}ph billed -> {cost:,.0f}VND",
            "BILLING_CYCLE", str(gid)
        )

        # Reload để lấy balance mới nhất sau khi deduct
        updated = await get_billing_group(gid)
        if updated:
            alerts = check_balance_alerts(updated)
            all_alerts.extend(alerts)

    return all_alerts
