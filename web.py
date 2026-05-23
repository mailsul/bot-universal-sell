"""web.py — Website versi bot Ibra Store"""

import os
import random
import string
import json
from datetime import datetime, timedelta
from functools import wraps

import httpx
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

from db import (
    init_db, init_web_tables,
    web_get_user_by_tid, web_create_user, web_update_password,
    web_get_all_users, web_save_otp, web_verify_otp,
    db_get_saldo, db_add_saldo, db_get_riwayat, db_add_riwayat,
    db_get_all_pending, db_get_pending_any_by_user,
    db_remove_pending_any_by_user, db_add_pending,
    db_get_all_statistik, db_get_all_saldo,
)

BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
OWNER_ID   = int(os.getenv("OWNER_ID", "1160642744"))
SECRET_KEY = os.getenv("WEB_SECRET_KEY", "ibra-store-web-2024-xK9mPq")
WEB_PORT   = int(os.getenv("WEB_PORT", "5000"))

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Init DB tables on startup
init_db()
init_web_tables()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def load_produk() -> list:
    try:
        with open("produk.json", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            result = []
            for pid, p in data.items():
                item = dict(p)
                item["id"] = pid
                result.append(item)
            return result
        return data
    except Exception:
        return []


def load_config() -> dict:
    try:
        with open("config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"nama_toko": "Ibra Store", "rekening": [], "kontak_admin": ""}


def send_telegram(chat_id: int, text: str) -> bool:
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=8,
        )
        return r.json().get("ok", False)
    except Exception:
        return False


def generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


def current_user() -> dict | None:
    tid = session.get("user_tid")
    if not tid:
        return None
    return web_get_user_by_tid(tid)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_tid" not in session:
            flash("Silakan login terlebih dahulu.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_tid" not in session:
            flash("Silakan login terlebih dahulu.", "warning")
            return redirect(url_for("login"))
        if session.get("user_role") != "admin":
            flash("Akses ditolak.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# ─── CONTEXT PROCESSOR ────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    cfg = load_config()
    return {
        "cfg": cfg,
        "current_user": current_user(),
        "current_saldo": db_get_saldo(session["user_tid"]) if "user_tid" in session else 0,
    }


# ─── ROUTES — PUBLIC ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    produk = load_produk()
    return render_template("index.html", produk=produk)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_tid" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        raw_tid = request.form.get("telegram_id", "").strip()
        pw      = request.form.get("password", "").strip()
        pw2     = request.form.get("password2", "").strip()

        try:
            tid = int(raw_tid)
        except ValueError:
            flash("Telegram ID harus berupa angka (misal: 1234567890).", "danger")
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

        otp     = generate_otp()
        expires = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        web_save_otp(tid, otp, expires)

        ok = send_telegram(
            tid,
            f"🔐 *Kode OTP Registrasi Ibra Store*\n\n"
            f"Kode OTP kamu: *{otp}*\n\n"
            f"Berlaku 5 menit. Jangan bagikan ke siapapun!"
        )

        if not ok:
            flash(
                "Gagal mengirim OTP ke Telegram kamu. "
                "Pastikan kamu sudah /start bot kami terlebih dahulu, "
                "lalu coba lagi.",
                "danger"
            )
            return redirect(url_for("register"))

        session["reg_tid"]     = tid
        session["reg_pw_hash"] = generate_password_hash(pw)
        flash("OTP berhasil dikirim ke Telegram kamu!", "success")
        return redirect(url_for("verify"))

    return render_template("register.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    if "reg_tid" not in session:
        return redirect(url_for("register"))

    if request.method == "POST":
        tid = session["reg_tid"]
        otp = request.form.get("otp", "").strip()

        if not web_verify_otp(tid, otp):
            flash("OTP salah atau sudah kedaluwarsa. Silakan daftar ulang.", "danger")
            session.pop("reg_tid", None)
            session.pop("reg_pw_hash", None)
            return redirect(url_for("register"))

        role = "admin" if tid == OWNER_ID else "user"
        web_create_user(tid, None, session.pop("reg_pw_hash"), role)
        session.pop("reg_tid", None)

        flash("Akun berhasil dibuat! Silakan login.", "success")
        return redirect(url_for("login"))

    return render_template("verify.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_tid" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        raw_tid = request.form.get("telegram_id", "").strip()
        pw      = request.form.get("password", "").strip()

        try:
            tid = int(raw_tid)
        except ValueError:
            flash("Telegram ID harus berupa angka.", "danger")
            return redirect(url_for("login"))

        user = web_get_user_by_tid(tid)
        if not user or not check_password_hash(user["password_hash"], pw):
            flash("Telegram ID atau password salah.", "danger")
            return redirect(url_for("login"))

        session["user_tid"]  = tid
        session["user_role"] = user["role"]
        flash("Selamat datang kembali! 👋", "success")

        if user["role"] == "admin":
            return redirect(url_for("admin"))
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Berhasil logout.", "info")
    return redirect(url_for("index"))


# ─── ROUTES — USER ────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    tid   = session["user_tid"]
    saldo = db_get_saldo(tid)
    hist  = db_get_riwayat(tid, 5)
    return render_template("dashboard.html", saldo=saldo, hist=hist)


@app.route("/riwayat")
@login_required
def riwayat():
    tid  = session["user_tid"]
    hist = db_get_riwayat(tid, 50)
    return render_template("riwayat.html", hist=hist)


@app.route("/deposit")
@login_required
def deposit():
    return render_template("deposit.html")


@app.route("/deposit/upload", methods=["POST"])
@login_required
def deposit_upload():
    tid  = session["user_tid"]
    user = current_user()

    raw = request.form.get("nominal", "0").replace(".", "").strip()
    try:
        nominal = int(raw)
        if nominal < 10_000:
            flash("Nominal minimal Rp10.000.", "danger")
            return redirect(url_for("deposit"))
    except ValueError:
        flash("Nominal tidak valid.", "danger")
        return redirect(url_for("deposit"))

    if "bukti" not in request.files or request.files["bukti"].filename == "":
        flash("Bukti transfer wajib diupload.", "danger")
        return redirect(url_for("deposit"))

    f    = request.files["bukti"]
    os.makedirs("bukti", exist_ok=True)
    path = f"bukti/web_{tid}_{int(datetime.now().timestamp())}.jpg"
    f.save(path)

    db_remove_pending_any_by_user(tid)
    db_add_pending({
        "user_id":        tid,
        "username":       user.get("username") if user else None,
        "metode":         "manual_web",
        "nominal":        nominal,
        "bukti_path":     path,
        "total_transfer": nominal,
        "waktu":          datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })

    flash("Bukti deposit berhasil dikirim! Tunggu konfirmasi admin.", "success")
    return redirect(url_for("dashboard"))


# ─── ROUTES — ADMIN ───────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin():
    pending     = db_get_all_pending()
    saldo_all   = db_get_all_saldo()
    stats       = db_get_all_statistik()
    users_web   = web_get_all_users()
    produk      = load_produk()
    total_saldo = sum(saldo_all.values())
    return render_template(
        "admin.html",
        pending=pending,
        saldo_all=saldo_all,
        stats=stats,
        users_web=users_web,
        produk=produk,
        total_saldo=total_saldo,
    )


@app.route("/admin/confirm/<int:uid>", methods=["POST"])
@admin_required
def admin_confirm(uid):
    item = db_get_pending_any_by_user(uid)
    if not item:
        flash("Data deposit tidak ditemukan.", "danger")
        return redirect(url_for("admin"))

    nominal = item["nominal"]
    db_add_saldo(uid, nominal)
    db_remove_pending_any_by_user(uid)
    trx_id = db_add_riwayat(uid, "DEPOSIT", "Konfirmasi Admin (Web)", nominal)

    send_telegram(
        uid,
        f"✅ Deposit *Rp{nominal:,}* telah dikonfirmasi!\n"
        f"🔖 ID Transaksi: `{trx_id}`\n\n"
        f"Saldo kamu sudah diperbarui."
    )

    flash(f"✅ Deposit Rp{nominal:,} dikonfirmasi. TRX: {trx_id}", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reject/<int:uid>", methods=["POST"])
@admin_required
def admin_reject(uid):
    db_remove_pending_any_by_user(uid)
    send_telegram(
        uid,
        "❌ Deposit kamu ditolak oleh admin.\n"
        "Silakan hubungi admin untuk informasi lebih lanjut."
    )
    flash("Deposit ditolak.", "warning")
    return redirect(url_for("admin"))


@app.route("/admin/password", methods=["GET", "POST"])
@admin_required
def admin_password():
    if request.method == "POST":
        tid     = session["user_tid"]
        cur_pw  = request.form.get("current_password", "").strip()
        new_pw  = request.form.get("new_password", "").strip()
        new_pw2 = request.form.get("new_password2", "").strip()
        user    = web_get_user_by_tid(tid)

        if not user or not check_password_hash(user["password_hash"], cur_pw):
            flash("Password lama tidak benar.", "danger")
            return redirect(url_for("admin_password"))

        if len(new_pw) < 6:
            flash("Password baru minimal 6 karakter.", "danger")
            return redirect(url_for("admin_password"))

        if new_pw != new_pw2:
            flash("Konfirmasi password tidak cocok.", "danger")
            return redirect(url_for("admin_password"))

        web_update_password(tid, generate_password_hash(new_pw))
        flash("Password berhasil diubah.", "success")
        return redirect(url_for("admin"))

    return render_template("admin_password.html")


# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)
