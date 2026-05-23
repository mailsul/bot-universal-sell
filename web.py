"""web.py — Website versi bot Ibra Store"""

import os
import random
import string
import json
import threading
import time
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

import httpx
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_file, jsonify, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash

from db import (
    init_db, init_web_tables,
    web_get_user_by_tid, web_create_user, web_update_password, web_update_role,
    web_get_all_users, web_save_otp, web_verify_otp,
    db_get_saldo, db_add_saldo, db_get_riwayat, db_add_riwayat,
    db_get_all_pending, db_get_pending_any_by_user,
    db_remove_pending_any_by_user, db_add_pending, db_remove_pending_by_id,
    db_get_all_statistik, db_get_all_saldo,
)
from qris_helper import generate_qr_with_amount

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
OWNER_ID    = int(os.getenv("OWNER_ID", "1160642744"))
SECRET_KEY  = os.getenv("WEB_SECRET_KEY", "ibra-store-web-2024-xK9mPq")
WEB_PORT    = int(os.getenv("WEB_PORT", "5000"))
URL_MUTASI  = os.getenv("URL_MUTASI", "")
QRIS_BASE64 = os.getenv("QRIS_BASE64", "")

QRIS_EXPIRY_SEC  = 5 * 60   # 5 menit
RATE_LIMIT_MAX   = 5         # pembelian per window
RATE_LIMIT_WIN   = 3600      # 1 jam

app = Flask(__name__)
app.secret_key = SECRET_KEY

init_db()
init_web_tables()

_purchase_lock = threading.Lock()
_rate_data: dict[str, list] = defaultdict(list)
_rate_lock = threading.Lock()


# ─── RATE LIMIT ───────────────────────────────────────────────────────────────

def _ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()

def _rl_allowed(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        times = [t for t in _rate_data[ip] if now - t < RATE_LIMIT_WIN]
        if len(times) >= RATE_LIMIT_MAX:
            _rate_data[ip] = times
            return False
        times.append(now)
        _rate_data[ip] = times
    return True

def _rl_remaining(ip: str) -> int:
    now = time.time()
    with _rate_lock:
        times = [t for t in _rate_data[ip] if now - t < RATE_LIMIT_WIN]
    return max(0, RATE_LIMIT_MAX - len(times))


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def load_produk_raw() -> dict:
    try:
        with open("produk.json", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def load_produk() -> list:
    return [{"id": k, **v} for k, v in load_produk_raw().items()]

def save_produk_raw(data: dict):
    tmp = "produk.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, "produk.json")

def load_config() -> dict:
    try:
        with open("config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"nama_toko": "Ibra Store", "rekening": [], "kontak_admin": ""}

def save_config(data: dict):
    tmp = "config.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, "config.json")

def send_telegram(chat_id: int, text: str) -> bool:
    if not BOT_TOKEN:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=8,
        )
        return r.json().get("ok", False)
    except Exception:
        return False

def gen_otp() -> str:
    return "".join(random.choices(string.digits, k=6))

def gen_trx_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"TRX-{datetime.now().strftime('%Y%m%d')}-{suffix}"

def current_user() -> dict | None:
    tid = session.get("user_tid")
    return web_get_user_by_tid(tid) if tid else None

def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if "user_tid" not in session:
            flash("Silakan login terlebih dahulu.", "warning")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if "user_tid" not in session:
            flash("Silakan login terlebih dahulu.", "warning")
            return redirect(url_for("login"))
        if session.get("user_role") != "admin":
            flash("Akses ditolak.", "danger")
            return redirect(url_for("index"))
        return f(*a, **kw)
    return dec


# ─── MUTATION CHECKER (web-side QRIS) ─────────────────────────────────────────

def _extract_amounts(data) -> set:
    amounts = set()
    KREDIT = ("kredit","credit","amount","nominal","jumlah","nilai","total","in")
    IN_ST  = {"in","kredit","cr","credit","masuk","success"}

    def _find(obj, d=0):
        if d > 5: return []
        if isinstance(obj, list) and obj and isinstance(obj[0], dict): return obj
        if isinstance(obj, dict):
            for k in ("results","qris_history","data","mutasi","records","transactions","result","items","history"):
                v = obj.get(k)
                if isinstance(v, list) and v and isinstance(v[0], dict): return v
                if isinstance(v, dict):
                    r = _find(v, d+1)
                    if r: return r
        return []

    def _parse(val):
        try: return int(float(str(val).replace(",","").replace(".","").strip()))
        except: return 0

    for it in _find(data):
        if not isinstance(it, dict): continue
        st = str(it.get("status","")).strip().lower()
        if st and st not in IN_ST: continue
        for f in KREDIT:
            val = it.get(f)
            if val is not None and str(val).strip() not in ("","0"):
                v = _parse(val)
                if v > 0:
                    amounts.add(v)
                    break
    return amounts

def check_mutation(expected: int) -> bool:
    if not URL_MUTASI:
        return False
    try:
        r = httpx.get(URL_MUTASI, timeout=10)
        return expected in _extract_amounts(r.json())
    except Exception:
        return False


# ─── CONTEXT ──────────────────────────────────────────────────────────────────

@app.context_processor
def _ctx():
    cfg = load_config()
    return {
        "cfg": cfg,
        "current_user": current_user(),
        "current_saldo": db_get_saldo(session["user_tid"]) if "user_tid" in session else 0,
    }


# ─── PUBLIC ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", produk=load_produk())


# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.route("/register", methods=["GET","POST"])
def register():
    if "user_tid" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        raw = request.form.get("telegram_id","").strip()
        pw  = request.form.get("password","").strip()
        pw2 = request.form.get("password2","").strip()
        try:
            tid = int(raw)
        except ValueError:
            flash("Telegram ID harus berupa angka.", "danger")
            return redirect(url_for("register"))
        if len(pw) < 6:
            flash("Password minimal 6 karakter.", "danger")
            return redirect(url_for("register"))
        if pw != pw2:
            flash("Konfirmasi password tidak cocok.", "danger")
            return redirect(url_for("register"))
        if web_get_user_by_tid(tid):
            flash("Telegram ID sudah terdaftar. Silakan login.", "warning")
            return redirect(url_for("login"))

        otp     = gen_otp()
        expires = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        web_save_otp(tid, otp, expires)
        ok = send_telegram(
            tid,
            f"🔐 *Kode OTP Registrasi {load_config().get('nama_toko','Ibra Store')}*\n\n"
            f"Kode OTP: *{otp}*\n\nBerlaku 5 menit."
        )
        if not ok:
            flash(
                "Gagal mengirim OTP. Kemungkinan kamu memblokir bot atau belum pernah "
                "memulai percakapan. Buka Telegram → cari bot kami → klik Start, "
                "lalu coba daftar lagi.",
                "danger"
            )
            return redirect(url_for("register"))

        session["reg_tid"]     = tid
        session["reg_pw_hash"] = generate_password_hash(pw)
        flash("OTP berhasil dikirim ke Telegram kamu!", "success")
        return redirect(url_for("verify"))
    return render_template("register.html")


@app.route("/verify", methods=["GET","POST"])
def verify():
    if "reg_tid" not in session:
        return redirect(url_for("register"))
    if request.method == "POST":
        tid = session["reg_tid"]
        otp = request.form.get("otp","").strip()
        if not web_verify_otp(tid, otp):
            flash("OTP salah atau kedaluwarsa. Silakan daftar ulang.", "danger")
            session.pop("reg_tid", None); session.pop("reg_pw_hash", None)
            return redirect(url_for("register"))
        role = "admin" if tid == OWNER_ID else "user"
        web_create_user(tid, None, session.pop("reg_pw_hash"), role)
        session.pop("reg_tid", None)
        flash("Akun berhasil dibuat! Silakan login.", "success")
        return redirect(url_for("login"))
    return render_template("verify.html")


@app.route("/login", methods=["GET","POST"])
def login():
    if "user_tid" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        raw = request.form.get("telegram_id","").strip()
        pw  = request.form.get("password","").strip()
        try:
            tid = int(raw)
        except ValueError:
            flash("Telegram ID harus berupa angka.", "danger")
            return redirect(url_for("login"))
        user = web_get_user_by_tid(tid)
        if not user or not check_password_hash(user["password_hash"], pw):
            flash("Telegram ID atau password salah.", "danger")
            return redirect(url_for("login"))
        # Auto-promote OWNER_ID ke admin jika role belum benar
        role = user["role"]
        if tid == OWNER_ID and role != "admin":
            web_update_role(tid, "admin")
            role = "admin"
        session["user_tid"]  = tid
        session["user_role"] = role
        flash("Selamat datang kembali!", "success")
        return redirect(url_for("admin") if role == "admin" else url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Berhasil logout.", "info")
    return redirect(url_for("index"))


# ─── BELI (no login required) ─────────────────────────────────────────────────

@app.route("/beli/<pid>")
def beli(pid):
    raw  = load_produk_raw()
    item = raw.get(pid)
    if not item:
        flash("Produk tidak ditemukan.", "danger")
        return redirect(url_for("index"))
    saldo   = db_get_saldo(session["user_tid"]) if "user_tid" in session else 0
    qris_ok = bool(QRIS_BASE64)
    return render_template(
        "beli.html", item=item, pid=pid, saldo=saldo,
        qris_ok=qris_ok, remaining=_rl_remaining(_ip()),
        rate_limit_max=RATE_LIMIT_MAX,
    )


@app.route("/beli/<pid>/saldo", methods=["POST"])
@login_required
def beli_saldo(pid):
    tid = session["user_tid"]
    if not _rl_allowed(_ip()):
        flash("Terlalu banyak pembelian. Coba lagi dalam 1 jam.", "danger")
        return redirect(url_for("beli", pid=pid))

    tg_kirim = request.form.get("telegram_id","").strip()

    with _purchase_lock:
        raw  = load_produk_raw()
        item = raw.get(pid)
        if not item or item.get("stok",0) < 1 or not item.get("akun_list"):
            flash("Stok habis.", "danger")
            return redirect(url_for("index"))
        harga = item["harga"]
        saldo = db_get_saldo(tid)
        if saldo < harga:
            flash(f"Saldo tidak cukup (Rp{saldo:,} < Rp{harga:,}). Top up dulu.", "danger")
            return redirect(url_for("beli", pid=pid))
        akun = item["akun_list"].pop(0)
        item["stok"] = len(item["akun_list"])
        raw[pid] = item
        save_produk_raw(raw)

    db_add_saldo(tid, -harga)
    trx_id = db_add_riwayat(tid, "BELI", f"{item['nama']} x1 (Web/Saldo)", harga)

    tg_sent = False
    if tg_kirim:
        try:
            tg_sent = send_telegram(
                int(tg_kirim),
                f"✅ *Pembelian Berhasil!*\n\n"
                f"🛍 *{item['nama']}*\n"
                f"👤 `{akun.get('username','')}`\n"
                f"🔑 `{akun.get('password','')}`\n"
                + (f"ℹ️ {akun.get('tipe','')}\n" if akun.get('tipe') else "")
                + f"\n🔖 TRX: `{trx_id}`"
            )
        except Exception:
            pass

    return render_template(
        "beli_sukses.html", akun=akun, item=item,
        trx_id=trx_id, tg_sent=tg_sent, tg_kirim=tg_kirim, metode="Saldo",
    )


@app.route("/beli/<pid>/qris", methods=["POST"])
def beli_qris(pid):
    if not _rl_allowed(_ip()):
        flash("Terlalu banyak pembelian. Coba lagi dalam 1 jam.", "danger")
        return redirect(url_for("beli", pid=pid))
    if not QRIS_BASE64:
        flash("QRIS belum dikonfigurasi.", "danger")
        return redirect(url_for("beli", pid=pid))

    tg_kirim = request.form.get("telegram_id","").strip()

    with _purchase_lock:
        raw  = load_produk_raw()
        item = raw.get(pid)
        if not item or item.get("stok",0) < 1 or not item.get("akun_list"):
            flash("Stok habis.", "danger")
            return redirect(url_for("index"))
        akun = item["akun_list"].pop(0)
        item["stok"] = len(item["akun_list"])
        raw[pid] = item
        save_produk_raw(raw)

    harga     = item["harga"]
    kode_unik = random.randint(1, 999)
    total     = harga + kode_unik

    if "user_tid" in session:
        buyer_uid = session["user_tid"]
    else:
        if "guest_uid" not in session:
            session["guest_uid"] = random.randint(10**13, 10**14 - 1)
        buyer_uid = session["guest_uid"]

    db_remove_pending_any_by_user(buyer_uid)
    db_add_pending({
        "user_id":         buyer_uid,
        "metode":          "qris_beli_web",
        "nominal":         harga,
        "expected_amount": total,
        "kode_unik":       kode_unik,
        "waktu":           datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "produk_id":       pid,
        "jumlah":          1,
        "reserved_akun":   [akun],
        "total_transfer":  total,
    })

    session["bq_uid"]   = buyer_uid
    session["bq_pid"]   = pid
    session["bq_total"] = total
    session["bq_nama"]  = item["nama"]
    session["bq_harga"] = harga
    session["bq_tg"]    = tg_kirim
    session["bq_start"] = int(time.time())

    return redirect(url_for("beli_waiting"))


@app.route("/beli/waiting")
def beli_waiting():
    uid = session.get("bq_uid")
    if not uid:
        return redirect(url_for("index"))
    total = session.get("bq_total", 0)
    nama  = session.get("bq_nama", "Produk")
    start = session.get("bq_start", int(time.time()))
    elapsed = int(time.time()) - start
    remaining_sec = max(0, QRIS_EXPIRY_SEC - elapsed)
    return render_template("beli_qris.html", total=total, nama=nama, remaining_sec=remaining_sec)


@app.route("/beli/qr.png")
def beli_qr():
    total = session.get("bq_total", 0)
    if not total or not QRIS_BASE64:
        return "Not found", 404
    try:
        img, _ = generate_qr_with_amount(QRIS_BASE64, int(total))
        if not img:
            return "QR error", 500
        img.seek(0)
        return send_file(img, mimetype="image/png")
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/api/beli/check")
def api_beli_check():
    uid   = session.get("bq_uid")
    total = session.get("bq_total", 0)
    start = session.get("bq_start", 0)

    if not uid or not total:
        return jsonify({"status": "error"})

    # Cek expired
    if int(time.time()) - start > QRIS_EXPIRY_SEC:
        pending = db_get_pending_any_by_user(uid)
        if pending:
            # Kembalikan stok
            reserved = pending.get("reserved_akun", [])
            pid      = session.get("bq_pid")
            if reserved and pid:
                with _purchase_lock:
                    raw  = load_produk_raw()
                    item = raw.get(pid, {})
                    if item:
                        item["akun_list"] = reserved + item.get("akun_list", [])
                        item["stok"]      = len(item["akun_list"])
                        raw[pid] = item
                        save_produk_raw(raw)
            db_remove_pending_any_by_user(uid)
        for k in ["bq_uid","bq_pid","bq_total","bq_nama","bq_harga","bq_tg","bq_start"]:
            session.pop(k, None)
        return jsonify({"status": "expired"})

    pending = db_get_pending_any_by_user(uid)
    if not pending:
        return jsonify({"status": "error"})

    if not check_mutation(int(total)):
        return jsonify({"status": "pending"})

    # === KONFIRMASI ===
    reserved = pending.get("reserved_akun", [])
    pid      = session.get("bq_pid")
    nama     = session.get("bq_nama", "Produk")
    harga    = session.get("bq_harga", total)
    tg_kirim = session.get("bq_tg", "")

    db_remove_pending_by_id(pending["id"])

    if "user_tid" in session:
        trx_id = db_add_riwayat(session["user_tid"], "BELI", f"{nama} x1 (Web/QRIS)", harga)
    else:
        trx_id = gen_trx_id()

    akun = reserved[0] if reserved else {}

    tg_sent = False
    if tg_kirim:
        try:
            tg_sent = send_telegram(
                int(tg_kirim),
                f"✅ *Pembelian Berhasil! (QRIS)*\n\n"
                f"🛍 *{nama}*\n"
                f"👤 `{akun.get('username','')}`\n"
                f"🔑 `{akun.get('password','')}`\n"
                + (f"ℹ️ {akun.get('tipe','')}\n" if akun.get('tipe') else "")
                + f"\n🔖 TRX: `{trx_id}`"
            )
        except Exception:
            pass

    session["bs_akun"]    = akun
    session["bs_trx"]     = trx_id
    session["bs_item"]    = {"nama": nama, "harga": harga}
    session["bs_tg_sent"] = tg_sent
    session["bs_tg"]      = tg_kirim
    session["bs_metode"]  = "QRIS"

    for k in ["bq_uid","bq_pid","bq_total","bq_nama","bq_harga","bq_tg","bq_start"]:
        session.pop(k, None)

    return jsonify({"status": "success", "redirect": url_for("beli_sukses")})


@app.route("/beli/sukses")
def beli_sukses():
    akun    = session.pop("bs_akun", {})
    trx_id  = session.pop("bs_trx", "-")
    item    = session.pop("bs_item", {})
    tg_sent = session.pop("bs_tg_sent", False)
    tg_kirim= session.pop("bs_tg", "")
    metode  = session.pop("bs_metode", "")
    if not akun:
        return redirect(url_for("index"))
    return render_template(
        "beli_sukses.html", akun=akun, item=item,
        trx_id=trx_id, tg_sent=tg_sent, tg_kirim=tg_kirim, metode=metode,
    )


# ─── USER ─────────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    tid  = session["user_tid"]
    return render_template("dashboard.html", saldo=db_get_saldo(tid), hist=db_get_riwayat(tid, 5))


@app.route("/riwayat")
@login_required
def riwayat():
    return render_template("riwayat.html", hist=db_get_riwayat(session["user_tid"], 50))


@app.route("/deposit")
@login_required
def deposit():
    return render_template("deposit.html", qris_ok=bool(QRIS_BASE64))


@app.route("/deposit/upload", methods=["POST"])
@login_required
def deposit_upload():
    tid  = session["user_tid"]
    raw  = request.form.get("nominal","0").replace(".","").strip()
    try:
        nominal = int(raw)
        if nominal < 10_000:
            flash("Nominal minimal Rp10.000.", "danger")
            return redirect(url_for("deposit"))
    except ValueError:
        flash("Nominal tidak valid.", "danger")
        return redirect(url_for("deposit"))

    if "bukti" not in request.files or not request.files["bukti"].filename:
        flash("Bukti transfer wajib diupload.", "danger")
        return redirect(url_for("deposit"))

    f = request.files["bukti"]
    os.makedirs("bukti", exist_ok=True)
    path = f"bukti/web_{tid}_{int(datetime.now().timestamp())}.jpg"
    f.save(path)

    db_remove_pending_any_by_user(tid)
    db_add_pending({
        "user_id": tid, "metode": "manual_web",
        "nominal": nominal, "bukti_path": path,
        "total_transfer": nominal,
        "waktu": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })
    flash("Bukti deposit terkirim! Tunggu konfirmasi admin.", "success")
    return redirect(url_for("dashboard"))


# ─── ADMIN ────────────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin():
    pending   = db_get_all_pending()
    saldo_all = db_get_all_saldo()
    stats     = db_get_all_statistik()
    return render_template(
        "admin.html",
        pending=pending, saldo_all=saldo_all, stats=stats,
        users_web=web_get_all_users(), produk=load_produk(),
        total_saldo=sum(saldo_all.values()),
    )


@app.route("/admin/confirm/<int:uid>", methods=["POST"])
@admin_required
def admin_confirm(uid):
    item = db_get_pending_any_by_user(uid)
    if not item:
        flash("Data tidak ditemukan.", "danger")
        return redirect(url_for("admin"))
    nominal = item["nominal"]
    db_add_saldo(uid, nominal)
    db_remove_pending_any_by_user(uid)
    trx_id = db_add_riwayat(uid, "DEPOSIT", "Konfirmasi Admin (Web)", nominal)
    send_telegram(uid, f"✅ Deposit *Rp{nominal:,}* dikonfirmasi!\n🔖 TRX: `{trx_id}`")
    flash(f"✅ Rp{nominal:,} dikonfirmasi. TRX: {trx_id}", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reject/<int:uid>", methods=["POST"])
@admin_required
def admin_reject(uid):
    db_remove_pending_any_by_user(uid)
    send_telegram(uid, "❌ Deposit kamu ditolak admin. Hubungi admin untuk info lebih lanjut.")
    flash("Deposit ditolak.", "warning")
    return redirect(url_for("admin"))


@app.route("/admin/password", methods=["GET","POST"])
@admin_required
def admin_password():
    if request.method == "POST":
        tid = session["user_tid"]
        cur = request.form.get("current_password","").strip()
        new = request.form.get("new_password","").strip()
        nw2 = request.form.get("new_password2","").strip()
        usr = web_get_user_by_tid(tid)
        if not usr or not check_password_hash(usr["password_hash"], cur):
            flash("Password lama salah.", "danger")
            return redirect(url_for("admin_password"))
        if len(new) < 6:
            flash("Password baru minimal 6 karakter.", "danger")
            return redirect(url_for("admin_password"))
        if new != nw2:
            flash("Konfirmasi tidak cocok.", "danger")
            return redirect(url_for("admin_password"))
        web_update_password(tid, generate_password_hash(new))
        flash("Password berhasil diubah.", "success")
        return redirect(url_for("admin"))
    return render_template("admin_password.html")


@app.route("/bukti/<path:filename>")
@admin_required
def serve_bukti(filename):
    return send_from_directory("bukti", filename)


# ─── DEPOSIT QRIS (otomatis) ──────────────────────────────────────────────────

@app.route("/deposit/qris", methods=["POST"])
@login_required
def deposit_qris_init():
    tid = session["user_tid"]
    if not QRIS_BASE64:
        flash("QRIS belum dikonfigurasi.", "danger")
        return redirect(url_for("deposit"))
    raw = request.form.get("nominal","0").replace(".","").strip()
    try:
        nominal = int(raw)
        if nominal < 10_000:
            flash("Nominal minimal Rp10.000.", "danger")
            return redirect(url_for("deposit"))
    except ValueError:
        flash("Nominal tidak valid.", "danger")
        return redirect(url_for("deposit"))

    kode_unik = random.randint(1, 999)
    total     = nominal + kode_unik

    db_remove_pending_any_by_user(tid)
    db_add_pending({
        "user_id":         tid,
        "metode":          "qris_deposit_web",
        "nominal":         nominal,
        "expected_amount": total,
        "kode_unik":       kode_unik,
        "total_transfer":  total,
        "waktu":           datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })
    session["dq_uid"]     = tid
    session["dq_total"]   = total
    session["dq_nominal"] = nominal
    session["dq_kode"]    = kode_unik
    session["dq_start"]   = int(time.time())
    return redirect(url_for("deposit_qris_waiting"))


@app.route("/deposit/qris/waiting")
@login_required
def deposit_qris_waiting():
    if "dq_uid" not in session:
        return redirect(url_for("deposit"))
    total     = session.get("dq_total", 0)
    nominal   = session.get("dq_nominal", 0)
    kode_unik = session.get("dq_kode", 0)
    start     = session.get("dq_start", int(time.time()))
    remaining_sec = max(0, QRIS_EXPIRY_SEC - (int(time.time()) - start))
    return render_template("deposit_qris.html",
        total=total, nominal=nominal, kode_unik=kode_unik, remaining_sec=remaining_sec)


@app.route("/deposit/qris/qr.png")
@login_required
def deposit_qris_qr():
    total = session.get("dq_total", 0)
    if not total or not QRIS_BASE64:
        return "Not found", 404
    try:
        img, _ = generate_qr_with_amount(QRIS_BASE64, int(total))
        if not img:
            return "QR error", 500
        img.seek(0)
        return send_file(img, mimetype="image/png")
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/api/deposit/check")
@login_required
def api_deposit_check():
    uid     = session.get("dq_uid")
    total   = session.get("dq_total", 0)
    nominal = session.get("dq_nominal", 0)
    start   = session.get("dq_start", 0)
    if not uid or not total:
        return jsonify({"status": "error"})

    if int(time.time()) - start > QRIS_EXPIRY_SEC:
        db_remove_pending_any_by_user(uid)
        for k in ["dq_uid","dq_total","dq_nominal","dq_kode","dq_start"]:
            session.pop(k, None)
        return jsonify({"status": "expired"})

    pending = db_get_pending_any_by_user(uid)
    if not pending:
        return jsonify({"status": "error"})
    if not check_mutation(int(total)):
        return jsonify({"status": "pending"})

    db_remove_pending_by_id(pending["id"])
    db_add_saldo(uid, nominal)
    trx_id = db_add_riwayat(uid, "DEPOSIT", f"QRIS Otomatis (Web) +kode unik Rp{total-nominal}", nominal)
    for k in ["dq_uid","dq_total","dq_nominal","dq_kode","dq_start"]:
        session.pop(k, None)
    send_telegram(uid, f"✅ Deposit QRIS *Rp{nominal:,}* berhasil!\n🔖 TRX: `{trx_id}`")
    flash(f"✅ Deposit Rp{nominal:,} berhasil dikonfirmasi otomatis! TRX: {trx_id}", "success")
    return jsonify({"status": "success", "redirect": url_for("dashboard")})


# ─── ADMIN MANAGEMENT ─────────────────────────────────────────────────────────

@app.route("/admin/produk/tambah", methods=["POST"])
@admin_required
def admin_produk_tambah():
    nama      = request.form.get("nama","").strip()
    harga_raw = request.form.get("harga","0").replace(".","").strip()
    deskripsi = request.form.get("deskripsi","").strip()
    akun_raw  = request.form.get("akun_list","").strip()
    if not nama:
        flash("Nama produk wajib diisi.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    try:
        harga = int(harga_raw)
    except ValueError:
        flash("Harga tidak valid.", "danger")
        return redirect(url_for("admin") + "#tab-produk")

    akun_list = _parse_akun_lines(akun_raw)
    with _purchase_lock:
        raw = load_produk_raw()
        nums = [int(k) for k in raw.keys() if k.isdigit()]
        pid  = str(max(nums, default=0) + 1)
        raw[pid] = {"nama": nama, "harga": harga, "deskripsi": deskripsi,
                    "stok": len(akun_list), "akun_list": akun_list}
        save_produk_raw(raw)
    flash(f"✅ Produk '{nama}' ditambah ({len(akun_list)} akun).", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/restock", methods=["POST"])
@admin_required
def admin_produk_restock(pid):
    akun_raw = request.form.get("akun_list","").strip()
    if not akun_raw:
        flash("Masukkan akun untuk restock.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    akun_baru = _parse_akun_lines(akun_raw)
    with _purchase_lock:
        raw  = load_produk_raw()
        item = raw.get(pid)
        if not item:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        item["akun_list"] = item.get("akun_list",[]) + akun_baru
        item["stok"]      = len(item["akun_list"])
        raw[pid] = item
        save_produk_raw(raw)
    flash(f"✅ Restock {len(akun_baru)} akun untuk '{item['nama']}'.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/harga", methods=["POST"])
@admin_required
def admin_produk_harga(pid):
    try:
        harga = int(request.form.get("harga","0").replace(".","").strip())
    except ValueError:
        flash("Harga tidak valid.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    with _purchase_lock:
        raw = load_produk_raw()
        if pid not in raw:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        raw[pid]["harga"] = harga
        save_produk_raw(raw)
    flash(f"✅ Harga diperbarui ke Rp{harga:,}.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/hapus", methods=["POST"])
@admin_required
def admin_produk_hapus(pid):
    with _purchase_lock:
        raw = load_produk_raw()
        if pid not in raw:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        nama = raw.pop(pid, {}).get("nama","")
        save_produk_raw(raw)
    flash(f"✅ Produk '{nama}' berhasil dihapus.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/config", methods=["POST"])
@admin_required
def admin_config_save():
    cfg           = load_config()
    nama_toko     = request.form.get("nama_toko","").strip()
    rekening_raw  = request.form.get("rekening","").strip()
    kontak        = request.form.get("kontak_admin","").strip()
    if nama_toko:
        cfg["nama_toko"] = nama_toko
    if rekening_raw:
        cfg["rekening"] = [r.strip() for r in rekening_raw.splitlines() if r.strip()]
    cfg["kontak_admin"] = kontak
    save_config(cfg)
    flash("✅ Pengaturan berhasil disimpan.", "success")
    return redirect(url_for("admin") + "#tab-config")


@app.route("/admin/saldo/atur", methods=["POST"])
@admin_required
def admin_saldo_atur():
    try:
        uid     = int(request.form.get("user_id","").strip())
        nominal = int(request.form.get("nominal","0").replace(".","").strip())
        if nominal <= 0:
            raise ValueError()
    except ValueError:
        flash("User ID atau nominal tidak valid.", "danger")
        return redirect(url_for("admin") + "#tab-saldo")
    aksi = request.form.get("aksi","tambah")
    if aksi == "kurangi":
        saldo = db_get_saldo(uid)
        if saldo < nominal:
            flash(f"Saldo tidak cukup (saldo: Rp{saldo:,}).", "danger")
            return redirect(url_for("admin") + "#tab-saldo")
        db_add_saldo(uid, -nominal)
        db_add_riwayat(uid, "KURANGI", "Dikurangi Admin (Web)", nominal)
        send_telegram(uid, f"⚠️ Saldo kamu dikurangi *Rp{nominal:,}* oleh admin.")
        flash(f"✅ Saldo {uid} dikurangi Rp{nominal:,}.", "success")
    else:
        db_add_saldo(uid, nominal)
        trx_id = db_add_riwayat(uid, "DEPOSIT", "Tambah Saldo Manual (Admin)", nominal)
        send_telegram(uid, f"✅ Saldo kamu ditambah *Rp{nominal:,}* oleh admin.\n🔖 TRX: `{trx_id}`")
        flash(f"✅ Saldo {uid} ditambah Rp{nominal:,}.", "success")
    return redirect(url_for("admin") + "#tab-saldo")


@app.route("/admin/broadcast", methods=["POST"])
@admin_required
def admin_broadcast():
    pesan = request.form.get("pesan","").strip()
    if not pesan:
        flash("Pesan tidak boleh kosong.", "danger")
        return redirect(url_for("admin") + "#tab-broadcast")
    saldo_all = db_get_all_saldo()
    users_web = web_get_all_users()
    uids = set(int(k) for k in saldo_all.keys())
    for u in users_web:
        uids.add(int(u["telegram_id"]))
    nama_toko = load_config().get("nama_toko","")
    ok_count = sum(
        1 for uid in uids
        if send_telegram(uid, f"📢 *Broadcast {nama_toko}*\n\n{pesan}")
    )
    flash(f"✅ Broadcast terkirim ke {ok_count}/{len(uids)} user.", "success")
    return redirect(url_for("admin") + "#tab-broadcast")


def _parse_akun_lines(raw: str) -> list:
    result = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            parts = line.split("|", 2)
            result.append({
                "username": parts[0].strip(),
                "password": parts[1].strip() if len(parts) > 1 else "",
                "tipe":     parts[2].strip() if len(parts) > 2 else "",
            })
        else:
            result.append({"username": line, "password": "", "tipe": ""})
    return result


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)
