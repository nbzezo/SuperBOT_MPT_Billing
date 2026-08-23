"""
modules/meeting.py — Logic tóm tắt cuộc họp bằng Gemini AI
Hỗ trợ: text / audio / video, chuyển mã ffmpeg, chọn model & kiểu biên bản & ngôn ngữ,
transcript đầy đủ (tuỳ chọn), chunk audio dài, retry khi lỗi tạm thời, callback tiến trình.
Note lưu bền trong notes/; nguồn gốc (text/transcript) lưu trong notes/.sources/ để tóm tắt lại.
"""
import os
import sys
import glob
import asyncio
import time
import shutil
import subprocess
from datetime import datetime
from typing import Optional, Callable, Awaitable
from google import genai

# log() từ state — fallback ra print nếu import lỗi (vd. chạy test độc lập)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from state import log
except Exception:  # pragma: no cover
    def log(msg, code="", target_id=""):
        print(f"[{code}] {msg}")


TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")
NOTES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "notes")
SOURCES_DIR = os.path.join(NOTES_DIR, ".sources")  # text/transcript nguồn để re-summarize
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(NOTES_DIR, exist_ok=True)
os.makedirs(SOURCES_DIR, exist_ok=True)

# ===== HẰNG SỐ CẤU HÌNH (gom một chỗ, tránh magic value rải rác) =====
DEFAULT_MODEL = "gemini-2.5-flash"
ALLOWED_MODELS = {"gemini-2.5-flash", "gemini-2.5-pro"}
MODEL_ALIASES = {"flash": "gemini-2.5-flash", "pro": "gemini-2.5-pro"}

DEFAULT_STYLE = "structured"
DEFAULT_LANGUAGE = "vi"
LANGUAGES = {"vi": "tiếng Việt", "en": "English", "ja": "日本語", "ko": "한국어"}

FILE_PROCESS_TIMEOUT = 300       # giây chờ 1 file Gemini chuyển sang ACTIVE
GEMINI_MAX_RETRIES = 3           # số lần thử lại khi lỗi tạm thời (429/503/timeout...)
LONG_AUDIO_THRESHOLD = 25 * 60   # > 25 phút thì cắt khúc thay vì xử lý 1 file
CHUNK_SECONDS = 18 * 60          # độ dài mỗi khúc khi cắt audio dài
FFMPEG_TIMEOUT = 1800            # giây cho 1 lệnh ffmpeg

# Lỗi tạm thời (đáng retry) — so khớp lỏng theo chuỗi lỗi
_RETRYABLE_HINTS = ("429", "500", "503", "rate", "quota", "deadline",
                    "timeout", "timed out", "unavailable", "overloaded", "internal")

# Hướng dẫn định dạng theo kiểu biên bản (ghép vào sau prompt người dùng)
STYLE_GUIDE = {
    "structured": ("Trình bày biên bản markdown theo các mục: **Tóm tắt chung**, "
                   "**Quyết định**, **Action items** (bảng: Việc | Người phụ trách | Hạn), "
                   "**Vấn đề còn mở**."),
    "short": "Tóm tắt thật ngắn gọn bằng gạch đầu dòng các ý chính và quyết định quan trọng nhất.",
    "detailed": ("Biên bản chi tiết theo trình tự diễn biến, kèm mục **Quyết định** và "
                 "**Action items** (bảng: Việc | Người phụ trách | Hạn)."),
    "action": ("CHỈ trích xuất Action Items dưới dạng bảng markdown: Việc | Người phụ trách | Hạn chót. "
               "Không viết phần khác."),
}

ProgressCb = Optional[Callable[[str], Awaitable[None]]]


# ===== CHUẨN HÓA THAM SỐ =====

def normalize_model(model: Optional[str]) -> str:
    """Nhận model đầy đủ hoặc alias (flash/pro); trả model hợp lệ, mặc định flash."""
    if not model:
        return DEFAULT_MODEL
    model = MODEL_ALIASES.get(model.lower(), model)
    return model if model in ALLOWED_MODELS else DEFAULT_MODEL


def normalize_language(language: Optional[str]) -> str:
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def resolve_api_key(cfg_key: str = "") -> str:
    """Ưu tiên key trong config; fallback biến môi trường GEMINI_API_KEY."""
    return (cfg_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()


def _compose_prompt(base_prompt: str, style: str, language: str = DEFAULT_LANGUAGE) -> str:
    base = (base_prompt or "").strip() or "Bạn là trợ lý tóm tắt cuộc họp chuyên nghiệp."
    parts = [base]
    guide = STYLE_GUIDE.get(style or DEFAULT_STYLE)
    if guide:
        parts.append(f"YÊU CẦU ĐỊNH DẠNG:\n{guide}")
    lang_name = LANGUAGES.get(normalize_language(language), LANGUAGES[DEFAULT_LANGUAGE])
    parts.append(f"NGÔN NGỮ ĐẦU RA: Viết toàn bộ biên bản bằng {lang_name}.")
    return "\n\n".join(parts)


# ===== LƯU FILE =====

def save_as_md(title: str, content: str, subtitle: str = "") -> tuple:
    """Lưu nội dung ra file .md bền trong notes/. Trả về (filepath, filename)."""
    filename = f"{title}.md"
    filepath = os.path.join(NOTES_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"_Được tạo lúc: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
        if subtitle:
            f.write(f" · _{subtitle}_")
        f.write("\n\n---\n\n")
        f.write(content or "")
    return filepath, filename


def save_source(note_filename: str, text: str):
    """Lưu văn bản nguồn (text gốc / transcript) để có thể tóm tắt lại với style khác,
    không cần upload lại file audio. Bỏ qua nếu không có nội dung."""
    if not note_filename or not (text or "").strip():
        return
    try:
        with open(os.path.join(SOURCES_DIR, note_filename + ".txt"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        log(f"Không lưu được source cho {note_filename}: {e}", "MEETING")


def read_source(note_filename: str) -> Optional[str]:
    """Đọc văn bản nguồn đã lưu cho note. None nếu không có."""
    p = os.path.join(SOURCES_DIR, os.path.basename(note_filename or "") + ".txt")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None
    return None


async def _emit(cb: ProgressCb, stage: str):
    if cb:
        try:
            await cb(stage)
        except Exception:
            pass


def _empty_result(error=None):
    return {"content": None, "filename": None, "filepath": None,
            "transcript_path": None, "transcript_name": None, "usage": None, "error": error}


# ===== GEMINI: GỌI CÓ RETRY + ĐO TOKEN =====

def _extract_usage(response) -> Optional[dict]:
    u = getattr(response, "usage_metadata", None)
    if not u:
        return None
    return {"input": getattr(u, "prompt_token_count", None),
            "output": getattr(u, "candidates_token_count", None),
            "total": getattr(u, "total_token_count", None)}


async def _generate(client, model: str, contents, label: str = "") -> object:
    """generate_content có retry/backoff cho lỗi tạm thời. Ném lỗi nếu hết lần thử."""
    last_err = None
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            return await asyncio.to_thread(
                client.models.generate_content, model=model, contents=contents)
        except Exception as e:
            last_err = e
            transient = any(h in str(e).lower() for h in _RETRYABLE_HINTS)
            if attempt < GEMINI_MAX_RETRIES - 1 and transient:
                wait = 2 ** attempt  # 1s, 2s, 4s...
                log(f"Gemini lỗi tạm thời [{label}] lần {attempt + 1}, thử lại sau {wait}s: {e}", "MEETING")
                await asyncio.sleep(wait)
                continue
            raise
    raise last_err  # pragma: no cover


def _log_usage(usage: Optional[dict], label: str):
    if usage and usage.get("total"):
        log(f"Gemini token [{label}]: in={usage.get('input')} out={usage.get('output')} "
            f"total={usage.get('total')}", "MEETING")


# ===== FFMPEG / FFPROBE =====

def _find_bin(name: str) -> Optional[str]:
    p = shutil.which(name)
    if p:
        return p
    cand = os.path.expanduser(f"~/.local/bin/{name}")
    return cand if os.path.exists(cand) else None


def _find_ffmpeg() -> Optional[str]:
    return _find_bin("ffmpeg")


def _transcode_to_mp3(src: str) -> Optional[str]:
    """Chuyển mã audio/video bất kỳ → MP3 16kHz mono (Gemini decode ổn định nhất).
    `-vn` bỏ luồng video nên dùng được cho cả file video. None nếu không có ffmpeg/lỗi."""
    ff = _find_ffmpeg()
    if not ff:
        log("Không tìm thấy ffmpeg — gửi file gốc cho Gemini (định dạng lạ có thể lỗi)", "MEETING")
        return None
    dst = src + ".conv.mp3"
    try:
        r = subprocess.run(
            [ff, "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "libmp3lame", "-q:a", "5", dst],
            capture_output=True, timeout=FFMPEG_TIMEOUT
        )
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
        log(f"ffmpeg transcode thất bại (rc={r.returncode}): "
            f"{(r.stderr or b'')[-300:].decode('utf-8', 'ignore')}", "MEETING")
    except Exception as e:
        log(f"ffmpeg transcode lỗi: {e}", "MEETING")
    return None


def _audio_duration(path: str) -> Optional[float]:
    """Độ dài audio (giây) qua ffprobe. None nếu không xác định được."""
    fp = _find_bin("ffprobe")
    if not fp:
        return None
    try:
        r = subprocess.run(
            [fp, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except Exception:
        return None


def _split_audio(mp3_path: str, chunk_seconds: int) -> list:
    """Cắt mp3 thành các khúc ~chunk_seconds. Trả danh sách path đã sort (rỗng nếu lỗi)."""
    ff = _find_ffmpeg()
    if not ff:
        return []
    pattern = mp3_path + ".seg%03d.mp3"
    try:
        r = subprocess.run(
            [ff, "-y", "-i", mp3_path, "-f", "segment",
             "-segment_time", str(chunk_seconds), "-c", "copy", pattern],
            capture_output=True, timeout=FFMPEG_TIMEOUT)
        if r.returncode == 0:
            return sorted(glob.glob(mp3_path + ".seg*.mp3"))
        log(f"ffmpeg split thất bại (rc={r.returncode})", "MEETING")
    except Exception as e:
        log(f"ffmpeg split lỗi: {e}", "MEETING")
    return []


# ===== GEMINI FILES: UPLOAD + CHỜ ACTIVE =====

async def _upload_wait_active(client, path: str, mime: Optional[str]):
    """Upload 1 file lên Gemini, chờ tới ACTIVE. Ném lỗi nếu FAILED/timeout."""
    cfg = {"mime_type": mime} if mime else None
    uploaded = await asyncio.to_thread(lambda: client.files.upload(file=path, config=cfg))
    start = time.time()
    while True:
        state = getattr(uploaded.state, "name", str(uploaded.state))
        if state == "ACTIVE":
            return uploaded
        if state == "FAILED":
            reason = getattr(uploaded, "error", None) or "không rõ lý do"
            raise RuntimeError(f"Gemini không xử lý được file (FAILED): {reason}")
        if time.time() - start > FILE_PROCESS_TIMEOUT:
            raise TimeoutError(f"Hết {FILE_PROCESS_TIMEOUT}s chờ Gemini xử lý file.")
        await asyncio.sleep(2)
        uploaded = await asyncio.to_thread(client.files.get, name=uploaded.name)


async def _delete_remote(client, name: str):
    try:
        await asyncio.to_thread(client.files.delete, name=name)
    except Exception as e:
        log(f"Không xoá được file Gemini {name}: {e}", "MEETING")


_TRANSCRIBE_PROMPT = ("Ghi lại transcript nguyên văn (verbatim) toàn bộ nội dung audio, "
                      "xuống dòng theo lượt nói, ghi rõ người nói nếu phân biệt được.")


# ===== SUMMARIZE TEXT =====

async def summarize_text(text: str, prompt: str, api_key: str,
                         model: str = DEFAULT_MODEL, style: str = DEFAULT_STYLE,
                         language: str = DEFAULT_LANGUAGE,
                         title_prefix: str = "Meeting_Note", subtitle_src: str = "text") -> dict:
    try:
        model = normalize_model(model)
        client = genai.Client(api_key=api_key)
        final_prompt = _compose_prompt(prompt, style, language)
        response = await _generate(
            client, model, f"{final_prompt}\n\nNội dung biên bản:\n{text}", label="summarize-text")
        content = response.text
        usage = _extract_usage(response)
        _log_usage(usage, "summarize-text")
        title = f"{title_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        filepath, filename = save_as_md(title, content, subtitle=f"{subtitle_src} · {model}")
        save_source(filename, text)  # giữ nguồn để re-summarize
        return {"content": content, "filename": filename, "filepath": filepath,
                "transcript_path": None, "transcript_name": None, "usage": usage, "error": None}
    except Exception as e:
        log(f"summarize_text lỗi: {e}", "MEETING")
        return _empty_result(str(e))


# ===== SUMMARIZE AUDIO / VIDEO =====

async def summarize_audio(audio_path: str, prompt: str, api_key: str,
                          model: str = DEFAULT_MODEL, style: str = DEFAULT_STYLE,
                          language: str = DEFAULT_LANGUAGE,
                          include_transcript: bool = False,
                          on_progress: ProgressCb = None) -> dict:
    """Tóm tắt audio/video: transcode → (nếu dài) cắt khúc → upload → chờ ACTIVE → tóm tắt."""
    conv_path = None
    segments = []
    try:
        model = normalize_model(model)
        client = genai.Client(api_key=api_key)

        await _emit(on_progress, "transcode")
        conv_path = await asyncio.to_thread(_transcode_to_mp3, audio_path)
        base_path = conv_path or audio_path
        mime = "audio/mpeg" if conv_path else None

        # Audio dài & cắt được → đi nhánh chunk (transcribe từng khúc rồi tóm tắt text)
        duration = _audio_duration(base_path) if conv_path else None
        if duration and duration > LONG_AUDIO_THRESHOLD:
            segments = await asyncio.to_thread(_split_audio, base_path, CHUNK_SECONDS)

        if len(segments) > 1:
            log(f"Audio {duration/60:.0f} phút → cắt {len(segments)} khúc", "MEETING")
            return await _summarize_chunked(
                client, segments, prompt, model, style, language,
                include_transcript, on_progress)

        # Nhánh thường: 1 file
        await _emit(on_progress, "upload")
        uploaded = await _upload_wait_active(client, base_path, mime)

        await _emit(on_progress, "summarize")
        final_prompt = _compose_prompt(prompt, style, language)
        response = await _generate(client, model, [final_prompt, uploaded], label="summarize-audio")
        content = response.text
        usage = _extract_usage(response)
        _log_usage(usage, "summarize-audio")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath, filename = save_as_md(f"Meeting_Note_{ts}", content, subtitle=f"audio · {model}")

        transcript_path = transcript_name = None
        if include_transcript:
            await _emit(on_progress, "transcript")
            try:
                tr = await _generate(client, model, [_TRANSCRIBE_PROMPT, uploaded], label="transcript")
                transcript_path, transcript_name = save_as_md(
                    f"Meeting_Transcript_{ts}", tr.text, subtitle=f"transcript · {model}")
                save_source(filename, tr.text)  # transcript làm nguồn re-summarize
            except Exception as e:
                log(f"Tạo transcript lỗi (bỏ qua, vẫn giữ tóm tắt): {e}", "MEETING")

        await _delete_remote(client, uploaded.name)
        return {"content": content, "filename": filename, "filepath": filepath,
                "transcript_path": transcript_path, "transcript_name": transcript_name,
                "usage": usage, "error": None}

    except Exception as e:
        log(f"summarize_audio lỗi: {e}", "MEETING")
        return _empty_result(str(e))
    finally:
        for p in [conv_path, *segments]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


async def _summarize_chunked(client, segments, prompt, model, style, language,
                             include_transcript, on_progress) -> dict:
    """Audio dài: transcribe từng khúc → ghép transcript → tóm tắt trên text.
    Tránh giới hạn xử lý 1-file của Gemini và timeout cho file rất dài."""
    transcripts = []
    for i, seg in enumerate(segments, 1):
        await _emit(on_progress, "upload")
        uploaded = await _upload_wait_active(client, seg, "audio/mpeg")
        await _emit(on_progress, "transcript")
        log(f"Transcribe khúc {i}/{len(segments)}", "MEETING")
        try:
            tr = await _generate(client, model,
                                 [f"(Phần {i}/{len(segments)}) {_TRANSCRIBE_PROMPT}", uploaded],
                                 label=f"chunk-{i}")
            transcripts.append((tr.text or "").strip())
        finally:
            await _delete_remote(client, uploaded.name)

    full_transcript = "\n\n".join(t for t in transcripts if t)
    if not full_transcript:
        return _empty_result("Không trích xuất được nội dung từ audio dài.")

    await _emit(on_progress, "summarize")
    final_prompt = _compose_prompt(prompt, style, language)
    response = await _generate(
        client, model,
        f"{final_prompt}\n\nĐây là transcript (đã ghép từ nhiều phần) của cuộc họp:\n{full_transcript}",
        label="summarize-chunked")
    content = response.text
    usage = _extract_usage(response)
    _log_usage(usage, "summarize-chunked")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath, filename = save_as_md(
        f"Meeting_Note_{ts}", content, subtitle=f"audio dài · {len(segments)} khúc · {model}")
    save_source(filename, full_transcript)

    transcript_path = transcript_name = None
    if include_transcript:
        transcript_path, transcript_name = save_as_md(
            f"Meeting_Transcript_{ts}", full_transcript, subtitle=f"transcript · {model}")

    return {"content": content, "filename": filename, "filepath": filepath,
            "transcript_path": transcript_path, "transcript_name": transcript_name,
            "usage": usage, "error": None}


# ===== TÓM TẮT LẠI TỪ NGUỒN ĐÃ LƯU =====

async def resummarize(note_filename: str, prompt: str, api_key: str,
                      model: str = DEFAULT_MODEL, style: str = DEFAULT_STYLE,
                      language: str = DEFAULT_LANGUAGE) -> dict:
    """Tạo biên bản mới từ văn bản nguồn đã lưu của một note cũ (không tốn API upload)."""
    source = read_source(note_filename)
    if not source:
        return _empty_result("Không có dữ liệu nguồn để tóm tắt lại (note cũ chưa lưu transcript/text).")
    return await summarize_text(source, prompt, api_key, model=model, style=style,
                                language=language, subtitle_src="tóm tắt lại")


# ===== TEST CONNECTION =====

async def test_gemini_connection(api_key: str, model: str = DEFAULT_MODEL) -> dict:
    try:
        model = normalize_model(model)
        client = genai.Client(api_key=api_key)
        await asyncio.to_thread(client.models.generate_content, model=model, contents="Hello")
        return {"ok": True, "msg": f"Kết nối {model} OK ✓"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ===== LIỆT KÊ / XÓA FILE =====

def list_meeting_files(limit: int = 20) -> list:
    """Liệt kê file .md trong notes/ (fallback nếu DB lịch sử trống)."""
    if not os.path.exists(NOTES_DIR):
        return []
    files = [f for f in os.listdir(NOTES_DIR) if f.endswith(".md") and f.startswith("Meeting_")]
    files.sort(reverse=True)
    return files[:limit]


def delete_note_files(filename: str) -> bool:
    """Xoá 1 note (.md) trong notes/ + file nguồn kèm theo. True nếu đã xoá .md."""
    safe = os.path.basename(filename or "")
    md = os.path.join(NOTES_DIR, safe)
    removed = False
    if os.path.exists(md):
        try:
            os.remove(md)
            removed = True
        except Exception as e:
            log(f"Không xoá được {safe}: {e}", "MEETING")
    src = os.path.join(SOURCES_DIR, safe + ".txt")
    if os.path.exists(src):
        try:
            os.remove(src)
        except Exception:
            pass
    return removed
