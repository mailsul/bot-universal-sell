"""premium_emoji.py

Memuat mapping emoji → custom_emoji_id dari emojis.txt dan menyediakan:
  - build_telebot_entities(text, parse_mode) → (plain_text, [MessageEntity])
  - build_http_entities(text, parse_mode)    → (plain_text, [entity_dict])
  - patch_telebot_bot(bot)                   → monkey-patch send_message/reply_to/edit

Cara pakai di file bot:
    from premium_emoji import patch_telebot_bot
    patch_telebot_bot(bot)   # setelah bot = telebot.TeleBot(...)
"""

from __future__ import annotations

import os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import telebot
    _TELEBOT_OK = True
except ImportError:
    _TELEBOT_OK = False

# ── Konstanta ──────────────────────────────────────────────────────────────────

_VARIATION_SELECTORS = {0xFE0F, 0xFE0E}

# ── Muat peta emoji dari emojis.txt ───────────────────────────────────────────

_EMOJI_MAP: dict[str, str] = {}
_MAP_LOADED = False


def _load_map():
    global _EMOJI_MAP, _MAP_LOADED
    if _MAP_LOADED:
        return
    _MAP_LOADED = True

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emojis.txt")
    if not os.path.exists(path):
        path = "emojis.txt"
    if not os.path.exists(path):
        logger.warning("[premium_emoji] emojis.txt tidak ditemukan, fitur premium emoji dinonaktifkan.")
        return

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Format: EMOJI_CHAR(S) NAME: custom_emoji_id
                colon_idx = line.rfind(": ")
                if colon_idx == -1:
                    continue
                cid = line[colon_idx + 2:].strip()
                if not cid.isdigit():
                    continue
                space_idx = line.find(" ")
                if space_idx == -1:
                    continue
                emoji_char = line[:space_idx]
                if not emoji_char:
                    continue
                # Simpan versi asli (dengan variation selector jika ada)
                if emoji_char not in _EMOJI_MAP:
                    _EMOJI_MAP[emoji_char] = cid
                # Simpan juga versi tanpa variation selector
                base = "".join(c for c in emoji_char if ord(c) not in _VARIATION_SELECTORS)
                if base and base != emoji_char and base not in _EMOJI_MAP:
                    _EMOJI_MAP[base] = cid
        logger.info("[premium_emoji] Peta emoji dimuat: %d entri dari emojis.txt", len(_EMOJI_MAP))
    except Exception as e:
        logger.warning("[premium_emoji] Gagal muat emojis.txt: %s", e)


def get_emoji_map() -> dict[str, str]:
    _load_map()
    return _EMOJI_MAP


# ── Helper UTF-16 ──────────────────────────────────────────────────────────────

def _utf16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


# ── Kelas segmen teks ──────────────────────────────────────────────────────────

class _Seg:
    __slots__ = ("text", "etype", "url", "uid")

    def __init__(self, text: str, etype: str = "", url: str = "", uid: int = 0):
        self.text = text
        self.etype = etype  # "" = plain, else entity type string
        self.url = url
        self.uid = uid


# ── Parser Markdown v1 ─────────────────────────────────────────────────────────

def _parse_markdown(src: str) -> list[_Seg]:
    segs: list[_Seg] = []
    i = 0
    n = len(src)
    buf = ""

    def _flush(text: str):
        nonlocal buf
        if text:
            segs.append(_Seg(text))

    while i < n:
        # Escape sequence
        if src[i] == "\\" and i + 1 < n:
            buf += src[i + 1]
            i += 2
            continue

        # Pre block ``` ... ```
        if src[i:i+3] == "```":
            _flush(buf); buf = ""
            end = src.find("```", i + 3)
            if end != -1:
                inner = src[i+3:end]
                # strip optional language tag on first line
                if "\n" in inner:
                    first_nl = inner.index("\n")
                    first = inner[:first_nl].strip()
                    if first and " " not in first and not any(ord(c) > 127 for c in first):
                        inner = inner[first_nl+1:]
                segs.append(_Seg(inner, "pre"))
                i = end + 3
            else:
                buf += "```"
                i += 3
            continue

        # Inline code `...`
        if src[i] == "`":
            _flush(buf); buf = ""
            end = src.find("`", i + 1)
            if end != -1:
                segs.append(_Seg(src[i+1:end], "code"))
                i = end + 1
            else:
                buf += "`"
                i += 1
            continue

        # Link [text](url)
        if src[i] == "[":
            m = re.match(r'\[([^\]]+)\]\(([^)]+)\)', src[i:])
            if m:
                _flush(buf); buf = ""
                ltxt = m.group(1)
                lurl = m.group(2)
                if lurl.startswith("tg://user?id="):
                    try:
                        uid = int(re.search(r'id=(\d+)', lurl).group(1))
                        segs.append(_Seg(ltxt, "text_mention", uid=uid))
                    except Exception:
                        segs.append(_Seg(ltxt, "text_link", url=lurl))
                else:
                    segs.append(_Seg(ltxt, "text_link", url=lurl))
                i += m.end()
                continue

        # Bold *text*
        if src[i] == "*":
            end = src.find("*", i + 1)
            if end != -1:
                _flush(buf); buf = ""
                segs.append(_Seg(src[i+1:end], "bold"))
                i = end + 1
                continue

        # Italic _text_
        if src[i] == "_":
            end = src.find("_", i + 1)
            if end != -1:
                _flush(buf); buf = ""
                segs.append(_Seg(src[i+1:end], "italic"))
                i = end + 1
                continue

        buf += src[i]
        i += 1

    _flush(buf)
    return segs


# ── Parser HTML ────────────────────────────────────────────────────────────────

_HTML_TAG_TO_ETYPE = {
    "b": "bold", "strong": "bold",
    "i": "italic", "em": "italic",
    "u": "underline",
    "s": "strikethrough", "del": "strikethrough",
    "code": "code",
    "pre": "pre",
    "tg-spoiler": "spoiler",
}

_HTML_UNESCAPE = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&nbsp;": " ",
}


def _html_unescape(s: str) -> str:
    for k, v in _HTML_UNESCAPE.items():
        s = s.replace(k, v)
    return s


def _parse_html(src: str) -> list[_Seg]:
    segs: list[_Seg] = []
    pat = re.compile(
        r'<a\s+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        r'|<(b|strong|i|em|u|s|del|code|pre|tg-spoiler)>(.*?)</\3>'
        r'|([^<]+)'
        r'|<[^>]+>',
        re.DOTALL | re.IGNORECASE,
    )
    for m in pat.finditer(src):
        if m.group(1) is not None:
            url = m.group(1)
            inner = _html_unescape(re.sub(r'<[^>]+>', '', m.group(2)))
            segs.append(_Seg(inner, "text_link", url=url))
        elif m.group(3) is not None:
            tag = m.group(3).lower()
            etype = _HTML_TAG_TO_ETYPE.get(tag, "bold")
            inner = _html_unescape(re.sub(r'<[^>]+>', '', m.group(4)))
            segs.append(_Seg(inner, etype))
        elif m.group(5) is not None:
            plain = _html_unescape(m.group(5))
            segs.append(_Seg(plain))
    if not segs:
        segs.append(_Seg(_html_unescape(re.sub(r'<[^>]+>', '', src))))
    return segs


# ── Scan emoji dalam segmen & bangun entity list ───────────────────────────────

def _build_entities_from_segs(segs: list[_Seg]) -> tuple[str, list]:
    """
    Konversi list _Seg ke (plain_text, raw_entities).
    raw_entities: list of (offset_utf16, length_utf16, etype, url, uid, cid)
    Emoji premium di-upgrade ke custom_emoji entity.
    Code/pre tidak di-scan emoji di dalamnya.
    """
    _load_map()
    parts: list[str] = []
    raw: list[tuple] = []
    off = 0  # current UTF-16 offset

    for seg in segs:
        text = seg.text
        seg_start = off

        if seg.etype in ("code", "pre"):
            ln = _utf16len(text)
            raw.append((seg_start, ln, seg.etype, "", 0, ""))
            parts.append(text)
            off += ln
            continue

        # Catat index entity formatting sebelum scan (agar bisa update length setelahnya)
        fmt_idx = -1
        if seg.etype:
            fmt_idx = len(raw)
            raw.append((seg_start, 0, seg.etype, seg.url, seg.uid, ""))

        # Scan karakter satu per satu, coba match emoji terpanjang dulu
        chars = list(text)
        ci = 0
        nc = len(chars)
        while ci < nc:
            matched = False
            # Coba sequence 4,3,2,1 karakter
            for sl in range(min(4, nc - ci), 0, -1):
                candidate = "".join(chars[ci:ci+sl])
                cid = _EMOJI_MAP.get(candidate)
                if cid:
                    clen = _utf16len(candidate)
                    raw.append((off, clen, "custom_emoji", "", 0, cid))
                    parts.append(candidate)
                    off += clen
                    ci += sl
                    matched = True
                    break
            if not matched:
                c = chars[ci]
                # Skip stray variation selector (U+FE0F / U+FE0E) that follows a matched emoji
                if ord(c) in _VARIATION_SELECTORS:
                    ci += 1
                    continue
                clen = _utf16len(c)
                parts.append(c)
                off += clen
                ci += 1

        # Update panjang formatting entity setelah scan selesai
        if fmt_idx >= 0:
            e = raw[fmt_idx]
            raw[fmt_idx] = (e[0], off - seg_start, e[2], e[3], e[4], e[5])

    plain = "".join(parts)
    return plain, raw


# ── Public API: build entity list untuk telebot ────────────────────────────────

def build_telebot_entities(
    text: str, parse_mode: Optional[str] = None
) -> tuple[str, list]:
    """
    Kembalikan (plain_text, list[telebot.types.MessageEntity]).
    Upgrade emoji plain ke premium custom emoji entity.
    Formatting (Markdown/HTML) dikonversi ke entities agar tidak hilang.
    Jika tidak ada emoji yang bisa di-upgrade, kembalikan (text, []) tanpa ubah apapun.
    """
    if not _TELEBOT_OK:
        return text, []
    _load_map()
    if not _EMOJI_MAP:
        return text, []

    pm = (parse_mode or "").lower().strip()
    if pm in ("markdown", "markdownv1", "markdown v1"):
        segs = _parse_markdown(text)
    elif pm == "html":
        segs = _parse_html(text)
    else:
        segs = [_Seg(text)]

    plain, raw = _build_entities_from_segs(segs)

    # Cek apakah ada custom_emoji di antara entity — kalau tidak ada, tidak perlu ganti
    has_premium = any(e[2] == "custom_emoji" for e in raw)
    if not has_premium:
        return text, []

    entities = []
    for offset, length, etype, url, uid, cid in raw:
        if length is None or length <= 0:
            continue
        try:
            kwargs: dict = {"type": etype, "offset": offset, "length": length}
            if url:
                kwargs["url"] = url
            if uid:
                kwargs["user_id"] = uid
            if cid:
                kwargs["custom_emoji_id"] = cid
            entities.append(telebot.types.MessageEntity(**kwargs))
        except Exception as ex:
            logger.debug("[premium_emoji] Skip entity %s: %s", etype, ex)

    return plain, entities


# ── Public API: build entity list untuk HTTP mentah (email_monitor dll) ────────

def build_http_entities(
    text: str, parse_mode: Optional[str] = None
) -> tuple[str, list[dict]]:
    """
    Seperti build_telebot_entities tapi mengembalikan list dict (cocok untuk raw HTTP API call).
    """
    _load_map()
    if not _EMOJI_MAP:
        return text, []

    pm = (parse_mode or "").lower().strip()
    if pm in ("markdown", "markdownv1"):
        segs = _parse_markdown(text)
    elif pm == "html":
        segs = _parse_html(text)
    else:
        segs = [_Seg(text)]

    plain, raw = _build_entities_from_segs(segs)
    has_premium = any(e[2] == "custom_emoji" for e in raw)
    if not has_premium:
        return text, []

    entities = []
    for offset, length, etype, url, uid, cid in raw:
        if length is None or length <= 0:
            continue
        e: dict = {"type": etype, "offset": offset, "length": length}
        if url:
            e["url"] = url
        if uid:
            e["user_id"] = uid
        if cid:
            e["custom_emoji_id"] = cid
        entities.append(e)

    return plain, entities


# ── Monkey-patch telebot bot instance ─────────────────────────────────────────

def patch_telebot_bot(bot) -> None:
    """
    Monkey-patch bot.send_message, bot.reply_to, dan bot.edit_message_text
    agar semua emoji plain otomatis di-upgrade ke versi premium custom emoji.
    Formatting (Markdown/HTML) tetap dipertahankan via entities.

    Panggil sekali setelah bot = telebot.TeleBot(...).
    """
    _load_map()
    if not _EMOJI_MAP:
        logger.warning("[premium_emoji] Peta emoji kosong, patch tidak dipasang.")
        return

    # Ambil default parse_mode dari konstruktor TeleBot (jika ada)
    _default_pm: Optional[str] = getattr(bot, "parse_mode", None)

    # ── send_message ──────────────────────────────────────────────────────────
    _orig_send = bot.send_message

    def _send_message(chat_id, text, parse_mode=None, entities=None, **kwargs):
        if entities:
            # Sudah punya entities — kirim apa adanya
            return _orig_send(chat_id, text, parse_mode=parse_mode,
                              entities=entities, **kwargs)
        effective_pm = parse_mode if parse_mode is not None else _default_pm
        new_text, new_ents = build_telebot_entities(str(text), effective_pm)
        if new_ents:
            # Gunakan parse_mode="" agar default bot (misal "HTML") tidak ikut dikirim
            # bersama entities — Telegram API tidak mengizinkan keduanya sekaligus.
            return _orig_send(chat_id, new_text, parse_mode="", entities=new_ents, **kwargs)
        return _orig_send(chat_id, text, parse_mode=parse_mode, **kwargs)

    bot.send_message = _send_message

    # ── reply_to ──────────────────────────────────────────────────────────────
    _orig_reply = bot.reply_to

    def _reply_to(message, text, parse_mode=None, entities=None, **kwargs):
        if entities:
            return _orig_reply(message, text, parse_mode=parse_mode,
                               entities=entities, **kwargs)
        effective_pm = parse_mode if parse_mode is not None else _default_pm
        new_text, new_ents = build_telebot_entities(str(text), effective_pm)
        if new_ents:
            return _orig_reply(message, new_text, parse_mode="", entities=new_ents, **kwargs)
        return _orig_reply(message, text, parse_mode=parse_mode, **kwargs)

    bot.reply_to = _reply_to

    # ── edit_message_text ────────────────────────────────────────────────────
    if hasattr(bot, "edit_message_text"):
        _orig_edit = bot.edit_message_text

        def _edit_message_text(text, chat_id=None, message_id=None,
                               inline_message_id=None, parse_mode=None,
                               entities=None, **kwargs):
            if entities:
                return _orig_edit(text, chat_id=chat_id, message_id=message_id,
                                  inline_message_id=inline_message_id,
                                  parse_mode=parse_mode, entities=entities, **kwargs)
            effective_pm = parse_mode if parse_mode is not None else _default_pm
            new_text, new_ents = build_telebot_entities(str(text), effective_pm)
            if new_ents:
                return _orig_edit(new_text, chat_id=chat_id, message_id=message_id,
                                  inline_message_id=inline_message_id,
                                  parse_mode="", entities=new_ents, **kwargs)
            return _orig_edit(text, chat_id=chat_id, message_id=message_id,
                              inline_message_id=inline_message_id,
                              parse_mode=parse_mode, **kwargs)

        bot.edit_message_text = _edit_message_text

    logger.info("[premium_emoji] Patch berhasil dipasang ke bot (default_pm=%s, %d emoji di peta).",
                _default_pm, len(_EMOJI_MAP))


# ── Bot API 9.4: Button helpers (icon_custom_emoji_id + style) ────────────────

# Deteksi dukungan parameter Bot API 9.4 di versi telebot yang terpasang
def _check_btn_support() -> tuple[bool, bool]:
    """Kembalikan (kb_supports_premium, ikb_supports_premium)."""
    if not _TELEBOT_OK:
        return False, False
    try:
        import inspect
        import telebot as _tb
        kb_params = inspect.signature(_tb.types.KeyboardButton.__init__).parameters
        ikb_params = inspect.signature(_tb.types.InlineKeyboardButton.__init__).parameters
        return ("icon_custom_emoji_id" in kb_params), ("icon_custom_emoji_id" in ikb_params)
    except Exception:
        return False, False

_KB_PREMIUM_OK, _IKB_PREMIUM_OK = _check_btn_support()
if not _KB_PREMIUM_OK:
    logger.warning(
        "[premium_emoji] KeyboardButton tidak mendukung icon_custom_emoji_id "
        "(pyTelegramBotAPI terlalu lama). Fallback ke teks biasa."
    )


def _get_icon_id(emoji_char: str) -> Optional[str]:
    """Cari custom_emoji_id untuk karakter emoji dari EMOJI_MAP."""
    _load_map()
    cid = _EMOJI_MAP.get(emoji_char)
    if not cid:
        base = "".join(c for c in emoji_char if ord(c) not in _VARIATION_SELECTORS)
        cid = _EMOJI_MAP.get(base)
    return cid


def kb_btn(label: str, emoji_char: str = "", style: Optional[str] = None, **kwargs):
    """
    Buat KeyboardButton dengan icon_custom_emoji_id dan style (Bot API 9.4).

    label      : teks tombol (boleh sudah ada emoji di depan atau tanpa emoji).
    emoji_char : karakter emoji untuk ikon premium.
                 - Jika emoji ada di peta → premium icon; emoji di-strip dari label agar tidak dobel.
                 - Jika tidak ada di peta → prepend ke label sebagai fallback teks biasa.
    style      : "primary" (biru), "success" (hijau), "danger" (merah), atau None.
    """
    if not _TELEBOT_OK:
        raise RuntimeError("[premium_emoji] telebot tidak tersedia")
    import telebot as _tb
    icon_id = _get_icon_id(emoji_char) if (emoji_char and _KB_PREMIUM_OK) else None
    if icon_id:
        clean = label
        if emoji_char and label.startswith(emoji_char):
            clean = label[len(emoji_char):].lstrip()
        display = clean
    else:
        display = f"{emoji_char} {label}".strip() if emoji_char else label
    if _KB_PREMIUM_OK:
        extra = {}
        if icon_id is not None:
            extra["icon_custom_emoji_id"] = icon_id
        if style is not None:
            extra["style"] = style
        return _tb.types.KeyboardButton(text=display, **extra, **kwargs)
    return _tb.types.KeyboardButton(text=display, **kwargs)


def ikb_btn(label: str, emoji_char: str = "", style: Optional[str] = None, **kwargs):
    """
    Buat InlineKeyboardButton dengan icon_custom_emoji_id dan style (Bot API 9.4).

    label      : teks tombol (boleh sudah ada emoji di depan atau tanpa emoji).
    emoji_char : karakter emoji untuk ikon premium.
                 - Jika ada di peta → premium icon; emoji di-strip dari label agar tidak dobel.
                 - Jika tidak → prepend ke label sebagai fallback.
    style      : "primary" (biru), "success" (hijau), "danger" (merah), atau None.
    """
    if not _TELEBOT_OK:
        raise RuntimeError("[premium_emoji] telebot tidak tersedia")
    import telebot as _tb
    icon_id = _get_icon_id(emoji_char) if (emoji_char and _IKB_PREMIUM_OK) else None
    if icon_id:
        clean = label
        if emoji_char and label.startswith(emoji_char):
            clean = label[len(emoji_char):].lstrip()
        display = clean
    else:
        display = f"{emoji_char} {label}".strip() if emoji_char else label
    if _IKB_PREMIUM_OK:
        extra = {}
        if icon_id is not None:
            extra["icon_custom_emoji_id"] = icon_id
        if style is not None:
            extra["style"] = style
        return _tb.types.InlineKeyboardButton(text=display, **extra, **kwargs)
    return _tb.types.InlineKeyboardButton(text=display, **kwargs)
