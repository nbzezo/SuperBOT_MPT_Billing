"""
modules/mpt_routing.py — Playwright headless thay thế Chrome Extension
Port logic từ background/routing.js + background/billing.js
"""
import asyncio
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PwTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils import find_chrome  # noqa: E402

CHROME_PATH = find_chrome()

# ===== SESSION MANAGER =====

class MPTSession:
    """Quản lý 1 session Playwright cho 1 target."""

    def __init__(self, target: dict):
        self.target = target
        self.target_id: str = target["id"]
        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._logged_in = False
        self._last_session_reset = 0
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._browser is None or not self._browser.is_connected():
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            launch_opts = {"headless": True}
            if CHROME_PATH:
                launch_opts["executable_path"] = CHROME_PATH
            self._browser = await self._playwright.chromium.launch(**launch_opts)
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()
            self._logged_in = False

    async def _detect_page_type(self) -> str:
        """Phân loại trang hiện tại: 'login' | 'routing' | 'unknown'"""
        try:
            url = self._page.url
            content = await self._page.content()
            if "username_softswitch" in content or "/user" in url:
                return "login"
            if "/routing/monitoring" in url or "routing" in content.lower():
                return "routing"
            return "unknown"
        except Exception:
            return "unknown"  # cố ý: lỗi đọc trang → coi như chưa xác định

    async def _do_login(self) -> bool:
        """Đăng nhập vào portal MPT."""
        cfg = self.target
        try:
            await self._page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=40000)

            # Chờ form login
            for _ in range(40):
                ptype = await self._detect_page_type()
                if ptype == "login":
                    break
                await asyncio.sleep(1)
            else:
                return False

            # Điền thông tin đăng nhập
            await self._page.fill("#username_softswitch", cfg["username"])
            await self._page.fill("#password_softswitch", cfg["password"])
            await self._page.click("button[type='submit'], input[type='submit']")

            # Chờ redirect sau login
            await asyncio.sleep(3)
            return True

        except Exception as e:
            from state import log
            log(f"Login exception: {e}", "LOGIN_ERR", self.target_id)
            return False

    async def scrape_routing(self) -> Optional[list]:
        """
        Truy cập trang monitoring và scrape bảng routing.
        Trả về list of dict: [{cluster, viettel, mobi, vina, other}, ...]
        """
        async with self._lock:
            await self._ensure_browser()
            cfg = self.target

            try:
                # Đi tới trang monitoring
                await self._page.goto(cfg["monitoring_url"], wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(2)

                ptype = await self._detect_page_type()

                # Session hết → login lại
                if ptype == "login":
                    from state import log
                    log("Session hết, đang login lại...", "LOGIN", self.target_id)
                    ok = await self._do_login()
                    if not ok:
                        return None
                    await self._page.goto(cfg["monitoring_url"], wait_until="domcontentloaded", timeout=40000)
                    await asyncio.sleep(2)

                # Chờ bảng routing xuất hiện
                try:
                    await self._page.wait_for_selector("table", timeout=15000)
                except PwTimeout:
                    return None

                rows = await self._parse_routing_table()
                return rows

            except Exception as e:
                from state import log
                log(f"scrape_routing exception: {e}", "SCRAPE_ERR", self.target_id)
                return None

    async def _parse_routing_table(self) -> list:
        """Parse bảng routing HTML → list dicts."""
        rows = []
        try:
            tables = await self._page.query_selector_all("table")
            for table in tables:
                caption = await table.query_selector("caption h5 a")
                if not caption:
                    continue
                cluster = (await caption.inner_text()).strip()
                
                tds = await table.query_selector_all("tbody tr td")
                if len(tds) < 4:
                    continue
                
                texts = []
                for cell in tds:
                    t = (await cell.inner_text()).strip()
                    texts.append(t)
                
                def to_int(s: str) -> int:
                    try:
                        return int(re.sub(r"[^\d]", "", s))
                    except Exception:
                        return 0

                viettel = to_int(texts[0]) if len(texts) > 0 else 0
                mobi    = to_int(texts[1]) if len(texts) > 1 else 0
                vina    = to_int(texts[2]) if len(texts) > 2 else 0
                other   = to_int(texts[3]) if len(texts) > 3 else 0

                rows.append({
                    "cluster": cluster,
                    "viettel": viettel,
                    "mobi":    mobi,
                    "vina":    vina,
                    "other":   other,
                })
        except Exception as e:
            from state import log
            log(f"parse_routing_table: {e}", "PARSE_ERR", self.target_id)

        return rows

    async def fetch_billing(self, day: str) -> dict:
        """
        Lấy báo cáo billing từ portal cho ngày `day` (format YYYY-MM-DD).
        Trả về: {"rows": [{name, duration}, ...]} hoặc {"error": "..."}
        """
        async with self._lock:
            await self._ensure_browser()
            cfg = self.target

            try:
                origin = cfg["monitoring_url"].split("/routing")[0]
                billing_url = f"{origin}/billing/report/customerDetail"

                # GET danh sách account
                resp = await self._page.goto(billing_url, wait_until="domcontentloaded", timeout=30000)

                ptype = await self._detect_page_type()
                if ptype == "login":
                    ok = await self._do_login()
                    if not ok:
                        return {"error": "session_expired"}
                    resp = await self._page.goto(billing_url, wait_until="domcontentloaded", timeout=30000)

                # Đọc danh sách account từ DOM (không dùng regex)
                accounts = await self._page.evaluate("""
                    () => {
                        const sel = document.querySelector('select[name="account_id[]"]');
                        if (!sel) return [];
                        return Array.from(sel.querySelectorAll('option'))
                            .filter(o => /^\\d+$/.test(o.value))
                            .map(o => ({id: o.value, name: (o.textContent || '').trim()}));
                    }
                """)
                if not accounts:
                    return {"error": "no_accounts"}

                results = []
                for acc in accounts:
                    dur = await self._fetch_one_account(billing_url, acc["id"], day)
                    if dur is not None and dur >= 0:
                        results.append({"name": acc["name"], "duration": dur})
                    await asyncio.sleep(0.3)

                results.sort(key=lambda x: x["duration"], reverse=True)
                return {"rows": results}

            except Exception as e:
                return {"error": str(e)}

    async def _fetch_one_account(self, billing_url: str, account_id: str, day: str) -> Optional[float]:
        try:
            # Truyền tham số (an toàn injection) + fetch & parse ngay trong browser
            # để dùng chung session cookie và bỏ regex phía Python.
            duration = await self._page.evaluate(
                """
                async ({billingUrl, accountId, day}) => {
                    const form = new FormData();
                    form.append('from_day', day);
                    form.append('to_day', day);
                    form.append('account_id[]', accountId);
                    form.append('call_scope', '');
                    form.append('caller_number', '');
                    form.append('dest_number', '');
                    const resp = await fetch(billingUrl, {method: 'POST', body: form, credentials: 'include'});
                    if (!resp.ok) return null;
                    const html = await resp.text();
                    if (html.includes('id="username_softswitch"')) return null;
                    const doc = new DOMParser().parseFromString(html, 'text/html');
                    const nums = Array.from(doc.querySelectorAll('[class*="text-size"]'))
                        .map(c => (c.textContent || '').trim())
                        .filter(t => /^[\\d.,]+$/.test(t));
                    if (nums.length >= 2) {
                        const v = parseFloat(nums[1].replace(/,/g, ''));
                        return isNaN(v) ? 0 : v;
                    }
                    return null;
                }
                """,
                {"billingUrl": billing_url, "accountId": account_id, "day": day},
            )
            return duration
        except Exception as e:
            from state import log
            log(f"_fetch_one_account: {e}", "BILLING_ERR", self.target_id)
            return None

    async def fetch_call_history(self, phone: str) -> dict:
        """
        Lấy lịch sử cuộc gọi từ portal cho số `phone`.
        Trả về: {"rows": [{account, startTime, billsec, cause}, ...]} hoặc {"error": "..."}
        """
        async with self._lock:
            await self._ensure_browser()
            cfg = self.target

            try:
                origin = cfg["monitoring_url"].split("/routing")[0]
                filter_url = f"{origin}/calldata/filter"

                await self._page.goto(cfg["monitoring_url"], wait_until="domcontentloaded", timeout=30000)
                ptype = await self._detect_page_type()
                if ptype == "login":
                    ok = await self._do_login()
                    if not ok:
                        return {"error": "session_expired"}
                    await self._page.goto(cfg["monitoring_url"], wait_until="domcontentloaded", timeout=30000)

                # Truyền tham số (an toàn injection) + fetch & parse trong browser
                result = await self._page.evaluate(
                    """
                    async ({filterUrl, phone}) => {
                        const form = new FormData();
                        form.append('account_id', '0');
                        form.append('caller_number', '');
                        form.append('dest_number', phone);
                        form.append('outbound', '');
                        form.append('from_day', '');
                        form.append('to_day', '');
                        form.append('hangupcause', '0');
                        form.append('calltype', '');
                        form.append('telco', '0');
                        const resp = await fetch(filterUrl, {method: 'POST', body: form, credentials: 'include'});
                        if (!resp.ok) return {error: 'fetch_failed'};
                        const html = await resp.text();
                        if (html.includes('id="username_softswitch"')) return {error: 'session_expired'};
                        const doc = new DOMParser().parseFromString(html, 'text/html');
                        const table = doc.querySelector('table.table-striped');
                        if (!table) return {rows: []};
                        const out = [];
                        for (const tr of table.querySelectorAll('tbody tr')) {
                            const tds = Array.from(tr.querySelectorAll('td')).map(td => (td.textContent || '').trim());
                            if (tds.length >= 14) {
                                out.push({account: tds[1], startTime: tds[6], billsec: tds[10], cause: tds[13]});
                            }
                        }
                        return {rows: out};
                    }
                    """,
                    {"filterUrl": filter_url, "phone": phone},
                )
                if not result:
                    return {"error": "fetch_failed"}
                if result.get("error"):
                    return {"error": result["error"]}
                return {"rows": result.get("rows", [])}
            except Exception as e:
                return {"error": str(e)}

    async def close(self):
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass  # cố ý: dọn dẹp best-effort, lỗi đóng browser không quan trọng
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._logged_in = False


# ===== SESSION REGISTRY =====
# Mỗi target_id → 1 MPTSession singleton

_sessions: dict[str, MPTSession] = {}

def get_session(target: dict) -> MPTSession:
    tid = target["id"]
    if tid not in _sessions:
        _sessions[tid] = MPTSession(target)
    return _sessions[tid]

async def close_all_sessions():
    for s in _sessions.values():
        await s.close()
    _sessions.clear()


# ===== ALERT HELPERS =====

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

def build_drop_alert(alert_name: str, row: dict, details: str, drop_threshold: int) -> str:
    now_str = datetime.now().strftime("%H:%M")
    text = f"◆ *{_md_escape(alert_name)} · Giảm sâu >{drop_threshold}%* · {now_str}\n"
    text += "```\n"
    text += _code_safe(row["cluster"]) + "  " + _code_safe(details) + "\n"
    text += "```"
    return text
