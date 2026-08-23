"""
modules/nospam.py — Logic tra cứu số spam qua nospam.vncert.vn
Dùng HTTP thuần (httpx) — thay thế Playwright/Chrome.

API (giải mã từ /js/searchDNC.js):
  POST /search-dnc  JSON {"phone": "0916..."}
  → {"code": "00", "data": "ACTIVE"|"UNACTIVE"}
"""
import re
from typing import Optional

import httpx

NOSPAM_URL = "https://nospam.vncert.vn/"
SEARCH_URL = NOSPAM_URL + "search-dnc"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MSG_ACTIVE = ("Số điện thoại của quý khách đã nằm trong danh sách "
              "không quảng cáo DNC. Để hủy đăng ký soạn: HUY DNC gửi 5656. Trân trọng!")
MSG_UNACTIVE = ("Số điện thoại của quý khách không nằm trong danh sách "
                "không quảng cáo DNC. Để đăng ký soạn: DK DNC gửi 5656. Trân trọng!")


def normalize_vn_phone(raw: str) -> Optional[str]:
    """
    Chuẩn hoá về dạng 10 số bắt đầu bằng 0 (VD: 0916705392)
    Chấp nhận: 84-..., +84..., 84..., 0..., hoặc 9 chữ số (thiếu 0 đầu).
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None

    if digits.startswith("84"):
        rest = digits[2:]
        if len(rest) == 9:
            return "0" + rest
        if len(rest) == 10 and rest.startswith("0"):
            return rest

    if len(digits) == 9:
        return "0" + digits

    if len(digits) == 10 and digits.startswith("0"):
        return digits

    if len(digits) > 10:
        last10 = digits[-10:]
        if last10.startswith("0"):
            return last10

    return None


async def query_nospam(raw_phone: str) -> dict:
    """
    Tra cứu số điện thoại trên nospam.vncert.vn (HTTP thuần, không browser).
    Trả về: {"phone": "0916...", "result": "nội dung", "error": None}
    """
    phone = normalize_vn_phone(raw_phone)
    if not phone:
        return {
            "phone": raw_phone,
            "result": None,
            "error": f"Số không hợp lệ: '{raw_phone}'. Vui lòng nhập định dạng VN (10 số, bắt đầu bằng 0 hoặc 84)"
        }

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=30, headers={"User-Agent": UA}
        ) as client:
            # GET trang chủ để nhận cookie cần thiết (nếu có)
            await client.get(NOSPAM_URL)
            r = await client.post(
                SEARCH_URL,
                json={"phone": phone},
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()

        code = data.get("code")
        payload = data.get("data")

        if code == "00":
            result = MSG_ACTIVE if payload == "ACTIVE" else MSG_UNACTIVE
            return {"phone": phone, "result": result, "error": None}

        # code khác 00 → server trả thông báo trong data
        return {
            "phone": phone,
            "result": None,
            "error": payload or f"Tra cứu thất bại (code={code})",
        }

    except httpx.HTTPStatusError as e:
        return {"phone": phone, "result": None, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"phone": phone, "result": None, "error": str(e)}
