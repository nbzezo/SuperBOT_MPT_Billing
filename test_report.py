import asyncio
import os
import sys

# setup path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from bots.bot_mpt import build_dauso_table
from state import get_daily_stats, get_all_snapshots
from utils import load_config
from telegram.helpers import escape_markdown

async def test_report():
    today = datetime.now().strftime("%Y-%m-%d")
    stats = await get_daily_stats(today)
    config = load_config()
    targets = [t for t in config.get("mpt", {}).get("targets", []) if t.get("enabled")]
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

    print("--- MSG ---")
    print(msg)
    print("--- END MSG ---")

    for t in targets:
        snap = snapshots.get(t["id"])
        rows = snap.get("rows") if snap else None
        if not rows:
            continue
        ts = datetime.fromtimestamp(snap["ts"] / 1000).strftime("%H:%M")
        m = build_dauso_table(t.get("alertName", t["id"]), rows, ts)
        print("--- TABLE ---")
        print(m)
        print("--- END TABLE ---")

if __name__ == "__main__":
    asyncio.run(test_report())
