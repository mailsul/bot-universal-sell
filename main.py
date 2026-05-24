import json  # Made With love by @govtrashit A.K.A RzkyO
import os    # DON'T CHANGE AUTHOR NAME!
import re
import html as _html
import asyncio
import shutil
import time
import random
import logging
import httpx
from werkzeug.security import generate_password_hash
from produk_lock import produk_lock
from db import (
    init_db, init_web_tables,
    db_get_saldo, db_add_saldo, db_set_saldo, db_get_all_saldo,
    db_get_all_pending, db_get_pending_by_user, db_get_pending_any_by_user,
    db_add_pending, db_remove_pending_by_user, db_remove_pending_any_by_user,
    db_update_pending_cek_count, db_remove_pending_by_id, db_update_pending_msg_id,
    db_add_riwayat, db_get_riwayat,
    db_update_statistik, db_get_statistik_user, db_get_all_statistik,
    web_get_user_by_tid, web_create_user,
    web_get_user_by_email, web_get_user_by_phone, web_update_profile,
    db_add_bot_user, db_get_all_bot_users,
    db_get_rekap_penjualan,
    db_add_voucher, db_use_voucher, db_check_voucher, db_get_all_vouchers,
    db_delete_voucher, db_toggle_voucher,
    db_add_rating, db_get_ratings,
    db_ticket_create, db_ticket_list, db_ticket_get, db_ticket_reply, db_ticket_close,
    db_referral_create, db_referral_list, db_is_first_purchase,
)

import sys
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%d/%m %H:%M:%S",
    stream=sys.stdout,
)
# Bungkam log bawaan library yang terlalu ramai
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
from qris_helper import generate_qr_with_amount
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    MessageEntity,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, CallbackContext
)
from datetime import datetime
import secrets as _secrets
import string as _string

BULAN_ID = ["","Januari","Februari","Maret","April","Mei","Juni",
            "Juli","Agustus","September","Oktober","November","Desember"]

def fmt_waktu(s: str) -> str:
    """Ubah '24/05/2026 13:00:00' → '24 Mei 2026, 13:00' atau '24/05/2026' → '24 Mei 2026'."""
    try:
        s = str(s).strip()
        if " " in s:
            dt = datetime.strptime(s, "%d/%m/%Y %H:%M:%S")
            return f"{dt.day} {BULAN_ID[dt.month]} {dt.year}, {dt.strftime('%H:%M')}"
        dt = datetime.strptime(s, "%d/%m/%Y")
        return f"{dt.day} {BULAN_ID[dt.month]} {dt.year}"
    except Exception:
        return s

def _auto_generate_password() -> str:
    """Generate password acak 12 karakter yang memenuhi semua syarat keamanan."""
    chars = _string.ascii_letters + _string.digits + "!@#$%&*"
    for _ in range(200):
        pw = "".join(_secrets.choice(chars) for _ in range(12))
        if (any(c.isupper() for c in pw) and any(c.islower() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%&*" for c in pw)):
            return pw
    return "Ibra@Store1!"

def _get_website_url() -> str:
    """Auto-detect URL website: config manual → REPLIT_DEV_DOMAIN → kosong."""
    cfg = load_config()
    manual = cfg.get("website_url", "").strip()
    if manual:
        return manual
    domain = os.environ.get("REPLIT_DEV_DOMAIN", "").strip()
    return f"https://{domain}" if domain else ""

# ─── PREMIUM EMOJI ADAPTER ────────────────────────────────────────────────────

try:
    from premium_emoji import build_http_entities as _pe_raw, get_emoji_map as _get_emoji_map
    _PE_OK = True
except Exception:
    _PE_OK = False
    def _get_emoji_map():
        return {}


def _utf16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _pe(text: str, pm: str = "Markdown") -> tuple[str, list]:
    """Konversi teks Markdown ke (plain, [MessageEntity]) dengan premium emoji.
    Jika tidak ada premium emoji atau ada entity tidak valid, fallback ke parse_mode biasa."""
    if not _PE_OK:
        return text, []
    try:
        plain, raw = _pe_raw(text, pm)
        if not raw:
            return text, []
        plen = _utf16len(plain)
        ents = []
        for e in raw:
            try:
                off = e["offset"]
                ln  = e["length"]
                # Validasi bounds: skip entity yang melampaui panjang teks
                if off < 0 or ln <= 0 or (off + ln) > plen:
                    continue
                kw: dict = {"type": e["type"], "offset": off, "length": ln}
                if e.get("custom_emoji_id"):
                    kw["custom_emoji_id"] = e["custom_emoji_id"]
                if e.get("url"):
                    kw["url"] = e["url"]
                ents.append(MessageEntity(**kw))
            except Exception:
                pass
        return (plain, ents) if ents else (text, [])
    except Exception:
        return text, []


def _ikb(text: str, emoji_char: str = "", style: str = None, **kwargs) -> InlineKeyboardButton:
    """InlineKeyboardButton dengan premium emoji icon dan warna tombol.
    - emoji_char dicari langsung di emojis.txt via get_emoji_map() — tidak ada dict hardcoded.
    - Teks emoji di-strip dari text agar tidak dobel (kecuali text hanya emoji saja).
    - style dikirim ke Telegram API: 'primary' (biru), 'success' (hijau), 'danger' (merah)."""
    kw = dict(kwargs)

    if emoji_char:
        base_char = "".join(c for c in emoji_char if ord(c) not in (0xFE0F, 0xFE0E))
        emap = _get_emoji_map()
        icon_id = emap.get(emoji_char) or emap.get(base_char)

        if icon_id:
            # Hapus semua kemunculan emoji dari teks (depan, belakang, manapun)
            stripped = text
            for v in {emoji_char, base_char, emoji_char + "\ufe0f", base_char + "\ufe0f"} - {""}:
                stripped = stripped.replace(v, "")
            stripped = stripped.strip()
            if stripped:
                kw["icon_custom_emoji_id"] = icon_id
                text = stripped

    if not text or not text.strip():
        text = "·"

    if style:
        kw["style"] = style

    return InlineKeyboardButton(text=text, **kw)

# ─── KONFIGURASI ────────────────────────────────────────────────────────────
BOT_TOKEN         = os.getenv("BOT_TOKEN")
OWNER_ID          = int(os.getenv("OWNER_ID", "1160642744"))
_extra_admins     = os.getenv("ADMIN_IDS", "")
ADMIN_IDS         = set(
    [OWNER_ID] + [int(x) for x in _extra_admins.split(",") if x.strip().isdigit()]
)

LOW_STOCK_THRESHOLD = 2
DEPOSIT_NOMINALS    = [10000, 15000, 20000, 25000, 50000]

_RE_PHONE = re.compile(r'^\+62[0-9]{8,13}$')
_RE_EMAIL  = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

def _validate_password(pw: str):
    """Validasi kekuatan password. Return pesan error atau None jika valid."""
    if len(pw) < 8:
        return "Password minimal *8 karakter*"
    if not re.search(r'[A-Z]', pw):
        return "Password harus ada huruf *KAPITAL* (A–Z)"
    if not re.search(r'[0-9]', pw):
        return "Password harus ada *angka* (0–9)"
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{};:\'",.<>?/\\|`~]', pw):
        return "Password harus ada *simbol* (!@#$%^&* dll)"
    return None

DEPOSIT_MIN         = 5000
DEPOSIT_MAX         = 1_000_000
RIWAYAT_LIMIT       = 10
QRIS_POLL_INTERVAL  = 30   # cek mutasi setiap 30 detik
QRIS_EXPIRY_MINUTES = 5    # pending QRIS kedaluwarsa setelah 5 menit

URL_MUTASI     = os.getenv("URL_MUTASI")
QRIS_BASE64    = os.getenv("QRIS_BASE64")
produk_file    = "produk.json"
saldo_file     = "saldo.json"
deposit_file   = "pending_deposit.json"
riwayat_file   = "riwayat.json"
statistik_file = "statistik.json"
config_file    = "config.json"
qris_file      = "qris.jpg"

# Lock global untuk mencegah race condition saat beli produk
purchase_lock = asyncio.Lock()

# Event untuk reset timer loop mutasi ketika user menekan "Cek Sekarang"
_manual_check_event = asyncio.Event()

# ─── FITUR TAMBAHAN: CONSTANTS ────────────────────────────────────────────────
LOG_GROUP_ID       = int(os.getenv("LOG_GROUP_ID", "0"))  # ID grup log admin
ANOMALY_THRESHOLD  = 5_000_000   # Rp5 juta → alert admin jika nominal lebih
ANTI_SPAM_INTERVAL = 1.5          # detik minimum antar klik tombol (anti-spam)
_ANTI_SPAM: dict   = {}           # {user_id: monotonic_time} throttle state
_pending_file_offers: dict = {}   # {user_id: {file_path, item_name, msg_id, chat_id, job_name}}


# ─── HELPER: CONFIG ──────────────────────────────────────────────────────────

_CONFIG_DEFAULT = {
    "nama_toko":    "Store Ekha",
    "rekening":     ["DANA : 0812-XXXX-XXXX a.n Admin"],
    "kontak_admin": "@admin",
    "website_url":  "",
}


def load_config() -> dict:
    if not os.path.exists(config_file):
        save_config(_CONFIG_DEFAULT.copy())
        return _CONFIG_DEFAULT.copy()
    with open(config_file, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return _CONFIG_DEFAULT.copy()
    data = json.loads(content)
    for k, v in _CONFIG_DEFAULT.items():
        data.setdefault(k, v)
    return data


def save_config(data: dict):
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── HELPER: JSON ───────────────────────────────────────────────────────────

def _backup_json(file: str):
    """Buat backup file JSON ke folder backups/ sebelum ditulis."""
    if not os.path.exists(file):
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name  = os.path.splitext(os.path.basename(file))[0]
    shutil.copy2(file, f"backups/{name}_{stamp}.json")


def load_json(file: str):
    if not os.path.exists(file):
        return [] if file == deposit_file else {}
    with open(file, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return [] if file == deposit_file else {}
    return json.loads(content)


def save_json(file: str, data, backup: bool = False):
    if backup:
        _backup_json(file)
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_produk_format(raw: dict) -> tuple[dict, bool]:
    """Convert format lama → format baru (tipe dict). Idempotent."""
    changed = False
    for pid, item in list(raw.items()):
        if "tipe" not in item:
            akun_list = item.get("akun_list", [])
            raw[pid] = {
                "nama": item["nama"],
                "tipe": {
                    "t1": {
                        "nama": item["nama"],
                        "harga": item.get("harga", 0),
                        "akun_list": akun_list,
                        "stok": len(akun_list),
                        "deskripsi": item.get("deskripsi", ""),
                    }
                },
            }
            if item.get("gambar"):
                raw[pid]["gambar"] = item["gambar"]
            changed = True
    return raw, changed


def load_produk() -> dict:
    """Load + migrate produk.json. Return {pid: {nama, gambar, tipe:{tid:{nama,harga,akun_list,stok}}}}"""
    raw = load_json(produk_file)
    if not isinstance(raw, dict):
        raw = {}
    raw, changed = _migrate_produk_format(raw)
    if changed:
        save_json(produk_file, raw, backup=False)
    # Sync stok semua tipe
    for item in raw.values():
        for t in item.get("tipe", {}).values():
            t["stok"] = len(t.get("akun_list", []))
    return raw


def save_produk(produk: dict):
    """Save produk dict (format tipe). Sync stok dulu."""
    for item in produk.values():
        for t in item.get("tipe", {}).values():
            t["stok"] = len(t.get("akun_list", []))
    save_json(produk_file, produk, backup=True)


def _generate_kode_unik(expected_nominal: int) -> int:
    """Generate kode unik (1-99) untuk membedakan pembayaran QRIS antar user."""
    pending = db_get_all_pending()
    used = {p.get("expected_amount", 0) for p in pending if p.get("metode", "").startswith("qris")}
    for _ in range(200):
        code = random.randint(1, 99)
        if (expected_nominal + code) not in used:
            return code
    return random.randint(1, 99)


def _parse_nominal(val) -> int:
    """Parse nominal dari string format Indonesia: '5.040' → 5040, '1.000.000' → 1000000."""
    try:
        s = str(val).strip()
        # Deteksi format: jika ada titik AND koma → titik=ribuan, koma=desimal (1.000,50)
        if "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".")
        # Hanya titik: bisa ribuan (5.040) atau desimal (5.5)
        # Anggap titik = ribuan jika bagian setelah titik terakhir >= 3 digit
        elif "." in s and not "," in s:
            parts = s.split(".")
            if len(parts[-1]) >= 3:
                s = s.replace(".", "")  # ribuan: 5.040 → 5040
            # else: desimal biasa, biarkan float handle
        else:
            s = s.replace(",", "")
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _extract_amounts_from_mutasi(data) -> set:
    """Ekstrak semua nominal kredit masuk (IN) dari respons API mutasi.
    Mendukung format orderkuota: data.data.qris_history.results[].kredit
    dan berbagai format API lainnya sebagai fallback."""
    amounts = set()

    # ── Cari list transaksi secara rekursif ──────────────────────────────────
    def _find_results(obj, depth=0) -> list:
        if depth > 5:
            return []
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            return obj
        if isinstance(obj, dict):
            # Prioritas nama key yang umum
            for key in ("results", "qris_history", "data", "mutasi",
                        "records", "transactions", "result", "items", "history"):
                v = obj.get(key)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
                if isinstance(v, dict):
                    found = _find_results(v, depth + 1)
                    if found:
                        return found
        return []

    items = _find_results(data)
    if not items:
        log.warning("⚠️ Tidak bisa menemukan list transaksi dalam respons mutasi")
        return amounts

    # ── Nama field nominal yang mungkin dipakai berbagai API ─────────────────
    KREDIT_FIELDS = ("kredit", "credit", "amount", "nominal",
                     "jumlah", "nilai", "total", "kredit_rupiah", "in")
    IN_STATUS     = {"in", "kredit", "cr", "credit", "masuk", "success"}

    for item in items:
        if not isinstance(item, dict):
            continue
        # Filter: hanya transaksi masuk
        status = str(item.get("status", "")).strip().lower()
        if status and status not in IN_STATUS:
            continue
        # Cari nominal dari field yang tersedia
        for field in KREDIT_FIELDS:
            val = item.get(field)
            if val is not None and str(val).strip() not in ("", "0"):
                v = _parse_nominal(val)
                if v > 0:
                    amounts.add(v)
                    break

    return amounts


# ─── HELPER: STATISTIK & RIWAYAT ────────────────────────────────────────────
# Fungsi ini kini di-handle oleh db.py (db_add_riwayat, db_update_statistik)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _qris_available() -> bool:
    """True jika QRIS tersedia (dari env var QRIS_BASE64 atau file statis qris.jpg)."""
    return bool(QRIS_BASE64) or os.path.exists(qris_file)


async def _send_qris_photo(bot, chat_id: int, nominal: int, kode: int, caption: str,
                           reply_markup=None, caption_entities=None):
    """Generate QR dinamis dan kirim ke user. Fallback ke file statis jika perlu.
    Mengembalikan objek Message yang terkirim."""
    def _photo_kw():
        if caption_entities:
            return {"caption": caption, "caption_entities": caption_entities, "reply_markup": reply_markup}
        return {"caption": caption, "parse_mode": "Markdown", "reply_markup": reply_markup}

    if QRIS_BASE64:
        img_bytes, _ = generate_qr_with_amount(QRIS_BASE64, nominal, kode)
        if img_bytes:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(img_bytes, filename="qris.png"),
                **_photo_kw()
            )
    if os.path.exists(qris_file):
        with open(qris_file, "rb") as f:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f),
                **_photo_kw()
            )
    return None


def _fmt_akun(akun: dict, i: int, markup: bool = True) -> str:
    """Format satu akun untuk pesan (markup=True) atau file txt (markup=False)."""
    q = "`" if markup else ""
    sep = "─────────────────────" if markup else "---------------------------"
    lines = [
        f"Akun #{i}",
        f"Username : {q}{akun.get('username','')}{q}",
        f"Password : {q}{akun.get('password','')}{q}",
    ]
    if akun.get("extra"):
        lines.append(f"Info     : {q}{akun['extra']}{q}")
    lines.append(sep)
    return "\n".join(lines) + "\n"


# ─── QRIS: CEK MUTASI OTOMATIS ───────────────────────────────────────────────

async def proses_mutasi(app: Application):
    """Ambil data mutasi dari API, cocokkan dengan pending QRIS, konfirmasi otomatis."""
    if not URL_MUTASI:
        return

    log.info("🔍 Cek mutasi ke URL_MUTASI...")
    raw = None
    for attempt in range(1, 4):  # max 3 percobaan
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(URL_MUTASI)
                resp.raise_for_status()
                raw = resp.json()
            break
        except Exception as e:
            log.warning(f"❌ Gagal ambil mutasi (percobaan {attempt}/3): [{type(e).__name__}] {e}")
            if attempt < 3:
                await asyncio.sleep(2)
    if raw is None:
        return

    mutation_amounts = _extract_amounts_from_mutasi(raw)
    log.info(f"📊 Mutasi ditemukan: {len(mutation_amounts)} nominal — {sorted(mutation_amounts)}")
    if not mutation_amounts:
        return

    pending    = db_get_all_pending()
    now        = datetime.now()
    to_confirm = []
    to_delete  = []

    for p in pending:
        metode = p.get("metode", "manual")
        if not metode.startswith("qris"):
            continue

        # Cek kedaluwarsa
        try:
            waktu = datetime.strptime(p["waktu"], "%d/%m/%Y %H:%M:%S")
            if (now - waktu).total_seconds() > QRIS_EXPIRY_MINUTES * 60:
                log.info(f"⏰ Pending QRIS user {p['user_id']} kedaluwarsa, dihapus.")
                to_delete.append(p["id"])
                # Kembalikan stok yang direservasi
                reserved_akun = p.get("reserved_akun", [])
                if reserved_akun:
                    async with purchase_lock:
                        with produk_lock():
                            produk_r  = load_produk()
                            item_r    = produk_r.get(p.get("produk_id"))
                            s_tipe_id = p.get("tipe_id")
                            if item_r and s_tipe_id and s_tipe_id in item_r.get("tipe", {}):
                                t_r = item_r["tipe"][s_tipe_id]
                                t_r["akun_list"] = reserved_akun + t_r.get("akun_list", [])
                                t_r["stok"]      = len(t_r["akun_list"])
                                save_produk(produk_r)
                            log.info(f"↩️ Stok dikembalikan: {len(reserved_akun)} akun → {p.get('produk_id')}/{s_tipe_id}")
                # Hapus pesan QRIS yang sudah expired
                qris_mid = p.get("qris_msg_id", 0)
                if qris_mid:
                    try:
                        await app.bot.delete_message(chat_id=p["user_id"], message_id=qris_mid)
                    except Exception:
                        pass
                try:
                    await _send_pe(
                        app.bot, p["user_id"],
                        "⏰ *Waktu pembayaran QRIS habis.*\n\n"
                        "Pesanan otomatis *dibatalkan*. Silakan buat pesanan baru jika masih ingin berbelanja."
                    )
                except Exception:
                    pass
                continue
        except Exception:
            pass

        expected = p.get("expected_amount", 0)
        if expected in mutation_amounts:
            log.info(f"✅ COCOK! user={p['user_id']} expected=Rp{expected:,} metode={metode}")
            to_confirm.append(p)
            to_delete.append(p["id"])
        else:
            log.info(f"⏳ Belum cocok: user={p['user_id']} expected=Rp{expected:,}")

    for pid in to_delete:
        db_remove_pending_by_id(pid)

    if not to_confirm:
        return

    for p in to_confirm:
        uid    = str(p["user_id"])
        metode = p.get("metode")

        if metode == "qris":
            # ── Deposit via QRIS ──────────────────────────────────────
            nominal   = p["nominal"]
            new_saldo = db_add_saldo(uid, nominal)
            trx_id    = db_add_riwayat(uid, "DEPOSIT", "QRIS Otomatis", nominal)
            log.info(f"💰 DEPOSIT QRIS dikonfirmasi: user={uid} nominal=Rp{nominal:,} saldo_baru=Rp{new_saldo:,} trx={trx_id}")
            try:
                await app.bot.send_message(
                    chat_id=p["user_id"],
                    text=(
                        f"✅ *Deposit QRIS berhasil!*\n"
                        f"💰 Rp{nominal:,} telah masuk ke saldo kamu.\n"
                        f"💳 Saldo sekarang: Rp{new_saldo:,}\n"
                        f"🔖 ID Transaksi: `{trx_id}`\n\n"
                        "Ketik /start untuk kembali ke menu."
                    ),
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardRemove()
                )
            except Exception:
                pass
            await _notify_group(
                app.bot,
                f"💰 *Deposit QRIS Otomatis (Bot)*\n"
                f"👤 `{uid}`\n"
                f"💵 Rp{nominal:,}\n"
                f"🔖 `{trx_id}`"
            )

        elif metode == "qris_beli":
            # ── Beli langsung via QRIS ────────────────────────────────
            produk_id     = p.get("produk_id")
            jumlah        = p.get("jumlah", 1)
            nominal       = p["nominal"]
            reserved_akun = p.get("reserved_akun", [])
            file_path     = None
            item          = None

            async with purchase_lock:
                tipe_id_p = p.get("tipe_id")
                if reserved_akun and len(reserved_akun) >= jumlah:
                    # Akun sudah direservasi saat QRIS diinisiasi — stok sudah terkurangi
                    akun_terpakai = reserved_akun[:jumlah]
                    produk = load_produk()
                    item   = produk.get(produk_id) or {"nama": produk_id or "Produk", "tipe": {}}
                    nama_tipe = ""
                    if tipe_id_p and tipe_id_p in item.get("tipe", {}):
                        nama_tipe = item["tipe"][tipe_id_p].get("nama", "")
                else:
                    # Fallback: pop dari produk (tidak ada reservasi)
                    produk = load_produk()
                    item   = produk.get(produk_id)
                    if not item:
                        try:
                            await app.bot.send_message(
                                chat_id=p["user_id"],
                                text="❌ *Stok habis setelah pembayaran.*\nHubungi admin untuk refund.",
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass
                        continue
                    tipe_id_p = tipe_id_p or next(iter(item.get("tipe", {})), None)
                    if not tipe_id_p or tipe_id_p not in item.get("tipe", {}):
                        try:
                            await app.bot.send_message(
                                chat_id=p["user_id"],
                                text="❌ *Stok habis setelah pembayaran.*\nHubungi admin untuk refund.",
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass
                        continue
                    tipe_p    = item["tipe"][tipe_id_p]
                    nama_tipe = tipe_p.get("nama", "")
                    if len(tipe_p.get("akun_list", [])) < jumlah:
                        try:
                            await app.bot.send_message(
                                chat_id=p["user_id"],
                                text="❌ *Stok habis setelah pembayaran.*\nHubungi admin untuk refund.",
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass
                        continue
                    akun_terpakai = [tipe_p["akun_list"].pop(0) for _ in range(jumlah)]
                    tipe_p["stok"] = len(tipe_p["akun_list"])
                    save_produk(produk)

                nama_tipe_str = f" [{nama_tipe}]" if nama_tipe else ""
                trx_id = db_add_riwayat(uid, "BELI", f"{item['nama']}{nama_tipe_str} x{jumlah} (QRIS)", nominal)
                log.info(f"🛒 BELI QRIS dikonfirmasi: user={uid} produk={item['nama']} x{jumlah} Rp{nominal:,} trx={trx_id}")

                # Buat file txt (simpan dulu, tawarkan ke user)
                os.makedirs("akun_dikirim", exist_ok=True)
                stamp     = int(time.time())
                file_path = f"akun_dikirim/{uid}_{produk_id}_x{jumlah}_{stamp}.txt"
                with open(file_path, "w", encoding="utf-8") as f:
                    for i, akun in enumerate(akun_terpakai, start=1):
                        f.write(_fmt_akun(akun, i, markup=False))

                # Ambil deskripsi tipe
                tipe_desc = ""
                if tipe_id_p and tipe_id_p in item.get("tipe", {}):
                    tipe_desc = item["tipe"][tipe_id_p].get("deskripsi", "").strip()

            # Hapus pesan QRIS sekarang pembayaran berhasil
            qris_mid = p.get("qris_msg_id", 0)
            if qris_mid:
                try:
                    await app.bot.delete_message(chat_id=p["user_id"], message_id=qris_mid)
                except Exception:
                    pass

            # Teks detail akun
            waktu_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            text_akun = (
                f"✅ *Pembelian QRIS Berhasil!*\n\n"
                f"📦 {item['nama']}{nama_tipe_str} x{jumlah}\n"
                f"💸 Dibayar: Rp{nominal:,}\n"
                f"🔖 ID Transaksi: `{trx_id}`\n"
                f"📅 {waktu_str}\n"
                f"─────────────────────\n"
            )
            for i, akun in enumerate(akun_terpakai, start=1):
                text_akun += _fmt_akun(akun, i, markup=True)
            if tipe_desc:
                text_akun += f"\n📋 *Catatan:*\n{tipe_desc}\n"

            try:
                await _send_pe(app.bot, p["user_id"], text_akun,
                               reply_markup=ReplyKeyboardRemove())
                # Tawarkan file .txt (opsional)
                if file_path and os.path.exists(file_path):
                    await _send_file_offer(app.bot, p["user_id"], file_path,
                                           item['nama'], app.job_queue)
            except Exception:
                pass

            await _notify_group(
                app.bot,
                f"🛒 *Pembelian QRIS Otomatis (Bot)*\n"
                f"👤 `{uid}`\n"
                f"📦 {item['nama']}{nama_tipe_str} x{jumlah}\n"
                f"💸 Rp{nominal:,}\n"
                f"🔖 `{trx_id}`"
            )

            # Notif stok rendah — hanya jika tipe_id_p valid DAN stok > 0 (bukan habis)
            if item and tipe_id_p and tipe_id_p in item.get("tipe", {}):
                sisa_tipe_stok = len(item["tipe"][tipe_id_p].get("akun_list", []))
                if 0 < sisa_tipe_stok <= LOW_STOCK_THRESHOLD:
                    for admin_id in ADMIN_IDS:
                        try:
                            await app.bot.send_message(
                                chat_id=admin_id,
                                text=f"⚠️ *Stok Rendah*\n{item['nama']}{nama_tipe_str} sisa {sisa_tipe_stok}x",
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass


async def mutasi_loop(app: Application):
    """Background task — polling setiap 30 detik, aktif hanya saat ada pending QRIS < 5 menit."""
    await asyncio.sleep(10)
    while True:
        # Cek apakah ada pending QRIS yang masih aktif (< 5 menit)
        try:
            pending = db_get_all_pending()
            now = datetime.now()
            has_active = any(
                p.get("metode", "").startswith("qris") and
                (now - datetime.strptime(p["waktu"], "%d/%m/%Y %H:%M:%S")).total_seconds() < 300
                for p in pending
                if "waktu" in p and p.get("metode", "").startswith("qris")
            )
            if has_active:
                log.info("🔄 Auto-poll: ada pending QRIS aktif, cek mutasi...")
                await proses_mutasi(app)
        except Exception as e:
            log.warning(f"⚠️ Error di mutasi_loop: {e}")
        # Tunggu 30 detik ATAU sampai user klik "Cek Sekarang"
        _manual_check_event.clear()
        try:
            await asyncio.wait_for(_manual_check_event.wait(), timeout=QRIS_POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break


async def post_init(app: Application):
    import datetime as _dt
    init_db()
    init_web_tables()
    if URL_MUTASI:
        asyncio.create_task(mutasi_loop(app))
    if app.job_queue is not None:
        app.job_queue.run_daily(_daily_report_job, time=_dt.time(23, 0, 0), name="daily_report")
        app.job_queue.run_daily(_auto_backup_job,  time=_dt.time(3,  0, 0), name="auto_backup")
    else:
        log.warning("⚠️ JobQueue tidak tersedia — laporan harian & backup otomatis dinonaktifkan.")


# ─── MENU UTAMA ─────────────────────────────────────────────────────────────

# ─── HELPER: GROUP LOG, DAILY REPORT, AUTO BACKUP ────────────────────────────

async def _notify_group(bot, text: str):
    """Kirim log aktivitas ke grup admin (atur LOG_GROUP_ID di env)."""
    if not LOG_GROUP_ID:
        return
    try:
        await _send_pe(bot, LOG_GROUP_ID, text)
    except Exception:
        pass


async def _daily_report_job(context: CallbackContext):
    """Laporan harian otomatis dikirim ke semua admin (job_queue run_daily)."""
    try:
        rekap = db_get_rekap_penjualan()
        b = rekap["beli"]
        d = rekap["deposit"]
        text = (
            f"📊 *Laporan Harian — {rekap['tanggal']}*\n"
            f"_{rekap['bulan']}_\n\n"
            f"🛒 *Penjualan Hari Ini*\n"
            f"• Transaksi : *{b['hari_ini']['count']}x*\n"
            f"• Pendapatan: *Rp{b['hari_ini']['total']:,}*\n\n"
            f"💰 *Deposit Hari Ini*\n"
            f"• Transaksi : *{d['hari_ini']['count']}x*\n"
            f"• Total     : *Rp{d['hari_ini']['total']:,}*\n\n"
            f"📦 *Akumulasi Semua Waktu*\n"
            f"• Penjualan : *{b['semua']['count']}x* — Rp{b['semua']['total']:,}\n"
            f"• Deposit   : *{d['semua']['count']}x* — Rp{d['semua']['total']:,}"
        )
        for admin_id in ADMIN_IDS:
            try:
                await _send_pe(context.bot, admin_id, text)
            except Exception:
                pass
    except Exception as e:
        log.warning(f"Daily report error: {e}")


async def _auto_backup_job(context: CallbackContext):
    """Backup database otomatis ke folder backup/ — simpan 7 terbaru (job_queue)."""
    os.makedirs("backup", exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    dst   = f"backup/toko_{today}.db"
    try:
        shutil.copy2("toko.db", dst)
        backups = sorted(
            f for f in os.listdir("backup") if f.startswith("toko_") and f.endswith(".db")
        )
        for old in backups[:-7]:
            try:
                os.remove(f"backup/{old}")
            except OSError:
                pass
        log.info(f"✅ Auto-backup DB: {dst}")
    except Exception as e:
        log.warning(f"⚠️ Auto-backup gagal: {e}")


_LOGO_EXTS = ["png", "jpg", "jpeg", "webp"]

def _get_logo_path() -> str | None:
    for ext in _LOGO_EXTS:
        p = os.path.join("static", f"logo.{ext}")
        if os.path.exists(p):
            return p
    return None


async def safe_edit(query, context, text: str, parse_mode: str = "Markdown",
                    reply_markup=None, disable_web_page_preview: bool = False):
    """Edit pesan (text atau caption) dengan premium emoji. Fallback: hapus lama + kirim baru."""
    plain, ents = _pe(text, parse_mode)
    if ents:
        kw_t = dict(entities=ents, reply_markup=reply_markup)
        kw_c = dict(caption_entities=ents, reply_markup=reply_markup)
        msg  = plain
    else:
        kw_t = dict(parse_mode=parse_mode, reply_markup=reply_markup)
        kw_c = dict(parse_mode=parse_mode, reply_markup=reply_markup)
        msg  = text
    if disable_web_page_preview:
        kw_t["disable_web_page_preview"] = True
    try:
        await query.edit_message_text(msg, **kw_t)
    except Exception:
        try:
            await query.edit_message_caption(msg, **kw_c)
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            kw_s = dict(kw_t)
            kw_s.pop("disable_web_page_preview", None)
            await context.bot.send_message(chat_id=query.from_user.id, text=msg, **kw_s)


async def _send_pe(bot, chat_id: int, text: str, reply_markup=None, parse_mode: str = "Markdown"):
    """Kirim pesan baru dengan premium emoji (entities) atau fallback parse_mode."""
    plain, ents = _pe(text, parse_mode)
    if ents:
        await bot.send_message(chat_id=chat_id, text=plain, entities=ents, reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)


async def send_main_menu(bot_or_context, chat_id: int, user):
    bot = getattr(bot_or_context, 'bot', bot_or_context)

    s      = db_get_saldo(user.id)
    stat   = db_get_statistik_user(user.id)
    jumlah = stat.get("jumlah", 0)

    # Stats toko
    all_stats   = db_get_all_statistik()
    all_saldo   = db_get_all_saldo()
    produk_dict = load_produk()
    total_produk    = len(produk_dict)
    total_penjualan = sum((v.get("jumlah", 0) if isinstance(v, dict) else 0) for v in all_stats.values())
    total_pengguna  = len(all_saldo)

    cfg_full = load_config()
    nama_toko = cfg_full["nama_toko"]
    uname = f"@{user.username}" if user.username else _html.escape(user.full_name)
    text = (
        f"🎯 <b>Selamat Datang di {_html.escape(nama_toko)}!</b>\n\n"
        f"🔵 <b>Sekilas Info Toko</b>\n"
        f"✅ Total Produk: <b>{total_produk}</b> jenis\n"
        f"✅ Total Penjualan: <b>{total_penjualan}</b> transaksi\n"
        f"✅ Total Pengguna: <b>{total_pengguna}</b> user\n\n"
        f"👑 <b>Profil Anda</b>\n"
        f"✅ Username: {uname}\n"
        f"✅ User ID: <code>{user.id}</code>\n"
        f"✅ Saldo: <b>Rp{s:,}</b>\n"
        f"✅ Total Beli: <b>{jumlah}</b> transaksi\n\n"
        f"🔴 <i>Pilih menu di bawah untuk melanjutkan.</i>"
    )

    keyboard = [
        [_ikb("🛍 List Produk",   "🛍", "success",  callback_data="list_produk"),
         _ikb("🆘 Bantuan",        "🆘", "danger",   callback_data="info_bot")],
        [_ikb("💰 Deposit Saldo",  "💰", "primary",  callback_data="deposit")],
        [_ikb("📜 Riwayat",         "📜", "primary",  callback_data="riwayat_user")],
    ]
    if is_admin(user.id):
        keyboard.append([_ikb("🛠 Admin Panel", "🛠", "danger", callback_data="admin_panel")])
    _ws = cfg_full.get("website_url", "").strip()
    if _ws:
        keyboard.append([_ikb("🌐 Kunjungi Website Toko", "🌐", "primary", url=_ws)])

    markup = InlineKeyboardMarkup(keyboard)

    # Konversi ke premium emoji entities (HTML mode)
    plain, ents = _pe(text, "HTML")

    # Kirim logo jika ada
    logo = _get_logo_path()
    if logo:
        try:
            with open(logo, "rb") as f:
                send_kw: dict = {"chat_id": chat_id, "photo": f, "reply_markup": markup}
                if ents:
                    send_kw["caption"] = plain
                    send_kw["caption_entities"] = ents
                else:
                    send_kw["caption"] = text
                    send_kw["parse_mode"] = "HTML"
                await bot.send_photo(**send_kw)
            return
        except Exception:
            pass  # fallback ke send_message biasa

    if ents:
        await bot.send_message(chat_id=chat_id, text=plain, entities=ents, reply_markup=markup)
    else:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup)


async def send_main_menu_safe(update: Update, context: CallbackContext):
    if update.message:
        await send_main_menu(context, update.effective_chat.id, update.effective_user)
    elif update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
        await send_main_menu(context, update.callback_query.from_user.id, update.callback_query.from_user)


# ─── LIST PRODUK & CEK STOK ─────────────────────────────────────────────────

_PRODUK_EMOJI = {
    "youtube": "🎬", "canva": "🎨", "spotify": "🎵", "netflix": "🎭",
    "disney": "🏰", "prime": "📦", "vpn": "🔐", "vps": "🌐",
    "digitalocean": "💧", "heroku": "⬡", "aws": "☁️", "adobe": "🖌",
    "tiktok": "📱", "capcut": "✂️", "grammarly": "📝", "duolingo": "🦉",
    "chatgpt": "🤖", "openai": "🤖", "midjourney": "🖼", "figma": "🎯",
}

def _produk_emoji(nama: str) -> str:
    n = nama.lower()
    for kw, em in _PRODUK_EMOJI.items():
        if kw in n:
            return em
    return "🛒"


PRODUK_PER_PAGE = 4   # max produk per halaman agar tidak melebihi limit 4096 char

async def handle_list_produk(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    items  = list(produk.items())
    total  = len(items)

    # Deteksi halaman dari callback_data
    data = query.data if query else ""
    page = 1
    if data.startswith("list_produk_p"):
        try:
            page = int(data[len("list_produk_p"):])
        except ValueError:
            page = 1

    total_pages = max(1, (total + PRODUK_PER_PAGE - 1) // PRODUK_PER_PAGE)
    page        = max(1, min(page, total_pages))
    start_idx   = (page - 1) * PRODUK_PER_PAGE
    page_items  = items[start_idx: start_idx + PRODUK_PER_PAGE]

    SEP = "━━━━━━━━━━━━━━━━━━━━━━\n"
    msg = f"🛒 *DAFTAR PRODUK* _(hal. {page}/{total_pages})_\n\n"
    btn_row, kb_rows = [], []

    for nomor_global, (pid, item) in enumerate(page_items, start=start_idx + 1):
        tipe_dict  = item.get("tipe", {})
        total_stok = sum(len(t.get("akun_list",[])) for t in tipe_dict.values())
        min_harga  = min((t.get("harga",0) for t in tipe_dict.values()), default=0)
        tipe_count = len(tipe_dict)
        em = _produk_emoji(item["nama"])

        stok_icon = "🟢" if total_stok > LOW_STOCK_THRESHOLD else ("🟡" if total_stok > 0 else "🔴")
        stok_str  = f"{stok_icon} {total_stok}" if total_stok > 0 else "🔴 Habis"
        harga_str = f"Rp {min_harga:,}+" if tipe_count > 1 else f"Rp {min_harga:,}"

        msg += SEP
        msg += f"{em} *[{nomor_global}] {item['nama']}*\n"
        msg += f"💰 {harga_str}  📦 {stok_str}\n"

        if tipe_count > 1:
            for t in tipe_dict.values():
                stok_t = len(t.get("akun_list",[]))
                ic = "🟢" if stok_t > 0 else "🔴"
                msg += f"  {ic} {t['nama']}: Rp {t.get('harga',0):,}\n"

        btn_style = "success" if total_stok > 0 else "danger"
        btn_row.append(_ikb(f"{nomor_global}", em, btn_style, callback_data=pid))
        if len(btn_row) == 4:
            kb_rows.append(btn_row)
            btn_row = []

    msg += SEP
    msg += f"\n🚀 *Pilih nomor — halaman {page} dari {total_pages}*"

    if btn_row:
        kb_rows.append(btn_row)

    # Navigasi halaman
    nav_row = []
    if page > 1:
        nav_row.append(_ikb("👈 Sebelumnya", "👈", "primary", callback_data=f"list_produk_p{page-1}"))
    if page < total_pages:
        nav_row.append(_ikb("Berikutnya 👉", "👉", "primary", callback_data=f"list_produk_p{page+1}"))
    if nav_row:
        kb_rows.append(nav_row)

    kb_rows.append([_ikb("🔥 Kembali ke Menu Utama", "🔥", "primary", callback_data="back_to_menu")])

    markup = InlineKeyboardMarkup(kb_rows)
    await safe_edit(query, context, msg, reply_markup=markup)


async def handle_cek_stok(update: Update, context: CallbackContext):
    """Alias ke handle_list_produk — tombol cek stok sudah digabung ke list produk."""
    await handle_list_produk(update, context)


# ─── PRODUK DETAIL & ORDER ───────────────────────────────────────────────────

def _order_text(item: dict, jumlah: int, tipe_item: dict | None = None) -> str:
    """item = produk dict (format tipe). tipe_item = tipe yang dipilih."""
    if tipe_item:
        harga     = tipe_item.get("harga", 0)
        nama_tipe = tipe_item.get("nama", "-")
        stok      = len(tipe_item.get("akun_list", []))
    else:
        # Fallback: gunakan tipe pertama
        tipe_dict = item.get("tipe", {})
        if tipe_dict:
            first = next(iter(tipe_dict.values()))
            harga     = first.get("harga", 0)
            nama_tipe = first.get("nama", "-")
            stok      = len(first.get("akun_list", []))
        else:
            harga, nama_tipe, stok = 0, "-", 0
    total = jumlah * harga
    return (
        "🛒 *KONFIRMASI PESANAN*\n"
        "╭─────────────────────────╮\n"
        f"┊ Produk     : {item['nama']}\n"
        f"┊ Tipe       : {nama_tipe}\n"
        f"┊ Harga/pcs  : Rp{harga:,}\n"
        f"┊ Stok       : {stok}x\n"
        "┊─────────────────────────\n"
        f"┊ Jumlah     : x{jumlah}\n"
        f"┊ Total      : Rp{total:,}\n"
        "╰─────────────────────────╯"
    )


def _order_keyboard(jumlah: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _ikb("➖", "➖", "danger",  callback_data="qty_minus"),
            _ikb(f"  {jumlah}  ", "", "primary", callback_data="ignore"),
            _ikb("➕", "➕", "success", callback_data="qty_plus"),
        ],
        [_ikb("✅ Konfirmasi Order", "✅", "success", callback_data="confirm_order")],
        [_ikb("🔙 Kembali",          "🔙", "danger",  callback_data="back_to_produk")],
    ])


async def _send_produk_with_tipe(bot, chat_id: int, pid: str, item: dict, context):
    """Kirim detail produk: gambar (jika ada) + tipe selector atau langsung order."""
    tipe_dict  = item.get("tipe", {})
    available  = {tid: t for tid, t in tipe_dict.items() if len(t.get("akun_list",[])) > 0}
    gambar     = item.get("gambar")

    if not available:
        await bot.send_message(chat_id=chat_id, text="❌ Semua tipe habis saat ini.")
        return

    # Kalau hanya 1 tipe tersedia, langsung ke order
    if len(tipe_dict) == 1 or len(available) >= 1 and len(tipe_dict) == 1:
        tid      = next(iter(available))
        tipe_obj = available[tid]
        context.user_data["konfirmasi"] = {"produk_id": pid, "tipe_id": tid, "jumlah": 1}
        order_txt = _order_text(item, 1, tipe_obj)
        kb        = _order_keyboard(1)
        plain, ents = _pe(order_txt, "Markdown")
        if gambar or item.get("gambar_file_id"):
            try:
                # F28: Gunakan file_id cache jika sudah ada (jauh lebih cepat)
                fid = item.get("gambar_file_id")
                if fid:
                    if ents:
                        await bot.send_photo(chat_id=chat_id, photo=fid,
                                             caption=plain, caption_entities=ents, reply_markup=kb)
                    else:
                        await bot.send_photo(chat_id=chat_id, photo=fid,
                                             caption=order_txt, reply_markup=kb, parse_mode="Markdown")
                else:
                    base = gambar.lstrip("/")
                    with open(base, "rb") as f:
                        if ents:
                            msg = await bot.send_photo(chat_id=chat_id, photo=InputFile(f),
                                                 caption=plain, caption_entities=ents, reply_markup=kb)
                        else:
                            msg = await bot.send_photo(chat_id=chat_id, photo=InputFile(f),
                                                 caption=order_txt, reply_markup=kb, parse_mode="Markdown")
                    # Simpan file_id untuk penggunaan berikutnya
                    try:
                        with produk_lock():
                            produk_all = load_produk()
                            if pid in produk_all:
                                produk_all[pid]["gambar_file_id"] = msg.photo[-1].file_id
                                save_produk(produk_all)
                    except Exception:
                        pass
                return
            except Exception:
                pass
        await _send_pe(bot, chat_id, order_txt, reply_markup=kb)
        return

    # Multiple tipe → tampilkan selector
    lines = [f"🛍 *{item['nama']}*\n\nPilih tipe:"]
    kb_rows = []
    row = []
    for tid, t in tipe_dict.items():
        stok = len(t.get("akun_list", []))
        icon = "✅" if stok > 0 else "❌"
        lines.append(f"{icon} *{t['nama']}* — Rp{t.get('harga',0):,} ({stok} stok)")
        if stok > 0:
            btn = _ikb(f"✅ {t['nama']} Rp{t.get('harga',0):,}", "✅", "success", callback_data=f"tipe_{pid}_{tid}")
        else:
            btn = _ikb(f"❌ {t['nama']} (habis)", "❌", "danger", callback_data="ignore")
        row.append(btn)
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([_ikb("🔙 Kembali ke Menu", "🔙", "danger", callback_data="back_to_produk")])

    text = "\n".join(lines)
    kb   = InlineKeyboardMarkup(kb_rows)
    plain, ents = _pe(text, "Markdown")

    if gambar or item.get("gambar_file_id"):
        try:
            fid = item.get("gambar_file_id")
            if fid:
                if ents:
                    await bot.send_photo(chat_id=chat_id, photo=fid,
                                         caption=plain, caption_entities=ents, reply_markup=kb)
                else:
                    await bot.send_photo(chat_id=chat_id, photo=fid,
                                         caption=text, reply_markup=kb, parse_mode="Markdown")
            else:
                base = gambar.lstrip("/")
                with open(base, "rb") as f:
                    if ents:
                        msg = await bot.send_photo(chat_id=chat_id, photo=InputFile(f),
                                             caption=plain, caption_entities=ents, reply_markup=kb)
                    else:
                        msg = await bot.send_photo(chat_id=chat_id, photo=InputFile(f),
                                             caption=text, reply_markup=kb, parse_mode="Markdown")
                try:
                    with produk_lock():
                        produk_all = load_produk()
                        if pid in produk_all:
                            produk_all[pid]["gambar_file_id"] = msg.photo[-1].file_id
                            save_produk(produk_all)
                except Exception:
                    pass
            return
        except Exception:
            pass
    if ents:
        await bot.send_message(chat_id=chat_id, text=plain, entities=ents, reply_markup=kb)
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="Markdown")


async def handle_produk_detail(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    pid    = query.data
    item   = produk.get(pid)

    tipe_dict  = item.get("tipe", {}) if item else {}
    total_stok = sum(len(t.get("akun_list",[])) for t in tipe_dict.values())

    if not item or total_stok <= 0:
        await query.answer("❌ Produk habis atau tidak tersedia", show_alert=True)
        return

    await query.message.delete()
    await _send_produk_with_tipe(context.bot, query.from_user.id, pid, item, context)


async def handle_tipe_select(update: Update, context: CallbackContext):
    """Callback tipe_{pid}_{tid} — user pilih tipe dari selector."""
    query  = update.callback_query
    parts  = query.data.split("_", 2)  # ["tipe", pid, tid]
    if len(parts) != 3:
        await query.answer("Data tidak valid")
        return
    _, pid, tid = parts

    produk = load_produk()
    item   = produk.get(pid)
    if not item or tid not in item.get("tipe", {}):
        await query.answer("❌ Tipe tidak ditemukan", show_alert=True)
        return

    tipe_obj = item["tipe"][tid]
    stok     = len(tipe_obj.get("akun_list", []))
    if stok <= 0:
        await query.answer("❌ Tipe ini habis", show_alert=True)
        return

    context.user_data["konfirmasi"] = {"produk_id": pid, "tipe_id": tid, "jumlah": 1}
    order_txt = _order_text(item, 1, tipe_obj)
    kb        = _order_keyboard(1)
    await safe_edit(query, context, order_txt, reply_markup=kb)


def _get_tipe_from_info(produk: dict, info: dict):
    """Ambil tipe_obj dari produk berdasarkan info konfirmasi."""
    item = produk.get(info.get("produk_id", ""))
    if not item:
        return None, None
    tipe_id  = info.get("tipe_id") or next(iter(item.get("tipe", {})), None)
    tipe_obj = item.get("tipe", {}).get(tipe_id) if tipe_id else None
    return item, tipe_obj


async def handle_qty_plus(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    info   = context.user_data.get("konfirmasi")
    if not info:
        await query.answer("Data tidak tersedia")
        return

    item, tipe_obj = _get_tipe_from_info(produk, info)
    if not item or not tipe_obj:
        await query.answer("Produk tidak ditemukan")
        return

    stok   = len(tipe_obj.get("akun_list", []))
    jumlah = info["jumlah"]
    if jumlah < stok:
        jumlah += 1
    context.user_data["konfirmasi"]["jumlah"] = jumlah
    txt = _order_text(item, jumlah, tipe_obj)
    await safe_edit(query, context, txt, reply_markup=_order_keyboard(jumlah))


async def handle_qty_minus(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    info   = context.user_data.get("konfirmasi")
    if not info:
        await query.answer("Data tidak tersedia")
        return

    item, tipe_obj = _get_tipe_from_info(produk, info)
    if not item or not tipe_obj:
        await query.answer("Produk tidak ditemukan")
        return

    jumlah = info["jumlah"]
    if jumlah > 1:
        jumlah -= 1
    context.user_data["konfirmasi"]["jumlah"] = jumlah
    txt = _order_text(item, jumlah, tipe_obj)
    await safe_edit(query, context, txt, reply_markup=_order_keyboard(jumlah))


async def handle_confirm_order(update: Update, context: CallbackContext):
    """Tampilkan pilihan metode pembayaran sebelum memproses pembelian."""
    query = update.callback_query
    info  = context.user_data.get("konfirmasi")

    if not info:
        await query.answer("❌ Data pesanan tidak ditemukan", show_alert=True)
        return

    produk = load_produk()
    item, tipe_obj = _get_tipe_from_info(produk, info)
    if not item or not tipe_obj:
        try:
            await query.edit_message_text("❌ Produk/tipe tidak ditemukan.")
        except Exception:
            try:
                await query.edit_message_caption("❌ Produk/tipe tidak ditemukan.")
            except Exception:
                pass
        return

    jumlah = info["jumlah"]
    harga  = tipe_obj.get("harga", 0)
    total  = jumlah * harga
    stok   = len(tipe_obj.get("akun_list", []))

    if stok < jumlah:
        try:
            await query.edit_message_text("❌ Stok tidak mencukupi. Silakan pilih jumlah lebih sedikit.")
        except Exception:
            try:
                await query.edit_message_caption("❌ Stok tidak mencukupi.")
            except Exception:
                pass
        return

    saldo_user  = db_get_saldo(query.from_user.id)
    nama_tipe   = tipe_obj.get("nama", "")
    voucher_kode = context.user_data.get("voucher_kode", "")
    diskon_vc    = context.user_data.get("voucher_diskon", 0)
    total_bayar  = max(0, total - diskon_vc)

    vc_line = ""
    if voucher_kode and diskon_vc:
        vc_line = f"\n🏷 Voucher `{voucher_kode}`: -Rp{diskon_vc:,}"

    msg_text = (
        f"💳 *Pilih metode pembayaran*\n\n"
        f"📦 {item['nama']} [{nama_tipe}] x{jumlah}\n"
        f"💸 Total: *Rp{total_bayar:,}*" + vc_line + f"\n"
        f"💰 Saldo kamu: Rp{saldo_user:,}"
    )

    if _qris_available():
        kb = []
        if saldo_user >= total_bayar:
            kb.append([_ikb(
                f"💰 Bayar dengan Saldo (Rp{saldo_user:,})", "💰", "success",
                callback_data="confirm_saldo"
            )])
        kb.append([_ikb("💳 Bayar via QRIS (Otomatis)", "💳", "primary", callback_data="beli_qris")])
        if saldo_user < total_bayar:
            kb.append([_ikb("💰 Top Up Saldo dulu", "💰", "primary", callback_data="deposit")])
        if voucher_kode:
            kb.append([_ikb("🔄 Ganti Voucher", "🔄", "primary", callback_data="voucher_input")])
        else:
            kb.append([_ikb("🏷 Masukkan Voucher (opsional)", "🏷", "primary", callback_data="voucher_input")])
        kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="list_produk")])
        await safe_edit(query, context, msg_text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await _proses_beli_saldo(query, context, info, item, tipe_obj, jumlah, total, saldo_user)


async def handle_confirm_saldo(update: Update, context: CallbackContext):
    """Proses pembelian menggunakan saldo (dipanggil setelah user pilih metode Saldo)."""
    query = update.callback_query
    info  = context.user_data.get("konfirmasi")

    if not info:
        await query.answer("❌ Data pesanan tidak ditemukan", show_alert=True)
        return

    produk     = load_produk()
    item, tipe_obj = _get_tipe_from_info(produk, info)
    if not item or not tipe_obj:
        try:
            await query.edit_message_text("❌ Produk/tipe tidak ditemukan.")
        except Exception:
            pass
        return
    jumlah     = info["jumlah"]
    harga_satuan = tipe_obj.get("harga", 0)
    total      = jumlah * harga_satuan
    saldo_user = db_get_saldo(query.from_user.id)

    # Terapkan voucher jika ada
    voucher_kode = context.user_data.pop("voucher_kode", "")
    diskon_vc    = context.user_data.pop("voucher_diskon", 0)
    uid_str      = str(query.from_user.id)
    if voucher_kode and diskon_vc:
        v_check = db_use_voucher(voucher_kode, uid_str)
        if isinstance(v_check, int):
            total = max(0, total - v_check)
        else:
            voucher_kode = ""
            diskon_vc    = 0

    await _proses_beli_saldo(query, context, info, item, tipe_obj, jumlah, total, saldo_user,
                             voucher_kode=voucher_kode, diskon_vc=diskon_vc)


async def _proses_beli_saldo(query, context, info, item, tipe_obj, jumlah, total, saldo_user,
                             voucher_kode: str = "", diskon_vc: int = 0):
    """Logika inti pembelian menggunakan saldo — dipanggil setelah metode dipilih."""
    uid       = str(query.from_user.id)
    produk_id = info["produk_id"]
    tipe_id   = info.get("tipe_id") or next(iter(item.get("tipe", {})), None)

    if saldo_user < total:
        kb_rows = [
            [_ikb("💰 Deposit Saldo", "💰", "primary", callback_data="deposit")],
        ]
        if _qris_available():
            kb_rows.append([_ikb("💳 Bayar via QRIS (Otomatis)", "💳", "primary", callback_data="beli_qris")])
        kb_rows.append([_ikb("🔙 Kembali ke Menu", "🔙", "danger", callback_data="back_to_produk")])
        await safe_edit(query, context,
                        "❌ *Saldo tidak cukup.*\nSilakan deposit atau bayar langsung via QRIS.",
                        reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    async with purchase_lock:
        with produk_lock():
            produk = load_produk()
            item   = produk.get(produk_id)
            if not item or tipe_id not in item.get("tipe", {}):
                try:
                    await query.edit_message_text("❌ Produk tidak ditemukan.")
                except Exception:
                    pass
                return
            tipe_now  = item["tipe"][tipe_id]
            akun_list = tipe_now.get("akun_list", [])
            if len(akun_list) < jumlah:
                try:
                    await query.edit_message_text("❌ Stok tidak mencukupi saat diproses. Coba lagi.")
                except Exception:
                    pass
                return

            new_saldo     = db_add_saldo(uid, -total)
            akun_terpakai = [tipe_now["akun_list"].pop(0) for _ in range(jumlah)]
            tipe_now["stok"] = len(tipe_now["akun_list"])
            save_produk(produk)
        nama_tipe = tipe_obj.get("nama", "")
        ket_vc = f" [Voucher -{diskon_vc:,}]" if diskon_vc else ""
        trx_id = db_add_riwayat(uid, "BELI", f"{item['nama']} [{nama_tipe}] x{jumlah}{ket_vc}", total)

        os.makedirs("akun_dikirim", exist_ok=True)
        stamp     = int(time.time())
        file_path = f"akun_dikirim/{uid}_{produk_id}_x{jumlah}_{stamp}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            for i, akun in enumerate(akun_terpakai, start=1):
                f.write(_fmt_akun(akun, i, markup=False))

    # Ambil deskripsi tipe
    tipe_desc = tipe_obj.get("deskripsi", "").strip()

    # Teks detail akun
    waktu_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    text_akun = (
        f"✅ *Pembelian Berhasil!*\n\n"
        f"📦 {item['nama']} [{nama_tipe}] x{jumlah}\n"
        + (f"🏷 Diskon Voucher: -Rp{diskon_vc:,}\n" if diskon_vc else "") +
        f"💸 Dipotong: Rp{total:,}\n"
        f"💰 Sisa saldo: Rp{new_saldo:,}\n"
        f"🔖 ID Transaksi: `{trx_id}`\n"
        f"📅 {waktu_str}\n"
        f"─────────────────────\n"
    )
    for i, akun in enumerate(akun_terpakai, start=1):
        text_akun += _fmt_akun(akun, i, markup=True)
    if tipe_desc:
        text_akun += f"\n📋 *Catatan:*\n{tipe_desc}\n"

    # Kirim akun + pin pesan agar tidak tenggelam
    plain_akun, ents_akun = _pe(text_akun, "Markdown")
    if ents_akun:
        sent_akun = await context.bot.send_message(
            chat_id=query.from_user.id, text=plain_akun, entities=ents_akun)
    else:
        sent_akun = await context.bot.send_message(
            chat_id=query.from_user.id, text=text_akun, parse_mode="Markdown")
    try:
        await context.bot.pin_chat_message(
            chat_id=query.from_user.id, message_id=sent_akun.message_id,
            disable_notification=True)
    except Exception:
        pass

    # Tawarkan file .txt (opsional — tidak otomatis dikirim)
    await _send_file_offer(context.bot, query.from_user.id, file_path,
                           item['nama'], context.application.job_queue)

    # Log ke grup admin
    await _notify_group(
        context.bot,
        f"🛒 *Penjualan Baru*\n"
        f"👤 @{query.from_user.username or '-'} (`{query.from_user.id}`)\n"
        f"📦 {item['nama']} [{nama_tipe}] x{jumlah}\n"
        f"💸 Rp{total:,} | Sisa saldo: Rp{new_saldo:,}"
    )

    sisa_stok = len(tipe_now.get("akun_list", []))
    if 0 < sisa_stok <= LOW_STOCK_THRESHOLD:
        alert_stok = (
            f"⚠️ *STOK RENDAH — Segera Restock!*\n"
            f"📦 {item['nama']} [{nama_tipe}]\n"
            f"Sisa stok: *{sisa_stok}x*"
        )
        for admin_id in ADMIN_IDS:
            try:
                await _send_pe(context.bot, admin_id, alert_stok)
            except Exception:
                pass

    context.user_data.pop("konfirmasi", None)

    # Referral bonus trigger — saat pembelian pertama user
    try:
        user_data_w = web_get_user_by_tid(int(uid))
        if user_data_w and user_data_w.get("referral_by") and db_is_first_purchase(int(uid)):
            cfg_r = load_config()
            if cfg_r.get("referral_aktif"):
                referrer_tid = int(user_data_w["referral_by"])
                bonus = int(cfg_r.get("referral_bonus", 5000))
                if cfg_r.get("referral_konfirmasi","otomatis") == "otomatis":
                    db_add_saldo(referrer_tid, bonus)
                    db_add_riwayat(referrer_tid, "REFERRAL",
                                   f"Bonus referral (user {uid})", bonus)
                    send_telegram(referrer_tid,
                        f"🎉 *Bonus Referral!*\nKamu mendapat *Rp{bonus:,}* karena teman "
                        f"kamu baru saja melakukan pembelian pertama!")
                else:
                    db_referral_create(referrer_tid, int(uid), bonus)
                    send_telegram(referrer_tid,
                        f"🎉 Ada bonus referral *Rp{bonus:,}* menunggu konfirmasi admin.")
    except Exception:
        pass

    await send_main_menu(context, query.from_user.id, query.from_user)


# ─── FILE OFFER ──────────────────────────────────────────────────────────────

async def _send_file_offer(bot, user_id: int, file_path: str, item_name: str, job_queue):
    """Tawari user file .txt — jika tidak dijawab 15 menit dianggap tidak mau."""
    job_name = f"file_offer_{user_id}"

    # Cancel job lama jika ada
    if job_queue:
        for j in job_queue.get_jobs_by_name(job_name):
            j.schedule_removal()

    kb = InlineKeyboardMarkup([[
        _ikb("✅ Ya, kirim file .txt", "✅", "success", callback_data="file_txt_ya"),
        _ikb("❌ Tidak", "❌", "danger", callback_data="file_txt_tidak"),
    ]])
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text="📎 *Mau file backup akun dalam format .txt?*\n_(Pilih dalam 15 menit, jika tidak dijawab dianggap tidak mau)_",
            parse_mode="Markdown",
            reply_markup=kb
        )
        _pending_file_offers[user_id] = {
            "file_path": file_path,
            "item_name": item_name,
            "msg_id": sent.message_id,
            "chat_id": user_id,
            "job_name": job_name,
        }
        if job_queue:
            job_queue.run_once(
                _expire_file_offer,
                when=900,
                data={"user_id": user_id},
                name=job_name
            )
    except Exception:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass


async def _expire_file_offer(context):
    """Job: 15 menit habis tanpa jawaban → hapus file offer."""
    user_id = context.job.data.get("user_id")
    offer   = _pending_file_offers.pop(user_id, None)
    if not offer:
        return
    if offer.get("file_path") and os.path.exists(offer["file_path"]):
        try:
            os.remove(offer["file_path"])
        except OSError:
            pass
    try:
        await context.bot.delete_message(chat_id=offer["chat_id"], message_id=offer["msg_id"])
    except Exception:
        pass


async def handle_file_txt_ya(update: Update, context: CallbackContext):
    """User mau file .txt backup."""
    query   = update.callback_query
    uid     = query.from_user.id
    offer   = _pending_file_offers.pop(uid, None)

    # Cancel expiry job
    for j in context.application.job_queue.get_jobs_by_name(offer["job_name"] if offer else f"file_offer_{uid}"):
        j.schedule_removal()

    try:
        await query.message.delete()
    except Exception:
        pass

    if not offer or not os.path.exists(offer.get("file_path", "")):
        await query.answer("❌ File tidak tersedia lagi.", show_alert=True)
        return

    fname = f"akun_{offer['item_name'].replace(' ', '_')}.txt"
    try:
        with open(offer["file_path"], "rb") as f:
            await context.bot.send_document(
                chat_id=uid,
                document=InputFile(f, filename=fname),
                caption="📎 File backup akun kamu."
            )
    except Exception:
        await query.answer("❌ Gagal kirim file.", show_alert=True)
    finally:
        try:
            os.remove(offer["file_path"])
        except OSError:
            pass


async def handle_file_txt_tidak(update: Update, context: CallbackContext):
    """User tidak mau file .txt backup."""
    query = update.callback_query
    uid   = query.from_user.id
    offer = _pending_file_offers.pop(uid, None)

    for j in context.application.job_queue.get_jobs_by_name(offer["job_name"] if offer else f"file_offer_{uid}"):
        j.schedule_removal()

    if offer and os.path.exists(offer.get("file_path", "")):
        try:
            os.remove(offer["file_path"])
        except OSError:
            pass
    try:
        await query.message.delete()
    except Exception:
        pass


# ─── DEPOSIT ─────────────────────────────────────────────────────────────────

async def handle_deposit(update: Update, context: CallbackContext):
    query         = update.callback_query
    qris_tersedia = _qris_available()
    keyboard      = [[_ikb(f"Rp{n:,}", "", "primary", callback_data=f"deposit_{n}") for n in DEPOSIT_NOMINALS]]
    keyboard.append([_ikb("🔧 Custom Nominal", "🔧", "primary", callback_data="deposit_custom")])
    keyboard.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="back_to_menu")])
    qris_note = "\n✅ _QRIS tersedia — pilih nominal lalu pilih metode!_" if qris_tersedia else ""
    text = (
        f"💰 *Pilih nominal deposit:*\n"
        f"_(Min: Rp{DEPOSIT_MIN:,} | Max: Rp{DEPOSIT_MAX:,})_{qris_note}"
    )
    markup = InlineKeyboardMarkup(keyboard)
    await safe_edit(query, context, text, reply_markup=markup)


async def _send_deposit_instructions(target, context, nominal: int, is_message=True):
    total    = nominal + 23
    rekening = load_config().get("rekening", [])
    rek_text = "\n".join(f"`{r}`" for r in rekening)
    text  = (
        f"💳 Transfer *Rp{total:,}* ke salah satu rekening:\n\n"
        f"{rek_text}\n\n"
        "📸 Setelah transfer, kirim *foto bukti transfer* ke sini."
    )
    kb = ReplyKeyboardMarkup([[KeyboardButton("❌ Batalkan Deposit")]], resize_keyboard=True, one_time_keyboard=True)
    if is_message:
        plain_d, ents_d = _pe(text, "Markdown")
        if ents_d:
            await target.reply_text(plain_d, entities=ents_d, reply_markup=kb)
        else:
            await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        try:
            await target.message.delete()
        except Exception:
            pass
        await _send_pe(context.bot, target.from_user.id, text, reply_markup=kb)


async def handle_deposit_nominal(update: Update, context: CallbackContext):
    query = update.callback_query
    data  = query.data

    if data == "deposit_custom":
        context.user_data["awaiting_custom"] = True
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"💬 Ketik jumlah deposit (angka saja):\n_Min: Rp{DEPOSIT_MIN:,} | Max: Rp{DEPOSIT_MAX:,}_",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batalkan Deposit")]], resize_keyboard=True, one_time_keyboard=True)
        )
        return

    nominal = int(data.split("_")[1])
    context.user_data["nominal_asli"]   = nominal
    context.user_data["total_transfer"] = nominal + 23
    await _show_metode_deposit(query, context, nominal)


async def _show_metode_deposit(query_or_message, context, nominal: int):
    """Tampilkan pilihan metode: Manual Transfer atau QRIS (toggle dari config)."""
    qris_tersedia   = _qris_available()
    cfg             = load_config()
    manual_aktif    = cfg.get("transfer_manual_aktif", True)
    kb = []
    if qris_tersedia:
        kb.append([_ikb("💳 QRIS (Otomatis / Lebih Cepat)", "", "primary", callback_data=f"dep_qris_{nominal}")])
    if manual_aktif:
        kb.append([_ikb("🏦 Transfer Manual (Konfirmasi Admin)", "🏦", "primary", callback_data=f"dep_manual_{nominal}")])
    if not kb:
        kb.append([_ikb("❌ Metode deposit sedang tidak tersedia", "❌", "danger", callback_data="ignore")])
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="deposit")])

    hints = []
    if qris_tersedia:
        hints.append("✅ *QRIS* — dikonfirmasi otomatis setelah bayar")
    if manual_aktif:
        hints.append("🏦 *Transfer Manual* — perlu foto bukti & konfirmasi admin")
    text = (
        f"💰 *Pilih metode pembayaran*\n\n"
        f"Nominal: *Rp{nominal:,}*\n\n"
        + ("\n".join(hints) if hints else "_Tidak ada metode aktif saat ini._")
    )

    try:
        plain_m, ents_m = _pe(text, "Markdown")
        if ents_m:
            await query_or_message.edit_message_text(plain_m, entities=ents_m, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query_or_message.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        try:
            await query_or_message.message.delete()
        except Exception:
            pass
        await _send_pe(context.bot, query_or_message.from_user.id, text, reply_markup=InlineKeyboardMarkup(kb))


async def handle_dep_metode(update: Update, context: CallbackContext):
    """Callback dep_manual_XXXXX atau dep_qris_XXXXX — arahkan ke metode yang dipilih."""
    query = update.callback_query
    data  = query.data  # dep_manual_10000 atau dep_qris_10000

    parts   = data.split("_", 2)   # ['dep', 'manual'/'qris', '10000']
    metode  = parts[1]
    nominal = int(parts[2])

    context.user_data["nominal_asli"]   = nominal
    context.user_data["total_transfer"] = nominal + 23

    if metode == "qris":
        await _show_qris_deposit(query.from_user, nominal, context, delete_msg=query.message)
    else:
        await _send_deposit_instructions(query, context, nominal, is_message=False)


async def handle_cancel_deposit(update: Update, context: CallbackContext):
    query = update.callback_query
    uid   = str(query.from_user.id)
    db_remove_pending_any_by_user(uid)
    context.user_data.pop("nominal_asli",   None)
    context.user_data.pop("total_transfer", None)
    context.user_data.pop("awaiting_custom", None)
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_main_menu(context, query.from_user.id, query.from_user)


async def handle_deposit_qris(update: Update, context: CallbackContext):
    """User memilih QRIS untuk deposit — tampilkan pilihan nominal dulu."""
    query = update.callback_query
    if not _qris_available():
        await query.answer("❌ QRIS belum diatur admin.", show_alert=True)
        return
    keyboard = [[_ikb(f"Rp{n:,}", "", "primary", callback_data=f"qris_dep_{n}") for n in DEPOSIT_NOMINALS]]
    keyboard.append([_ikb("🔧 Custom Nominal", "🔧", "primary", callback_data="qris_dep_custom")])
    keyboard.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="deposit")])
    await safe_edit(
        query, context,
        f"💳 *Deposit via QRIS*\n_(Min: Rp{DEPOSIT_MIN:,} | Max: Rp{DEPOSIT_MAX:,})_\n\nPilih nominal deposit:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_qris_dep_nominal(update: Update, context: CallbackContext):
    """User memilih nominal QRIS deposit — tampilkan QR code dengan kode unik."""
    query = update.callback_query
    data  = query.data

    if data == "qris_dep_custom":
        context.user_data["awaiting_qris_custom"] = True
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"💬 Ketik jumlah deposit QRIS (angka saja):\n_Min: Rp{DEPOSIT_MIN:,} | Max: Rp{DEPOSIT_MAX:,}_",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batalkan Deposit")]], resize_keyboard=True, one_time_keyboard=True)
        )
        return

    nominal = int(data.split("_")[2])
    await _show_qris_deposit(query.from_user, nominal, context, delete_msg=query.message)


async def _show_qris_deposit(user, nominal: int, context, delete_msg=None):
    """Tampilkan QRIS dengan kode unik dan simpan ke pending."""
    if not _qris_available():
        await context.bot.send_message(
            chat_id=user.id,
            text="❌ QRIS belum dikonfigurasi admin."
        )
        return

    kode     = _generate_kode_unik(nominal)
    expected = nominal + kode
    log.info(f"📲 QRIS Deposit: user={user.id} (@{user.username}) nominal=Rp{nominal:,} expected=Rp{expected:,}")

    db_remove_pending_by_user(user.id)
    db_add_pending({
        "user_id":         user.id,
        "username":        user.username,
        "metode":          "qris",
        "nominal":         nominal,
        "expected_amount": expected,
        "kode_unik":       kode,
        "waktu":           datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "cek_count":       3,
    })

    caption = (
        f"💳 *Bayar via QRIS*\n\n"
        f"💰 Nominal deposit: Rp{nominal:,}\n"
        f"🔢 Transfer tepat *Rp{expected:,}*\n"
        f"_(nominal + kode unik Rp{kode} untuk identifikasi)_\n\n"
        f"⏰ Batas waktu: {QRIS_EXPIRY_MINUTES} menit\n"
        f"✅ Akan dikonfirmasi *otomatis* setelah pembayaran terdeteksi."
    )

    if delete_msg:
        try:
            await delete_msg.delete()
        except Exception:
            pass

    kb = InlineKeyboardMarkup([[
        _ikb("🔄 Cek Sekarang (3x tersisa)", "", "primary", callback_data="cek_mutasi")
    ]])
    plain_c, ents_c = _pe(caption, "Markdown")
    msg = await _send_qris_photo(
        context.bot, user.id, nominal, kode,
        caption=plain_c, reply_markup=kb,
        caption_entities=ents_c if ents_c else None
    )
    if msg:
        db_update_pending_msg_id(user.id, msg.message_id)


async def handle_beli_qris(update: Update, context: CallbackContext):
    """User memilih bayar via QRIS langsung untuk pembelian."""
    query = update.callback_query
    info  = context.user_data.get("konfirmasi")
    if not info:
        await query.answer("❌ Data pesanan tidak ditemukan", show_alert=True)
        return
    if not _qris_available():
        await query.answer("❌ QRIS belum diatur admin.", show_alert=True)
        return

    jumlah  = info["jumlah"]
    tipe_id = info.get("tipe_id")

    async with purchase_lock:
        with produk_lock():
            produk = load_produk()
            item   = produk.get(info["produk_id"])
            if not item:
                await query.answer("❌ Produk tidak ditemukan", show_alert=True)
                return
            tipe_id = tipe_id or next(iter(item.get("tipe", {})), None)
            if not tipe_id or tipe_id not in item.get("tipe", {}):
                await query.answer("❌ Tipe tidak ditemukan", show_alert=True)
                return
            tipe_obj = item["tipe"][tipe_id]
            if len(tipe_obj.get("akun_list", [])) < jumlah:
                await query.answer("❌ Stok tidak mencukupi", show_alert=True)
                return
            # Reservasi stok
            reserved_akun = [tipe_obj["akun_list"].pop(0) for _ in range(jumlah)]
            tipe_obj["stok"] = len(tipe_obj["akun_list"])
            save_produk(produk)
            log.info(f"🔒 Stok direservasi: {len(reserved_akun)} akun {item['nama']} untuk user {query.from_user.id}")

    nama_tipe = tipe_obj.get("nama", "")
    nominal   = jumlah * tipe_obj.get("harga", 0)
    kode      = _generate_kode_unik(nominal)
    expected  = nominal + kode
    log.info(f"🛒 QRIS Beli: user={query.from_user.id} produk={item['nama']} [{nama_tipe}] x{jumlah} nominal=Rp{nominal:,} expected=Rp{expected:,}")

    db_remove_pending_by_user(query.from_user.id)
    db_add_pending({
        "user_id":         query.from_user.id,
        "username":        query.from_user.username,
        "metode":          "qris_beli",
        "produk_id":       info["produk_id"],
        "tipe_id":         tipe_id,
        "jumlah":          jumlah,
        "nominal":         nominal,
        "expected_amount": expected,
        "kode_unik":       kode,
        "waktu":           datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "cek_count":       3,
        "reserved_akun":   reserved_akun,
    })
    context.user_data.pop("konfirmasi", None)

    caption = (
        f"💳 *Bayar via QRIS*\n\n"
        f"📦 {item['nama']} [{nama_tipe}] x{jumlah}\n"
        f"💰 Total: Rp{nominal:,}\n"
        f"🔢 Transfer tepat *Rp{expected:,}*\n"
        f"_(nominal + kode unik Rp{kode})_\n\n"
        f"⏰ Batas waktu: {QRIS_EXPIRY_MINUTES} menit\n"
        f"✅ Akun akan dikirim *otomatis* setelah pembayaran terdeteksi."
    )

    try:
        await query.message.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup([[
        _ikb("🔄 Cek Sekarang (3x tersisa)", "", "primary", callback_data="cek_mutasi")
    ]])
    plain_c, ents_c = _pe(caption, "Markdown")
    msg = await _send_qris_photo(
        context.bot, query.from_user.id, nominal, kode,
        caption=plain_c, reply_markup=kb,
        caption_entities=ents_c if ents_c else None
    )
    if msg:
        db_update_pending_msg_id(query.from_user.id, msg.message_id)


# ─── QRIS: CEK SEKARANG (manual trigger) ─────────────────────────────────────

async def handle_cek_mutasi(update: Update, context: CallbackContext):
    """User klik tombol 'Cek Sekarang' — cek mutasi langsung, max 3x per pending."""
    query = update.callback_query
    uid   = query.from_user.id

    user_pending = db_get_pending_by_user(uid)
    if not user_pending:
        await query.answer("✅ Tidak ada pembayaran aktif.", show_alert=True)
        return

    cek_count = user_pending.get("cek_count", 3)
    if cek_count <= 0:
        log.info(f"🚫 Cek manual ditolak (habis): user={uid}")
        await query.answer("❌ Batas cek manual (3x) habis. Tunggu cek otomatis setiap 30 detik.", show_alert=True)
        return

    log.info(f"🔍 Cek manual oleh user={uid} (sisa {cek_count-1}x setelah ini)")
    await query.answer("🔍 Mengecek pembayaran...")

    # Kurangi counter & simpan
    db_update_pending_cek_count(uid, cek_count - 1)

    # Jalankan cek mutasi sekarang + reset timer 30-detik loop
    await proses_mutasi(context.application)
    _manual_check_event.set()

    # Cek apakah pembayaran sudah terkonfirmasi
    still_pending = db_get_pending_by_user(uid) is not None
    if not still_pending:
        return  # Sudah terkonfirmasi — proses_mutasi sudah kirim pesan sukses

    new_count = cek_count - 1
    if new_count > 0:
        label = f"🔄 Cek Sekarang ({new_count}x tersisa)"
    else:
        label = "⏳ Menunggu... (ketuk untuk info)"
    kb = InlineKeyboardMarkup([[
        _ikb(label, "", "primary", callback_data="cek_mutasi")
    ]])
    try:
        await query.edit_message_reply_markup(reply_markup=kb)
    except Exception:
        pass


# ─── RIWAYAT USER ─────────────────────────────────────────────────────────────

async def _show_riwayat(update: Update, context: CallbackContext, filter_tipe: str = "semua"):
    """Tampilkan riwayat transaksi user dengan filter."""
    if update.callback_query:
        user_id = update.callback_query.from_user.id
    else:
        user_id = update.effective_user.id

    semua  = db_get_riwayat(user_id, 50)

    if filter_tipe == "beli":
        data   = [r for r in semua if r["tipe"] in ("BELI", "BELI_QRIS")]
        judul  = "🛒 *Riwayat Pembelian*"
    elif filter_tipe == "deposit":
        data   = [r for r in semua if "DEPOSIT" in r["tipe"] or r["tipe"] == "KURANGI"]
        judul  = "💰 *Riwayat Deposit / Saldo*"
    else:
        data   = semua
        judul  = "📜 *Riwayat Mutasi*"

    data = data[:15]   # tampilkan max 15 item

    if not data:
        text = f"{judul}\n\n_Belum ada transaksi._"
    else:
        text = f"{judul} _(last {len(data)})_\n\n"
        for r in data:
            if "DEPOSIT" in r["tipe"]:
                icon = "💰"
            elif r["tipe"] == "KURANGI":
                icon = "➖"
            else:
                icon = "🛒"
            tipe_str = r["tipe"].replace("_", " ")
            trx  = f"\n   🔖 `{r['trx_id']}`" if r.get("trx_id") else ""
            text += f"{icon} *{tipe_str}* — Rp{r['jumlah']:,}\n"
            text += f"   _{r['keterangan']}_\n"
            text += f"   🕐 {r['waktu']}{trx}\n\n"

    # Batas char Telegram
    if len(text) > 3800:
        text = text[:3750] + "\n\n_...dan lebih lagi_"

    kb = InlineKeyboardMarkup([
        [
            _ikb("📋 Semua",     "📋", "primary" if filter_tipe == "semua"   else "danger", callback_data="riwayat_user"),
            _ikb("🛒 Pembelian", "🛒", "success" if filter_tipe == "beli"    else "danger", callback_data="riwayat_beli"),
            _ikb("💰 Deposit",   "💰", "primary" if filter_tipe == "deposit" else "danger", callback_data="riwayat_deposit"),
        ],
        [_ikb("🔙 Kembali ke Menu", "🔙", "danger", callback_data="back_to_menu")],
    ])

    if update.callback_query:
        await safe_edit(update.callback_query, context, text, reply_markup=kb)
    else:
        await _send_pe(context.bot, update.effective_chat.id, text, reply_markup=kb)


async def handle_riwayat_user(update: Update, context: CallbackContext):
    """Tampilkan riwayat transaksi user (via button atau command /riwayat)."""
    await _show_riwayat(update, context, filter_tipe="semua")


async def handle_riwayat_beli(update: Update, context: CallbackContext):
    """Riwayat pembelian saja."""
    await _show_riwayat(update, context, filter_tipe="beli")


async def handle_riwayat_deposit(update: Update, context: CallbackContext):
    """Riwayat deposit/saldo saja."""
    await _show_riwayat(update, context, filter_tipe="deposit")


async def cmd_riwayat(update: Update, context: CallbackContext):
    await handle_riwayat_user(update, context)


# ─── ADMIN PANEL ──────────────────────────────────────────────────────────────

async def handle_admin_panel(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return

    saldo       = db_get_all_saldo()
    pending     = db_get_all_pending()
    all_bot     = db_get_all_bot_users()
    bot_cnt     = len(all_bot)
    uid_uname   = {str(u["telegram_id"]): u.get("username") for u in all_bot}

    text  = f"*🛠 ADMIN PANEL*\n\n"
    text += f"👥 Total user bot: *{bot_cnt}*\n\n"
    text += "*📊 DATA USER (bersaldo):*\n"
    ada_saldo = False
    for u_id, s in saldo.items():
        if s <= 0:
            continue
        ada_saldo = True
        uname = uid_uname.get(str(u_id))
        label = f"@{uname} ({u_id})" if uname else f"`{u_id}`"
        text += f"  • {label}: Rp{s:,}\n"
    if not ada_saldo:
        text += "  _Belum ada user bersaldo._\n"

    text += "\n*⏳ PENDING DEPOSIT:*\n"
    if pending:
        for p in pending:
            text += f"  - @{p.get('username') or p['user_id']} (`{p['user_id']}`) → Rp{p['nominal']:,}\n"
    else:
        text += "  _Tidak ada._\n"

    keyboard = [
        [_ikb("📦 Kelola Produk",    "📦", "success", callback_data="admin_kelola_produk"),
         _ikb("📊 Rekap & Statistik","📊", "primary", callback_data="admin_rekap")],
        [_ikb("👥 Kelola User",      "👥", "primary", callback_data="admin_kelola_user"),
         _ikb("🎟 Kelola Voucher",   "🎟", "primary", callback_data="admin_kelola_voucher")],
        [_ikb("⚙️ Pengaturan Bot",   "⚙",  "primary", callback_data="admin_settings")],
        [_ikb("📢 Broadcast",         "📢", "primary", callback_data="admin_broadcast")],
        [_ikb("🔙 Kembali ke Menu",   "🔙", "danger",  callback_data="back_to_menu")],
    ]
    await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_admin_kelola_produk(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return

    produk = load_produk()
    lines  = []
    for pid, item in produk.items():
        tipe_dict  = item.get("tipe", {})
        total_stok = sum(len(t.get("akun_list", [])) for t in tipe_dict.values())
        icon = "🟢" if total_stok > 0 else "🔴"
        lines.append(f"{icon} *{item['nama']}* (ID:`{pid}`) — total {total_stok} stok")
        for tid, t in tipe_dict.items():
            stok_t = len(t.get("akun_list", []))
            harga_t = t.get("harga", 0)
            lines.append(f"  ↳ {t['nama']}: {stok_t} stok | Rp{harga_t:,}")
    text = f"*📦 KELOLA PRODUK*\n\n" + ("\n".join(lines) or "_Belum ada produk._")
    keyboard = [
        [_ikb("➕ Tambah Produk",    "➕", "success", callback_data="admin_add_produk"),
         _ikb("➕ Tambah Tipe",      "➕", "success", callback_data="admin_add_tipe")],
        [_ikb("📦 Restock",           "📦", "primary", callback_data="admin_restock_produk"),
         _ikb("💰 Ubah Harga",        "💰", "primary", callback_data="admin_ubah_harga_tipe")],
        [_ikb("✏️ Rename Produk",     "✏",  "primary", callback_data="admin_rename_produk"),
         _ikb("✏️ Rename Tipe",       "✏",  "primary", callback_data="admin_rename_tipe")],
        [_ikb("📝 Deskripsi Tipe",    "📝", "primary", callback_data="admin_edit_tipe_desc"),
         _ikb("🗑 Hapus Tipe",        "🗑", "danger",  callback_data="admin_hapus_tipe")],
        [_ikb("🗑 Hapus Akun (1x1)",  "🗑", "danger",  callback_data="admin_hapus_akun"),
         _ikb("🗑 Hapus Produk",      "🗑", "danger",  callback_data="admin_hapus_produk")],
        [_ikb("🔙 Kembali",           "🔙", "danger",  callback_data="admin_panel")],
    ]
    await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(keyboard))


# ─── ADMIN: DESKRIPSI TIPE ───────────────────────────────────────────────────

async def handle_admin_edit_tipe_desc(update: Update, context: CallbackContext):
    """Pilih produk untuk edit deskripsi tipe."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    kb_rows = []
    for pid, item in produk.items():
        total_stok = sum(len(t.get("akun_list",[])) for t in item.get("tipe",{}).values())
        em = "🟢" if total_stok > 0 else "🔴"
        kb_rows.append([_ikb(f"{em} {item['nama']}", em, "primary", callback_data=f"edesc_p_{pid}")])
    kb_rows.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])
    await safe_edit(query, context, "📝 *Edit Deskripsi Tipe*\n\nPilih produk:", reply_markup=InlineKeyboardMarkup(kb_rows))


async def handle_edesc_produk_sel(update: Update, context: CallbackContext):
    """Pilih tipe dari produk yang dipilih."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    pid   = query.data.replace("edesc_p_", "")
    produk = load_produk()
    if pid not in produk:
        await query.answer("Produk tidak ditemukan!", show_alert=True)
        return
    item = produk[pid]
    kb_rows = []
    for tid, t in item.get("tipe", {}).items():
        desc_preview = f" — {t['deskripsi'][:20]}…" if t.get("deskripsi") else " — (kosong)"
        kb_rows.append([_ikb(f"{t['nama']}{desc_preview}", "", "primary", callback_data=f"edesc_t_{pid}_{tid}")])
    kb_rows.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_edit_tipe_desc")])
    await safe_edit(query, context, f"📝 *{item['nama']}*\n\nPilih tipe:", reply_markup=InlineKeyboardMarkup(kb_rows))


async def handle_edesc_tipe_sel(update: Update, context: CallbackContext):
    """Tampilkan deskripsi tipe saat ini dan minta input baru."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    parts = query.data.replace("edesc_t_", "").split("_", 1)
    if len(parts) != 2:
        await query.answer("Data tidak valid", show_alert=True)
        return
    pid, tid = parts
    produk = load_produk()
    if pid not in produk or tid not in produk[pid].get("tipe", {}):
        await query.answer("Tipe tidak ditemukan!", show_alert=True)
        return
    tipe_obj   = produk[pid]["tipe"][tid]
    current_d  = tipe_obj.get("deskripsi", "").strip() or "_(kosong)_"
    context.user_data["edit_tipe_desc"] = {"pid": pid, "tid": tid}
    context.user_data["admin_state"]    = "edit_tipe_desc_input"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            f"📝 *Edit Deskripsi Tipe*\n\n"
            f"Produk : *{produk[pid]['nama']}*\n"
            f"Tipe   : *{tipe_obj['nama']}*\n\n"
            f"Deskripsi saat ini:\n{current_d}\n\n"
            "Ketik deskripsi baru, atau ketik `-` untuk menghapus deskripsi:"
        ),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── ADMIN: TAMBAH PRODUK ────────────────────────────────────────────────────

async def handle_admin_add_produk(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "add_nama"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="➕ *TAMBAH PRODUK*\n\nKetik *nama produk*:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


async def handle_admin_restock_produk(update: Update, context: CallbackContext):
    """Tampilkan daftar produk sebagai inline keyboard untuk dipilih di-restock."""
    query  = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return

    btn_row, kb_rows = [], []
    for nomor, (pid, item) in enumerate(produk.items(), start=1):
        total_stok = sum(len(t.get("akun_list",[])) for t in item.get("tipe",{}).values())
        em        = _produk_emoji(item["nama"])
        btn_style = "success" if total_stok > 0 else "danger"
        btn_row.append(_ikb(f"{nomor}", em, btn_style, callback_data=f"restock_sel_{pid}"))
        if len(btn_row) == 5:
            kb_rows.append(btn_row)
            btn_row = []

    if btn_row:
        kb_rows.append(btn_row)
    kb_rows.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])

    lines = []
    for nomor, (pid, item) in enumerate(produk.items(), start=1):
        total_stok = sum(len(t.get("akun_list",[])) for t in item.get("tipe",{}).values())
        icon = "🟢" if total_stok > 0 else "🔴"
        lines.append(f"  {icon} [{nomor}] {item['nama']} (stok: {total_stok})")

    text = "📦 *RESTOCK PRODUK*\n\n" + "\n".join(lines) + "\n\nPilih nomor produk:"
    await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(kb_rows))


async def handle_restock_sel(update: Update, context: CallbackContext):
    """Handler: admin memilih produk untuk direstock via inline button."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    pid   = query.data.replace("restock_sel_", "")
    produk = load_produk()
    if pid not in produk:
        await query.answer("Produk tidak ditemukan!", show_alert=True)
        return

    tipe_dict = produk[pid].get("tipe", {})
    if len(tipe_dict) == 1:
        tipe_id   = list(tipe_dict.keys())[0]
        tipe_nm   = tipe_dict[tipe_id]["nama"]
        stok_saat = len(tipe_dict[tipe_id].get("akun_list", []))
        context.user_data["restock_pid"]     = pid
        context.user_data["restock_tipe_id"] = tipe_id
        context.user_data["admin_state"]     = "restock_akun"
        await safe_edit(query, context,
            f"📦 *Restock: {produk[pid]['nama']}*\n"
            f"Tipe: *{tipe_nm}* (stok: {stok_saat})\n\n"
            "Kirim akun baru, *satu per baris*:\n"
            "`email|password`\n\n_Contoh:_ `user@gmail.com|Pass123!`"
        )
    else:
        # Multi tipe → tampilkan pilihan tipe sebagai inline keyboard
        kb_rows = []
        for tid, t in tipe_dict.items():
            stok_t = len(t.get("akun_list", []))
            em_t   = "🟢" if stok_t > 0 else "🔴"
            style_t = "success" if stok_t > 0 else "danger"
            kb_rows.append([_ikb(f"{em_t} {t['nama']} (stok: {stok_t})", em_t, style_t,
                                  callback_data=f"restock_tipe_{pid}_{tid}")])
        kb_rows.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_restock_produk")])
        context.user_data["restock_pid"] = pid
        await safe_edit(query, context,
            f"📦 *{produk[pid]['nama']}*\n\nPilih tipe yang ingin direstock:",
            reply_markup=InlineKeyboardMarkup(kb_rows)
        )


async def handle_restock_tipe_sel(update: Update, context: CallbackContext):
    """Handler: admin memilih tipe produk untuk direstock via inline button."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    # data format: restock_tipe_{pid}_{tid}
    parts   = query.data.replace("restock_tipe_", "").split("_", 1)
    pid, tid = parts[0], parts[1]
    produk  = load_produk()
    if pid not in produk or tid not in produk[pid].get("tipe", {}):
        await query.answer("Tipe tidak ditemukan!", show_alert=True)
        return
    tipe_obj  = produk[pid]["tipe"][tid]
    stok_saat = len(tipe_obj.get("akun_list", []))
    context.user_data["restock_pid"]     = pid
    context.user_data["restock_tipe_id"] = tid
    context.user_data["admin_state"]     = "restock_akun"
    await safe_edit(query, context,
        f"📦 *Restock: {produk[pid]['nama']}*\n"
        f"Tipe: *{tipe_obj['nama']}* (stok: {stok_saat})\n\n"
        "Kirim akun baru, *satu per baris*:\n"
        "`email|password`\n\n_Contoh:_ `user@gmail.com|Pass123!`"
    )


async def handle_admin_hapus_produk(update: Update, context: CallbackContext):
    query  = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    context.user_data["admin_state"] = "hapus_pid"
    keyboard = [[KeyboardButton(f"{pid} - {item['nama']}")] for pid, item in produk.items()]
    keyboard.append([KeyboardButton("❌ Batal")])
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🗑 *HAPUS PRODUK*\n\nPilih ID produk yang ingin dihapus:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )


# ─── ADMIN: TAMBAH TIPE ──────────────────────────────────────────────────────

async def handle_admin_add_tipe(update: Update, context: CallbackContext):
    """Pilih produk untuk ditambahkan tipe baru."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    kb = [[_ikb(f"{item['nama']} (ID:{pid})", "", "primary", callback_data=f"addtipe_sel_{pid}")]
          for pid, item in produk.items()]
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])
    await safe_edit(query, context, "➕ *Tambah Tipe*\n\nPilih produk:", reply_markup=InlineKeyboardMarkup(kb))


async def handle_addtipe_sel(update: Update, context: CallbackContext):
    """Admin memilih produk untuk ditambahkan tipe baru."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    pid   = query.data.replace("addtipe_sel_", "")
    produk = load_produk()
    if pid not in produk:
        await query.answer("Produk tidak ditemukan!", show_alert=True)
        return
    context.user_data["add_tipe_pid"]   = pid
    context.user_data["admin_state"]    = "add_tipe_nama"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f"➕ *Tambah Tipe — {produk[pid]['nama']}*\n\nKetik *nama* tipe baru:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── ADMIN: UBAH HARGA TIPE ──────────────────────────────────────────────────

async def handle_admin_ubah_harga_tipe(update: Update, context: CallbackContext):
    """Pilih produk untuk ubah harga tipe."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    kb = [[_ikb(f"{item['nama']} (ID:{pid})", "", "primary", callback_data=f"uharga_sel_{pid}")]
          for pid, item in produk.items()]
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])
    await safe_edit(query, context, "💰 *Ubah Harga Tipe*\n\nPilih produk:", reply_markup=InlineKeyboardMarkup(kb))


async def handle_uharga_sel(update: Update, context: CallbackContext):
    """Pilih tipe dari produk yang dipilih untuk ubah harga."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    pid   = query.data.replace("uharga_sel_", "")
    produk = load_produk()
    if pid not in produk:
        await query.answer("Produk tidak ditemukan!", show_alert=True)
        return
    tipe_dict = produk[pid].get("tipe", {})
    kb = []
    for tid, t in tipe_dict.items():
        kb.append([_ikb(f"{t['nama']} — Rp{t.get('harga',0):,}", "", "primary",
                        callback_data=f"uharga_tid_{pid}_{tid}")])
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_ubah_harga_tipe")])
    await safe_edit(query, context, f"💰 *{produk[pid]['nama']}*\n\nPilih tipe:", reply_markup=InlineKeyboardMarkup(kb))


async def handle_uharga_tid(update: Update, context: CallbackContext):
    """Admin memilih tipe, minta input harga baru."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    parts = query.data.replace("uharga_tid_", "").split("_", 1)
    if len(parts) != 2:
        await query.answer("Data tidak valid", show_alert=True)
        return
    pid, tid = parts
    produk = load_produk()
    if pid not in produk or tid not in produk[pid].get("tipe", {}):
        await query.answer("Tipe tidak ditemukan!", show_alert=True)
        return
    tipe_obj = produk[pid]["tipe"][tid]
    context.user_data["ubah_harga_pid"]  = pid
    context.user_data["ubah_harga_tid"]  = tid
    context.user_data["admin_state"]     = "ubah_harga_input"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            f"💰 *Ubah Harga — {produk[pid]['nama']} [{tipe_obj['nama']}]*\n\n"
            f"Harga saat ini: *Rp{tipe_obj.get('harga', 0):,}*\n\n"
            "Ketik harga baru (angka saja, contoh: `15000`):"
        ),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── ADMIN: HAPUS TIPE ───────────────────────────────────────────────────────

async def handle_admin_hapus_tipe(update: Update, context: CallbackContext):
    """Pilih produk untuk hapus tipe."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    kb = [[_ikb(f"{item['nama']} (ID:{pid})", "", "danger", callback_data=f"htipe_sel_{pid}")]
          for pid, item in produk.items()]
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])
    await safe_edit(query, context, "🗑 *Hapus Tipe*\n\nPilih produk:", reply_markup=InlineKeyboardMarkup(kb))


async def handle_htipe_sel(update: Update, context: CallbackContext):
    """Pilih tipe dari produk yang dipilih untuk dihapus."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    pid   = query.data.replace("htipe_sel_", "")
    produk = load_produk()
    if pid not in produk:
        await query.answer("Produk tidak ditemukan!", show_alert=True)
        return
    tipe_dict = produk[pid].get("tipe", {})
    if len(tipe_dict) <= 1:
        await query.answer("⚠️ Produk hanya punya 1 tipe, tidak bisa dihapus!", show_alert=True)
        return
    kb = []
    for tid, t in tipe_dict.items():
        stok_t = len(t.get("akun_list", []))
        kb.append([_ikb(f"🗑 {t['nama']} ({stok_t} stok)", "🗑", "danger",
                        callback_data=f"htipe_tid_{pid}_{tid}")])
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_hapus_tipe")])
    await safe_edit(query, context,
        f"🗑 *{produk[pid]['nama']}*\n\n⚠️ Pilih tipe yang ingin dihapus (stok ikut terhapus):",
        reply_markup=InlineKeyboardMarkup(kb))


async def handle_htipe_tid(update: Update, context: CallbackContext):
    """Konfirmasi hapus tipe."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    parts = query.data.replace("htipe_tid_", "").split("_", 1)
    if len(parts) != 2:
        await query.answer("Data tidak valid", show_alert=True)
        return
    pid, tid = parts
    produk = load_produk()
    if pid not in produk or tid not in produk[pid].get("tipe", {}):
        await query.answer("Tipe tidak ditemukan!", show_alert=True)
        return
    tipe_obj = produk[pid]["tipe"][tid]
    stok_t   = len(tipe_obj.get("akun_list", []))
    kb = InlineKeyboardMarkup([[
        _ikb("✅ Ya, hapus", "✅", "danger",   callback_data=f"htipe_ok_{pid}_{tid}"),
        _ikb("❌ Batal",     "❌", "primary",  callback_data="admin_hapus_tipe"),
    ]])
    await safe_edit(query, context,
        f"🗑 *Konfirmasi Hapus Tipe*\n\n"
        f"Produk : *{produk[pid]['nama']}*\n"
        f"Tipe   : *{tipe_obj['nama']}*\n"
        f"Stok   : *{stok_t} akun* (ikut dihapus!)\n\n"
        "Yakin ingin menghapus tipe ini?",
        reply_markup=kb)


async def handle_htipe_ok(update: Update, context: CallbackContext):
    """Eksekusi hapus tipe."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    parts = query.data.replace("htipe_ok_", "").split("_", 1)
    if len(parts) != 2:
        await query.answer("Data tidak valid", show_alert=True)
        return
    pid, tid = parts
    produk = load_produk()
    if pid not in produk or tid not in produk[pid].get("tipe", {}):
        await query.answer("Tipe tidak ditemukan!", show_alert=True)
        return
    nama_tipe = produk[pid]["tipe"].pop(tid, {}).get("nama", tid)
    save_produk(produk)
    await query.answer(f"✅ Tipe '{nama_tipe}' dihapus!", show_alert=True)
    await handle_admin_kelola_produk(update, context)


# ─── ADMIN: PENGATURAN BOT ───────────────────────────────────────────────────

async def handle_admin_settings(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    cfg  = load_config()
    rek  = "\n".join(f"  • {r}" for r in cfg.get("rekening", []))
    web_url = _get_website_url() or "Belum diatur"
    text = (
        f"⚙️ *PENGATURAN BOT*\n\n"
        f"🏪 *Nama Toko*: `{cfg['nama_toko']}`\n\n"
        f"🏦 *Rekening*:\n{rek}\n\n"
        f"📞 *Kontak Admin*: `{cfg['kontak_admin']}`\n\n"
        f"🌐 *Website URL*: `{web_url}`"
    )
    qris_status = "✅ Aktif via env var" if QRIS_BASE64 else ("✅ Ada (gambar)" if os.path.exists(qris_file) else "❌ Belum diatur")
    text += f"\n\n🔳 *QRIS*: {qris_status}"
    maint = cfg.get("maintenance", False)
    maint_label = "🔴 Maintenance: ON  — Nonaktifkan" if maint else "🟢 Maintenance: OFF — Aktifkan"
    keyboard = [
        [_ikb("✏️ Ubah Nama Toko",   "✏",  "primary", callback_data="admin_ubah_nama")],
        [_ikb("🏦 Ubah Rekening",     "🏦", "primary", callback_data="admin_ubah_rekening")],
        [_ikb("📞 Ubah Kontak Admin", "📞", "primary", callback_data="admin_ubah_kontak")],
        [_ikb("🌐 Ubah Website URL",  "🌐", "primary", callback_data="admin_ubah_website")],
        [_ikb(maint_label,            "🔧", "danger" if maint else "primary", callback_data="admin_toggle_maintenance")],
        [_ikb("🔙 Kembali",           "🔙", "danger",  callback_data="admin_panel")],
    ]
    await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_admin_ubah_nama(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "ubah_nama_toko"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f"✏️ Nama toko saat ini: *{load_config()['nama_toko']}*\n\nKetik nama toko baru:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


async def handle_admin_ubah_rekening(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "ubah_rekening"
    rek = "\n".join(load_config().get("rekening", []))
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            "🏦 Ketik daftar rekening baru, *satu per baris*:\n\n"
            "Contoh:\n"
            "`DANA      : 0812-XXXX-XXXX a.n Nama`\n"
            "`SEABANK   : 9012345678 a.n Nama`\n\n"
            f"Rekening saat ini:\n`{rek}`"
        ),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


async def handle_admin_ubah_kontak(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "ubah_kontak"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f"📞 Kontak admin saat ini: `{load_config()['kontak_admin']}`\n\nKetik kontak admin baru (contoh: @username):",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


async def handle_admin_ubah_website(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "ubah_website"
    cur = load_config().get("website_url", "") or "Belum diatur"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            f"🌐 *Ubah Website URL*\n\n"
            f"URL saat ini: `{cur}`\n\n"
            "Ketik URL website (contoh: `https://toko-saya.replit.app`).\n"
            "Kosongkan/ketik `-` untuk menghapus."
        ),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


async def handle_admin_upload_qris(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "upload_qris"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            "📷 *Upload Gambar QRIS*\n\n"
            "Kirim foto/gambar QRIS kamu sekarang.\n"
            "Gambar ini akan ditampilkan ke user saat memilih bayar via QRIS."
        ),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── ADMIN: KONFIRMASI & TOLAK DEPOSIT ────────────────────────────────────────

async def handle_admin_confirm(update: Update, context: CallbackContext):
    query   = update.callback_query
    user_id = int(query.data.split(":")[1])
    keyboard = InlineKeyboardMarkup([
        [_ikb("✅ YA, konfirmasi", "✅", "success", callback_data=f"final:{user_id}")],
        [_ikb("❌ Tolak deposit",  "",  "danger",   callback_data=f"reject:{user_id}")],
    ])
    try:
        await query.edit_message_caption("⚠️ Konfirmasi deposit ke user ini?", reply_markup=keyboard)
    except Exception:
        await query.edit_message_text("⚠️ Konfirmasi deposit ke user ini?", reply_markup=keyboard)


async def handle_admin_final(update: Update, context: CallbackContext):
    query   = update.callback_query
    user_id = int(query.data.split(":")[1])
    item = db_get_pending_any_by_user(user_id)
    if not item:
        try:
            await query.edit_message_caption("❌ Data deposit tidak ditemukan.")
        except Exception:
            await query.edit_message_text("❌ Data deposit tidak ditemukan.")
        return

    nominal = item["nominal"]
    db_add_saldo(user_id, nominal)
    db_remove_pending_any_by_user(user_id)
    trx_id = db_add_riwayat(user_id, "DEPOSIT", "Konfirmasi Admin", nominal)

    result_text = (
        f"✅ Saldo *Rp{nominal:,}* berhasil ditambahkan\n"
        f"👤 @{item.get('username') or user_id} (`{user_id}`)"
    )
    try:
        plain_r, ents_r = _pe(result_text, "Markdown")
        if ents_r:
            await query.edit_message_caption(plain_r, caption_entities=ents_r)
        else:
            await query.edit_message_caption(result_text, parse_mode="Markdown")
    except Exception:
        await safe_edit(query, context, result_text)

    notif_text = (
        f"✅ Deposit *Rp{nominal:,}* telah dikonfirmasi dan masuk ke saldo kamu!\n"
        f"🔖 ID Transaksi: `{trx_id}`"
    )
    await _send_pe(context.bot, user_id, notif_text, reply_markup=ReplyKeyboardRemove())
    await _notify_group(
        context.bot,
        f"✅ *Deposit Dikonfirmasi*\n"
        f"👤 @{item.get('username') or user_id} (`{user_id}`)\n"
        f"💰 Rp{nominal:,}\n"
        f"🔖 `{trx_id}`"
    )
    await send_main_menu(context, user_id, await context.bot.get_chat(user_id))


async def handle_admin_reject(update: Update, context: CallbackContext):
    query   = update.callback_query
    user_id = int(query.data.split(":")[1])

    db_remove_pending_any_by_user(user_id)

    try:
        await query.edit_message_caption("❌ Deposit telah ditolak.")
    except Exception:
        await query.edit_message_text("❌ Deposit telah ditolak.")

    await _send_pe(
        context.bot, user_id,
        "❌ Deposit kamu *ditolak* oleh admin. Hubungi admin jika ada pertanyaan.",
        reply_markup=ReplyKeyboardRemove()
    )
    await _notify_group(
        context.bot,
        f"❌ *Deposit Ditolak*\n"
        f"👤 User ID: `{user_id}`"
    )


# ─── BACK & NAVIGASI ─────────────────────────────────────────────────────────

async def handle_back(update: Update, context: CallbackContext):
    query = update.callback_query
    try:
        await query.edit_message_text("✅ Dibatalkan.")
    except Exception:
        try:
            await query.edit_message_caption("✅ Dibatalkan.")
        except Exception:
            pass


async def handle_back_to_produk(update: Update, context: CallbackContext):
    """Kembali ke daftar produk (halaman 1)."""
    try:
        await update.callback_query.answer()
    except Exception:
        pass
    await handle_list_produk(update, context)


async def handle_back_to_menu(update: Update, context: CallbackContext):
    """Kembali ke menu utama."""
    query = update.callback_query
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_main_menu(context, query.from_user.id, query.from_user)


async def handle_info_bot(update: Update, context: CallbackContext):
    query  = update.callback_query
    cfg    = load_config()
    kontak = cfg.get("kontak_admin", "")
    web_on  = cfg.get("web_aktif", True)
    web_url = _get_website_url()
    web_line = ""
    if web_on and web_url:
        web_line = f"\n🌐 <b>Website</b>: {_html.escape(web_url)}\n"
    text = (
        "🆘 <b>PUSAT BANTUAN</b>\n\n"
        "Selamat datang! Berikut panduan lengkap penggunaan bot kami:\n\n"
        "💡 <b>Panduan Pembelian:</b>\n"
        "◉ Lihat Katalog — Tekan tombol \"List Produk\"\n"
        "◉ Pilih Produk — Pilih produk yang diinginkan\n"
        "◉ Tentukan Jumlah — Atur jumlah pembelian\n"
        "◉ Bayar &amp; Terima — Lakukan pembayaran &amp; terima akun\n\n"
        "📋 <b>FAQ:</b>\n"
        "◉ Pembayaran otomatis dikonfirmasi sistem\n"
        "◉ Akun dikirim instan setelah pembayaran\n"
        "◉ Pesan berisi akun akan disematkan\n"
        "◉ Butuh bantuan? Hubungi Admin"
        f"{web_line}"
    )
    kb_rows = []
    if web_on and web_url:
        kb_rows.append([_ikb("🌐 Buka Website ↗", "🌐", "primary", url=web_url)])
    if kontak:
        tg = kontak.lstrip("@")
        kb_rows.append([_ikb("👤 Hubungi Admin ↗", "👤", "primary", url=f"https://t.me/{tg}")])
    kb_rows.append([_ikb("🎫 Buat Tiket Support", "🎫", "primary", callback_data="buat_tiket")])
    kb_rows.append([_ikb("🔥 Kembali ke Menu", "🔥", "danger", callback_data="back_to_menu")])
    markup = InlineKeyboardMarkup(kb_rows)
    await query.answer()
    await safe_edit(query, context, text, parse_mode="HTML",
                    disable_web_page_preview=True, reply_markup=markup)


async def handle_ignore(update: Update, context: CallbackContext):
    await update.callback_query.answer()


# ─── ADMIN: BROADCAST ─────────────────────────────────────────────────────────

async def handle_admin_broadcast(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    users = db_get_all_bot_users()
    kb = InlineKeyboardMarkup([
        [_ikb("⚡ Ya, convert otomatis",       "⚡", "success", callback_data="broadcast_yes")],
        [_ikb("💎 Tidak (sudah pakai premium)", "💎", "primary", callback_data="broadcast_no")],
        [_ikb("🔙 Batal",                       "🔙", "danger",  callback_data="admin_panel")],
    ])
    await safe_edit(query, context,
        f"📢 *BROADCAST*\n\n"
        f"Total penerima: *{len(users)} user*\n\n"
        "Mau otomatis convert emoji biasa ke premium?\n\n"
        "⚡ *Ya, convert otomatis* — emoji biasa 😊 akan dicari padanan premiumnya\n"
        "💎 *Tidak* — disarankan jika pesanmu sudah menggunakan emoji premium "
        "(convert akan mengacak pilihan emoji premium)",
        reply_markup=kb
    )


async def _broadcast_ask_message(query, context: CallbackContext, convert: bool):
    """Helper: minta admin kirim pesan broadcast setelah memilih mode emoji."""
    context.user_data["broadcast_convert"] = convert
    context.user_data["admin_state"]       = "broadcast_msg"
    if convert:
        mode_str = "⚡ *Mode Auto-convert*\nKetik markdown: `*bold*` `_italic_` `` `kode` `` — dan emoji biasa (😊🔥⭐) otomatis jadi premium animasi."
    else:
        mode_str = "💎 *Mode Preserve*\nGunakan toolbar format Telegram (bold/italic/code) — pesan dikirim persis apa adanya termasuk emoji premium."
    await safe_edit(query, context,
        f"📢 *BROADCAST — Kirim Pesan*\n\n"
        f"{mode_str}\n\n"
        "Sekarang kirim pesanmu. Bot akan broadcast ke semua user.\n\n"
        "_(Kirim ❌ Batal untuk membatalkan)_"
    )


# ─── VOUCHER INPUT (di konfirmasi pesanan) ───────────────────────────────────

async def handle_voucher_input(update: Update, context: CallbackContext):
    """Tombol Masukkan Voucher di konfirmasi pesanan — minta input kode."""
    query = update.callback_query
    context.user_data["admin_state"] = "voucher_input"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🏷 *Masukkan Kode Voucher*\n\nKetik kode voucher kamu:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── TIKET SUPPORT (tombol di Bantuan) ───────────────────────────────────────

async def handle_buat_tiket(update: Update, context: CallbackContext):
    """Tombol Buat Tiket di menu Bantuan."""
    query = update.callback_query
    context.user_data["admin_state"] = "tiket_input"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            "🎫 *Buat Tiket Support*\n\n"
            "Ketik pesan/keluhan kamu, admin akan segera merespons.\n\n"
            "_(Kirim ❌ Batal untuk membatalkan)_"
        ),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── ADMIN: REKAP & STATISTIK ────────────────────────────────────────────────

async def handle_admin_rekap(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    rekap  = db_get_rekap_penjualan()
    b      = rekap["beli"]
    d      = rekap["deposit"]
    produk = load_produk()
    stats  = db_get_all_statistik()
    text = (
        f"📊 *REKAP & STATISTIK*\n"
        f"_{rekap['bulan']} | {rekap['tanggal']}_\n\n"
        f"🛒 *PENJUALAN*\n"
        f"• Hari ini : *{b['hari_ini']['count']}x* — Rp{b['hari_ini']['total']:,}\n"
        f"• Bulan ini: *{b['bulan_ini']['count']}x* — Rp{b['bulan_ini']['total']:,}\n"
        f"• Semua    : *{b['semua']['count']}x* — Rp{b['semua']['total']:,}\n\n"
        f"💰 *DEPOSIT*\n"
        f"• Hari ini : *{d['hari_ini']['count']}x* — Rp{d['hari_ini']['total']:,}\n"
        f"• Bulan ini: *{d['bulan_ini']['count']}x* — Rp{d['bulan_ini']['total']:,}\n"
        f"• Semua    : *{d['semua']['count']}x* — Rp{d['semua']['total']:,}\n\n"
        f"📦 *STOK PRODUK*\n"
    )
    for pid, item in produk.items():
        total_stok = sum(len(t.get("akun_list", [])) for t in item.get("tipe", {}).values())
        icon = "🟢" if total_stok > 0 else "🔴"
        text += f"  {icon} {item['nama']}: *{total_stok}* stok\n"
    kb = [[_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_panel")]]
    await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(kb))


# ─── ADMIN: KELOLA USER ───────────────────────────────────────────────────────

async def handle_admin_kelola_user(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    users  = db_get_all_bot_users()
    saldo  = db_get_all_saldo()
    stats  = db_get_all_statistik()
    total  = len(users)
    text   = f"👥 *KELOLA USER* ({total} terdaftar)\n\n"
    for u in users[:25]:
        tid = str(u["telegram_id"])
        s   = saldo.get(tid, 0)
        j   = stats.get(tid, {}).get("jumlah", 0)
        un  = f"@{u['username']}" if u.get("username") else f"`{tid}`"
        text += f"• {un} — 💰Rp{s:,} | 🛒{j}x\n"
    if total > 25:
        text += f"\n_...dan {total - 25} user lainnya_"
    kb = [
        [_ikb("📨 Kirim DM ke User", "📨", "primary", callback_data="admin_dm_start")],
        [_ikb("🔙 Kembali",          "🔙", "danger",  callback_data="admin_panel")],
    ]
    await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(kb))


async def handle_admin_dm_start(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "admin_dm_id"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="📨 *Kirim DM ke User*\n\nKetik *Telegram ID* user tujuan:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── ADMIN: KELOLA VOUCHER ────────────────────────────────────────────────────

async def handle_admin_kelola_voucher(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    vouchers = db_get_all_vouchers()
    text = "*🎟 KELOLA VOUCHER*\n\n"
    if vouchers:
        for v in vouchers:
            status = "✅" if v.get("aktif", True) else "❌"
            text += (
                f"{status} `{v['kode']}` — Rp{v['nominal']:,} "
                f"(sisa: {v.get('max_pakai',1) - v.get('used',0)}x)\n"
            )
    else:
        text += "_Belum ada voucher._\n"
    kb = [
        [_ikb("➕ Buat Voucher Baru",   "➕", "success", callback_data="admin_add_voucher")],
        [_ikb("🔄 Toggle ON/OFF",        "🔄", "primary", callback_data="admin_toggle_voucher")],
        [_ikb("🗑 Hapus Voucher",         "🗑", "danger",  callback_data="admin_hapus_voucher")],
        [_ikb("🔙 Kembali",              "🔙", "danger",  callback_data="admin_panel")],
    ]
    await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(kb))


async def handle_admin_add_voucher(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "add_voucher_kode"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🎟 *Buat Voucher Baru*\n\nKetik *kode* voucher (huruf besar, contoh: `PROMO2025`):",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


async def handle_admin_toggle_voucher_start(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "toggle_voucher_kode"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🔄 *Toggle Voucher ON/OFF*\n\nKetik kode voucher yang ingin di-toggle:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


async def handle_admin_hapus_voucher_start(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    context.user_data["admin_state"] = "hapus_voucher_kode"
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🗑 *Hapus Voucher*\n\nKetik kode voucher yang ingin dihapus:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
    )


# ─── ADMIN: RENAME PRODUK / TIPE ─────────────────────────────────────────────

async def handle_admin_rename_produk(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    kb = [[_ikb(f"{item['nama']} (ID:{pid})", "", "primary",
                callback_data=f"rename_produk_sel_{pid}")]
          for pid, item in produk.items()]
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])
    await safe_edit(query, context, "✏️ *Rename Produk*\n\nPilih produk:",
                    reply_markup=InlineKeyboardMarkup(kb))


async def handle_admin_rename_tipe(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    kb = [[_ikb(f"{item['nama']} (ID:{pid})", "", "primary",
                callback_data=f"rename_tipe_pid_{pid}")]
          for pid, item in produk.items()]
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])
    await safe_edit(query, context, "✏️ *Rename Tipe*\n\nPilih produk:",
                    reply_markup=InlineKeyboardMarkup(kb))


# ─── ADMIN: HAPUS AKUN SATU PER SATU ─────────────────────────────────────────

async def handle_admin_hapus_akun(update: Update, context: CallbackContext):
    """Pilih produk untuk hapus akun satu per satu."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    kb = [[_ikb(f"{item['nama']} (ID:{pid})", "", "primary",
                callback_data=f"hapus_akun_pid_{pid}")]
          for pid, item in produk.items()]
    kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_kelola_produk")])
    await safe_edit(query, context, "🗑 *Hapus Akun Stok*\n\nPilih produk:",
                    reply_markup=InlineKeyboardMarkup(kb))


# ─── ADMIN: MAINTENANCE MODE ──────────────────────────────────────────────────

async def handle_admin_toggle_maintenance(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    cfg = load_config()
    maint = not cfg.get("maintenance", False)
    cfg["maintenance"] = maint
    save_config(cfg)
    status = "🔴 AKTIF" if maint else "🟢 NONAKTIF"
    await query.answer(f"Maintenance mode {status}", show_alert=True)
    await handle_admin_settings(update, context)


async def handle_broadcast_yes(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    await _broadcast_ask_message(query, context, convert=True)


async def handle_broadcast_no(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    await _broadcast_ask_message(query, context, convert=False)




# ─── ROUTING CALLBACK ─────────────────────────────────────────────────────────

CALLBACK_MAP = {
    "list_produk":              handle_list_produk,
    "cek_stok":                 handle_cek_stok,
    "info_bot":                 handle_info_bot,
    "deposit":                  handle_deposit,
    "deposit_custom":           handle_deposit_nominal,
    "cancel_deposit":           handle_cancel_deposit,
    "admin_panel":              handle_admin_panel,
    "admin_kelola_produk":      handle_admin_kelola_produk,
    "admin_add_produk":         handle_admin_add_produk,
    "admin_restock_produk":     handle_admin_restock_produk,
    "admin_hapus_produk":       handle_admin_hapus_produk,
    "admin_settings":           handle_admin_settings,
    "admin_ubah_nama":          handle_admin_ubah_nama,
    "admin_ubah_rekening":      handle_admin_ubah_rekening,
    "admin_ubah_kontak":        handle_admin_ubah_kontak,
    "admin_ubah_website":       handle_admin_ubah_website,
    "admin_upload_qris":        handle_admin_upload_qris,
    "admin_broadcast":          handle_admin_broadcast,
    "broadcast_yes":            handle_broadcast_yes,
    "broadcast_no":             handle_broadcast_no,
    "deposit_qris":             handle_deposit_qris,
    "qris_dep_custom":          handle_qris_dep_nominal,
    "beli_qris":                handle_beli_qris,
    "cek_mutasi":               handle_cek_mutasi,
    "qty_plus":                 handle_qty_plus,
    "qty_minus":                handle_qty_minus,
    "confirm_order":            handle_confirm_order,
    "confirm_saldo":            handle_confirm_saldo,
    "back":                     handle_back,
    "back_to_produk":           handle_back_to_produk,
    "back_to_menu":             handle_back_to_menu,
    "riwayat_user":             handle_riwayat_user,
    "riwayat_beli":             handle_riwayat_beli,
    "riwayat_deposit":          handle_riwayat_deposit,
    "ignore":                   handle_ignore,
    "file_txt_ya":              handle_file_txt_ya,
    "file_txt_tidak":           handle_file_txt_tidak,
    "admin_edit_tipe_desc":     handle_admin_edit_tipe_desc,
    "admin_add_tipe":           handle_admin_add_tipe,
    "admin_ubah_harga_tipe":    handle_admin_ubah_harga_tipe,
    "admin_hapus_tipe":         handle_admin_hapus_tipe,
    # ── Baru ──
    "voucher_input":            handle_voucher_input,
    "buat_tiket":               handle_buat_tiket,
    "admin_rekap":              handle_admin_rekap,
    "admin_kelola_user":        handle_admin_kelola_user,
    "admin_dm_start":           handle_admin_dm_start,
    "admin_kelola_voucher":     handle_admin_kelola_voucher,
    "admin_add_voucher":        handle_admin_add_voucher,
    "admin_toggle_voucher":     handle_admin_toggle_voucher_start,
    "admin_hapus_voucher":      handle_admin_hapus_voucher_start,
    "admin_rename_produk":      handle_admin_rename_produk,
    "admin_rename_tipe":        handle_admin_rename_tipe,
    "admin_hapus_akun":         handle_admin_hapus_akun,
    "admin_toggle_maintenance": handle_admin_toggle_maintenance,
}


async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    data  = query.data
    await query.answer()

    # ── Anti-spam: throttle klik cepat ──────────────────────────────
    uid_spam = query.from_user.id
    now_spam = time.monotonic()
    _NON_SPAM = {"ignore", "qty_minus", "qty_plus"}
    if data not in _NON_SPAM and (now_spam - _ANTI_SPAM.get(uid_spam, 0.0)) < ANTI_SPAM_INTERVAL:
        await query.answer("⏳ Terlalu cepat, tunggu sebentar.", show_alert=True)
        return
    _ANTI_SPAM[uid_spam] = now_spam

    log.info(f"🖱️ Tombol: [{data}] oleh user={uid_spam} (@{query.from_user.username})")

    produk = load_produk()
    if data in produk:
        await handle_produk_detail(update, context)
    elif data.startswith("list_produk_p"):
        await handle_list_produk(update, context)
    elif data.startswith("tipe_"):
        await handle_tipe_select(update, context)
    elif data.startswith("deposit_"):
        await handle_deposit_nominal(update, context)
    elif data.startswith("restock_sel_"):
        await handle_restock_sel(update, context)
    elif data.startswith("restock_tipe_"):
        await handle_restock_tipe_sel(update, context)
    elif data.startswith("edesc_p_"):
        await handle_edesc_produk_sel(update, context)
    elif data.startswith("edesc_t_"):
        await handle_edesc_tipe_sel(update, context)
    elif data.startswith("addtipe_sel_"):
        await handle_addtipe_sel(update, context)
    elif data.startswith("uharga_sel_"):
        await handle_uharga_sel(update, context)
    elif data.startswith("uharga_tid_"):
        await handle_uharga_tid(update, context)
    elif data.startswith("htipe_sel_"):
        await handle_htipe_sel(update, context)
    elif data.startswith("htipe_tid_"):
        await handle_htipe_tid(update, context)
    elif data.startswith("htipe_ok_"):
        await handle_htipe_ok(update, context)
    elif data.startswith("rename_produk_sel_"):
        pid = data.replace("rename_produk_sel_", "")
        context.user_data["rename_produk_pid"] = pid
        context.user_data["admin_state"] = "rename_produk_input"
        produk = load_produk()
        nama_lama = produk.get(pid, {}).get("nama", pid)
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"✏️ *Rename Produk*\n\nNama saat ini: *{nama_lama}*\n\nKetik nama baru:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
        )
    elif data.startswith("rename_tipe_pid_"):
        pid = data.replace("rename_tipe_pid_", "")
        produk = load_produk()
        item = produk.get(pid)
        if not item:
            await query.answer("Produk tidak ditemukan!", show_alert=True)
            return
        kb = [[_ikb(f"{t['nama']} (ID:{tid})", "", "primary",
                    callback_data=f"rename_tipe_tid_{pid}_{tid}")]
              for tid, t in item.get("tipe", {}).items()]
        kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_rename_tipe")])
        await safe_edit(query, context, f"✏️ *Rename Tipe — {item['nama']}*\n\nPilih tipe:",
                        reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("rename_tipe_tid_"):
        parts = data.replace("rename_tipe_tid_", "").split("_", 1)
        if len(parts) == 2:
            pid, tid = parts
            context.user_data["rename_tipe_pid"] = pid
            context.user_data["rename_tipe_tid"] = tid
            context.user_data["admin_state"] = "rename_tipe_input"
            produk = load_produk()
            nama_lama = produk.get(pid, {}).get("tipe", {}).get(tid, {}).get("nama", tid)
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=f"✏️ *Rename Tipe*\n\nNama saat ini: *{nama_lama}*\n\nKetik nama baru:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Batal")]], resize_keyboard=True)
            )
    elif data.startswith("hapus_akun_pid_"):
        pid = data.replace("hapus_akun_pid_", "")
        produk = load_produk()
        item = produk.get(pid)
        if not item:
            await query.answer("Produk tidak ditemukan!", show_alert=True)
            return
        kb = [[_ikb(f"{t['nama']} ({len(t.get('akun_list',[]))} akun)", "", "primary",
                    callback_data=f"hapus_akun_tipe_{pid}_{tid}")]
              for tid, t in item.get("tipe", {}).items() if t.get("akun_list")]
        if not kb:
            await query.answer("Tidak ada akun untuk dihapus!", show_alert=True)
            return
        kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="admin_hapus_akun")])
        await safe_edit(query, context, f"🗑 *Hapus Akun — {item['nama']}*\n\nPilih tipe:",
                        reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("hapus_akun_tipe_"):
        parts = data.replace("hapus_akun_tipe_", "").split("_", 1)
        if len(parts) == 2:
            pid, tid = parts
            produk = load_produk()
            item = produk.get(pid)
            tipe = item.get("tipe", {}).get(tid) if item else None
            if not tipe or not tipe.get("akun_list"):
                await query.answer("Tidak ada akun!", show_alert=True)
                return
            akun_list = tipe["akun_list"]
            lines = []
            for i, a in enumerate(akun_list[:20]):
                lines.append(f"`{i}` — {a.get('username','')}")
            text = f"🗑 *Pilih akun yang dihapus — {tipe['nama']}*\n\n" + "\n".join(lines)
            if len(akun_list) > 20:
                text += f"\n_...dan {len(akun_list)-20} lainnya_"
            kb = [[_ikb(f"[{i}] {a.get('username','')[:20]}", "", "danger",
                        callback_data=f"hapus_akun_idx_{pid}_{tid}_{i}")]
                  for i, a in enumerate(akun_list[:20])]
            kb.append([_ikb("🔙 Kembali", "🔙", "danger",
                            callback_data=f"hapus_akun_pid_{pid}")])
            await safe_edit(query, context, text, reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("hapus_akun_idx_"):
        parts = data.replace("hapus_akun_idx_", "").split("_", 2)
        if len(parts) == 3:
            pid, tid, idx_str = parts
            try:
                idx = int(idx_str)
            except ValueError:
                await query.answer("Index tidak valid!", show_alert=True)
                return
            produk = load_produk()
            item = produk.get(pid)
            tipe = item.get("tipe", {}).get(tid) if item else None
            if not tipe or idx >= len(tipe.get("akun_list", [])):
                await query.answer("Akun tidak ditemukan!", show_alert=True)
                return
            akun = tipe["akun_list"].pop(idx)
            tipe["stok"] = len(tipe["akun_list"])
            save_produk(produk)
            await query.answer(f"✅ Akun {akun.get('username','')} dihapus!", show_alert=True)
            await safe_edit(query, context,
                            f"✅ Akun *{akun.get('username','')}* berhasil dihapus dari *{tipe['nama']}*.\n"
                            f"Sisa stok: {tipe['stok']}",
                            reply_markup=InlineKeyboardMarkup(
                                [[_ikb("🔙 Kembali ke Kelola Produk", "🔙", "danger",
                                       callback_data="admin_kelola_produk")]]
                            ))
    elif data.startswith("dep_manual_") or data.startswith("dep_qris_"):
        await handle_dep_metode(update, context)
    elif data.startswith("qris_dep_"):
        await handle_qris_dep_nominal(update, context)
    elif data.startswith("confirm:"):
        await handle_admin_confirm(update, context)
    elif data.startswith("final:"):
        await handle_admin_final(update, context)
    elif data.startswith("reject:"):
        await handle_admin_reject(update, context)
    elif data in CALLBACK_MAP:
        await CALLBACK_MAP[data](update, context)
    else:
        try:
            await query.edit_message_text("❌ Aksi tidak dikenali.")
        except Exception:
            pass


# ─── HANDLER TEKS (termasuk admin multi-step) ─────────────────────────────────

async def handle_text(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    uid  = str(update.effective_user.id)

    # ── Batal universal ──────────────────────────────────────────────
    if text == "❌ Batal" or text == "❌ Batalkan Deposit":
        db_remove_pending_any_by_user(uid)
        for key in ["awaiting_custom", "awaiting_qris_custom", "nominal_asli", "total_transfer",
                    "admin_state", "new_produk", "restock_pid", "restock_tipe_id", "konfirmasi",
                    "reg_state", "reg_phone", "reg_email"]:
            context.user_data.pop(key, None)
        await update.message.reply_text("✅ Dibatalkan.", reply_markup=ReplyKeyboardRemove())
        await send_main_menu_safe(update, context)
        return

    # ── Alur registrasi bot ──────────────────────────────────────────
    reg_state = context.user_data.get("reg_state")
    if reg_state:
        if reg_state == "reg_phone":
            phone = text.strip()
            if not _RE_PHONE.match(phone):
                await update.message.reply_text(
                    "❌ Format nomor HP tidak valid.\n"
                    "Gunakan format: `+6281234567890`\n\nCoba lagi:",
                    parse_mode="Markdown"
                )
                return
            if web_get_user_by_phone(phone):
                await update.message.reply_text(
                    "❌ Nomor HP ini sudah terdaftar. Gunakan nomor lain:"
                )
                return
            context.user_data["reg_phone"] = phone
            context.user_data["reg_state"] = "reg_email"
            await update.message.reply_text(
                "✅ Nomor HP diterima!\n\n"
                "📧 *Langkah 2/2* — Masukkan *email* kamu:\n"
                "Contoh: `nama@gmail.com`",
                parse_mode="Markdown"
            )
            return

        if reg_state == "reg_email":
            email = text.strip().lower()
            if not _RE_EMAIL.match(email):
                await update.message.reply_text(
                    "❌ Format email tidak valid. Coba lagi:"
                )
                return
            if web_get_user_by_email(email):
                await update.message.reply_text(
                    "❌ Email ini sudah terdaftar. Gunakan email lain:"
                )
                return
            phone    = context.user_data.pop("reg_phone", None)
            context.user_data.pop("reg_state", None)
            password = _auto_generate_password()
            pw_hash  = generate_password_hash(password)
            role     = "admin" if is_admin(update.effective_user.id) else "user"
            try:
                web_create_user(
                    update.effective_user.id,
                    update.effective_user.username,
                    pw_hash, role,
                    phone=phone, email=email
                )
            except Exception as e:
                log.error(f"web_create_user error: {e}")
                await update.message.reply_text(
                    "❌ Terjadi kesalahan saat membuat akun. Coba lagi nanti.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            cfg_r   = load_config()
            web_on  = cfg_r.get("web_aktif", True)
            web_url = _get_website_url()
            if web_on and web_url:
                web_line = (
                    f"\n🌐 *Website*: {web_url}\n"
                    f"🔐 *Password*: `{password}`\n\n"
                    "_Simpan password ini\\! Login dengan email/nomor HP kamu\\._"
                )
            else:
                web_line = f"\n🔐 *Password*: `{password}`\n\n_Simpan password ini\\!_"
            await update.message.reply_text(
                "🎉 *Akun berhasil dibuat\\!*\n\n"
                f"📱 HP: `{phone}`\n"
                f"📧 Email: `{email}`"
                f"{web_line}\n\n"
                "Selamat berbelanja\\!",
                parse_mode="MarkdownV2",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return
        if admin_state == "admin_dm_id":
            try:
                tgt = int(text.strip())
            except ValueError:
                await update.message.reply_text("❌ ID tidak valid. Ketik angka Telegram ID:")
                return
            context.user_data["admin_dm_target"] = tgt
            context.user_data["admin_state"]     = "admin_dm_msg"
            await update.message.reply_text(f"📨 Kirim ke ID `{tgt}`.\n\nKetik pesan:", parse_mode="Markdown")
            return

        if admin_state == "admin_dm_msg":
            tgt = context.user_data.pop("admin_dm_target", None)
            context.user_data.pop("admin_state", None)
            if not tgt:
                await update.message.reply_text("❌ Sesi tidak valid.", reply_markup=ReplyKeyboardRemove())
                return
            try:
                await context.bot.send_message(chat_id=tgt, text=text)
                await update.message.reply_text(f"✅ Pesan terkirim ke `{tgt}`.", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
            except Exception as e:
                await update.message.reply_text(f"❌ Gagal kirim: {e}", reply_markup=ReplyKeyboardRemove())
            await send_main_menu_safe(update, context)
            return

        if admin_state == "add_voucher_kode":
            kode = text.strip().upper()
            if not kode or len(kode) < 3:
                await update.message.reply_text("❌ Kode terlalu pendek. Minimal 3 karakter:")
                return
            context.user_data["new_voucher_kode"] = kode
            context.user_data["admin_state"]      = "add_voucher_nominal"
            await update.message.reply_text(f"✅ Kode: *{kode}*\n\nKetik *nominal* diskon (angka, contoh: `25000`):", parse_mode="Markdown")
            return

        if admin_state == "add_voucher_nominal":
            try:
                nominal = int(text.strip().replace(".", "").replace(",", ""))
                if nominal <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Nominal tidak valid. Ketik angka positif:")
                return
            context.user_data["new_voucher_nominal"] = nominal
            context.user_data["admin_state"]         = "add_voucher_max"
            await update.message.reply_text(f"💰 Nominal: *Rp{nominal:,}*\n\nKetik *max pemakaian* (contoh: `100`):", parse_mode="Markdown")
            return

        if admin_state == "add_voucher_max":
            try:
                max_uses = int(text.strip())
                if max_uses <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Tidak valid. Ketik angka positif:")
                return
            kode    = context.user_data.pop("new_voucher_kode", "")
            nominal = context.user_data.pop("new_voucher_nominal", 0)
            context.user_data.pop("admin_state", None)
            ok = db_add_voucher(kode, nominal, max_uses)
            if ok:
                await update.message.reply_text(
                    f"✅ *Voucher dibuat!*\n🏷 `{kode}` | Rp{nominal:,} | Max {max_uses}x",
                    parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
                )
            else:
                await update.message.reply_text("❌ Kode sudah ada.", reply_markup=ReplyKeyboardRemove())
            await send_main_menu_safe(update, context)
            return

        if admin_state == "toggle_voucher_kode":
            kode = text.strip().upper()
            context.user_data.pop("admin_state", None)
            ok = db_toggle_voucher(kode)
            msg = f"✅ Voucher `{kode}` di-toggle." if ok else f"❌ Voucher `{kode}` tidak ditemukan."
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
            await send_main_menu_safe(update, context)
            return

        if admin_state == "hapus_voucher_kode":
            kode = text.strip().upper()
            context.user_data.pop("admin_state", None)
            db_delete_voucher(kode)
            await update.message.reply_text(f"🗑 Voucher `{kode}` dihapus.", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
            await send_main_menu_safe(update, context)
            return

        if admin_state == "rename_produk_input":
            pid = context.user_data.pop("rename_produk_pid", None)
            context.user_data.pop("admin_state", None)
            nama_baru = text.strip()
            if not pid or len(nama_baru) < 2:
                await update.message.reply_text("❌ Nama tidak valid.", reply_markup=ReplyKeyboardRemove())
                return
            produk = load_produk()
            if pid not in produk:
                await update.message.reply_text("❌ Produk tidak ditemukan.", reply_markup=ReplyKeyboardRemove())
                return
            nama_lama = produk[pid]["nama"]
            produk[pid]["nama"] = nama_baru
            save_produk(produk)
            await update.message.reply_text(
                f"✅ Produk direname: *{nama_lama}* → *{nama_baru}*",
                parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "rename_tipe_input":
            pid = context.user_data.pop("rename_tipe_pid", None)
            tid = context.user_data.pop("rename_tipe_tid", None)
            context.user_data.pop("admin_state", None)
            nama_baru = text.strip()
            if not pid or not tid or len(nama_baru) < 1:
                await update.message.reply_text("❌ Input tidak valid.", reply_markup=ReplyKeyboardRemove())
                return
            produk = load_produk()
            if pid not in produk or tid not in produk[pid].get("tipe", {}):
                await update.message.reply_text("❌ Tipe tidak ditemukan.", reply_markup=ReplyKeyboardRemove())
                return
            nama_lama = produk[pid]["tipe"][tid]["nama"]
            produk[pid]["tipe"][tid]["nama"] = nama_baru
            save_produk(produk)
            await update.message.reply_text(
                f"✅ Tipe direname: *{nama_lama}* → *{nama_baru}*",
                parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        return  # state tidak dikenal, abaikan

    # ── QRIS custom nominal ──────────────────────────────────────────
    if context.user_data.get("awaiting_qris_custom"):
        context.user_data.pop("awaiting_qris_custom", None)
        try:
            nominal = int(text.replace(".", "").replace(",", "").replace(" ", ""))
            if nominal < DEPOSIT_MIN or nominal > DEPOSIT_MAX:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                f"❌ Nominal tidak valid.\nMinimum Rp{DEPOSIT_MIN:,}, maksimum Rp{DEPOSIT_MAX:,}.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        await _show_qris_deposit(update.effective_user, nominal, context)
        return

    # ── States untuk semua user (voucher, tiket) ─────────────────────
    _gen_state = context.user_data.get("admin_state")

    if _gen_state == "voucher_input":
        kode    = text.strip().upper()
        uid_str = str(update.effective_user.id)
        chk     = db_check_voucher(kode, uid_str)
        if chk == "invalid":
            await update.message.reply_text(
                "❌ Voucher tidak valid atau sudah habis pakai.",
                reply_markup=ReplyKeyboardRemove()
            )
            context.user_data.pop("admin_state", None)
            await send_main_menu_safe(update, context)
            return
        if chk == "used":
            await update.message.reply_text(
                "❌ Kamu sudah pernah menggunakan voucher ini.",
                reply_markup=ReplyKeyboardRemove()
            )
            context.user_data.pop("admin_state", None)
            await send_main_menu_safe(update, context)
            return
        context.user_data["voucher_kode"]   = kode
        context.user_data["voucher_diskon"] = chk
        context.user_data.pop("admin_state", None)
        await update.message.reply_text(
            f"✅ *Voucher `{kode}` valid!*\n"
            f"💰 Diskon: *-Rp{chk:,}*\n\nLanjutkan ke pembayaran:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text(
            "↩ Kembali ke konfirmasi pesanan:",
            reply_markup=InlineKeyboardMarkup([[
                _ikb("✅ Lanjut ke Pembayaran", "✅", "success", callback_data="confirm_order")
            ]])
        )
        return

    if _gen_state == "tiket_input":
        pesan = text.strip()
        if not pesan or len(pesan) < 5:
            await update.message.reply_text("❌ Pesan terlalu pendek. Coba lagi:")
            return
        uid_str = str(update.effective_user.id)
        tid     = db_ticket_create(uid_str, pesan)
        context.user_data.pop("admin_state", None)
        await update.message.reply_text(
            f"🎫 *Tiket #{tid} berhasil dibuat!*\n\nAdmin akan segera merespons. Terima kasih.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        _u   = update.effective_user
        _nm  = _u.full_name or str(_u.id)
        await _notify_group(
            context.bot,
            f"🎫 *Tiket Support Baru #{tid}*\n\n"
            f"👤 {_nm} (`{_u.id}`)\n"
            f"💬 {pesan[:500]}"
        )
        await send_main_menu_safe(update, context)
        return

    # ── Alur admin multi-step ────────────────────────────────────────
    if is_admin(update.effective_user.id):
        admin_state = context.user_data.get("admin_state")

        if admin_state == "add_nama":
            context.user_data["new_produk"] = {"nama": text}
            context.user_data["admin_state"] = "add_harga"
            await update.message.reply_text(
                f"📦 Nama: *{text}*\n\nSekarang ketik *harga* produk (angka saja, contoh: 15000):",
                parse_mode="Markdown"
            )
            return

        if admin_state == "add_harga":
            try:
                harga = int(text.replace(".", "").replace(",", ""))
                if harga <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Harga tidak valid. Ketik angka saja, contoh: 15000")
                return
            context.user_data["new_produk"]["harga"] = harga
            context.user_data["admin_state"] = "add_akun"
            await update.message.reply_text(
                f"💰 Harga: Rp{harga:,}\n\n"
                "Sekarang kirim daftar akun, satu per baris dengan format:\n"
                "`email|password|tipe`\n\n"
                "Contoh:\n`user1@mail.com|pass123|1 Bulan`\n`user2@mail.com|pass456|1 Bulan`",
                parse_mode="Markdown"
            )
            return

        if admin_state == "add_akun":
            lines     = [l.strip() for l in text.strip().splitlines() if l.strip()]
            akun_list = []
            errors    = []
            for i, line in enumerate(lines, 1):
                parts = [p.strip() for p in line.split("|", 2)]
                if len(parts) < 2 or not parts[0] or not parts[1]:
                    errors.append(f"Baris {i}: format salah (minimal `username|password`)")
                    continue
                akun = {"username": parts[0], "password": parts[1]}
                if len(parts) == 3 and parts[2]:
                    akun["extra"] = parts[2]
                akun_list.append(akun)

            if errors:
                await update.message.reply_text("❌ Ada format yang salah:\n" + "\n".join(errors) + "\n\nCoba lagi:")
                return

            np = context.user_data["new_produk"]
            produk = load_produk()
            new_id = str(max((int(k) for k in produk.keys()), default=0) + 1)
            produk[new_id] = {"nama": np["nama"], "harga": np["harga"], "akun_list": akun_list, "stok": 0}
            save_produk(produk)

            for key in ["admin_state", "new_produk"]:
                context.user_data.pop(key, None)

            await update.message.reply_text(
                f"✅ Produk *{np['nama']}* berhasil ditambahkan!\n"
                f"ID: `{new_id}` | Harga: Rp{np['harga']:,} | Stok: {len(akun_list)}x",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "broadcast_msg":
            convert = context.user_data.pop("broadcast_convert", False)
            context.user_data.pop("admin_state", None)
            users   = db_get_all_bot_users()
            total   = len(users)
            success = 0
            failed  = 0
            src_cid = update.message.chat_id
            src_mid = update.message.message_id

            # Tentukan strategi pengiriman
            orig_ents     = list(update.message.entities or [])
            has_premium   = any(e.type == "custom_emoji" for e in orig_ents)
            raw_text      = update.message.text or ""

            send_mode     = "copy"       # default: copy_message (preserve segalanya)
            send_kw: dict = {}           # kwargs untuk send_message
            mode_label    = "Preserve asli (copy)"

            if convert and not has_premium:
                # Mode convert: parse markdown + tambah premium emoji
                pe_text, pe_ents = _pe(raw_text, "Markdown")
                if pe_ents:
                    send_mode  = "entities"
                    send_kw    = {"text": pe_text, "entities": pe_ents}
                    mode_label = "Auto-convert + Markdown"
                else:
                    # Tidak ada emoji/markdown → coba parse_mode Markdown minimal
                    send_mode  = "markdown"
                    send_kw    = {"text": raw_text, "parse_mode": "Markdown"}
                    mode_label = "Markdown parse"
            elif convert and has_premium:
                # Pesan sudah ada premium emoji → copy preserves them
                mode_label = "Copy (premium emoji terdeteksi)"

            await update.message.reply_text(
                f"📢 Memulai broadcast ke *{total}* user...",
                parse_mode="Markdown"
            )
            for u in users:
                try:
                    if send_mode == "copy":
                        await context.bot.copy_message(
                            chat_id=u["telegram_id"],
                            from_chat_id=src_cid,
                            message_id=src_mid
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=u["telegram_id"],
                            **send_kw
                        )
                    success += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.05)   # flood control ~20 msg/s
            await update.message.reply_text(
                f"✅ *Broadcast selesai!*\n\n"
                f"⚡ Mode    : {mode_label}\n"
                f"✔️ Terkirim : *{success}*\n"
                f"❌ Gagal    : *{failed}*\n"
                f"📊 Total    : *{total}*",
                parse_mode="Markdown"
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "edit_tipe_desc_input":
            meta = context.user_data.pop("edit_tipe_desc", {})
            context.user_data.pop("admin_state", None)
            pid = meta.get("pid")
            tid = meta.get("tid")
            produk = load_produk()
            if not pid or not tid or pid not in produk or tid not in produk[pid].get("tipe", {}):
                await update.message.reply_text("❌ Sesi tidak valid. Mulai ulang.",
                                                reply_markup=ReplyKeyboardRemove())
                return
            new_desc = "" if text.strip() == "-" else text.strip()
            produk[pid]["tipe"][tid]["deskripsi"] = new_desc
            save_produk(produk)
            label = f'"{new_desc}"' if new_desc else "_(dihapus)_"
            await update.message.reply_text(
                f"✅ Deskripsi tipe *{produk[pid]['tipe'][tid]['nama']}* diperbarui: {label}",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "add_tipe_nama":
            context.user_data["add_tipe_nama_val"] = text.strip()
            context.user_data["admin_state"]        = "add_tipe_harga"
            await update.message.reply_text(
                f"✅ Nama tipe: *{text.strip()}*\n\nSekarang ketik *harga* (angka saja, contoh: `25000`):",
                parse_mode="Markdown"
            )
            return

        if admin_state == "add_tipe_harga":
            try:
                harga = int(text.strip().replace(".", "").replace(",", ""))
                if harga <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Harga tidak valid. Masukkan angka positif:")
                return
            context.user_data["add_tipe_harga_val"] = harga
            context.user_data["admin_state"]         = "add_tipe_akun"
            await update.message.reply_text(
                f"💰 Harga: Rp{harga:,}\n\n"
                "Kirim daftar akun untuk tipe ini, satu per baris:\n"
                "`username|password` atau `username|password|info tambahan`\n\n"
                "Contoh:\n`user@gmail.com|Pass123`\n`user2@gmail.com|Pass456|Region ID`",
                parse_mode="Markdown"
            )
            return

        if admin_state == "add_tipe_akun":
            pid       = context.user_data.get("add_tipe_pid")
            nama_tipe = context.user_data.get("add_tipe_nama_val", "")
            harga     = context.user_data.get("add_tipe_harga_val", 0)
            for k in ["add_tipe_pid","add_tipe_nama_val","add_tipe_harga_val","admin_state"]:
                context.user_data.pop(k, None)
            produk = load_produk()
            if not pid or pid not in produk:
                await update.message.reply_text("❌ Sesi tidak valid. Mulai ulang.",
                                                reply_markup=ReplyKeyboardRemove())
                return
            lines_in  = [l.strip() for l in text.strip().splitlines() if l.strip()]
            akun_baru = []
            errors    = []
            for i, line in enumerate(lines_in, 1):
                parts = [p.strip() for p in line.split("|", 2)]
                if len(parts) >= 2 and parts[0] and parts[1]:
                    akun = {"username": parts[0], "password": parts[1]}
                    if len(parts) == 3 and parts[2]:
                        akun["extra"] = parts[2]
                    akun_baru.append(akun)
                else:
                    errors.append(f"Baris {i}: format salah")
            if errors:
                context.user_data["add_tipe_pid"]       = pid
                context.user_data["add_tipe_nama_val"]  = nama_tipe
                context.user_data["add_tipe_harga_val"] = harga
                context.user_data["admin_state"]        = "add_tipe_akun"
                await update.message.reply_text(
                    "❌ Ada format yang salah:\n" + "\n".join(errors) + "\n\nKirim ulang akun:",
                    parse_mode="Markdown"
                )
                return
            # Generate tipe ID baru
            tipe_ids = list(produk[pid].get("tipe", {}).keys())
            new_tid  = f"t{len(tipe_ids)+1}" if tipe_ids else "t1"
            # Pastikan unik
            while new_tid in produk[pid].get("tipe", {}):
                new_tid = f"t{int(new_tid[1:])+1}"
            produk[pid].setdefault("tipe", {})[new_tid] = {
                "nama":      nama_tipe,
                "harga":     harga,
                "akun_list": akun_baru,
                "stok":      len(akun_baru),
                "deskripsi": "",
            }
            save_produk(produk)
            await update.message.reply_text(
                f"✅ *Tipe baru berhasil ditambahkan!*\n\n"
                f"Produk : *{produk[pid]['nama']}*\n"
                f"Tipe   : *{nama_tipe}*\n"
                f"Harga  : Rp{harga:,}\n"
                f"Akun   : {len(akun_baru)} akun",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "ubah_harga_input":
            pid = context.user_data.pop("ubah_harga_pid", None)
            tid = context.user_data.pop("ubah_harga_tid", None)
            context.user_data.pop("admin_state", None)
            try:
                harga_baru = int(text.strip().replace(".", "").replace(",", ""))
                if harga_baru <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Harga tidak valid. Ulangi dari awal.",
                                                reply_markup=ReplyKeyboardRemove())
                return
            produk = load_produk()
            if not pid or not tid or pid not in produk or tid not in produk[pid].get("tipe", {}):
                await update.message.reply_text("❌ Sesi tidak valid. Ulangi dari awal.",
                                                reply_markup=ReplyKeyboardRemove())
                return
            harga_lama = produk[pid]["tipe"][tid].get("harga", 0)
            produk[pid]["tipe"][tid]["harga"] = harga_baru
            save_produk(produk)
            await update.message.reply_text(
                f"✅ *Harga diperbarui!*\n\n"
                f"Produk : *{produk[pid]['nama']}*\n"
                f"Tipe   : *{produk[pid]['tipe'][tid]['nama']}*\n"
                f"Lama   : Rp{harga_lama:,}\n"
                f"Baru   : Rp{harga_baru:,}",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "restock_akun":
            pid     = context.user_data.get("restock_pid")
            tipe_id = context.user_data.get("restock_tipe_id")
            produk  = load_produk()
            if not pid or pid not in produk or not tipe_id or tipe_id not in produk[pid].get("tipe",{}):
                await update.message.reply_text("❌ Sesi restock tidak valid. Ulangi dari awal.")
                for k in ["admin_state","restock_pid","restock_tipe_id"]: context.user_data.pop(k, None)
                return

            tipe_obj  = produk[pid]["tipe"][tipe_id]
            lines_in  = [l.strip() for l in text.strip().splitlines() if l.strip()]
            akun_baru = []
            errors    = []
            for i, line in enumerate(lines_in, 1):
                parts = [p.strip() for p in line.split("|", 2)]
                if len(parts) >= 2 and parts[0] and parts[1]:
                    akun = {"username": parts[0], "password": parts[1]}
                    if len(parts) == 3 and parts[2]:
                        akun["extra"] = parts[2]
                    akun_baru.append(akun)
                elif len(parts) == 1 and parts[0]:
                    akun_baru.append({"username": parts[0], "password": ""})
                else:
                    errors.append(f"Baris {i}: format salah (gunakan `username|password` atau `username|password|info tambahan`)")

            if errors:
                await update.message.reply_text(
                    "❌ Ada format yang salah:\n" + "\n".join(errors) +
                    "\n\nPerbaiki dan kirim ulang semua akun:",
                    parse_mode="Markdown"
                )
                return

            tipe_obj.setdefault("akun_list", []).extend(akun_baru)
            tipe_obj["stok"] = len(tipe_obj["akun_list"])
            save_produk(produk)

            total_stok = sum(len(t.get("akun_list",[])) for t in produk[pid]["tipe"].values())
            for k in ["admin_state","restock_pid","restock_tipe_id"]: context.user_data.pop(k, None)

            await update.message.reply_text(
                f"✅ *Restock berhasil!*\n\n"
                f"Produk : *{produk[pid]['nama']}*\n"
                f"Tipe   : *{tipe_obj['nama']}*\n"
                f"Ditambah : *{len(akun_baru)} akun*\n"
                f"Stok tipe : *{tipe_obj['stok']}*\n"
                f"Total stok produk : *{total_stok}*",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "hapus_pid":
            pid    = text.split(" - ")[0].strip()
            produk = load_produk()
            if pid not in produk:
                await update.message.reply_text("❌ ID produk tidak valid. Coba lagi:")
                return
            nama = produk[pid]["nama"]
            del produk[pid]
            save_produk(produk)
            context.user_data.pop("admin_state", None)
            await update.message.reply_text(
                f"🗑 Produk *{nama}* berhasil dihapus.",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "ubah_nama_toko":
            nama_baru = text.strip()
            if len(nama_baru) < 2 or len(nama_baru) > 64:
                await update.message.reply_text("❌ Nama toko harus 2–64 karakter. Coba lagi:")
                return
            cfg = load_config()
            cfg["nama_toko"] = nama_baru
            save_config(cfg)
            context.user_data.pop("admin_state", None)
            await update.message.reply_text(
                f"✅ Nama toko berhasil diubah menjadi *{nama_baru}*",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "ubah_rekening":
            lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
            if not lines:
                await update.message.reply_text("❌ Rekening tidak boleh kosong. Coba lagi:")
                return
            cfg = load_config()
            cfg["rekening"] = lines
            save_config(cfg)
            context.user_data.pop("admin_state", None)
            rek_text = "\n".join(f"  • {r}" for r in lines)
            await update.message.reply_text(
                f"✅ Rekening berhasil diperbarui:\n{rek_text}",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "ubah_kontak":
            kontak_baru = text.strip()
            if len(kontak_baru) < 2 or len(kontak_baru) > 64:
                await update.message.reply_text("❌ Kontak tidak valid. Coba lagi:")
                return
            cfg = load_config()
            cfg["kontak_admin"] = kontak_baru
            save_config(cfg)
            context.user_data.pop("admin_state", None)
            await update.message.reply_text(
                f"✅ Kontak admin diubah menjadi `{kontak_baru}`",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

        if admin_state == "ubah_website":
            raw_url = text.strip()
            if raw_url == "-":
                raw_url = ""
            if raw_url and not (raw_url.startswith("http://") or raw_url.startswith("https://")):
                await update.message.reply_text(
                    "❌ URL harus dimulai dengan `http://` atau `https://`\nCoba lagi:",
                    parse_mode="Markdown"
                )
                return
            cfg = load_config()
            cfg["website_url"] = raw_url
            save_config(cfg)
            context.user_data.pop("admin_state", None)
            label = f"`{raw_url}`" if raw_url else "_(dihapus)_"
            await update.message.reply_text(
                f"✅ Website URL diperbarui: {label}",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await send_main_menu_safe(update, context)
            return

    # ── Custom deposit nominal ───────────────────────────────────────
    if context.user_data.get("awaiting_custom"):
        try:
            nominal = int(text.replace(".", "").replace(",", "").replace(" ", ""))
            if nominal < DEPOSIT_MIN or nominal > DEPOSIT_MAX:
                await update.message.reply_text(
                    f"❌ Nominal harus antara Rp{DEPOSIT_MIN:,} dan Rp{DEPOSIT_MAX:,}. Coba lagi:"
                )
                return
            context.user_data["awaiting_custom"] = False
            context.user_data["nominal_asli"]    = nominal
            context.user_data["total_transfer"]  = nominal + 23
            # Tunjukkan pilihan metode (Manual vs QRIS) lalu hapus keyboard
            await update.message.reply_text("✅", reply_markup=ReplyKeyboardRemove())

            # Kirim method selection sebagai pesan inline baru
            qris_tersedia = _qris_available()
            kb = []
            if qris_tersedia:
                kb.append([_ikb("💳 QRIS (Otomatis / Lebih Cepat)", "", "primary", callback_data=f"dep_qris_{nominal}")])
            kb.append([_ikb("🏦 Transfer Manual (Konfirmasi Admin)", "🏦", "primary", callback_data=f"dep_manual_{nominal}")])
            kb.append([_ikb("🔙 Kembali", "🔙", "danger", callback_data="deposit")])
            metode_hint = (
                "✅ *QRIS* — dikonfirmasi otomatis setelah bayar\n"
                "🏦 *Transfer Manual* — perlu foto bukti & konfirmasi admin"
                if qris_tersedia else
                "🏦 *Transfer Manual* — perlu foto bukti & konfirmasi admin"
            )
            await update.message.reply_text(
                f"💰 *Pilih metode pembayaran*\n\nNominal: *Rp{nominal:,}*\n\n{metode_hint}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except ValueError:
            await update.message.reply_text("❌ Format salah. Ketik angka saja, contoh: 50000")
        return

    # ── Pilih produk dari keyboard ───────────────────────────────────
    if "SOLDOUT" in text:
        text = text.split()[0]

    produk = load_produk()
    if text in produk:
        item       = produk[text]
        tipe_dict  = item.get("tipe", {})
        total_stok = sum(len(t.get("akun_list",[])) for t in tipe_dict.values())
        if total_stok <= 0:
            await update.message.reply_text("❌ Stok habis.", reply_markup=ReplyKeyboardRemove())
            await send_main_menu_safe(update, context)
            return

        await update.message.reply_text("✅", reply_markup=ReplyKeyboardRemove())
        await _send_produk_with_tipe(context.bot, update.effective_user.id, text, item, context)
        return

    # ── Tombol kembali ────────────────────────────────────────────────
    if text in ("🔙 Kembali", "🔥 Kembali ke Menu Utama"):
        await send_main_menu_safe(update, context)
        return

    await send_main_menu_safe(update, context)


# ─── HANDLER FOTO (bukti deposit) ─────────────────────────────────────────────

async def handle_photo(update: Update, context: CallbackContext):
    user = update.effective_user

    # ── Admin upload QRIS ───────────────────────────────────────────
    if is_admin(user.id) and context.user_data.get("admin_state") == "upload_qris":
        photo = update.message.photo[-1]
        file  = await context.bot.get_file(photo.file_id)
        await file.download_to_drive(qris_file)
        context.user_data.pop("admin_state", None)
        await _send_pe(
            context.bot, update.effective_chat.id,
            "✅ *Gambar QRIS berhasil disimpan!*\nUser sekarang bisa bayar via QRIS.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    nominal = context.user_data.get("nominal_asli", 0)

    # ── Deteksi anomali: nominal sangat besar → alert admin ──────────
    if nominal > ANOMALY_THRESHOLD:
        for admin_id in ADMIN_IDS:
            try:
                await _send_pe(
                    context.bot, admin_id,
                    f"⚠️ *DEPOSIT ANOMALI!*\n"
                    f"👤 @{user.username or '-'} (`{user.id}`)\n"
                    f"💸 Nominal: *Rp{nominal:,}*\n"
                    f"_(Melebihi threshold Rp{ANOMALY_THRESHOLD:,})_\n"
                    f"Verifikasi manual sebelum mengkonfirmasi!"
                )
            except Exception:
                pass

    if nominal == 0:
        await update.message.reply_text("⚠️ Kamu belum memilih nominal deposit. Silakan mulai dari menu deposit.")
        return

    photo = update.message.photo[-1]
    file  = await context.bot.get_file(photo.file_id)
    os.makedirs("bukti", exist_ok=True)
    path  = f"bukti/{user.id}_{int(time.time())}.jpg"
    await file.download_to_drive(path)

    total = context.user_data.get("total_transfer", nominal)
    # Cegah duplikat: hapus pending lama user ini, lalu simpan yang baru
    db_remove_pending_any_by_user(user.id)
    db_add_pending({
        "user_id":        user.id,
        "username":       user.username,
        "metode":         "manual",
        "bukti_path":     path,
        "nominal":        nominal,
        "total_transfer": total,
        "waktu":          datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })

    keyboard = InlineKeyboardMarkup([
        [_ikb("✅ Konfirmasi", "✅", "success", callback_data=f"confirm:{user.id}")],
        [_ikb("❌ Tolak",      "",  "danger",   callback_data=f"reject:{user.id}")],
    ])

    # Kirim ke semua admin
    for admin_id in ADMIN_IDS:
        try:
            with open(path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=InputFile(f),
                    caption=(
                        f"📥 *Deposit masuk!*\n"
                        f"👤 @{user.username or '-'} (`{user.id}`)\n"
                        f"💸 Transfer: Rp{total:,}\n"
                        f"💰 Masuk ke saldo: Rp{nominal:,}\n"
                        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
                    ),
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
        except Exception:
            pass

    # Hapus file bukti dari disk setelah dikirim ke semua admin (data sensitif)
    try:
        os.remove(path)
    except OSError:
        pass

    await update.message.reply_text(
        "✅ Bukti transfer berhasil dikirim!\nTunggu konfirmasi dari admin ya.",
        reply_markup=ReplyKeyboardRemove()
    )


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    # Catat user yang sudah start bot
    db_add_bot_user(user.id, user.username)
    # Cek apakah user sudah punya akun web
    existing = web_get_user_by_tid(user.id)
    if not existing:
        # Mulai alur registrasi
        context.user_data["reg_state"] = "reg_phone"
        cfg_r = load_config()
        await update.message.reply_text(
            f"👋 *Selamat datang di {cfg_r.get('nama_toko','Ibra Store')}\\!*\n\n"
            "Untuk mulai berbelanja, kamu perlu *mendaftar* dulu\\.\n\n"
            "📱 *Langkah 1/2* — Masukkan nomor HP kamu:\n"
            "Format: `\\+6281234567890`\n\n"
            "_Nomor ini untuk login di website_",
            parse_mode="MarkdownV2",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("❌ Batal")]], resize_keyboard=True, one_time_keyboard=True
            ),
        )
        return
    await send_main_menu(context, update.effective_chat.id, user)


async def cmd_rekap(update: Update, context: CallbackContext):
    """Rekap penjualan untuk admin. Dukung /rekap hari | bulan | semua."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        rekap = db_get_rekap_penjualan()
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal ambil rekap: {e}")
        return
    b = rekap["beli"]
    d = rekap["deposit"]

    args   = context.args
    filter = args[0].lower() if args else "all"

    if filter in ("hari", "h", "today", "d"):
        label  = f"Hari Ini — {rekap['tanggal']}"
        beli_c, beli_t = b['hari_ini']['count'], b['hari_ini']['total']
        dep_c,  dep_t  = d['hari_ini']['count'], d['hari_ini']['total']
        text = (
            f"📊 *Rekap {label}*\n\n"
            f"🛒 *Penjualan* : *{beli_c}x* — Rp{beli_t:,}\n"
            f"💰 *Deposit*   : *{dep_c}x* — Rp{dep_t:,}\n\n"
            f"_/rekap hari | /rekap bulan | /rekap semua_"
        )
    elif filter in ("bulan", "b", "month", "m"):
        label  = rekap['bulan']
        beli_c, beli_t = b['bulan_ini']['count'], b['bulan_ini']['total']
        dep_c,  dep_t  = d['bulan_ini']['count'], d['bulan_ini']['total']
        text = (
            f"📊 *Rekap {label}*\n\n"
            f"🛒 *Penjualan* : *{beli_c}x* — Rp{beli_t:,}\n"
            f"💰 *Deposit*   : *{dep_c}x* — Rp{dep_t:,}\n\n"
            f"_/rekap hari | /rekap bulan | /rekap semua_"
        )
    elif filter in ("semua", "all", "s", "a"):
        beli_c, beli_t = b['semua']['count'], b['semua']['total']
        dep_c,  dep_t  = d['semua']['count'], d['semua']['total']
        text = (
            f"📊 *Rekap Semua Waktu*\n\n"
            f"🛒 *Penjualan* : *{beli_c}x* — Rp{beli_t:,}\n"
            f"💰 *Deposit*   : *{dep_c}x* — Rp{dep_t:,}\n\n"
            f"_/rekap hari | /rekap bulan | /rekap semua_"
        )
    else:
        text = (
            f"📊 *Rekap Penjualan — {rekap['tanggal']}*\n"
            f"_{rekap['bulan']}_\n\n"
            f"🛒 *PENJUALAN*\n"
            f"• Hari ini : *{b['hari_ini']['count']}x* — Rp{b['hari_ini']['total']:,}\n"
            f"• Bulan ini: *{b['bulan_ini']['count']}x* — Rp{b['bulan_ini']['total']:,}\n"
            f"• Semua    : *{b['semua']['count']}x* — Rp{b['semua']['total']:,}\n\n"
            f"💰 *DEPOSIT*\n"
            f"• Hari ini : *{d['hari_ini']['count']}x* — Rp{d['hari_ini']['total']:,}\n"
            f"• Bulan ini: *{d['bulan_ini']['count']}x* — Rp{d['bulan_ini']['total']:,}\n"
            f"• Semua    : *{d['semua']['count']}x* — Rp{d['semua']['total']:,}\n\n"
            f"_Tip: /rekap hari | /rekap bulan | /rekap semua_"
        )
    await _send_pe(context.bot, update.effective_chat.id, text)


# ─── COMMAND: /dm — Admin kirim DM ke user tertentu ──────────────────────────

async def cmd_dm(update: Update, context: CallbackContext):
    """Admin: /dm <user_id> <pesan> — kirim pesan langsung ke user."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Cara pakai: /dm <user_id> <pesan>")
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id harus berupa angka.")
        return
    msg = " ".join(args[1:])
    try:
        await _send_pe(context.bot, target_id, f"📬 *Pesan dari Admin:*\n\n{msg}")
        await _send_pe(context.bot, update.effective_chat.id,
                       f"✅ Pesan terkirim ke `{target_id}`")
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal: {e}")


# ─── COMMAND: /users — Daftar semua user bot ──────────────────────────────────

async def cmd_users(update: Update, context: CallbackContext):
    """Admin: /users — daftar semua user yang terdaftar di bot."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    users  = db_get_all_bot_users()
    saldo  = db_get_all_saldo()
    stats  = db_get_all_statistik()
    total  = len(users)
    text   = f"👥 *DAFTAR USER BOT* ({total} terdaftar)\n\n"
    for u in users[:30]:
        tid = str(u["telegram_id"])
        s   = saldo.get(tid, 0)
        j   = stats.get(tid, {}).get("jumlah", 0)
        un  = f"@{u['username']}" if u.get("username") else f"`{tid}`"
        text += f"• {un} — 💰Rp{s:,} | 🛒{j}x\n"
    if total > 30:
        text += f"\n_...dan {total - 30} user lainnya_"
    text += f"\n\n_Gunakan /rekap untuk statistik penjualan_"
    await _send_pe(context.bot, update.effective_chat.id, text)


# ─── COMMAND: /stats — Statistik per produk ───────────────────────────────────

async def cmd_stats(update: Update, context: CallbackContext):
    """Admin: /stats [nama_produk] — statistik stok + info per produk."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    produk     = load_produk()
    query_name = " ".join(context.args).lower() if context.args else ""
    text       = "📊 *STATISTIK PRODUK*\n\n"
    found      = False
    for pid, item in produk.items():
        if query_name and query_name not in item["nama"].lower():
            continue
        found = True
        total_stok = 0
        text += f"🎯 *{item['nama']}*\n"
        for tid, tipe in item.get("tipe", {}).items():
            stok_t     = len(tipe.get("akun_list", []))
            total_stok += stok_t
            icon        = "🟢" if stok_t > 0 else "🔴"
            text += f"  {icon} {tipe['nama']}: Rp{tipe.get('harga',0):,} — *{stok_t} stok*\n"
        text += f"  📦 Total stok: *{total_stok}*\n\n"
    if not found:
        text += "_Produk tidak ditemukan._\n\nContoh: /stats netflix"
    await _send_pe(context.bot, update.effective_chat.id, text)


# ─── COMMAND: /addstok — Admin tambah stok via bot ────────────────────────────

async def cmd_addstok(update: Update, context: CallbackContext):
    """Admin: /addstok <produk_id> <tipe_id> <user:pass> — tambah akun ke stok."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Cara pakai: /addstok <produk_id> <tipe_id> <email:password>\n"
            "Contoh: /addstok netflix sharing user@gmail.com:pass123"
        )
        return
    pid, tid = args[0], args[1]
    akun_str = " ".join(args[2:])
    if ":" not in akun_str:
        await update.message.reply_text("❌ Format akun harus: email:password")
        return
    parts    = akun_str.split(":", 1)
    username = parts[0].strip()
    password = parts[1].strip()
    with produk_lock():
        produk = load_produk()
        item   = produk.get(pid)
        if not item:
            await update.message.reply_text(f"❌ Produk `{pid}` tidak ditemukan.\nGunakan /stats untuk lihat produk ID.")
            return
        if tid not in item.get("tipe", {}):
            await update.message.reply_text(f"❌ Tipe `{tid}` tidak ada di produk `{pid}`.")
            return
        item["tipe"][tid]["akun_list"].append({"username": username, "password": password})
        item["tipe"][tid]["stok"] = len(item["tipe"][tid]["akun_list"])
        save_produk(produk)
    await _send_pe(
        context.bot, update.effective_chat.id,
        f"✅ *Stok berhasil ditambahkan!*\n"
        f"📦 `{pid}` → `{tid}`\n"
        f"👤 `{username}`\n"
        f"📊 Total stok sekarang: *{item['tipe'][tid]['stok']}*"
    )


# ─── COMMAND: /voucher — User pakai kode promo ────────────────────────────────

async def cmd_voucher(update: Update, context: CallbackContext):
    """User: /voucher <kode> — tukarkan voucher/kode promo dengan saldo."""
    uid  = str(update.effective_user.id)
    args = context.args
    if not args:
        await _send_pe(context.bot, update.effective_chat.id,
                       "Cara pakai: /voucher <kode>\nContoh: /voucher PROMO2025")
        return
    kode   = args[0].upper().strip()
    result = db_use_voucher(kode, uid)
    if result == "invalid":
        await _send_pe(context.bot, update.effective_chat.id,
                       f"❌ Kode voucher *{kode}* tidak valid atau sudah habis.")
    elif result == "used":
        await _send_pe(context.bot, update.effective_chat.id,
                       f"❌ Kamu sudah pernah menggunakan voucher *{kode}*.")
    elif isinstance(result, int):
        db_add_saldo(uid, result)
        trx_id_vc = db_add_riwayat(uid, "DEPOSIT", f"Voucher {kode}", result)
        new_saldo = db_get_saldo(update.effective_user.id)
        await _send_pe(
            context.bot, update.effective_chat.id,
            f"🎉 *Voucher {kode} berhasil digunakan!*\n"
            f"💰 Saldo bertambah: *Rp{result:,}*\n"
            f"💳 Saldo kamu sekarang: *Rp{new_saldo:,}*"
        )
        await _notify_group(
            context.bot,
            f"🎟 *Voucher Digunakan (Bot)*\n"
            f"👤 `{uid}`\n"
            f"🏷 Kode: `{kode}`\n"
            f"💵 +Rp{result:,}\n"
            f"🔖 `{trx_id_vc}`"
        )
        await send_main_menu(context, update.effective_chat.id, update.effective_user)
    else:
        await _send_pe(context.bot, update.effective_chat.id, "❌ Gagal memproses voucher.")


# ─── COMMAND: /addvoucher — Admin buat voucher baru ───────────────────────────

async def cmd_addvoucher(update: Update, context: CallbackContext):
    """Admin: /addvoucher <kode> <nominal> [max_uses] — buat voucher baru."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Cara pakai: /addvoucher <kode> <nominal> [max_pakai]\n"
            "Contoh: /addvoucher PROMO2025 50000 100"
        )
        return
    kode     = args[0].upper().strip()
    try:
        nominal  = int(args[1])
        max_uses = int(args[2]) if len(args) > 2 else 1
    except ValueError:
        await update.message.reply_text("❌ Nominal dan max_pakai harus angka.")
        return
    ok = db_add_voucher(kode, nominal, max_uses)
    if ok:
        await _send_pe(
            context.bot, update.effective_chat.id,
            f"✅ *Voucher berhasil dibuat!*\n"
            f"🎟 Kode  : `{kode}`\n"
            f"💰 Nilai : Rp{nominal:,}\n"
            f"♻️ Max pakai: {max_uses}x"
        )
    else:
        await update.message.reply_text(f"❌ Kode `{kode}` sudah ada.")


# ─── COMMAND: /tiket — User buat support ticket ───────────────────────────────

async def cmd_tiket(update: Update, context: CallbackContext):
    """User: /tiket <pesan> — buat tiket support."""
    uid   = update.effective_user.id
    args  = context.args
    if not args:
        await _send_pe(context.bot, update.effective_chat.id,
                       "📨 Cara pakai: /tiket <pesan>\n"
                       "Contoh: /tiket Saya tidak menerima akun setelah bayar")
        return
    pesan = " ".join(args).strip()
    if len(pesan) < 5:
        await _send_pe(context.bot, update.effective_chat.id,
                       "❌ Pesan terlalu pendek. Deskripsikan masalahmu dengan jelas.")
        return
    if len(pesan) > 1000:
        await _send_pe(context.bot, update.effective_chat.id,
                       "❌ Pesan terlalu panjang (maks 1000 karakter).")
        return
    tid = db_ticket_create(uid, pesan)
    await _send_pe(context.bot, update.effective_chat.id,
                   f"✅ *Tiket Support #{tid} berhasil dibuat!*\n\n"
                   f"📝 Pesan: _{pesan[:200]}_\n\n"
                   f"Admin akan segera membalas. Terima kasih atas kesabarannya.")
    # Notif admin
    user = update.effective_user
    nama = user.full_name or str(uid)
    notif_msg = (
        f"🎫 *Tiket Support Baru #{tid}*\n\n"
        f"👤 {nama} (`{uid}`)\n"
        f"💬 {pesan[:500]}"
    )
    for admin_id in ADMIN_IDS:
        send_telegram(admin_id, notif_msg)


# ─── COMMAND: /rating — Admin lihat rekap rating ─────────────────────────────

async def cmd_rating(update: Update, context: CallbackContext):
    """Admin: /rating [produk] — rekap rating & ulasan produk."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    query_name = " ".join(context.args) if context.args else None
    ratings    = db_get_ratings(produk=query_name, limit=50)
    if not ratings:
        await update.message.reply_text("⭐ Belum ada rating masuk.")
        return
    avg = sum(r["bintang"] for r in ratings) / len(ratings)
    dist = {i: sum(1 for r in ratings if r["bintang"] == i) for i in range(1, 6)}
    text = (
        f"⭐ *Rekap Rating*\n"
        f"Total: *{len(ratings)} ulasan*\n"
        f"Rata-rata: *{avg:.1f} / 5*\n\n"
        + "".join(f"{'⭐'*i} — {dist[i]}x\n" for i in range(5, 0, -1))
    )
    if query_name:
        text = f"🔍 Filter: _{query_name}_\n\n" + text
    await _send_pe(context.bot, update.effective_chat.id, text)


# ─── GLOBAL ERROR HANDLER ────────────────────────────────────────────────────

async def error_handler(update: object, context: CallbackContext):
    """Tangkap semua error yang tidak tertangkap — log + beritahu user."""
    log.error("❌ Unhandled exception", exc_info=context.error)
    try:
        if update and hasattr(update, "effective_user") and update.effective_user:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="⚠️ Terjadi kesalahan internal. Silakan coba lagi atau hubungi admin."
            )
    except Exception:
        pass


def main():  # Made With love by @govtrashit A.K.A RzkyO
    if not BOT_TOKEN:
        raise RuntimeError("❌ BOT_TOKEN tidak ditemukan di environment variable!")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO,              handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    print(f"✅ Bot {load_config()['nama_toko']} berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
