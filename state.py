"""
state.py — Quản lý trạng thái bằng SQLite (thay Chrome Storage)
"""
import asyncio
import json
import os
import time
from datetime import datetime
import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), "superbot.db")


def _connect():
    """
    Mở connection SQLite với busy_timeout để chịu được nhiều tiến trình
    (main + các bot) cùng ghi. WAL được bật bền vững một lần ở init_db.
    """
    return aiosqlite.connect(DB_PATH, timeout=5.0)


# ===== INIT =====

async def init_db():
    async with _connect() as db:
        # WAL bền vững ở cấp DB → cho phép đọc/ghi đồng thời giữa các tiến trình
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                target_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                rows TEXT NOT NULL,
                PRIMARY KEY (target_id)
            );

            CREATE TABLE IF NOT EXISTS routing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                rows TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                level TEXT NOT NULL,
                code TEXT,
                target_id TEXT,
                msg TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date_str TEXT NOT NULL,
                target_id TEXT NOT NULL,
                drops INTEGER DEFAULT 0,
                bads INTEGER DEFAULT 0,
                PRIMARY KEY (date_str, target_id)
            );

            CREATE TABLE IF NOT EXISTS persist (
                target_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS billing_cache (
                date_str TEXT NOT NULL,
                target_id TEXT NOT NULL,
                data TEXT NOT NULL,
                ts INTEGER NOT NULL,
                PRIMARY KEY (date_str, target_id)
            );

            CREATE TABLE IF NOT EXISTS meeting_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                ts INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_status (
                name TEXT PRIMARY KEY,
                running INTEGER DEFAULT 0,
                pid INTEGER,
                started_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS ai_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS billing_groups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT UNIQUE NOT NULL,
                target_id       TEXT NOT NULL,
                price_per_min   REAL NOT NULL DEFAULT 0,
                block_factor    REAL NOT NULL DEFAULT 1.02,
                balance         REAL NOT NULL DEFAULT 0,
                warn_low        REAL NOT NULL DEFAULT 0,
                warn_neg        REAL NOT NULL DEFAULT 0,
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      INTEGER NOT NULL,
                notes           TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS billing_group_clusters (
                group_id        INTEGER NOT NULL REFERENCES billing_groups(id) ON DELETE CASCADE,
                cluster_name    TEXT NOT NULL,
                PRIMARY KEY (group_id, cluster_name)
            );

            CREATE TABLE IF NOT EXISTS billing_topups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id    INTEGER NOT NULL REFERENCES billing_groups(id),
                amount      REAL NOT NULL,
                note        TEXT DEFAULT '',
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS billing_usage (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id        INTEGER NOT NULL,
                date_str        TEXT NOT NULL,
                minutes_raw     REAL NOT NULL,
                minutes_billed  REAL NOT NULL,
                cost            REAL NOT NULL,
                ts              INTEGER NOT NULL,
                UNIQUE(group_id, date_str)
            );
        """)
        await db.commit()

# ===== CONFIG =====

async def get_config_value(key: str, default=None):
    async with _connect() as db:
        async with db.execute("SELECT value FROM config WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except Exception:
                    return row[0]  # cố ý: giá trị cũ lưu dạng chuỗi thô
            return default

async def set_config_value(key: str, value):
    async with _connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO config(key, value) VALUES(?,?)",
            (key, json.dumps(value))
        )
        await db.commit()

# ===== LOGGING =====

async def add_log(msg: str, code: str = "", target_id: str = "", level: str = "INFO"):
    ts = int(time.time() * 1000)
    async with _connect() as db:
        await db.execute(
            "INSERT INTO logs(ts, level, code, target_id, msg) VALUES(?,?,?,?,?)",
            (ts, level, code or "", target_id or "", msg)
        )
        # Giữ tối đa 500 dòng log
        await db.execute(
            "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 500)"
        )
        await db.commit()

def log(msg: str, code: str = "", target_id: str = ""):
    """Sync wrapper — schedule coroutine vào event loop đang chạy nếu có.
    Nếu không có loop (gọi từ context sync) thì chỉ in ra stdout."""
    try:
        asyncio.get_running_loop()
        asyncio.ensure_future(add_log(msg, code, target_id))
    except RuntimeError:
        # Không có event loop đang chạy → bỏ qua ghi DB, chỉ in
        pass
    ts_str = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{code}]" if code else ""
    tg = f"[{target_id}]" if target_id else ""
    print(f"[{ts_str}]{prefix}{tg} {msg}")

async def get_logs(limit: int = 200):
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT ts, level, code, target_id, msg FROM logs ORDER BY id DESC LIMIT ?",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in reversed(rows)]

async def clear_logs():
    async with _connect() as db:
        await db.execute("DELETE FROM logs")
        await db.commit()

# ===== SNAPSHOT =====

async def save_snapshot(target_id: str, rows: list):
    ts = int(time.time() * 1000)
    async with _connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO snapshots(target_id, ts, rows) VALUES(?,?,?)",
            (target_id, ts, json.dumps(rows))
        )
        # Routing history (giữ 30 entry)
        await db.execute(
            "INSERT INTO routing_history(target_id, ts, rows) VALUES(?,?,?)",
            (target_id, ts, json.dumps(rows))
        )
        await db.execute(
            """DELETE FROM routing_history WHERE target_id=? AND id NOT IN (
                SELECT id FROM routing_history WHERE target_id=? ORDER BY id DESC LIMIT 30
            )""",
            (target_id, target_id)
        )
        await db.commit()

async def get_snapshot(target_id: str):
    async with _connect() as db:
        async with db.execute(
            "SELECT rows, ts FROM snapshots WHERE target_id=?", (target_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"rows": json.loads(row[0]), "ts": row[1]}
            return None

async def get_all_snapshots():
    async with _connect() as db:
        async with db.execute("SELECT target_id, rows, ts FROM snapshots") as cur:
            rows = await cur.fetchall()
            return {r[0]: {"rows": json.loads(r[1]), "ts": r[2]} for r in rows}

# ===== PERSIST (per-target runtime state) =====

async def get_persist(target_id: str) -> dict:
    async with _connect() as db:
        async with db.execute("SELECT data FROM persist WHERE target_id=?", (target_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return json.loads(row[0])
            return {
                "lastStatus": {},
                "clusterAlertTimes": {},
                "clusterDropAlertTimes": {},
                "lastSessionReset": 0,
                "lastRunAt": 0,
                "loginCooldownUntil": 0,
                "loginFailCount": 0,
                "lastReportDate": "",
                "lastBillingCacheDate": "",
                "lastMorningBillingDate": ""
            }

async def save_persist(target_id: str, data: dict):
    async with _connect() as db:
        existing = await get_persist(target_id)
        existing.update(data)
        await db.execute(
            "INSERT OR REPLACE INTO persist(target_id, data) VALUES(?,?)",
            (target_id, json.dumps(existing))
        )
        await db.commit()

# ===== DAILY STATS =====

async def increment_daily_stat(target_id: str, stat_type: str):
    date_str = datetime.now().strftime("%Y-%m-%d")
    col = "drops" if stat_type == "drop" else "bads"
    async with _connect() as db:
        await db.execute(
            f"INSERT INTO daily_stats(date_str, target_id, {col}) VALUES(?,?,1) "
            f"ON CONFLICT(date_str, target_id) DO UPDATE SET {col}={col}+1",
            (date_str, target_id)
        )
        await db.commit()

async def get_daily_stats(date_str: str = None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    async with _connect() as db:
        async with db.execute(
            "SELECT target_id, drops, bads FROM daily_stats WHERE date_str=?", (date_str,)
        ) as cur:
            rows = await cur.fetchall()
            return {r[0]: {"drops": r[1], "bads": r[2]} for r in rows}

async def get_daily_stats_range(days: int = 14):
    """Trả {date_str: {target_id: {'drops':, 'bads':}}} cho `days` ngày gần nhất (gồm hôm nay)."""
    from datetime import timedelta
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    async with _connect() as db:
        async with db.execute(
            "SELECT date_str, target_id, drops, bads FROM daily_stats WHERE date_str>=? ORDER BY date_str",
            (start,)
        ) as cur:
            rows = await cur.fetchall()
    out = {}
    for d, tid, drops, bads in rows:
        out.setdefault(d, {})[tid] = {"drops": drops, "bads": bads}
    return out

# ===== BILLING CACHE =====

async def get_billing_cache(date_str: str, target_id: str):
    async with _connect() as db:
        async with db.execute(
            "SELECT data FROM billing_cache WHERE date_str=? AND target_id=?",
            (date_str, target_id)
        ) as cur:
            row = await cur.fetchone()
            return json.loads(row[0]) if row else None

async def get_billing_cache_range(days: int = 7):
    """Trả {date_str: {target_id: data}} cho `days` ngày gần nhất (dùng cho biểu đồ xu hướng)."""
    from datetime import timedelta
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    async with _connect() as db:
        async with db.execute(
            "SELECT date_str, target_id, data FROM billing_cache WHERE date_str>=? ORDER BY date_str",
            (start,)
        ) as cur:
            rows = await cur.fetchall()
    out = {}
    for d, tid, data in rows:
        try:
            out.setdefault(d, {})[tid] = json.loads(data)
        except Exception:
            pass
    return out

async def set_billing_cache(date_str: str, target_id: str, data: dict):
    async with _connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO billing_cache(date_str, target_id, data, ts) VALUES(?,?,?,?)",
            (date_str, target_id, json.dumps(data), int(time.time()))
        )
        # Xóa cache > 7 ngày
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        await db.execute("DELETE FROM billing_cache WHERE date_str<?", (cutoff,))
        await db.commit()

# ===== MEETING HISTORY =====

async def add_meeting_history(filename: str):
    async with _connect() as db:
        await db.execute(
            "INSERT INTO meeting_history(filename, ts) VALUES(?,?)",
            (filename, int(time.time()))
        )
        await db.commit()

async def get_meeting_history(limit: int = 20):
    async with _connect() as db:
        async with db.execute(
            "SELECT filename, ts FROM meeting_history ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [{"filename": r[0], "ts": r[1]} for r in rows]

async def delete_meeting_history(filename: str):
    async with _connect() as db:
        await db.execute("DELETE FROM meeting_history WHERE filename=?", (filename,))
        await db.commit()

# ===== BOT STATUS =====

async def set_bot_status(name: str, running: bool, pid: int = None):
    async with _connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO bot_status(name, running, pid, started_at) VALUES(?,?,?,?)",
            (name, 1 if running else 0, pid, int(time.time()) if running else None)
        )
        await db.commit()

async def get_bot_status(name: str):
    async with _connect() as db:
        async with db.execute(
            "SELECT running, pid, started_at FROM bot_status WHERE name=?", (name,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"running": bool(row[0]), "pid": row[1], "started_at": row[2]}
            return {"running": False, "pid": None, "started_at": None}

# ===== AI HISTORY =====

async def save_ai_history(chat_id: str, role: str, content: str):
    """Lưu một lượt hội thoại (user hoặc model) của AI Agent."""
    ts = int(time.time() * 1000)
    async with _connect() as db:
        await db.execute(
            "INSERT INTO ai_history(chat_id, role, content, ts) VALUES(?,?,?,?)",
            (str(chat_id), role, content, ts)
        )
        # Giữ tối đa 100 turn per chat
        await db.execute(
            "DELETE FROM ai_history WHERE chat_id=? AND id NOT IN "
            "(SELECT id FROM ai_history WHERE chat_id=? ORDER BY id DESC LIMIT 100)",
            (str(chat_id), str(chat_id))
        )
        await db.commit()

async def get_ai_history(chat_id: str, limit: int = 20) -> list:
    """Lấy `limit` turn hội thoại gần nhất của `chat_id` (thứ tự cũ → mới)."""
    async with _connect() as db:
        async with db.execute(
            "SELECT role, content FROM ("
            "  SELECT id, role, content FROM ai_history WHERE chat_id=? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (str(chat_id), limit)
        ) as cur:
            rows = await cur.fetchall()
            return [{"role": r[0], "content": r[1]} for r in rows]

async def clear_ai_history(chat_id: str):
    """Xóa toàn bộ lịch sử hội thoại của `chat_id`."""
    async with _connect() as db:
        await db.execute("DELETE FROM ai_history WHERE chat_id=?", (str(chat_id),))
        await db.commit()


# ===== BILLING MANAGER =====

async def create_billing_group(name: str, target_id: str, price_per_min: float,
                                block_factor: float = 1.02, warn_low: float = 0,
                                warn_neg: float = 0, notes: str = "") -> int:
    """Tạo nhóm billing mới. Trả về id."""
    async with _connect() as db:
        cur = await db.execute(
            """INSERT INTO billing_groups
               (name, target_id, price_per_min, block_factor, balance, warn_low, warn_neg, enabled, created_at, notes)
               VALUES (?,?,?,?,0,?,?,1,?,?)""",
            (name, target_id, price_per_min, block_factor, warn_low, warn_neg, int(time.time()), notes)
        )
        await db.commit()
        return cur.lastrowid


async def get_billing_groups(enabled_only: bool = False) -> list:
    """Lấy danh sách nhóm billing kèm clusters."""
    async with _connect() as db:
        q = "SELECT * FROM billing_groups"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY id"
        async with db.execute(q) as cur:
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
        groups = [dict(zip(cols, r)) for r in rows]
        for g in groups:
            async with db.execute(
                "SELECT cluster_name FROM billing_group_clusters WHERE group_id=?", (g["id"],)
            ) as cur2:
                g["clusters"] = [r[0] for r in await cur2.fetchall()]
        return groups


async def get_billing_group(group_id: int) -> dict | None:
    """Lấy 1 nhóm theo id, kèm clusters."""
    async with _connect() as db:
        async with db.execute("SELECT * FROM billing_groups WHERE id=?", (group_id,)) as cur:
            cols = [d[0] for d in cur.description]
            row = await cur.fetchone()
        if not row:
            return None
        g = dict(zip(cols, row))
        async with db.execute(
            "SELECT cluster_name FROM billing_group_clusters WHERE group_id=?", (group_id,)
        ) as cur2:
            g["clusters"] = [r[0] for r in await cur2.fetchall()]
        return g


async def update_billing_group(group_id: int, **kwargs):
    """Cập nhật fields của nhóm (name, price_per_min, block_factor, warn_low, warn_neg, enabled, notes)."""
    allowed = {"name", "target_id", "price_per_min", "block_factor",
               "warn_low", "warn_neg", "enabled", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [group_id]
    async with _connect() as db:
        await db.execute(f"UPDATE billing_groups SET {sets} WHERE id=?", vals)
        await db.commit()


async def delete_billing_group(group_id: int):
    async with _connect() as db:
        await db.execute("DELETE FROM billing_groups WHERE id=?", (group_id,))
        await db.commit()


async def set_group_clusters(group_id: int, cluster_names: list):
    """Gán lại toàn bộ danh sách cluster cho nhóm (replace all)."""
    async with _connect() as db:
        await db.execute("DELETE FROM billing_group_clusters WHERE group_id=?", (group_id,))
        for name in cluster_names:
            await db.execute(
                "INSERT OR IGNORE INTO billing_group_clusters(group_id, cluster_name) VALUES(?,?)",
                (group_id, name)
            )
        await db.commit()


async def topup_group(group_id: int, amount: float, note: str = ""):
    """Nạp tiền vào nhóm. Cộng vào balance, ghi log topup."""
    async with _connect() as db:
        await db.execute(
            "UPDATE billing_groups SET balance = balance + ? WHERE id=?",
            (amount, group_id)
        )
        await db.execute(
            "INSERT INTO billing_topups(group_id, amount, note, created_at) VALUES(?,?,?,?)",
            (group_id, amount, note, int(time.time()))
        )
        await db.commit()


async def get_topup_history(group_id: int, limit: int = 20) -> list:
    async with _connect() as db:
        async with db.execute(
            "SELECT amount, note, created_at FROM billing_topups WHERE group_id=? ORDER BY id DESC LIMIT ?",
            (group_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    return [{"amount": r[0], "note": r[1], "created_at": r[2]} for r in rows]


async def record_billing_usage(group_id: int, date_str: str,
                                minutes_raw: float, minutes_billed: float, cost: float):
    """Upsert billing_usage và cập nhật balance (trừ phần chi phí tăng thêm so với lần trước)."""
    async with _connect() as db:
        # Lấy cost cũ trong ngày (nếu có)
        async with db.execute(
            "SELECT cost FROM billing_usage WHERE group_id=? AND date_str=?",
            (group_id, date_str)
        ) as cur:
            old = await cur.fetchone()
        old_cost = old[0] if old else 0.0
        delta = cost - old_cost  # phần tăng thêm

        # Upsert usage
        await db.execute(
            """INSERT INTO billing_usage(group_id, date_str, minutes_raw, minutes_billed, cost, ts)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(group_id, date_str) DO UPDATE SET
                 minutes_raw=excluded.minutes_raw,
                 minutes_billed=excluded.minutes_billed,
                 cost=excluded.cost,
                 ts=excluded.ts""",
            (group_id, date_str, minutes_raw, minutes_billed, cost, int(time.time()))
        )
        # Trừ balance với phần delta
        if delta != 0:
            await db.execute(
                "UPDATE billing_groups SET balance = balance - ? WHERE id=?",
                (delta, group_id)
            )
        await db.commit()


async def get_billing_usage(group_id: int, date_str: str) -> dict | None:
    async with _connect() as db:
        async with db.execute(
            "SELECT * FROM billing_usage WHERE group_id=? AND date_str=?",
            (group_id, date_str)
        ) as cur:
            cols = [d[0] for d in cur.description]
            row = await cur.fetchone()
    return dict(zip(cols, row)) if row else None


async def get_billing_usage_range(group_id: int, start_date: str, end_date: str) -> list:
    """Lấy usage từ start_date đến end_date (YYYY-MM-DD)."""
    async with _connect() as db:
        async with db.execute(
            "SELECT date_str, minutes_raw, minutes_billed, cost FROM billing_usage "
            "WHERE group_id=? AND date_str>=? AND date_str<=? ORDER BY date_str",
            (group_id, start_date, end_date)
        ) as cur:
            rows = await cur.fetchall()
    return [{"date": r[0], "minutes_raw": r[1], "minutes_billed": r[2], "cost": r[3]} for r in rows]
