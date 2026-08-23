"""
modules/mpt_http.py — Truy cập portal MPT bằng HTTP thuần (httpx)
Thay thế Playwright/Chrome: cùng interface với MPTSession (mpt_routing.py)
nhưng không cần browser → RAM giảm từ ~4.5GB xuống ~100MB.

Phát hiện từ phân tích:
- Portal là CodeIgniter: cookie `ci_session`, không CSRF token
- Login: POST {login_url}/checkAuthen với username_softswitch/password_softswitch
- Dữ liệu là HTML server-rendered, parse trực tiếp bằng regex
"""
import asyncio
import re
from typing import Optional

import httpx

# User-Agent cố định giống Chrome — CI session có thể kiểm tra user_agent
CHROME_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

LOGIN_MARKER = "username_softswitch"


class MPTHTTPSession:
    """1 session HTTP (cookie jar) cho 1 target — thay thế MPTSession."""

    def __init__(self, target: dict):
        self.target = target
        self.target_id: str = target["id"]
        self._client: Optional[httpx.AsyncClient] = None
        self._logged_in = False
        self._lock = asyncio.Lock()

    # ===== INTERNAL =====

    def _ensure_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=False,
                follow_redirects=True,
                timeout=40,
                headers={"User-Agent": CHROME_UA},
            )
            self._logged_in = False

    @staticmethod
    def _is_login_page(html: str) -> bool:
        return LOGIN_MARKER in html

    async def _do_login(self) -> bool:
        """Login vào portal. Trả về True nếu thành công."""
        cfg = self.target
        try:
            self._ensure_client()
            # GET trang login để nhận cookie ci_session
            await self._client.get(cfg["login_url"])
            # POST checkAuthen (form action của trang login)
            r = await self._client.post(
                cfg["login_url"] + "/checkAuthen",
                data={
                    "username_softswitch": cfg["username"],
                    "password_softswitch": cfg["password"],
                },
            )
            if self._is_login_page(r.text):
                return False  # vẫn ở trang login → sai mật khẩu / bị chặn
            self._logged_in = True
            return True
        except Exception as e:
            from state import log
            log(f"HTTP login exception: {e}", "LOGIN_ERR", self.target_id)
            return False

    async def _get_html(self, url: str) -> Optional[str]:
        """GET url; nếu session hết → login lại và thử 1 lần. None nếu thất bại."""
        self._ensure_client()
        r = await self._client.get(url)
        if self._is_login_page(r.text):
            self._logged_in = False
            if not await self._do_login():
                return None
            r = await self._client.get(url)
            if self._is_login_page(r.text):
                return None
        return r.text

    # ===== PUBLIC API (giống MPTSession) =====

    async def scrape_routing(self) -> Optional[list]:
        """
        Lấy bảng routing monitoring. Trả về list:
        [{cluster, viettel, mobi, vina, other}, ...] hoặc None nếu lỗi.
        """
        async with self._lock:
            self._ensure_client()
            cfg = self.target
            try:
                html = await self._get_html(cfg["monitoring_url"])
                if html is None:
                    return None
                return self._parse_routing_table(html)
            except Exception as e:
                from state import log
                log(f"HTTP scrape exception: {e}", "SCRAPE_ERR", self.target_id)
                return None

    @staticmethod
    def _parse_routing_table(html: str) -> list:
        """Parse HTML → rows. Giữ logic tương đương parser Playwright cũ:
        mỗi table có <caption><h5><a>CLUSTER</a> → lấy các td trong tbody."""
        rows = []
        pattern = re.compile(
            r"<table.*?<caption>\s*<h5>\s*<a[^>]*>(.*?)</a>.*?</caption>(.*?)</table>",
            re.S | re.I,
        )
        for m in pattern.finditer(html):
            # Portal dùng CSS text-transform: uppercase → Playwright trả tên UPPERCASE.
            # Phải .upper() để snapshot khớp với dữ liệu cũ (so sánh cluster giữa 2 lần quét).
            cluster = re.sub(r"<[^>]+>", "", m.group(1)).strip().upper()
            body = m.group(2)
            tds = re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)
            texts = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]

            def to_int(s: str) -> int:
                try:
                    return int(re.sub(r"[^\d]", "", s) or 0)
                except Exception:
                    return 0

            rows.append({
                "cluster": cluster,
                "viettel": to_int(texts[0]) if len(texts) > 0 else 0,
                "mobi":    to_int(texts[1]) if len(texts) > 1 else 0,
                "vina":    to_int(texts[2]) if len(texts) > 2 else 0,
                "other":   to_int(texts[3]) if len(texts) > 3 else 0,
            })
        return rows

    async def fetch_billing(self, day: str) -> dict:
        """
        Báo cáo billing cho ngày `day` (YYYY-MM-DD).
        Trả về {"rows": [{name, duration}, ...]} hoặc {"error": ...}
        """
        async with self._lock:
            self._ensure_client()
            cfg = self.target
            try:
                origin = cfg["monitoring_url"].split("/routing")[0]
                billing_url = f"{origin}/billing/report/customerDetail"

                html = await self._get_html(billing_url)
                if html is None:
                    return {"error": "session_expired"}

                # Parse danh sách account từ <select name="account_id[]">
                accounts = []
                sel = re.search(
                    r'<select[^>]*name="account_id\[\]"[^>]*>(.*?)</select>', html, re.S
                )
                if sel:
                    for opt in re.finditer(
                        r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', sel.group(1), re.S
                    ):
                        name = re.sub(r"<[^>]+>", "", opt.group(2)).strip()
                        accounts.append({"id": opt.group(1), "name": name})
                if not accounts:
                    return {"error": "no_accounts"}

                results = []
                for acc in accounts:
                    dur = await self._fetch_one_account(billing_url, acc["id"], day)
                    if dur is not None and dur >= 0:
                        results.append({"name": acc["name"], "duration": dur})
                    await asyncio.sleep(0.3)  # tránh spam request

                results.sort(key=lambda x: x["duration"], reverse=True)
                return {"rows": results}
            except Exception as e:
                return {"error": str(e)}

    async def _fetch_one_account(self, billing_url: str, account_id: str,
                                 day: str) -> Optional[float]:
        """POST form billing cho 1 account → duration (phút). None nếu lỗi."""
        try:
            r = await self._client.post(
                billing_url,
                data={
                    "from_day": day,
                    "to_day": day,
                    "account_id[]": account_id,
                    "call_scope": "",
                    "caller_number": "",
                    "dest_number": "",
                },
            )
            if not r.is_success:
                return None
            if self._is_login_page(r.text):
                return None
            # Số liệu nằm trong element có class chứa "text-size"
            nums = re.findall(
                r'class="[^"]*text-size[^"]*"[^>]*>\s*([\d.,]+)', r.text
            )
            if len(nums) >= 2:
                try:
                    return float(nums[1].replace(",", ""))
                except ValueError:
                    return 0
            return None
        except Exception as e:
            from state import log
            log(f"HTTP _fetch_one_account: {e}", "BILLING_ERR", self.target_id)
            return None

    async def fetch_call_history(self, phone: str) -> dict:
        """
        Lịch sử cuộc gọi cho `phone`.
        Trả về {"rows": [{account, startTime, billsec, cause}, ...]} hoặc {"error": ...}
        """
        async with self._lock:
            self._ensure_client()
            cfg = self.target
            try:
                origin = cfg["monitoring_url"].split("/routing")[0]
                filter_url = f"{origin}/calldata/filter"

                # Đảm bảo session còn sống (giống code cũ: goto monitoring trước)
                html = await self._get_html(cfg["monitoring_url"])
                if html is None:
                    return {"error": "session_expired"}

                r = await self._client.post(
                    filter_url,
                    data={
                        "account_id": "0",
                        "caller_number": "",
                        "dest_number": phone,
                        "outbound": "",
                        "from_day": "",
                        "to_day": "",
                        "hangupcause": "0",
                        "calltype": "",
                        "telco": "0",
                    },
                )
                if not r.is_success:
                    return {"error": "fetch_failed"}
                if self._is_login_page(r.text):
                    return {"error": "session_expired"}

                rows = []
                table = re.search(
                    r'<table[^>]*class="[^"]*table-striped[^"]*"[^>]*>(.*?)</table>',
                    r.text, re.S,
                )
                if table:
                    for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", table.group(1), re.S):
                        tds = [
                            re.sub(r"<[^>]+>", "", td).strip()
                            for td in re.findall(r"<td[^>]*>(.*?)</td>", tr.group(1), re.S)
                        ]
                        if len(tds) >= 14:
                            rows.append({
                                "account": tds[1],
                                "startTime": tds[6],
                                "billsec": tds[10],
                                "cause": tds[13],
                            })
                return {"rows": rows}
            except Exception as e:
                return {"error": str(e)}

    async def close(self):
        try:
            if self._client is not None:
                await self._client.aclose()
        except Exception:
            pass
        self._client = None
        self._logged_in = False


# ===== SESSION REGISTRY (giống mpt_routing) =====

_sessions: dict[str, MPTHTTPSession] = {}


def get_session(target: dict) -> MPTHTTPSession:
    tid = target["id"]
    if tid not in _sessions:
        _sessions[tid] = MPTHTTPSession(target)
    return _sessions[tid]


async def close_all_sessions():
    for s in _sessions.values():
        await s.close()
    _sessions.clear()
