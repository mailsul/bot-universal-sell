import json  # Made With love by @govtrashit A.K.A RzkyO
import os    # DON'T CHANGE AUTHOR NAME!
import asyncio
import shutil
import time
import random
import logging
import httpx
from produk_lock import produk_lock
from db import (
    init_db,
    db_get_saldo, db_add_saldo, db_set_saldo, db_get_all_saldo,
    db_get_all_pending, db_get_pending_by_user, db_get_pending_any_by_user,
    db_add_pending, db_remove_pending_by_user, db_remove_pending_any_by_user,
    db_update_pending_cek_count, db_remove_pending_by_id,
    db_add_riwayat, db_get_riwayat,
    db_update_statistik, db_get_statistik_user,
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
    InputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, CallbackContext
)
from datetime import datetime

# ─── KONFIGURASI ────────────────────────────────────────────────────────────
BOT_TOKEN         = os.getenv("BOT_TOKEN")
OWNER_ID          = int(os.getenv("OWNER_ID", "1160642744"))
_extra_admins     = os.getenv("ADMIN_IDS", "")
ADMIN_IDS         = set(
    [OWNER_ID] + [int(x) for x in _extra_admins.split(",") if x.strip().isdigit()]
)

LOW_STOCK_THRESHOLD = 2
DEPOSIT_NOMINALS    = [10000, 15000, 20000, 25000, 50000]
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


# ─── HELPER: CONFIG ──────────────────────────────────────────────────────────

_CONFIG_DEFAULT = {
    "nama_toko":    "Store Ekha",
    "rekening":     ["DANA : 0812-XXXX-XXXX a.n Admin"],
    "kontak_admin": "@admin",
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
                           reply_markup=None):
    """Generate QR dinamis dan kirim ke user. Fallback ke file statis jika perlu.
    Mengembalikan objek Message yang terkirim."""
    if QRIS_BASE64:
        img_bytes, _ = generate_qr_with_amount(QRIS_BASE64, nominal, kode)
        if img_bytes:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(img_bytes, filename="qris.png"),
                caption=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
    # Fallback ke file statis
    if os.path.exists(qris_file):
        with open(qris_file, "rb") as f:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f),
                caption=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
    return None


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
                try:
                    await app.bot.send_message(
                        chat_id=p["user_id"],
                        text="⏰ *Pembayaran QRIS kedaluwarsa.*\nSilakan buat permintaan baru.",
                        parse_mode="Markdown"
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

                os.makedirs("akun_dikirim", exist_ok=True)
                stamp     = int(time.time())
                file_path = f"akun_dikirim/{uid}_{produk_id}_x{jumlah}_{stamp}.txt"
                with open(file_path, "w", encoding="utf-8") as f:
                    for i, akun in enumerate(akun_terpakai, start=1):
                        f.write(
                            f"Akun #{i}\n"
                            f"Username : {akun['username']}\n"
                            f"Password : {akun['password']}\n"
                            f"Tipe     : {akun['tipe']}\n"
                            "---------------------------\n"
                        )

            # Teks detail akun — dikirim sebagai pesan teks PLUS file backup
            waktu_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            text_akun = (
                f"✅ *Pembelian QRIS Berhasil!*\n\n"
                f"📦 {item['nama']} x{jumlah}\n"
                f"💸 Dibayar: Rp{nominal:,}\n"
                f"🔖 ID Transaksi: `{trx_id}`\n"
                f"📅 {waktu_str}\n"
                f"─────────────────────\n"
            )
            for i, akun in enumerate(akun_terpakai, start=1):
                text_akun += (
                    f"Akun #{i}\n"
                    f"Username : `{akun['username']}`\n"
                    f"Password : `{akun['password']}`\n"
                    f"─────────────────────\n"
                )

            try:
                await app.bot.send_message(
                    chat_id=p["user_id"],
                    text=text_akun,
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardRemove()
                )
                if file_path and os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        await app.bot.send_document(
                            chat_id=p["user_id"],
                            document=InputFile(f, filename=f"akun_{item['nama'].replace(' ','_')}.txt"),
                            caption="📎 File backup akun kamu.",
                        )
                    os.remove(file_path)
            except Exception:
                pass

            # Notif stok rendah
            sisa_tipe_stok = 0
            if item and tipe_id_p and tipe_id_p in item.get("tipe", {}):
                sisa_tipe_stok = len(item["tipe"][tipe_id_p].get("akun_list", []))
            if item and sisa_tipe_stok <= LOW_STOCK_THRESHOLD:
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
    init_db()
    if URL_MUTASI:
        asyncio.create_task(mutasi_loop(app))


# ─── MENU UTAMA ─────────────────────────────────────────────────────────────

_LOGO_EXTS = ["png", "jpg", "jpeg", "webp"]

def _get_logo_path() -> str | None:
    for ext in _LOGO_EXTS:
        p = os.path.join("static", f"logo.{ext}")
        if os.path.exists(p):
            return p
    return None


async def send_main_menu(bot_or_context, chat_id: int, user):
    # Menerima context (handler) atau bot langsung (background task)
    bot = getattr(bot_or_context, 'bot', bot_or_context)

    s      = db_get_saldo(user.id)
    stat   = db_get_statistik_user(user.id)
    jumlah = stat.get("jumlah", 0)
    total  = stat.get("nominal", 0)

    nama_toko = load_config()["nama_toko"]
    text = (
        f"👋 Selamat datang di *{nama_toko}*!\n\n"
        f"🧑 Nama: {user.full_name}\n"
        f"🆔 ID: `{user.id}`\n"
        f"💰 Saldo: Rp{s:,}\n"
        f"📦 Total Transaksi: {jumlah}\n"
        f"💸 Total Nominal: Rp{total:,}"
    )

    keyboard = [
        [InlineKeyboardButton("📋 List Produk",   callback_data="list_produk"),
         InlineKeyboardButton("🛒 Cek Stok",       callback_data="cek_stok")],
        [InlineKeyboardButton("💰 Deposit Saldo",  callback_data="deposit")],
        [InlineKeyboardButton("📖 Info Bot",        callback_data="info_bot"),
         InlineKeyboardButton("📜 Riwayat",         callback_data="riwayat_user")],
    ]
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(keyboard)

    # Kirim logo jika ada
    logo = _get_logo_path()
    if logo:
        try:
            with open(logo, "rb") as f:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=text,
                    reply_markup=markup,
                    parse_mode="Markdown",
                )
            return
        except Exception:
            pass  # fallback ke send_message biasa

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
        parse_mode="Markdown"
    )


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

async def handle_list_produk(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    msg    = "🛍 *LIST PRODUK*\n\n"
    keyboard, row = [], []

    for pid, item in produk.items():
        tipe_dict  = item.get("tipe", {})
        total_stok = sum(len(t.get("akun_list",[])) for t in tipe_dict.values())
        min_harga  = min((t.get("harga",0) for t in tipe_dict.values()), default=0)
        tipe_count = len(tipe_dict)

        if tipe_count > 1:
            harga_str = f"Rp{min_harga:,}+"
        else:
            harga_str = f"Rp{min_harga:,}"

        msg += f"📦 *{item['nama']}* — {harga_str}\n"
        if tipe_count > 1:
            for tid, t in tipe_dict.items():
                stok_t = len(t.get("akun_list",[]))
                msg += f"  └ {t['nama']}: Rp{t.get('harga',0):,} {'✅' if stok_t>0 else '❌'}\n"

        if total_stok > 0:
            row.append(KeyboardButton(pid))
        else:
            row.append(KeyboardButton(f"{pid} SOLDOUT ❌"))
        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)
    keyboard.append([KeyboardButton("🔙 Kembali")])

    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=msg + "\n📌 Pilih nomor produk:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown"
    )


async def handle_cek_stok(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    now    = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
    msg    = f"📦 *Informasi Stok*\n_{now}_\n\n"
    keyboard, row = [], []

    for pid, item in produk.items():
        tipe_dict  = item.get("tipe", {})
        total_stok = sum(len(t.get("akun_list",[])) for t in tipe_dict.values())
        icon = "✅" if total_stok > LOW_STOCK_THRESHOLD else ("⚠️" if total_stok > 0 else "❌")
        msg += f"{icon} *{item['nama']}*\n"
        for tid, t in tipe_dict.items():
            stok_t = len(t.get("akun_list",[]))
            ic = "✅" if stok_t > 0 else "❌"
            msg += f"  {ic} {t['nama']}: *{stok_t}* unit\n"
        msg += "\n"

        if total_stok > 0:
            row.append(KeyboardButton(pid))
        else:
            row.append(KeyboardButton(f"{pid} SOLDOUT ❌"))
        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)
    keyboard.append([KeyboardButton("🔙 Kembali")])

    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=msg,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown"
    )


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
            InlineKeyboardButton("➖", callback_data="qty_minus"),
            InlineKeyboardButton(f"  {jumlah}  ", callback_data="ignore"),
            InlineKeyboardButton("➕", callback_data="qty_plus"),
        ],
        [InlineKeyboardButton("✅ Konfirmasi Order", callback_data="confirm_order")],
        [InlineKeyboardButton("🔙 Kembali",          callback_data="back_to_produk")],
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
        if gambar:
            try:
                base = gambar.lstrip("/")
                with open(base, "rb") as f:
                    await bot.send_photo(chat_id=chat_id, photo=InputFile(f),
                                         caption=order_txt, reply_markup=kb, parse_mode="Markdown")
                return
            except Exception:
                pass
        await bot.send_message(chat_id=chat_id, text=order_txt, reply_markup=kb, parse_mode="Markdown")
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
            btn = InlineKeyboardButton(f"{t['nama']} Rp{t.get('harga',0):,}", callback_data=f"tipe_{pid}_{tid}")
        else:
            btn = InlineKeyboardButton(f"❌ {t['nama']} (habis)", callback_data="ignore")
        row.append(btn)
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton("🔙 Kembali ke Menu", callback_data="back_to_produk")])

    text = "\n".join(lines)
    kb   = InlineKeyboardMarkup(kb_rows)

    if gambar:
        try:
            base = gambar.lstrip("/")
            with open(base, "rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=InputFile(f),
                                     caption=text, reply_markup=kb, parse_mode="Markdown")
            return
        except Exception:
            pass
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
    try:
        await query.edit_message_text(order_txt, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        try:
            await query.edit_message_caption(order_txt, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass


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
    try:
        await query.edit_message_text(txt, reply_markup=_order_keyboard(jumlah), parse_mode="Markdown")
    except Exception:
        try:
            await query.edit_message_caption(txt, reply_markup=_order_keyboard(jumlah), parse_mode="Markdown")
        except Exception:
            pass


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
    try:
        await query.edit_message_text(txt, reply_markup=_order_keyboard(jumlah), parse_mode="Markdown")
    except Exception:
        try:
            await query.edit_message_caption(txt, reply_markup=_order_keyboard(jumlah), parse_mode="Markdown")
        except Exception:
            pass


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

    saldo_user = db_get_saldo(query.from_user.id)
    nama_tipe  = tipe_obj.get("nama", "")
    msg_text   = (
        f"💳 *Pilih metode pembayaran*\n\n"
        f"📦 {item['nama']} [{nama_tipe}] x{jumlah}\n"
        f"💸 Total: *Rp{total:,}*\n"
        f"💰 Saldo kamu: Rp{saldo_user:,}"
    )

    # Pilih metode pembayaran
    if _qris_available():
        kb = []
        if saldo_user >= total:
            kb.append([InlineKeyboardButton(
                f"💰 Bayar dengan Saldo (Rp{saldo_user:,})",
                callback_data="confirm_saldo"
            )])
        kb.append([InlineKeyboardButton("💳 Bayar via QRIS (Otomatis)", callback_data="beli_qris")])
        if saldo_user < total:
            kb.append([InlineKeyboardButton("💰 Top Up Saldo dulu", callback_data="deposit")])
        kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_to_produk")])
        try:
            await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        except Exception:
            try:
                await query.edit_message_caption(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            except Exception:
                pass
    else:
        # QRIS tidak tersedia — langsung proses dengan saldo
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
    total      = jumlah * tipe_obj.get("harga", 0)
    saldo_user = db_get_saldo(query.from_user.id)

    await _proses_beli_saldo(query, context, info, item, tipe_obj, jumlah, total, saldo_user)


async def _proses_beli_saldo(query, context, info, item, tipe_obj, jumlah, total, saldo_user):
    """Logika inti pembelian menggunakan saldo — dipanggil setelah metode dipilih."""
    uid       = str(query.from_user.id)
    produk_id = info["produk_id"]
    tipe_id   = info.get("tipe_id") or next(iter(item.get("tipe", {})), None)

    if saldo_user < total:
        kb_rows = [
            [InlineKeyboardButton("💰 Deposit Saldo", callback_data="deposit")],
        ]
        if _qris_available():
            kb_rows.append([InlineKeyboardButton("💳 Bayar via QRIS (Otomatis)", callback_data="beli_qris")])
        kb_rows.append([InlineKeyboardButton("🔙 Kembali ke Menu", callback_data="back_to_produk")])
        try:
            await query.edit_message_text(
                "❌ *Saldo tidak cukup.*\nSilakan deposit atau bayar langsung via QRIS.",
                reply_markup=InlineKeyboardMarkup(kb_rows),
                parse_mode="Markdown"
            )
        except Exception:
            try:
                await query.edit_message_caption(
                    "❌ *Saldo tidak cukup.*",
                    reply_markup=InlineKeyboardMarkup(kb_rows),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
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
        trx_id = db_add_riwayat(uid, "BELI", f"{item['nama']} [{nama_tipe}] x{jumlah}", total)

        os.makedirs("akun_dikirim", exist_ok=True)
        stamp     = int(time.time())
        file_path = f"akun_dikirim/{uid}_{produk_id}_x{jumlah}_{stamp}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            for i, akun in enumerate(akun_terpakai, start=1):
                f.write(
                    f"Akun #{i}\n"
                    f"Username : {akun['username']}\n"
                    f"Password : {akun['password']}\n"
                    "---------------------------\n"
                )

    # Teks detail akun — dikirim sebagai pesan teks PLUS file backup
    waktu_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    text_akun = (
        f"✅ *Pembelian Berhasil!*\n\n"
        f"📦 {item['nama']} [{nama_tipe}] x{jumlah}\n"
        f"💸 Dipotong: Rp{total:,}\n"
        f"💰 Sisa saldo: Rp{new_saldo:,}\n"
        f"🔖 ID Transaksi: `{trx_id}`\n"
        f"📅 {waktu_str}\n"
        f"─────────────────────\n"
    )
    for i, akun in enumerate(akun_terpakai, start=1):
        text_akun += (
            f"Akun #{i}\n"
            f"Username : `{akun['username']}`\n"
            f"Password : `{akun['password']}`\n"
            f"─────────────────────\n"
        )

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=text_akun,
        parse_mode="Markdown"
    )
    with open(file_path, "rb") as f:
        await context.bot.send_document(
            chat_id=query.from_user.id,
            document=InputFile(f, filename=f"akun_{item['nama'].replace(' ', '_')}.txt"),
            caption="📎 File backup akun kamu.",
        )
    try:
        os.remove(file_path)
    except OSError:
        pass

    sisa_stok = len(tipe_now.get("akun_list", []))
    if sisa_stok <= LOW_STOCK_THRESHOLD:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ *PERINGATAN STOK RENDAH*\n"
                        f"Produk: {item['nama']} [{nama_tipe}]\n"
                        f"Sisa stok: {sisa_stok}x\n"
                        f"Segera lakukan restock!"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    context.user_data.pop("konfirmasi", None)
    await send_main_menu(context, query.from_user.id, query.from_user)


# ─── DEPOSIT ─────────────────────────────────────────────────────────────────

async def handle_deposit(update: Update, context: CallbackContext):
    query         = update.callback_query
    qris_tersedia = _qris_available()
    keyboard      = [[InlineKeyboardButton(f"Rp{n:,}", callback_data=f"deposit_{n}") for n in DEPOSIT_NOMINALS]]
    keyboard.append([InlineKeyboardButton("🔧 Custom Nominal", callback_data="deposit_custom")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_to_produk")])
    qris_note = "\n✅ _QRIS tersedia — pilih nominal lalu pilih metode!_" if qris_tersedia else ""
    await query.edit_message_text(
        f"💰 *Pilih nominal deposit:*\n_(Min: Rp{DEPOSIT_MIN:,} | Max: Rp{DEPOSIT_MAX:,})_{qris_note}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


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
        await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await target.message.delete()
        await context.bot.send_message(chat_id=target.from_user.id, text=text, parse_mode="Markdown", reply_markup=kb)


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
        kb.append([InlineKeyboardButton("💳 QRIS (Otomatis / Lebih Cepat)", callback_data=f"dep_qris_{nominal}")])
    if manual_aktif:
        kb.append([InlineKeyboardButton("🏦 Transfer Manual (Konfirmasi Admin)", callback_data=f"dep_manual_{nominal}")])
    if not kb:
        kb.append([InlineKeyboardButton("❌ Metode deposit sedang tidak tersedia", callback_data="ignore")])
    kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="deposit")])

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
        await query_or_message.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        try:
            await query_or_message.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query_or_message.from_user.id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )


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
    await query.edit_message_text("✅ Deposit dibatalkan.")
    await send_main_menu(context, query.from_user.id, query.from_user)


async def handle_deposit_qris(update: Update, context: CallbackContext):
    """User memilih QRIS untuk deposit — tampilkan pilihan nominal dulu."""
    query = update.callback_query
    if not _qris_available():
        await query.answer("❌ QRIS belum diatur admin.", show_alert=True)
        return
    keyboard = [[InlineKeyboardButton(f"Rp{n:,}", callback_data=f"qris_dep_{n}") for n in DEPOSIT_NOMINALS]]
    keyboard.append([InlineKeyboardButton("🔧 Custom Nominal", callback_data="qris_dep_custom")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali",        callback_data="deposit")])
    await query.edit_message_text(
        f"💳 *Deposit via QRIS*\n_(Min: Rp{DEPOSIT_MIN:,} | Max: Rp{DEPOSIT_MAX:,})_\n\n"
        "Pilih nominal deposit:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
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
        InlineKeyboardButton("🔄 Cek Sekarang (3x tersisa)", callback_data="cek_mutasi")
    ]])
    await _send_qris_photo(context.bot, user.id, nominal, kode, caption, reply_markup=kb)


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
        InlineKeyboardButton("🔄 Cek Sekarang (3x tersisa)", callback_data="cek_mutasi")
    ]])
    await _send_qris_photo(context.bot, query.from_user.id, nominal, kode, caption, reply_markup=kb)


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
        InlineKeyboardButton(label, callback_data="cek_mutasi")
    ]])
    try:
        await query.edit_message_reply_markup(reply_markup=kb)
    except Exception:
        pass


# ─── RIWAYAT USER ─────────────────────────────────────────────────────────────

async def handle_riwayat_user(update: Update, context: CallbackContext):
    """Tampilkan riwayat transaksi user (via button atau command /riwayat)."""
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        send_fn = lambda txt, kb: update.callback_query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")
    else:
        user_id = update.effective_user.id
        send_fn = lambda txt, kb: update.message.reply_text(txt, reply_markup=kb, parse_mode="Markdown")

    data = db_get_riwayat(user_id, RIWAYAT_LIMIT)

    if not data:
        text = "📜 *Riwayat Transaksi*\n\nBelum ada transaksi."
    else:
        text = f"📜 *Riwayat Transaksi* (last {len(data)})\n\n"
        for r in data:
            icon  = "📥" if r["tipe"] == "DEPOSIT" else "🛒"
            trx   = f"\n   🔖 `{r['trx_id']}`" if r.get("trx_id") else ""
            text += f"{icon} `{r['tipe']}` — Rp{r['jumlah']:,}\n"
            text += f"   _{r['keterangan']}_\n"
            text += f"   🕐 {r['waktu']}{trx}\n\n"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Menu", callback_data="back_to_produk")]])
    await send_fn(text, kb)


async def cmd_riwayat(update: Update, context: CallbackContext):
    await handle_riwayat_user(update, context)


# ─── ADMIN PANEL ──────────────────────────────────────────────────────────────

async def handle_admin_panel(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return

    saldo   = db_get_all_saldo()
    pending = db_get_all_pending()

    text = "*📊 DATA USER:*\n"
    for u, s in saldo.items():
        text += f"• `{u}`: Rp{s:,}\n"

    text += "\n*⏳ PENDING DEPOSIT:*\n"
    if pending:
        for p in pending:
            text += f"- @{p.get('username') or p['user_id']} (`{p['user_id']}`) → Rp{p['nominal']:,}\n"
    else:
        text += "_Tidak ada._\n"

    keyboard = [
        [InlineKeyboardButton("📦 Kelola Produk",   callback_data="admin_kelola_produk")],
        [InlineKeyboardButton("⚙️ Pengaturan Bot",  callback_data="admin_settings")],
        [InlineKeyboardButton("🔙 Kembali ke Menu", callback_data="back_to_produk")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_admin_kelola_produk(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return

    produk  = load_produk()
    produk_list = "\n".join([f"`{pid}` - {item['nama']} ({item['stok']}x)" for pid, item in produk.items()])
    text    = f"*📦 KELOLA PRODUK*\n\n{produk_list or '_Belum ada produk._'}"
    keyboard = [
        [InlineKeyboardButton("➕ Tambah Produk",  callback_data="admin_add_produk")],
        [InlineKeyboardButton("📦 Restock",         callback_data="admin_restock_produk")],
        [InlineKeyboardButton("🗑 Hapus Produk",    callback_data="admin_hapus_produk")],
        [InlineKeyboardButton("🔙 Kembali",         callback_data="admin_panel")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


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
    query  = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    produk = load_produk()
    if not produk:
        await query.answer("Belum ada produk!", show_alert=True)
        return
    context.user_data["admin_state"] = "restock_pid"
    keyboard = [[KeyboardButton(f"{pid} - {item['nama']}")] for pid, item in produk.items()]
    keyboard.append([KeyboardButton("❌ Batal")])
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="📦 *RESTOCK PRODUK*\n\nPilih ID produk yang ingin direstock:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
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


# ─── ADMIN: PENGATURAN BOT ───────────────────────────────────────────────────

async def handle_admin_settings(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak", show_alert=True)
        return
    cfg  = load_config()
    rek  = "\n".join(f"  • {r}" for r in cfg.get("rekening", []))
    text = (
        f"⚙️ *PENGATURAN BOT*\n\n"
        f"🏪 *Nama Toko*: `{cfg['nama_toko']}`\n\n"
        f"🏦 *Rekening*:\n{rek}\n\n"
        f"📞 *Kontak Admin*: `{cfg['kontak_admin']}`"
    )
    qris_status = "✅ Aktif via env var" if QRIS_BASE64 else ("✅ Ada (gambar)" if os.path.exists(qris_file) else "❌ Belum diatur")
    text += f"\n\n📷 *QRIS*: {qris_status}"
    keyboard = [
        [InlineKeyboardButton("✏️ Ubah Nama Toko",    callback_data="admin_ubah_nama")],
        [InlineKeyboardButton("🏦 Ubah Rekening",      callback_data="admin_ubah_rekening")],
        [InlineKeyboardButton("📞 Ubah Kontak Admin",  callback_data="admin_ubah_kontak")],
        [InlineKeyboardButton("📷 Upload Gambar QRIS", callback_data="admin_upload_qris")],
        [InlineKeyboardButton("🔙 Kembali",            callback_data="admin_panel")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


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
        [InlineKeyboardButton("✅ YA, konfirmasi",  callback_data=f"final:{user_id}")],
        [InlineKeyboardButton("❌ Tolak deposit",   callback_data=f"reject:{user_id}")],
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
        await query.edit_message_caption(result_text, parse_mode="Markdown")
    except Exception:
        await query.edit_message_text(result_text, parse_mode="Markdown")

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"✅ Deposit *Rp{nominal:,}* telah dikonfirmasi dan masuk ke saldo kamu!\n"
            f"🔖 ID Transaksi: `{trx_id}`"
        ),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await send_main_menu(context, user_id, await context.bot.get_chat(user_id))


async def handle_admin_reject(update: Update, context: CallbackContext):
    query   = update.callback_query
    user_id = int(query.data.split(":")[1])

    db_remove_pending_any_by_user(user_id)

    try:
        await query.edit_message_caption("❌ Deposit telah ditolak.", parse_mode="Markdown")
    except Exception:
        await query.edit_message_text("❌ Deposit telah ditolak.")

    await context.bot.send_message(
        chat_id=user_id,
        text="❌ Deposit kamu *ditolak* oleh admin. Hubungi admin jika ada pertanyaan.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
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
    query = update.callback_query
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_main_menu(context, query.from_user.id, query.from_user)


async def handle_info_bot(update: Update, context: CallbackContext):
    query  = update.callback_query
    cfg    = load_config()
    nama   = cfg["nama_toko"]
    kontak = cfg["kontak_admin"]
    text   = (
        f"📖 *{nama}*\n"
        "╭─────────────────────────────╮\n"
        "├ 🛒 *Layanan*\n"
        "│   Jual akun digital & subscription\n"
        "│   premium secara otomatis.\n"
        "├─────────────────────────────\n"
        "├ 💰 *Cara Deposit*\n"
        "│   1. Pilih menu Deposit Saldo\n"
        "│   2. Pilih atau ketik nominal\n"
        "│   3. Transfer ke rekening kami\n"
        "│   4. Kirim foto bukti transfer\n"
        "│   5. Tunggu konfirmasi admin\n"
        "├─────────────────────────────\n"
        "├ 🛍️ *Cara Beli*\n"
        "│   1. Pilih List Produk\n"
        "│   2. Pilih produk & atur jumlah\n"
        "│   3. Konfirmasi — akun langsung\n"
        "│      dikirim otomatis via bot\n"
        "├─────────────────────────────\n"
        f"├ 📞 *Hubungi Admin*: {kontak}\n"
        "╰─────────────────────────────╯"
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Menu", callback_data="back_to_produk")]])
    await query.edit_message_text(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)


async def handle_ignore(update: Update, context: CallbackContext):
    await update.callback_query.answer()


# ─── ROUTING CALLBACK ─────────────────────────────────────────────────────────

CALLBACK_MAP = {
    "list_produk":            handle_list_produk,
    "cek_stok":               handle_cek_stok,
    "info_bot":               handle_info_bot,
    "deposit":                handle_deposit,
    "deposit_custom":         handle_deposit_nominal,
    "cancel_deposit":         handle_cancel_deposit,
    "admin_panel":            handle_admin_panel,
    "admin_kelola_produk":    handle_admin_kelola_produk,
    "admin_add_produk":       handle_admin_add_produk,
    "admin_restock_produk":   handle_admin_restock_produk,
    "admin_hapus_produk":     handle_admin_hapus_produk,
    "admin_settings":         handle_admin_settings,
    "admin_ubah_nama":        handle_admin_ubah_nama,
    "admin_ubah_rekening":    handle_admin_ubah_rekening,
    "admin_ubah_kontak":      handle_admin_ubah_kontak,
    "admin_upload_qris":      handle_admin_upload_qris,
    "deposit_qris":           handle_deposit_qris,
    "qris_dep_custom":        handle_qris_dep_nominal,
    "beli_qris":              handle_beli_qris,
    "cek_mutasi":             handle_cek_mutasi,
    "qty_plus":               handle_qty_plus,
    "qty_minus":              handle_qty_minus,
    "confirm_order":          handle_confirm_order,
    "confirm_saldo":          handle_confirm_saldo,
    "back":                   handle_back,
    "back_to_produk":         handle_back_to_produk,
    "riwayat_user":           handle_riwayat_user,
    "ignore":                 handle_ignore,
}


async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data  = query.data
    log.info(f"🖱️ Tombol: [{data}] oleh user={query.from_user.id} (@{query.from_user.username})")

    produk = load_produk()
    if data in produk:
        await handle_produk_detail(update, context)
    elif data.startswith("tipe_"):
        await handle_tipe_select(update, context)
    elif data.startswith("deposit_"):
        await handle_deposit_nominal(update, context)
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
        # Bersihkan semua state
        for key in ["awaiting_custom", "awaiting_qris_custom", "nominal_asli", "total_transfer",
                    "admin_state", "new_produk", "restock_pid",
                    "konfirmasi"]:
            context.user_data.pop(key, None)
        await update.message.reply_text("✅ Dibatalkan.", reply_markup=ReplyKeyboardRemove())
        await send_main_menu_safe(update, context)
        return

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
                parts = line.split("|")
                if len(parts) != 3:
                    errors.append(f"Baris {i}: format salah (harus `email|password|tipe`)")
                    continue
                akun_list.append({"username": parts[0].strip(), "password": parts[1].strip(), "tipe": parts[2].strip()})

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

        if admin_state == "restock_pid":
            pid = text.split(" - ")[0].strip()
            produk = load_produk()
            if pid not in produk:
                await update.message.reply_text("❌ ID produk tidak valid. Coba lagi:")
                return
            context.user_data["restock_pid"]  = pid
            context.user_data["admin_state"] = "restock_akun"
            await update.message.reply_text(
                f"📦 Restock: *{produk[pid]['nama']}* (stok saat ini: {produk[pid]['stok']}x)\n\n"
                "Kirim akun baru (satu per baris):\n`email|password|tipe`",
                parse_mode="Markdown"
            )
            return

        if admin_state == "restock_akun":
            pid    = context.user_data.get("restock_pid")
            produk = load_produk()
            if not pid or pid not in produk:
                await update.message.reply_text("❌ Produk tidak ditemukan. Ulangi dari awal.")
                context.user_data.pop("admin_state", None)
                return

            lines     = [l.strip() for l in text.strip().splitlines() if l.strip()]
            akun_baru = []
            errors    = []
            for i, line in enumerate(lines, 1):
                parts = line.split("|")
                if len(parts) != 3:
                    errors.append(f"Baris {i}: format salah")
                    continue
                akun_baru.append({"username": parts[0].strip(), "password": parts[1].strip(), "tipe": parts[2].strip()})

            if errors:
                await update.message.reply_text("❌ Format salah:\n" + "\n".join(errors) + "\n\nCoba lagi:")
                return

            produk[pid]["akun_list"].extend(akun_baru)
            save_produk(produk)
            for key in ["admin_state", "restock_pid"]:
                context.user_data.pop(key, None)

            await update.message.reply_text(
                f"✅ Berhasil tambah {len(akun_baru)} akun ke *{produk[pid]['nama']}*\n"
                f"Stok sekarang: {produk[pid]['stok']}x",
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
                kb.append([InlineKeyboardButton("💳 QRIS (Otomatis / Lebih Cepat)", callback_data=f"dep_qris_{nominal}")])
            kb.append([InlineKeyboardButton("🏦 Transfer Manual (Konfirmasi Admin)", callback_data=f"dep_manual_{nominal}")])
            kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="deposit")])
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
    if text == "🔙 Kembali":
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
        await update.message.reply_text(
            "✅ *Gambar QRIS berhasil disimpan!*\nUser sekarang bisa bayar via QRIS.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    nominal = context.user_data.get("nominal_asli", 0)

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
        [InlineKeyboardButton("✅ Konfirmasi",  callback_data=f"confirm:{user.id}")],
        [InlineKeyboardButton("❌ Tolak",       callback_data=f"reject:{user.id}")],
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
    await send_main_menu(context, update.effective_chat.id, update.effective_user)


def main():  # Made With love by @govtrashit A.K.A RzkyO
    if not BOT_TOKEN:
        raise RuntimeError("❌ BOT_TOKEN tidak ditemukan di environment variable!")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("riwayat",  cmd_riwayat))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO,              handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print(f"✅ Bot {load_config()['nama_toko']} berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
