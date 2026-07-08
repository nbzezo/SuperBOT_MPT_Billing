"""
bots/bot_nospam.py — Telegram Bot riêng cho Nospam
Đọc config từ config.json, gọi modules/nospam.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, MessageHandler, ContextTypes, filters

from modules.nospam import query_nospam, normalize_vn_phone
from utils import load_config


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (update.message.text or "").strip()
    phone = normalize_vn_phone(msg)

    if not phone:
        await update.message.reply_text(
            "📞 Chưa nhận ra số hợp lệ.\n"
            "Ví dụ: <b>84-916705392</b> | <b>+84916705392</b> | <b>0916705392</b>",
            parse_mode=ParseMode.HTML
        )
        return

    await update.message.reply_text(
        f"📞 Số đã chuẩn hoá: <b>{phone}</b>\n⏳ Đang tra cứu...",
        parse_mode=ParseMode.HTML
    )

    result = await query_nospam(phone)

    if result["error"]:
        await update.message.reply_text(f"❌ Lỗi: {result['error']}")
        return

    await update.message.reply_text(
        f"✅ Kết quả cho <b>{phone}</b>:\n\n{result['result']}",
        parse_mode=ParseMode.HTML
    )


def main() -> None:
    config = load_config()
    token = config.get("nospam", {}).get("bot_token", "")
    if not token:
        print("❌ Chưa cấu hình nospam.bot_token trong config.json")
        return

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"🤖 Nospam Bot đang chạy... (Ctrl+C để dừng)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
