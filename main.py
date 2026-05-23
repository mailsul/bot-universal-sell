import json  # Made With love by @govtrashit A.K.A RzkyO
import os    # DON'T CHANGE AUTHOR NAME!
import asyncio
import shutil
import time
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
RIWAYAT_LIMIT       = 10   # jumlah transaksi yang ditampilkan di /riwayat

produk_file    = "produk.json"
saldo_file     = "saldo.json"
deposit_file   = "pending_deposit.json"
riwayat_file   = "riwayat.json"
statistik_file = "statistik.json"
config_file    = "config.json"

# Lock global untuk mencegah race condition saat beli produk
purchase_lock = asyncio.Lock()


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


def sync_stok(produk: dict) -> dict:
    """Pastikan field stok selalu sinkron dengan panjang akun_list."""
    for item in produk.values():
        item["stok"] = len(item.get("akun_list", []))
    return produk


def load_produk() -> dict:
    """Load produk dan otomatis sinkronkan stok."""
    return sync_stok(load_json(produk_file))


def save_produk(produk: dict):
    save_json(produk_file, sync_stok(produk), backup=True)


# ─── HELPER: STATISTIK & RIWAYAT ────────────────────────────────────────────

def update_statistik(uid, nominal: int):
    statistik = load_json(statistik_file)
    uid = str(uid)
    if uid not in statistik:
        statistik[uid] = {"jumlah": 0, "nominal": 0}
    statistik[uid]["jumlah"] += 1
    statistik[uid]["nominal"] += nominal
    save_json(statistik_file, statistik)


def add_riwayat(uid, tipe: str, keterangan: str, jumlah: int):
    riwayat = load_json(riwayat_file)
    uid = str(uid)
    if uid not in riwayat:
        riwayat[uid] = []
    riwayat[uid].append({
        "tipe": tipe,
        "keterangan": keterangan,
        "jumlah": jumlah,
        "waktu": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })
    save_json(riwayat_file, riwayat, backup=False)
    if tipe == "BELI":
        update_statistik(uid, jumlah)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ─── MENU UTAMA ─────────────────────────────────────────────────────────────

async def send_main_menu(context, chat_id: int, user):
    saldo     = load_json(saldo_file)
    statistik = load_json(statistik_file)
    s      = saldo.get(str(user.id), 0)
    jumlah = statistik.get(str(user.id), {}).get("jumlah", 0)
    total  = statistik.get(str(user.id), {}).get("nominal", 0)

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

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
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
    msg    = "*📋 LIST PRODUK*\n\n"
    keyboard, row = [], []

    for i, (pid, item) in enumerate(produk.items(), start=1):
        stok_label = f"{item['stok']}x" if item["stok"] > 0 else "HABIS"
        msg += f"`{pid}` {item['nama']} — Rp{item.get('harga', 0):,} [{stok_label}]\n"
        if item["stok"] > 0:
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
        text=msg + "\nPilih nomor produk yang ingin dibeli:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown"
    )


async def handle_cek_stok(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    now    = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
    msg    = f"*📦 Informasi Stok*\n_{now}_\n\n"
    keyboard, row = [], []

    for pid, item in produk.items():
        stok = item["stok"]
        icon = "✅" if stok > LOW_STOCK_THRESHOLD else ("⚠️" if stok > 0 else "❌")
        msg += f"{icon} `{pid}`. {item['nama']} → {stok}x\n"
        if stok > 0:
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

def _order_text(item: dict, jumlah: int) -> str:
    tipe  = item["akun_list"][0]["tipe"] if item["akun_list"] else "-"
    total = jumlah * item["harga"]
    return (
        "🛒 *KONFIRMASI PESANAN*\n"
        "╭─────────────────────────╮\n"
        f"┊ Produk     : {item['nama']}\n"
        f"┊ Variasi    : {tipe}\n"
        f"┊ Harga/pcs  : Rp{item['harga']:,}\n"
        f"┊ Stok       : {item['stok']}x\n"
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


async def handle_produk_detail(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    item   = produk.get(query.data)

    if not item or item["stok"] <= 0:
        await query.answer("❌ Produk habis atau tidak tersedia", show_alert=True)
        return

    context.user_data["konfirmasi"] = {"produk_id": query.data, "jumlah": 1}
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=_order_text(item, 1),
        reply_markup=_order_keyboard(1),
        parse_mode="Markdown"
    )


async def handle_qty_plus(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    info   = context.user_data.get("konfirmasi")
    if not info:
        await query.answer("Data tidak tersedia")
        return

    item   = produk.get(info["produk_id"])
    if not item:
        await query.answer("Produk tidak ditemukan")
        return

    jumlah = info["jumlah"]
    if jumlah < item["stok"]:
        jumlah += 1
    context.user_data["konfirmasi"]["jumlah"] = jumlah
    await query.edit_message_text(_order_text(item, jumlah), reply_markup=_order_keyboard(jumlah), parse_mode="Markdown")


async def handle_qty_minus(update: Update, context: CallbackContext):
    query  = update.callback_query
    produk = load_produk()
    info   = context.user_data.get("konfirmasi")
    if not info:
        await query.answer("Data tidak tersedia")
        return

    item   = produk.get(info["produk_id"])
    if not item:
        await query.answer("Produk tidak ditemukan")
        return

    jumlah = info["jumlah"]
    if jumlah > 1:
        jumlah -= 1
    context.user_data["konfirmasi"]["jumlah"] = jumlah
    await query.edit_message_text(_order_text(item, jumlah), reply_markup=_order_keyboard(jumlah), parse_mode="Markdown")


async def handle_confirm_order(update: Update, context: CallbackContext):
    """Proses pembelian dengan lock untuk mencegah race condition."""
    query = update.callback_query
    uid   = str(query.from_user.id)
    info  = context.user_data.get("konfirmasi")

    if not info:
        await query.answer("❌ Data pesanan tidak ditemukan", show_alert=True)
        return

    async with purchase_lock:
        produk = load_produk()
        saldo  = load_json(saldo_file)

        produk_id = info["produk_id"]
        jumlah    = info["jumlah"]
        item      = produk.get(produk_id)

        if not item:
            await query.edit_message_text("❌ Produk tidak ditemukan.")
            return

        total = jumlah * item["harga"]

        if saldo.get(uid, 0) < total:
            await query.edit_message_text(
                "❌ *Saldo tidak cukup.*\nSilakan deposit terlebih dahulu.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 Deposit Saldo",    callback_data="deposit")],
                    [InlineKeyboardButton("🔙 Kembali ke Menu",  callback_data="back_to_produk")],
                ]),
                parse_mode="Markdown"
            )
            return

        if item["stok"] < jumlah or len(item.get("akun_list", [])) < jumlah:
            await query.edit_message_text("❌ Stok tidak mencukupi. Silakan pilih jumlah lebih sedikit.")
            return

        # Proses transaksi
        saldo[uid] = saldo.get(uid, 0) - total
        akun_terpakai = [item["akun_list"].pop(0) for _ in range(jumlah)]
        save_json(saldo_file, saldo, backup=True)
        save_produk(produk)
        add_riwayat(uid, "BELI", f"{item['nama']} x{jumlah}", total)

        # Kirim akun sebagai file .txt dengan nama unik (timestamp)
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

    # Kirim file di luar lock, lalu hapus dari disk (data sensitif)
    with open(file_path, "rb") as f:
        await context.bot.send_document(
            chat_id=query.from_user.id,
            document=InputFile(f, filename=f"akun_{item['nama'].replace(' ', '_')}.txt"),
            caption=(
                f"✅ *Pembelian berhasil!*\n"
                f"📦 {item['nama']} x{jumlah}\n"
                f"💸 Dipotong: Rp{total:,}\n"
                f"💰 Sisa saldo: Rp{saldo[uid]:,}"
            ),
            parse_mode="Markdown"
        )
    try:
        os.remove(file_path)
    except OSError:
        pass

    # Notifikasi stok hampir habis ke semua admin
    if item["stok"] <= LOW_STOCK_THRESHOLD:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ *PERINGATAN STOK RENDAH*\n"
                        f"Produk: {item['nama']}\n"
                        f"Sisa stok: {item['stok']}x\n"
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
    query    = update.callback_query
    keyboard = [[InlineKeyboardButton(f"Rp{n:,}", callback_data=f"deposit_{n}") for n in DEPOSIT_NOMINALS]]
    keyboard.append([InlineKeyboardButton("🔧 Custom Nominal", callback_data="deposit_custom")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali",        callback_data="back_to_produk")])
    await query.edit_message_text(
        f"💰 *Pilih nominal deposit:*\n_(Min: Rp{DEPOSIT_MIN:,} | Max: Rp{DEPOSIT_MAX:,})_",
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
    context.user_data["nominal_asli"]    = nominal
    context.user_data["total_transfer"]  = nominal + 23
    await _send_deposit_instructions(query, context, nominal, is_message=False)


async def handle_cancel_deposit(update: Update, context: CallbackContext):
    query = update.callback_query
    uid   = str(query.from_user.id)
    pending = load_json(deposit_file)
    pending = [p for p in pending if str(p["user_id"]) != uid]
    save_json(deposit_file, pending)
    context.user_data.pop("nominal_asli",   None)
    context.user_data.pop("total_transfer", None)
    context.user_data.pop("awaiting_custom", None)
    await query.edit_message_text("✅ Deposit dibatalkan.")
    await send_main_menu(context, query.from_user.id, query.from_user)


# ─── RIWAYAT USER ─────────────────────────────────────────────────────────────

async def handle_riwayat_user(update: Update, context: CallbackContext):
    """Tampilkan riwayat transaksi user (via button atau command /riwayat)."""
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        send_fn = lambda txt, kb: update.callback_query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")
    else:
        user_id = update.effective_user.id
        send_fn = lambda txt, kb: update.message.reply_text(txt, reply_markup=kb, parse_mode="Markdown")

    riwayat = load_json(riwayat_file)
    data    = riwayat.get(str(user_id), [])

    if not data:
        text = "📜 *Riwayat Transaksi*\n\nBelum ada transaksi."
    else:
        recent = data[-RIWAYAT_LIMIT:][::-1]
        text   = f"📜 *Riwayat Transaksi* (last {len(recent)})\n\n"
        for r in recent:
            icon = "📥" if r["tipe"] == "DEPOSIT" else "🛒"
            text += f"{icon} `{r['tipe']}` — Rp{r['jumlah']:,}\n"
            text += f"   _{r['keterangan']}_\n"
            text += f"   🕐 {r['waktu']}\n\n"

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

    saldo   = load_json(saldo_file)
    pending = load_json(deposit_file)

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
    keyboard = [
        [InlineKeyboardButton("✏️ Ubah Nama Toko",    callback_data="admin_ubah_nama")],
        [InlineKeyboardButton("🏦 Ubah Rekening",      callback_data="admin_ubah_rekening")],
        [InlineKeyboardButton("📞 Ubah Kontak Admin",  callback_data="admin_ubah_kontak")],
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
    pending = load_json(deposit_file)
    saldo   = load_json(saldo_file)

    item = next((p for p in pending if p["user_id"] == user_id), None)
    if not item:
        try:
            await query.edit_message_caption("❌ Data deposit tidak ditemukan.")
        except Exception:
            await query.edit_message_text("❌ Data deposit tidak ditemukan.")
        return

    nominal           = item["nominal"]
    saldo[str(user_id)] = saldo.get(str(user_id), 0) + nominal
    save_json(saldo_file, saldo, backup=True)

    pending = [p for p in pending if p["user_id"] != user_id]
    save_json(deposit_file, pending)
    add_riwayat(user_id, "DEPOSIT", "Konfirmasi Admin", nominal)

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
        text=f"✅ Deposit *Rp{nominal:,}* telah dikonfirmasi dan masuk ke saldo kamu!",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await send_main_menu(context, user_id, await context.bot.get_chat(user_id))


async def handle_admin_reject(update: Update, context: CallbackContext):
    query   = update.callback_query
    user_id = int(query.data.split(":")[1])

    # Hapus dari pending
    pending = load_json(deposit_file)
    pending = [p for p in pending if p["user_id"] != user_id]
    save_json(deposit_file, pending)

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
    "qty_plus":               handle_qty_plus,
    "qty_minus":              handle_qty_minus,
    "confirm_order":          handle_confirm_order,
    "back":                   handle_back,
    "back_to_produk":         handle_back_to_produk,
    "riwayat_user":           handle_riwayat_user,
    "ignore":                 handle_ignore,
}


async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data  = query.data

    produk = load_produk()
    if data in produk:
        await handle_produk_detail(update, context)
    elif data.startswith("deposit_"):
        await handle_deposit_nominal(update, context)
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
        # Hapus pending deposit jika ada
        pending = load_json(deposit_file)
        pending = [p for p in pending if str(p["user_id"]) != uid]
        save_json(deposit_file, pending)
        # Bersihkan semua state
        for key in ["awaiting_custom", "nominal_asli", "total_transfer",
                    "admin_state", "new_produk", "restock_pid",
                    "konfirmasi"]:
            context.user_data.pop(key, None)
        await update.message.reply_text("✅ Dibatalkan.", reply_markup=ReplyKeyboardRemove())
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
            nominal = int(text.replace(".", "").replace(",", ""))
            if nominal < DEPOSIT_MIN or nominal > DEPOSIT_MAX:
                await update.message.reply_text(
                    f"❌ Nominal harus antara Rp{DEPOSIT_MIN:,} dan Rp{DEPOSIT_MAX:,}. Coba lagi:"
                )
                return
            context.user_data["awaiting_custom"]  = False
            context.user_data["nominal_asli"]      = nominal
            context.user_data["total_transfer"]    = nominal + 23
            await _send_deposit_instructions(update.message, context, nominal, is_message=True)
        except ValueError:
            await update.message.reply_text("❌ Format salah. Ketik angka saja, contoh: 50000")
        return

    # ── Pilih produk dari keyboard ───────────────────────────────────
    if "SOLDOUT" in text:
        text = text.split()[0]

    produk = load_produk()
    if text in produk:
        item = produk[text]
        if item["stok"] <= 0:
            await update.message.reply_text("❌ Stok habis.", reply_markup=ReplyKeyboardRemove())
            await send_main_menu_safe(update, context)
            return

        context.user_data["konfirmasi"] = {"produk_id": text, "jumlah": 1}
        await update.message.reply_text(
            _order_text(item, 1),
            reply_markup=_order_keyboard(1),
            parse_mode="Markdown"
        )
        return

    # ── Tombol kembali ────────────────────────────────────────────────
    if text == "🔙 Kembali":
        await send_main_menu_safe(update, context)
        return

    await send_main_menu_safe(update, context)


# ─── HANDLER FOTO (bukti deposit) ─────────────────────────────────────────────

async def handle_photo(update: Update, context: CallbackContext):
    user    = update.effective_user
    nominal = context.user_data.get("nominal_asli", 0)

    if nominal == 0:
        await update.message.reply_text("⚠️ Kamu belum memilih nominal deposit. Silakan mulai dari menu deposit.")
        return

    photo = update.message.photo[-1]
    file  = await context.bot.get_file(photo.file_id)
    os.makedirs("bukti", exist_ok=True)
    path  = f"bukti/{user.id}_{int(time.time())}.jpg"
    await file.download_to_drive(path)

    total   = context.user_data.get("total_transfer", nominal)
    pending = load_json(deposit_file)

    # Cegah duplikat pending dari user yang sama
    pending = [p for p in pending if p["user_id"] != user.id]
    pending.append({
        "user_id":      user.id,
        "username":     user.username,
        "bukti_path":   path,
        "nominal":      nominal,
        "total_transfer": total,
        "waktu":        datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })
    save_json(deposit_file, pending)

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

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("riwayat",  cmd_riwayat))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO,              handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("✅ Bot Store Ekha berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
