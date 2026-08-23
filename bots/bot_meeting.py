"""
bots/bot_meeting.py — Telegram Bot Meeting Note AI
Lệnh: /help /style /model /transcript. Nhận text/voice/audio/video/document.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from modules.meeting import (
    summarize_text, summarize_audio, STYLE_GUIDE, normalize_model, normalize_language,
    resolve_api_key, MODEL_ALIASES, DEFAULT_STYLE, LANGUAGES,
)
from utils import load_config, update_config_field
from state import add_meeting_history

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

TELEGRAM_DL_LIMIT = 19 * 1024 * 1024  # bot getFile ~20MB

STAGE_TEXT = {
    "transcode": "🎛 Đang chuyển mã âm thanh...",
    "upload": "☁️ Đang tải lên Gemini...",
    "processing": "⏳ Gemini đang xử lý file...",
    "summarize": "🧠 Đang tóm tắt nội dung...",
    "transcript": "📝 Đang tạo transcript đầy đủ...",
}


# ===== HELPERS =====

def _meeting_cfg() -> dict:
    return load_config().get("meeting", {})


def _save_meeting_field(key: str, value):
    update_config_field("meeting", key, value)


def _is_allowed(update: Update) -> bool:
    allowed = _meeting_cfg().get("allowed_chats") or []
    if not allowed:
        return True
    cid = str(update.effective_chat.id)
    return cid in [str(x).strip() for x in allowed]


async def _send_chunks(update: Update, text: str, prefix: str = ""):
    """Gửi text dài thành nhiều tin ≤4096 (plain text, tránh lỗi parse markdown)."""
    body = (prefix + (text or "")).strip()
    if not body:
        return
    for i in range(0, len(body), 3800):
        await update.message.reply_text(body[i:i + 3800])


# ===== COMMANDS =====

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎙 *Meeting Note Bot*\nGửi văn bản, ghi âm, audio hoặc video — tôi sẽ tóm tắt thành biên bản.\n"
        "Gõ /help để xem tuỳ chọn.", parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _meeting_cfg()
    await update.message.reply_text(
        "🎙 *Meeting Note Bot — Hướng dẫn*\n\n"
        "Gửi *text / voice / audio / video / file* → nhận biên bản tóm tắt.\n\n"
        "*/style* `structured|short|detailed|action` — kiểu biên bản\n"
        "*/model* `flash|pro` — chọn model Gemini\n"
        "*/lang* `vi|en|ja|ko` — ngôn ngữ biên bản\n"
        "*/transcript* `on|off` — kèm transcript nguyên văn\n"
        "*/help* — hướng dẫn\n\n"
        f"Hiện tại: kiểu *{cfg.get('summary_style', DEFAULT_STYLE)}* · "
        f"model *{normalize_model(cfg.get('model'))}* · "
        f"ngôn ngữ *{normalize_language(cfg.get('language'))}* · "
        f"transcript *{'on' if cfg.get('include_transcript') else 'off'}*",
        parse_mode="Markdown")


async def cmd_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg not in STYLE_GUIDE:
        await update.message.reply_text(
            f"Kiểu hiện tại: *{_meeting_cfg().get('summary_style','structured')}*\n"
            f"Chọn: {', '.join(STYLE_GUIDE.keys())}\nVD: `/style short`", parse_mode="Markdown")
        return
    _save_meeting_field("summary_style", arg)
    await update.message.reply_text(f"✓ Đã đặt kiểu biên bản: *{arg}*", parse_mode="Markdown")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg not in MODEL_ALIASES:
        cur = normalize_model(_meeting_cfg().get("model"))
        await update.message.reply_text(
            f"Model hiện tại: *{cur}*\nChọn: flash | pro\nVD: `/model pro`", parse_mode="Markdown")
        return
    _save_meeting_field("model", MODEL_ALIASES[arg])
    await update.message.reply_text(f"✓ Đã đặt model: *{MODEL_ALIASES[arg]}*", parse_mode="Markdown")


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg not in LANGUAGES:
        cur = normalize_language(_meeting_cfg().get("language"))
        await update.message.reply_text(
            f"Ngôn ngữ hiện tại: *{cur}*\nChọn: {', '.join(LANGUAGES.keys())}\nVD: `/lang en`",
            parse_mode="Markdown")
        return
    _save_meeting_field("language", arg)
    await update.message.reply_text(
        f"✓ Đã đặt ngôn ngữ biên bản: *{arg}* ({LANGUAGES[arg]})", parse_mode="Markdown")


async def cmd_transcript(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg not in ("on", "off"):
        cur = "on" if _meeting_cfg().get("include_transcript") else "off"
        await update.message.reply_text(
            f"Transcript hiện: *{cur}*\nDùng: `/transcript on` hoặc `/transcript off`", parse_mode="Markdown")
        return
    _save_meeting_field("include_transcript", arg == "on")
    await update.message.reply_text(f"✓ Transcript đầy đủ: *{arg}*", parse_mode="Markdown")


# ===== MAIN HANDLER =====

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return

    cfg = _meeting_cfg()
    gemini_key = resolve_api_key(cfg.get("gemini_api_key", ""))
    prompt = cfg.get("summary_prompt", "")
    model = normalize_model(cfg.get("model"))
    style = cfg.get("summary_style", DEFAULT_STYLE)
    language = normalize_language(cfg.get("language"))
    include_transcript = bool(cfg.get("include_transcript"))

    if not gemini_key:
        await update.message.reply_text("⚠️ Chưa cấu hình Gemini API Key. Vào Dashboard → Tab Cuộc họp.")
        return

    msg = update.message
    media = msg.voice or msg.audio or msg.video or msg.document
    status = await msg.reply_text("📥 Đã nhận! Đang chuẩn bị...")

    async def progress(stage):
        try:
            await status.edit_text(STAGE_TEXT.get(stage, "⏳ Đang xử lý..."))
        except Exception:
            pass

    try:
        if media:
            size = getattr(media, "file_size", 0) or 0
            if size > TELEGRAM_DL_LIMIT:
                await status.edit_text(
                    f"⚠️ File {size/1024/1024:.1f}MB vượt giới hạn tải của Telegram bot (~20MB).\n"
                    "Hãy gửi file ngắn hơn, hoặc dùng nút *Tải file lên* trên Dashboard (không giới hạn).",
                    parse_mode="Markdown")
                return

            ext = (getattr(media, "mime_type", "") or "audio/ogg").split("/")[-1] or "bin"
            raw_path = os.path.join(TEMP_DIR, f"raw_{media.file_id}.{ext}")
            await status.edit_text("📥 Đang tải file...")
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(raw_path)
            try:
                result = await summarize_audio(raw_path, prompt, gemini_key, model=model,
                                               style=style, language=language,
                                               include_transcript=include_transcript,
                                               on_progress=progress)
            finally:
                if os.path.exists(raw_path):
                    os.remove(raw_path)
        elif msg.text:
            await status.edit_text("🧠 Đang tóm tắt...")
            result = await summarize_text(msg.text, prompt, gemini_key, model=model,
                                          style=style, language=language)
        else:
            await status.edit_text("⚠️ Không nhận ra nội dung. Gửi text / ghi âm / audio / video / file.")
            return

        if result.get("error"):
            await status.edit_text(f"❌ Lỗi Gemini AI:\n`{result['error']}`", parse_mode="Markdown")
            return

        # Lưu lịch sử
        try:
            await add_meeting_history(result["filename"])
            if result.get("transcript_name"):
                await add_meeting_history(result["transcript_name"])
        except Exception:
            pass

        # Gửi tóm tắt thẳng trong chat + đính kèm file .md
        await status.edit_text("✅ Xong! Đang gửi kết quả...")
        await _send_chunks(update, result["content"], prefix="📝 *BIÊN BẢN*\n\n".replace("*", ""))
        with open(result["filepath"], "rb") as f:
            await msg.reply_document(document=f, filename=result["filename"],
                                     caption=f"📄 {result['filename']}")
        if result.get("transcript_path"):
            with open(result["transcript_path"], "rb") as f:
                await msg.reply_document(document=f, filename=result["transcript_name"],
                                         caption="🗒 Transcript đầy đủ")
        try:
            await status.delete()
        except Exception:
            pass

    except Exception as e:
        logging.error(f"Meeting bot error: {e}")
        try:
            await status.edit_text(f"❌ Có lỗi xảy ra:\n`{str(e)}`", parse_mode="Markdown")
        except Exception:
            pass


def main() -> None:
    config = load_config()
    token = config.get("meeting", {}).get("bot_token", "")
    if not token:
        print("❌ Chưa cấu hình meeting.bot_token trong config.json")
        return

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("style", cmd_style))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("transcript", cmd_transcript))
    # Nội dung để tóm tắt: mọi tin KHÔNG phải lệnh
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    print("🎙 Meeting Note Bot đang chạy... (Ctrl+C để dừng)")
    app.run_polling()


if __name__ == "__main__":
    main()
