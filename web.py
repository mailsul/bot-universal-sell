"""web.py — Website versi bot Ibra Store"""

import os
import re
import random
import string
import json
import secrets
import threading
import time
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

import httpx
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_file, jsonify, send_from_directory, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from produk_lock import produk_lock

from db import (
    init_db, init_web_tables,
    web_get_user_by_tid, web_create_user, web_update_password, web_update_role,
    web_get_all_users, web_save_otp, web_verify_otp,
    web_get_user_by_email, web_get_user_by_phone,
    web_get_user_by_identifier, web_update_profile,
    db_get_saldo, db_add_saldo, db_get_riwayat, db_add_riwayat,
    db_get_all_pending, db_get_pending_any_by_user,
    db_remove_pending_any_by_user, db_add_pending, db_remove_pending_by_id,
    db_get_all_statistik, db_get_all_saldo, db_get_all_bot_users,
)
from qris_helper import generate_qr_with_amount

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
OWNER_ID    = int(os.getenv("OWNER_ID", "1160642744"))
SECRET_KEY  = os.getenv("WEB_SECRET_KEY", "ibra-store-web-2024-xK9mPq")
WEB_PORT    = int(os.getenv("WEB_PORT", "5000"))
URL_MUTASI  = os.getenv("URL_MUTASI", "")
QRIS_BASE64 = os.getenv("QRIS_BASE64", "")

QRIS_EXPIRY_SEC   = 5 * 60   # 5 menit
RATE_LIMIT_MAX    = 5         # pembelian per window
RATE_LIMIT_WIN    = 3600      # 1 jam
LOGIN_FAIL_MAX    = 5         # gagal login per window
LOGIN_FAIL_WIN    = 900       # 15 menit

_RE_PHONE = re.compile(r'^\+62[0-9]{8,13}$')
_RE_EMAIL  = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = "Lax",
    SESSION_COOKIE_SECURE   = False,   # set True di production HTTPS
    PERMANENT_SESSION_LIFETIME = timedelta(days=7),
)

init_db()
init_web_tables()

_purchase_lock = threading.Lock()
_rate_data:     dict[str, list] = defaultdict(list)
_rate_lock      = threading.Lock()
_login_fail:    dict[str, list] = defaultdict(list)
_login_fl_lock  = threading.Lock()


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


# ─── BRUTE FORCE PROTECTION (login) ──────────────────────────────────────────

def _login_check(ip: str) -> bool:
    """Return True jika boleh mencoba login (belum kena block)."""
    now = time.time()
    with _login_fl_lock:
        times = [t for t in _login_fail[ip] if now - t < LOGIN_FAIL_WIN]
        _login_fail[ip] = times
        return len(times) < LOGIN_FAIL_MAX

def _login_record_fail(ip: str):
    now = time.time()
    with _login_fl_lock:
        _login_fail[ip].append(now)

def _login_clear(ip: str):
    with _login_fl_lock:
        _login_fail[ip] = []


# ─── CSRF ─────────────────────────────────────────────────────────────────────

def _csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]

def _csrf_ok() -> bool:
    t = session.get("_csrf")
    s = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
    return bool(t and s and secrets.compare_digest(t, s))

app.jinja_env.globals["csrf_token"] = _csrf_token


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _migrate_produk_format(raw: dict) -> tuple[dict, bool]:
    """Convert format lama (flat harga/akun_list) → format baru (tipe dict). Idempotent."""
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

def load_produk_raw() -> dict:
    """Load + auto-migrate produk.json ke format baru (tipe dict). Return dict."""
    try:
        with open("produk.json", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}
    except Exception:
        return {}
    d, changed = _migrate_produk_format(d)
    if changed:
        tmp = "produk.json.tmp"
        with open(tmp, "w", encoding="utf-8") as ff:
            json.dump(d, ff, ensure_ascii=False, indent=2)
        shutil.move(tmp, "produk.json")
    return d

def load_produk() -> list:
    """Return list produk untuk template. Tiap item punya tipe (list), stok total, harga min."""
    raw = load_produk_raw()
    result = []
    for pid, item in raw.items():
        tipe_list = []
        total_stok = 0
        min_harga  = None
        for tid, t in item.get("tipe", {}).items():
            stok = len(t.get("akun_list", []))
            tipe_list.append({"id": tid, **t, "stok": stok})
            total_stok += stok
            h = t.get("harga", 0)
            if min_harga is None or h < min_harga:
                min_harga = h
        result.append({
            "id":     pid,
            "nama":   item["nama"],
            "gambar": item.get("gambar"),
            "stok":   total_stok,
            "harga":  min_harga or 0,
            "tipe":   tipe_list,
        })
    return result

def save_produk_raw(data: dict):
    tmp = "produk.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, "produk.json")

PRODUK_IMG_DIR = os.path.join("static", "produk_img")
os.makedirs(PRODUK_IMG_DIR, exist_ok=True)

ALLOWED_IMG = {"png", "jpg", "jpeg", "webp", "gif"}

def _save_produk_img(pid: str, file) -> str | None:
    """Save uploaded image, return relative URL path or None."""
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMG:
        return None
    fname = f"{pid}.{ext}"
    file.save(os.path.join(PRODUK_IMG_DIR, fname))
    return f"/static/produk_img/{fname}"

# ─── BRAND COLOR PRESETS ──────────────────────────────────────────────────────
BRAND_PRESETS: dict[str, dict] = {
    "purple": {
        "brand": "#7c3aed", "dark": "#5b21b6", "light": "#ede9fe", "mid": "#8b5cf6",
        "dm_brand": "#a78bfa", "dm_dark": "#7c3aed", "dm_light": "#2d1d5e",
    },
    "blue": {
        "brand": "#2563eb", "dark": "#1d4ed8", "light": "#dbeafe", "mid": "#3b82f6",
        "dm_brand": "#60a5fa", "dm_dark": "#2563eb", "dm_light": "#1e2a4a",
    },
    "green": {
        "brand": "#059669", "dark": "#047857", "light": "#d1fae5", "mid": "#10b981",
        "dm_brand": "#34d399", "dm_dark": "#059669", "dm_light": "#0d2e20",
    },
    "red": {
        "brand": "#dc2626", "dark": "#b91c1c", "light": "#fee2e2", "mid": "#ef4444",
        "dm_brand": "#f87171", "dm_dark": "#dc2626", "dm_light": "#2d0a0a",
    },
    "orange": {
        "brand": "#ea580c", "dark": "#c2410c", "light": "#ffedd5", "mid": "#f97316",
        "dm_brand": "#fb923c", "dm_dark": "#ea580c", "dm_light": "#2d1400",
    },
    "pink": {
        "brand": "#db2777", "dark": "#be185d", "light": "#fce7f3", "mid": "#ec4899",
        "dm_brand": "#f472b6", "dm_dark": "#db2777", "dm_light": "#2d0a1e",
    },
    "teal": {
        "brand": "#0d9488", "dark": "#0f766e", "light": "#ccfbf1", "mid": "#14b8a6",
        "dm_brand": "#2dd4bf", "dm_dark": "#0d9488", "dm_light": "#0a2525",
    },
    "indigo": {
        "brand": "#4f46e5", "dark": "#4338ca", "light": "#e0e7ff", "mid": "#6366f1",
        "dm_brand": "#818cf8", "dm_dark": "#4f46e5", "dm_light": "#1a1a45",
    },
}


def load_config() -> dict:
    try:
        with open("config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"nama_toko": "Ibra Store", "rekening": [], "kontak_admin": ""}

LOGO_EXTS = ["png", "jpg", "jpeg", "webp"]

def get_logo_path() -> str | None:
    """Return path ke logo toko jika ada, misal 'static/logo.png'."""
    for ext in LOGO_EXTS:
        p = os.path.join("static", f"logo.{ext}")
        if os.path.exists(p):
            return p
    return None

def get_logo_url() -> str | None:
    """Return URL /static/logo.ext atau None."""
    p = get_logo_path()
    if p:
        return "/" + p.replace("\\", "/")
    return None

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
    cfg          = load_config()
    preset_name  = cfg.get("brand_color", "purple")
    brand        = BRAND_PRESETS.get(preset_name, BRAND_PRESETS["purple"])
    return {
        "cfg":           cfg,
        "current_user":  current_user(),
        "current_saldo": db_get_saldo(session["user_tid"]) if "user_tid" in session else 0,
        "logo_url":      get_logo_url(),
        "brand":         brand,
        "brand_presets": BRAND_PRESETS,
        "brand_name":    preset_name,
    }


# ─── PUBLIC ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", produk=load_produk())


# ─── AUTH ─────────────────────────────────────────────────────────────────────

def _validate_password_web(pw: str):
    if len(pw) < 8:
        return "Password minimal 8 karakter"
    if not re.search(r'[A-Z]', pw):
        return "Password harus ada huruf kapital (A–Z)"
    if not re.search(r'[0-9]', pw):
        return "Password harus ada angka (0–9)"
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{};:\'",.<>?/\\|`~]', pw):
        return "Password harus ada simbol (!@#$%^&* dll)"
    return None


@app.route("/register", methods=["GET","POST"])
def register():
    if "user_tid" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        raw   = request.form.get("telegram_id","").strip()
        phone = request.form.get("phone","").strip() or None
        email = (request.form.get("email","").strip().lower()) or None
        pw    = request.form.get("password","").strip()
        pw2   = request.form.get("password2","").strip()
        try:
            tid = int(raw)
        except ValueError:
            flash("Telegram ID harus berupa angka.", "danger")
            return redirect(url_for("register"))
        if phone and not _RE_PHONE.match(phone):
            flash("Format nomor HP tidak valid. Gunakan: +6281234567890", "danger")
            return redirect(url_for("register"))
        if email and not _RE_EMAIL.match(email):
            flash("Format email tidak valid.", "danger")
            return redirect(url_for("register"))
        pw_err = _validate_password_web(pw)
        if pw_err:
            flash(pw_err, "danger")
            return redirect(url_for("register"))
        if pw != pw2:
            flash("Konfirmasi password tidak cocok.", "danger")
            return redirect(url_for("register"))
        if web_get_user_by_tid(tid):
            flash("Telegram ID sudah terdaftar. Silakan login.", "warning")
            return redirect(url_for("login"))
        if phone and web_get_user_by_phone(phone):
            flash("Nomor HP sudah terdaftar.", "warning")
            return redirect(url_for("login"))
        if email and web_get_user_by_email(email):
            flash("Email sudah terdaftar.", "warning")
            return redirect(url_for("login"))

        otp     = gen_otp()
        expires = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        web_save_otp(tid, otp, expires)
        toko = load_config().get('nama_toko','Ibra Store')
        ok   = send_telegram(
            tid,
            f"🔐 *Kode OTP Registrasi {toko}*\n\n"
            f"Kode OTP: *{otp}*\n\nBerlaku 5 menit.\nJangan bagikan ke siapapun."
        )
        if not ok:
            flash(
                "Gagal mengirim OTP. Pastikan sudah pernah memulai percakapan "
                "dengan bot kami di Telegram, lalu coba daftar lagi.",
                "danger"
            )
            return redirect(url_for("register"))

        session["reg_tid"]     = tid
        session["reg_pw_hash"] = generate_password_hash(pw)
        session["reg_phone"]   = phone
        session["reg_email"]   = email
        flash("OTP berhasil dikirim ke Telegram kamu!", "success")
        return redirect(url_for("verify"))
    return render_template("register.html")


@app.route("/verify", methods=["GET","POST"])
def verify():
    if "reg_tid" not in session:
        return redirect(url_for("register"))
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        tid   = session["reg_tid"]
        otp   = request.form.get("otp","").strip()
        if not web_verify_otp(tid, otp):
            flash("OTP salah atau kedaluwarsa. Silakan daftar ulang.", "danger")
            for k in ("reg_tid","reg_pw_hash","reg_phone","reg_email"):
                session.pop(k, None)
            return redirect(url_for("register"))
        role  = "admin" if tid == OWNER_ID else "user"
        phone = session.pop("reg_phone", None)
        email = session.pop("reg_email", None)
        web_create_user(tid, None, session.pop("reg_pw_hash"), role,
                        phone=phone, email=email)
        session.pop("reg_tid", None)
        flash("Akun berhasil dibuat! Silakan login.", "success")
        return redirect(url_for("login"))
    return render_template("verify.html")


@app.route("/login", methods=["GET","POST"])
def login():
    if "user_tid" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        ip  = _ip()
        if not _login_check(ip):
            flash("Terlalu banyak percobaan login. Coba lagi dalam 15 menit.", "danger")
            return redirect(url_for("login"))
        idf = request.form.get("identifier","").strip()
        pw  = request.form.get("password","").strip()
        user = web_get_user_by_identifier(idf)
        if not user or not check_password_hash(user["password_hash"], pw):
            _login_record_fail(ip)
            flash("Email / nomor HP / ID atau password salah.", "danger")
            return redirect(url_for("login"))
        _login_clear(ip)
        tid  = user["telegram_id"]
        role = user["role"]
        if tid == OWNER_ID and role != "admin":
            web_update_role(tid, "admin")
            role = "admin"
        session["user_tid"]  = tid
        session["user_role"] = role
        session.permanent    = True
        flash("Selamat datang kembali!", "success")
        return redirect(url_for("admin") if role == "admin" else url_for("dashboard"))
    return render_template("login.html", prefill=request.args.get("id",""))


@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        idf  = request.form.get("identifier","").strip()
        user = web_get_user_by_identifier(idf)
        if not user:
            flash("Akun tidak ditemukan. Pastikan email/nomor HP sudah benar.", "danger")
            return redirect(url_for("forgot_password"))
        tid  = user["telegram_id"]
        otp  = gen_otp()
        exp  = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        web_save_otp(tid, otp, exp)
        toko = load_config().get("nama_toko","Ibra Store")
        ok   = send_telegram(
            tid,
            f"🔑 *Reset Password {toko}*\n\n"
            f"Kode OTP: *{otp}*\nBerlaku 10 menit.\n\n"
            "Jangan bagikan kode ini ke siapapun!"
        )
        if not ok:
            flash("Gagal mengirim OTP ke Telegram. Pastikan bot belum diblokir.", "danger")
            return redirect(url_for("forgot_password"))
        session["reset_tid"] = tid
        flash("OTP dikirim ke Telegram kamu!", "success")
        return redirect(url_for("reset_password"))
    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET","POST"])
def reset_password():
    if "reset_tid" not in session:
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        tid  = session["reset_tid"]
        otp  = request.form.get("otp","").strip()
        pw   = request.form.get("password","").strip()
        pw2  = request.form.get("password2","").strip()
        if not web_verify_otp(tid, otp):
            flash("OTP salah atau kedaluwarsa.", "danger")
            return redirect(url_for("reset_password"))
        pw_err = _validate_password_web(pw)
        if pw_err:
            flash(pw_err, "danger")
            return redirect(url_for("reset_password"))
        if pw != pw2:
            flash("Konfirmasi password tidak cocok.", "danger")
            return redirect(url_for("reset_password"))
        web_update_password(tid, generate_password_hash(pw))
        session.pop("reset_tid", None)
        flash("Password berhasil direset! Silakan login.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Berhasil logout.", "info")
    return redirect(url_for("index"))


# ─── BELI (no login required) ─────────────────────────────────────────────────

@app.route("/beli/<pid>")
def beli(pid):
    raw  = load_produk_raw()
    prod = raw.get(pid)
    if not prod:
        flash("Produk tidak ditemukan.", "danger")
        return redirect(url_for("index"))
    # Build item for template
    tipe_list  = []
    total_stok = 0
    min_harga  = None
    for tid, t in prod.get("tipe", {}).items():
        stok = len(t.get("akun_list", []))
        tipe_list.append({"id": tid, **t, "stok": stok})
        total_stok += stok
        h = t.get("harga", 0)
        if min_harga is None or h < min_harga:
            min_harga = h
    item = {
        "nama":   prod["nama"],
        "gambar": prod.get("gambar"),
        "stok":   total_stok,
        "harga":  min_harga or 0,
        "tipe":   tipe_list,
    }
    current_saldo   = db_get_saldo(session["user_tid"]) if "user_tid" in session else 0
    selected_harga  = tipe_list[0]["harga"] if tipe_list else 0
    qris_ok         = bool(QRIS_BASE64)
    return render_template(
        "beli.html", item=item, pid=pid,
        current_saldo=current_saldo, selected_harga=selected_harga,
        qris_ok=qris_ok, remaining=_rl_remaining(_ip()),
        rate_limit_max=RATE_LIMIT_MAX,
    )


@app.route("/beli/<pid>/saldo", methods=["POST"])
@login_required
def beli_saldo(pid):
    user_tid = session["user_tid"]
    if not _rl_allowed(_ip()):
        flash("Terlalu banyak pembelian. Coba lagi dalam 1 jam.", "danger")
        return redirect(url_for("beli", pid=pid))

    tg_kirim = request.form.get("telegram_id","").strip()
    form_tid = request.form.get("tid","").strip()

    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("index"))
        # Cari tipe
        tipe_dict = prod.get("tipe", {})
        tid        = form_tid if form_tid in tipe_dict else (next(iter(tipe_dict), None))
        if not tid:
            flash("Tidak ada tipe tersedia.", "danger")
            return redirect(url_for("beli", pid=pid))
        tipe = tipe_dict[tid]
        akun_list = tipe.get("akun_list", [])
        if not akun_list:
            flash("Stok habis.", "danger")
            return redirect(url_for("beli", pid=pid))
        harga = tipe["harga"]
        saldo = db_get_saldo(user_tid)
        if saldo < harga:
            flash(f"Saldo tidak cukup (Rp{saldo:,} < Rp{harga:,}). Top up dulu.", "danger")
            return redirect(url_for("beli", pid=pid))
        akun = akun_list.pop(0)
        tipe["akun_list"] = akun_list
        tipe["stok"]      = len(akun_list)
        prod["tipe"][tid] = tipe
        raw[pid] = prod
        save_produk_raw(raw)

    nama_tipe = tipe.get("nama", prod["nama"])
    db_add_saldo(user_tid, -harga)
    trx_id = db_add_riwayat(user_tid, "BELI", f"{prod['nama']} [{nama_tipe}] x1 (Web/Saldo)", harga)

    tg_sent = False
    if tg_kirim:
        try:
            tg_sent = send_telegram(
                int(tg_kirim),
                f"✅ *Pembelian Berhasil!*\n\n"
                f"🛍 *{prod['nama']}*\n"
                f"📦 Tipe: {nama_tipe}\n"
                f"👤 `{akun.get('username','')}`\n"
                f"🔑 `{akun.get('password','')}`\n"
                f"\n🔖 TRX: `{trx_id}`"
            )
        except Exception:
            pass

    item = {"nama": prod["nama"], "harga": harga, "tipe": nama_tipe}
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
    form_tid = request.form.get("tid","").strip()

    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod:
            flash("Stok habis.", "danger")
            return redirect(url_for("index"))
        tipe_dict = prod.get("tipe", {})
        tid        = form_tid if form_tid in tipe_dict else (next(iter(tipe_dict), None))
        if not tid:
            flash("Tidak ada tipe tersedia.", "danger")
            return redirect(url_for("beli", pid=pid))
        tipe = tipe_dict[tid]
        if not tipe.get("akun_list"):
            flash("Stok habis.", "danger")
            return redirect(url_for("beli", pid=pid))
        akun = tipe["akun_list"].pop(0)
        tipe["stok"]      = len(tipe["akun_list"])
        prod["tipe"][tid] = tipe
        raw[pid] = prod
        save_produk_raw(raw)

    harga     = tipe["harga"]
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
    session["bq_tid"]   = tid
    session["bq_total"] = total
    session["bq_nama"]  = f"{prod['nama']} [{tipe.get('nama','')}]"
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
            s_tid    = session.get("bq_tid")
            if reserved and pid:
                with _purchase_lock, produk_lock():
                    raw  = load_produk_raw()
                    prod = raw.get(pid, {})
                    if prod and s_tid and s_tid in prod.get("tipe", {}):
                        t = prod["tipe"][s_tid]
                        t["akun_list"] = reserved + t.get("akun_list", [])
                        t["stok"]      = len(t["akun_list"])
                        raw[pid] = prod
                        save_produk_raw(raw)
            db_remove_pending_any_by_user(uid)
        for k in ["bq_uid","bq_pid","bq_tid","bq_total","bq_nama","bq_harga","bq_tg","bq_start"]:
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

@app.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    tid  = session["user_tid"]
    user = web_get_user_by_tid(tid)
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        action = request.form.get("action","")
        if action == "change_password":
            old_pw  = request.form.get("old_password","").strip()
            new_pw  = request.form.get("new_password","").strip()
            conf_pw = request.form.get("confirm_password","").strip()
            if not check_password_hash(user["password_hash"], old_pw):
                flash("Password lama salah.", "danger")
                return redirect(url_for("profile"))
            pw_err = _validate_password_web(new_pw)
            if pw_err:
                flash(pw_err, "danger")
                return redirect(url_for("profile"))
            if new_pw != conf_pw:
                flash("Konfirmasi password tidak cocok.", "danger")
                return redirect(url_for("profile"))
            web_update_password(tid, generate_password_hash(new_pw))
            flash("Password berhasil diperbarui!", "success")
            return redirect(url_for("profile"))
        elif action == "update_contact":
            phone = request.form.get("phone","").strip() or None
            email = (request.form.get("email","").strip().lower()) or None
            if phone and not _RE_PHONE.match(phone):
                flash("Format nomor HP tidak valid.", "danger")
                return redirect(url_for("profile"))
            if email and not _RE_EMAIL.match(email):
                flash("Format email tidak valid.", "danger")
                return redirect(url_for("profile"))
            if phone and phone != user.get("phone"):
                existing = web_get_user_by_phone(phone)
                if existing and existing["telegram_id"] != tid:
                    flash("Nomor HP sudah digunakan akun lain.", "danger")
                    return redirect(url_for("profile"))
            if email and email != user.get("email"):
                existing = web_get_user_by_email(email)
                if existing and existing["telegram_id"] != tid:
                    flash("Email sudah digunakan akun lain.", "danger")
                    return redirect(url_for("profile"))
            web_update_profile(tid, phone=phone, email=email)
            flash("Profil berhasil diperbarui!", "success")
            return redirect(url_for("profile"))
    return render_template("profile.html", user=user)


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
    cfg = load_config()
    return render_template(
        "deposit.html",
        qris_ok=bool(QRIS_BASE64),
        manual_aktif=cfg.get("transfer_manual_aktif", True)
    )


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
    pending     = db_get_all_pending()
    saldo_all   = db_get_all_saldo()
    stats       = db_get_all_statistik()
    bot_users   = db_get_all_bot_users()
    bot_uid_map = {str(u["telegram_id"]): u.get("username") for u in bot_users}
    return render_template(
        "admin.html",
        pending=pending, saldo_all=saldo_all, stats=stats,
        users_web=web_get_all_users(), produk=load_produk(),
        total_saldo=sum(saldo_all.values()),
        bot_uid_map=bot_uid_map,
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
    """Tambah produk baru (nama + gambar saja; tipe ditambah lewat route terpisah)."""
    nama = request.form.get("nama","").strip()
    if not nama:
        flash("Nama produk wajib diisi.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        nums = [int(k) for k in raw.keys() if k.isdigit()]
        pid  = str(max(nums, default=0) + 1)
        gambar = _save_produk_img(pid, request.files.get("gambar"))
        entry  = {"nama": nama, "tipe": {}}
        if gambar:
            entry["gambar"] = gambar
        raw[pid] = entry
        save_produk_raw(raw)
    flash(f"✅ Produk '{nama}' ditambah. Sekarang tambahkan Tipe.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/tipe/tambah", methods=["POST"])
@admin_required
def admin_produk_tipe_tambah(pid):
    """Tambah tipe baru ke produk."""
    nama_tipe = request.form.get("nama_tipe","").strip()
    harga_raw = request.form.get("harga","0").replace(".","").strip()
    deskripsi = request.form.get("deskripsi","").strip()
    akun_raw  = request.form.get("akun_list","").strip()
    if not nama_tipe:
        flash("Nama tipe wajib diisi.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    try:
        harga = int(harga_raw)
    except ValueError:
        flash("Harga tidak valid.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    akun_list = _parse_akun_lines(akun_raw)
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        tipe_dict = prod.get("tipe", {})
        # Generate tid baru
        nums = [int(k[1:]) for k in tipe_dict.keys() if k.startswith("t") and k[1:].isdigit()]
        tid  = f"t{max(nums, default=0) + 1}"
        tipe_dict[tid] = {
            "nama":      nama_tipe,
            "harga":     harga,
            "deskripsi": deskripsi,
            "akun_list": akun_list,
            "stok":      len(akun_list),
        }
        prod["tipe"] = tipe_dict
        raw[pid] = prod
        save_produk_raw(raw)
    flash(f"✅ Tipe '{nama_tipe}' ditambah ({len(akun_list)} akun).", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/tipe/<tid>/hapus", methods=["POST"])
@admin_required
def admin_produk_tipe_hapus(pid, tid):
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod or tid not in prod.get("tipe", {}):
            flash("Tipe tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        nama_tipe = prod["tipe"].pop(tid, {}).get("nama","")
        raw[pid] = prod
        save_produk_raw(raw)
    flash(f"✅ Tipe '{nama_tipe}' dihapus.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/tipe/<tid>/restock", methods=["POST"])
@admin_required
def admin_produk_tipe_restock(pid, tid):
    akun_raw = request.form.get("akun_list","").strip()
    if not akun_raw:
        flash("Masukkan akun untuk restock.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    akun_baru = _parse_akun_lines(akun_raw)
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod or tid not in prod.get("tipe", {}):
            flash("Tipe tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        t = prod["tipe"][tid]
        t["akun_list"] = t.get("akun_list",[]) + akun_baru
        t["stok"]      = len(t["akun_list"])
        raw[pid] = prod
        save_produk_raw(raw)
    flash(f"✅ Restock {len(akun_baru)} akun untuk tipe '{t.get('nama',tid)}'.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/tipe/<tid>/harga", methods=["POST"])
@admin_required
def admin_produk_tipe_harga(pid, tid):
    try:
        harga = int(request.form.get("harga","0").replace(".","").strip())
    except ValueError:
        flash("Harga tidak valid.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod or tid not in prod.get("tipe", {}):
            flash("Tipe tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        prod["tipe"][tid]["harga"] = harga
        raw[pid] = prod
        save_produk_raw(raw)
    flash(f"✅ Harga tipe diperbarui ke Rp{harga:,}.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/akun")
@admin_required
def admin_produk_akun(pid):
    """JSON: semua akun di semua tipe untuk Lihat Stok modal."""
    raw  = load_produk_raw()
    prod = raw.get(pid)
    if not prod:
        return jsonify({"error": "Produk tidak ditemukan"}), 404
    tipe_data = []
    for tid, t in prod.get("tipe", {}).items():
        akun_list = []
        for i, a in enumerate(t.get("akun_list", [])):
            akun_list.append({
                "idx":      i,
                "username": a.get("username",""),
                "password": a.get("password",""),
            })
        tipe_data.append({"tid": tid, "nama": t.get("nama",""), "akun": akun_list})
    return jsonify({"nama": prod["nama"], "tipe": tipe_data})


@app.route("/admin/produk/<pid>/tipe/<tid>/akun/<int:idx>/hapus", methods=["POST"])
@admin_required
def admin_produk_akun_hapus(pid, tid, idx):
    """Hapus satu akun dari stok berdasarkan tipe + index."""
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod or tid not in prod.get("tipe", {}):
            return jsonify({"error": "Tipe tidak ditemukan"}), 404
        lst = prod["tipe"][tid].get("akun_list", [])
        if idx < 0 or idx >= len(lst):
            return jsonify({"error": "Index tidak valid"}), 400
        lst.pop(idx)
        prod["tipe"][tid]["akun_list"] = lst
        prod["tipe"][tid]["stok"]      = len(lst)
        raw[pid] = prod
        save_produk_raw(raw)
    return jsonify({"ok": True, "sisa": len(lst)})


@app.route("/admin/produk/<pid>/gambar/hapus", methods=["POST"])
@admin_required
def admin_produk_gambar_hapus(pid):
    """Hapus gambar produk."""
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        gambar = prod.pop("gambar", None)
        if gambar:
            # Hapus file fisik
            path = gambar.lstrip("/")
            if os.path.exists(path):
                os.remove(path)
        raw[pid] = prod
        save_produk_raw(raw)
    flash("✅ Gambar produk dihapus.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/restock", methods=["POST"])
@admin_required
def admin_produk_restock(pid):
    """Backward compat: restock ke tipe pertama."""
    akun_raw = request.form.get("akun_list","").strip()
    if not akun_raw:
        flash("Masukkan akun untuk restock.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    akun_baru = _parse_akun_lines(akun_raw)
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        tipe_dict = prod.get("tipe", {})
        if not tipe_dict:
            flash("Produk tidak memiliki tipe.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        tid = next(iter(tipe_dict))
        t   = tipe_dict[tid]
        t["akun_list"] = t.get("akun_list",[]) + akun_baru
        t["stok"]      = len(t["akun_list"])
        raw[pid] = prod
        save_produk_raw(raw)
    flash(f"✅ Restock {len(akun_baru)} akun.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/gambar", methods=["POST"])
@admin_required
def admin_produk_gambar(pid):
    """Ganti/upload gambar produk yang sudah ada."""
    file = request.files.get("gambar")
    if not file or not file.filename:
        flash("Pilih file gambar terlebih dahulu.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        item = raw.get(pid)
        if not item:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        # Hapus gambar lama dulu (semua ekstensi)
        for ext in ALLOWED_IMG:
            old = os.path.join(PRODUK_IMG_DIR, f"{pid}.{ext}")
            if os.path.exists(old):
                os.remove(old)
        gambar = _save_produk_img(pid, file)
        if not gambar:
            flash("Format gambar tidak valid (gunakan PNG/JPG/WEBP).", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        item["gambar"] = gambar
        raw[pid] = item
        save_produk_raw(raw)
    flash(f"✅ Gambar produk diperbarui.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/logo", methods=["POST"])
@admin_required
def admin_logo_upload():
    """Upload logo toko — dipakai di navbar web, favicon, dan bot /start."""
    file = request.files.get("logo")
    if not file or not file.filename:
        flash("Pilih file logo terlebih dahulu.", "danger")
        return redirect(url_for("admin") + "#tab-config")
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMG:
        flash("Format tidak valid (gunakan PNG/JPG/WEBP).", "danger")
        return redirect(url_for("admin") + "#tab-config")
    # Hapus logo lama semua ekstensi
    for e in LOGO_EXTS:
        old = os.path.join("static", f"logo.{e}")
        if os.path.exists(old):
            os.remove(old)
    dest = os.path.join("static", f"logo.{ext}")
    file.save(dest)
    flash("✅ Logo toko berhasil diperbarui.", "success")
    return redirect(url_for("admin") + "#tab-config")


@app.route("/admin/produk/<pid>/rename", methods=["POST"])
@admin_required
def admin_produk_rename(pid):
    """Ganti nama produk."""
    nama_baru = request.form.get("nama_baru","").strip()
    if not nama_baru:
        flash("Nama produk tidak boleh kosong.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    with _purchase_lock, produk_lock():
        raw = load_produk_raw()
        if pid not in raw:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        nama_lama = raw[pid].get("nama","")
        raw[pid]["nama"] = nama_baru
        save_produk_raw(raw)
    flash(f"✅ Nama produk diubah dari '{nama_lama}' → '{nama_baru}'.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/produk/<pid>/tipe/<tid>/rename", methods=["POST"])
@admin_required
def admin_produk_tipe_rename(pid, tid):
    """Ganti nama tipe produk."""
    nama_baru = request.form.get("nama_baru","").strip()
    if not nama_baru:
        flash("Nama tipe tidak boleh kosong.", "danger")
        return redirect(url_for("admin") + "#tab-produk")
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod or tid not in prod.get("tipe", {}):
            flash("Tipe tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        nama_lama = prod["tipe"][tid].get("nama","")
        prod["tipe"][tid]["nama"] = nama_baru
        raw[pid] = prod
        save_produk_raw(raw)
    flash(f"✅ Nama tipe diubah dari '{nama_lama}' → '{nama_baru}'.", "success")
    return redirect(url_for("admin") + "#tab-produk")


@app.route("/admin/user/<int:tid>/gen-password", methods=["POST"])
@admin_required
def admin_user_gen_password(tid):
    """Generate password baru untuk user dan kirim via Telegram."""
    import secrets, string
    chars    = string.ascii_letters + string.digits
    new_pw   = "".join(secrets.choice(chars) for _ in range(12))
    usr      = web_get_user_by_tid(tid)
    if not usr:
        flash("User tidak ditemukan.", "danger")
        return redirect(url_for("admin") + "#tab-users")
    web_update_password(tid, generate_password_hash(new_pw))
    send_telegram(tid,
        f"🔑 *Password akunmu direset oleh admin.*\n\n"
        f"Password baru: `{new_pw}`\n\n"
        f"Segera login dan ganti password kamu di pengaturan."
    )
    flash(f"✅ Password baru untuk @{usr.get('telegram_username') or tid} sudah di-generate dan dikirim via Telegram.", "success")
    return redirect(url_for("admin") + "#tab-users")


@app.route("/admin/produk/<pid>/hapus", methods=["POST"])
@admin_required
def admin_produk_hapus(pid):
    with _purchase_lock, produk_lock():
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
    # Toggle Transfer Manual
    cfg["transfer_manual_aktif"] = request.form.get("transfer_manual_aktif") == "on"
    # Brand color
    brand_color = request.form.get("brand_color", "").strip()
    if brand_color in BRAND_PRESETS:
        cfg["brand_color"] = brand_color
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
    pesan     = request.form.get("pesan","").strip()
    convert   = request.form.get("convert_emoji") == "on"
    if not pesan:
        flash("Pesan tidak boleh kosong.", "danger")
        return redirect(url_for("admin") + "#tab-broadcast")

    # Coba convert emoji ke premium jika diminta
    final_text    = pesan
    final_ents    = None  # None = pakai parse_mode Markdown
    if convert:
        try:
            from premium_emoji import build_http_entities as _pe_raw_web
            import json as _json
            plain, raw = _pe_raw_web(pesan, "Markdown")
            if raw:
                final_text = plain
                final_ents = raw   # list of dicts (raw entity format for HTTP API)
        except Exception:
            pass

    saldo_all = db_get_all_saldo()
    users_web = web_get_all_users()
    uids      = set(int(k) for k in saldo_all.keys())
    for u in users_web:
        uids.add(int(u["telegram_id"]))

    ok_count = 0
    for uid in uids:
        if final_ents:
            try:
                import httpx as _hx
                r = _hx.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": uid, "text": final_text, "entities": final_ents},
                    timeout=8,
                )
                if r.json().get("ok"):
                    ok_count += 1
            except Exception:
                pass
        else:
            if send_telegram(uid, final_text):
                ok_count += 1

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
