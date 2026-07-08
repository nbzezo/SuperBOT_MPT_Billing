"""
modules/nospam.py — Logic tra cứu số spam qua nospam.vncert.vn
Tách từ Nospam/bot_nospam.py (không có Telegram bot ở đây)
"""
import re
import asyncio
import os
import sys
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils import find_chrome  # noqa: E402

NOSPAM_URL = "https://nospam.vncert.vn/"

CHROME_PATH = find_chrome()

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
    Tra cứu số điện thoại trên nospam.vncert.vn.
    Trả về: {"phone": "0916...", "result": "nội dung", "error": None}
    """
    phone = normalize_vn_phone(raw_phone)
    if not phone:
        return {
            "phone": raw_phone,
            "result": None,
            "error": f"Số không hợp lệ: '{raw_phone}'. Vui lòng nhập định dạng VN (10 số, bắt đầu bằng 0 hoặc 84)"
        }

    async with async_playwright() as p:
        launch_opts = {"headless": True}
        if CHROME_PATH:
            launch_opts["executable_path"] = CHROME_PATH
        browser = await p.chromium.launch(**launch_opts)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(NOSPAM_URL, wait_until="domcontentloaded", timeout=30000)
            await page.fill("#searchPhoneNumber", phone)
            await page.click("#btnSearchPhoneNumber")

            try:
                await page.wait_for_selector("#searchContent", timeout=15000)
            except PlaywrightTimeoutError:
                pass

            # Chờ text có nội dung (tối đa 15s)
            for _ in range(30):
                text = (await page.inner_text("#searchContent")).strip()
                if text:
                    try:
                        await page.click("#confirmSearchBtn", timeout=1000)
                    except Exception:
                        pass  # cố ý: nút xác nhận có thể không xuất hiện
                    return {"phone": phone, "result": text, "error": None}
                await asyncio.sleep(0.5)

            return {
                "phone": phone,
                "result": None,
                "error": "Timeout — không lấy được kết quả. Vui lòng thử lại."
            }

        except Exception as e:
            return {"phone": phone, "result": None, "error": str(e)}
        finally:
            await context.close()
            await browser.close()
