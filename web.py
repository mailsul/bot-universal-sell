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
    db_get_rekap_penjualan,
    db_add_audit_log, db_get_audit_log, db_get_daily_sales,
    web_set_force_password_change,
    db_add_voucher, db_get_all_vouchers, db_use_voucher, db_check_voucher, db_delete_voucher, db_toggle_voucher,
    web_session_create, web_session_list, web_session_update_seen,
    web_session_deactivate, web_session_deactivate_all,
    db_ticket_create, db_ticket_list, db_ticket_list_by_user,
    db_ticket_reply, db_ticket_close, db_ticket_get,
    web_ensure_referral_kode, web_get_user_by_referral_kode,
    web_set_referral_by, db_referral_create, db_referral_list,
    db_referral_pending_list, db_referral_approve, db_referral_reject,
    db_is_first_purchase, web_set_dua_fa,
)
from qris_helper import generate_qr_with_amount

BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
OWNER_ID     = int(os.getenv("OWNER_ID", "1160642744"))
_extra_admins = os.getenv("EXTRA_ADMIN_IDS", "")
ADMIN_IDS    = [OWNER_ID] + [int(x) for x in _extra_admins.split(",") if x.strip().isdigit()]
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "0"))
SECRET_KEY   = os.getenv("WEB_SECRET_KEY") or secrets.token_hex(32)
WEB_PORT     = int(os.getenv("WEB_PORT", "5000"))

_bot_username_cache: str = ""

def _get_bot_username() -> str:
    """Ambil username bot dari Telegram API (lazy, cached)."""
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    if not BOT_TOKEN:
        return ""
    try:
        r = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=5)
        data = r.json()
        if data.get("ok"):
            _bot_username_cache = data["result"].get("username", "")
    except Exception:
        pass
    return _bot_username_cache

URL_MUTASI  = os.getenv("URL_MUTASI", "")
QRIS_BASE64 = os.getenv("QRIS_BASE64", "")

QRIS_EXPIRY_SEC    = 5 * 60   # 5 menit
RATE_LIMIT_MAX     = 5         # pembelian per window
RATE_LIMIT_WIN     = 3600      # 1 jam
LOGIN_FAIL_MAX     = 5         # gagal login per window
LOGIN_FAIL_WIN     = 900       # 15 menit
SESSION_INACTIVITY = 1800      # 30 menit idle → logout otomatis
QRIS_RATE_MAX      = 3         # maks QRIS per IP per 30 menit
QRIS_RATE_WIN      = 1800      # 30 menit
REG_RATE_MAX       = 5         # maks register per IP per jam
REG_RATE_WIN       = 3600      # 1 jam

# ─── IMAGE MAGIC BYTES ────────────────────────────────────────────────────────
def _validate_image_magic(stream) -> bool:
    """Cek magic bytes file — pastikan ini benar-benar gambar, bukan file berbahaya."""
    header = stream.read(16)
    stream.seek(0)
    if header[:3]  == b'\xff\xd8\xff':           return True   # JPEG
    if header[:8]  == b'\x89PNG\r\n\x1a\n':      return True   # PNG
    if header[:4]  in (b'GIF8', b'GIF9'):         return True   # GIF
    if header[:4]  == b'RIFF' and header[8:12] == b'WEBP': return True  # WEBP
    return False

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
_qris_ip_active: dict[str, float] = {}
_qris_ip_lock   = threading.Lock()
_rl_qris_data:  dict[str, list]  = defaultdict(list)
_rl_reg_data:   dict[str, list]  = defaultdict(list)
_web_cancel_log: dict[str, list] = defaultdict(list)
_web_cancel_lock = threading.Lock()
_WEB_CANCEL_LIMIT = 3
_WEB_CANCEL_WIN   = 30 * 60  # 30 menit

def _web_cancel_record(ip: str) -> bool:
    """Catat QRIS cancel (guest/web). Return True jika IP sekarang di-block."""
    now = time.time()
    with _web_cancel_lock:
        h = [t for t in _web_cancel_log[ip] if now - t < _WEB_CANCEL_WIN]
        h.append(now)
        _web_cancel_log[ip] = h
        return len(h) >= _WEB_CANCEL_LIMIT

def _web_cancel_blocked(ip: str) -> int:
    """Sisa detik block untuk IP ini, atau 0."""
    now = time.time()
    with _web_cancel_lock:
        h = [t for t in _web_cancel_log[ip] if now - t < _WEB_CANCEL_WIN]
        _web_cancel_log[ip] = h
        if len(h) < _WEB_CANCEL_LIMIT:
            return 0
        oldest = min(h)
        return max(0, int(_WEB_CANCEL_WIN - (now - oldest)))


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

def _rl_allowed_bucket(ip: str, bucket: dict, max_n: int, win: int) -> bool:
    """Generic rate limit untuk bucket tertentu (QRIS, register, dll)."""
    now = time.time()
    with _rate_lock:
        times = [t for t in bucket[ip] if now - t < win]
        if len(times) >= max_n:
            bucket[ip] = times
            return False
        times.append(now)
        bucket[ip] = times
    return True

def _generate_kode_unik_web(nominal: int) -> int:
    """Generate kode unik (1–999) yang tidak tabrakan dengan pending QRIS aktif."""
    pending = db_get_all_pending()
    used = {p.get("expected_amount", 0) for p in pending
            if p.get("metode", "").startswith("qris")}
    for _ in range(500):
        code = random.randint(1, 999)
        if (nominal + code) not in used:
            return code
    return random.randint(1, 999)


def _qris_ip_claim(ip: str) -> bool:
    """Klaim slot QRIS untuk IP. Return False jika sudah ada QRIS aktif dari IP ini."""
    now = time.time()
    with _qris_ip_lock:
        ts = _qris_ip_active.get(ip, 0)
        if now - ts < QRIS_EXPIRY_SEC:
            return False
        _qris_ip_active[ip] = now
    return True

def _qris_ip_release(ip: str):
    """Lepas slot QRIS untuk IP."""
    with _qris_ip_lock:
        _qris_ip_active.pop(ip, None)


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

def _safe_url(url: str) -> str:
    """Blok href berbahaya (javascript:, data:, vbscript:, dll)."""
    if not url:
        return "#"
    if url.strip().lower().startswith(("javascript:", "data:", "vbscript:", "file:")):
        return "#"
    return url

app.jinja_env.globals["csrf_token"] = _csrf_token
app.jinja_env.filters["fmt_waktu"]  = fmt_waktu
app.jinja_env.filters["safe_url"]   = _safe_url


# ─── MIDDLEWARE ────────────────────────────────────────────────────────────────

@app.before_request
def _csrf_middleware():
    """CSRF global — validasi semua POST request kecuali route yang punya check sendiri."""
    if request.method != "POST":
        return
    ep = request.endpoint or ""
    # Route yang sudah punya _csrf_ok() check sendiri
    _has_own = {
        "register", "verify", "login", "admin_verify_otp_page",
        "forgot_password", "reset_password", "profile",
    }
    if ep in _has_own:
        return
    # Route yang di-exempt: sendBeacon cancel (hanya hapus sesi sendiri, low risk)
    _exempt = {"beli_cancel", "deposit_cancel", "static"}
    if ep in _exempt or not ep:
        return
    if not _csrf_ok():
        if request.headers.get("Accept", "").startswith("application/json") or request.is_json:
            return jsonify({"error": "CSRF token tidak valid"}), 403
        flash("Sesi tidak valid. Refresh halaman dan coba lagi.", "danger")
        return redirect(request.referrer or url_for("index"))


@app.before_request
def _global_middleware():
    """Session inactivity timeout (30 menit) + maintenance mode."""
    # 1. Inactivity timeout
    if "user_tid" in session:
        last = session.get("_last_active", 0)
        if time.time() - last > SESSION_INACTIVITY:
            session.clear()
            flash("Sesi berakhir karena tidak aktif 30 menit.", "warning")
            return redirect(url_for("login"))
        session["_last_active"] = time.time()
    # 2. Maintenance mode — admin dan endpoint terkait admin tidak terkena
    ep = request.endpoint or ""
    if ep and ep != "static" and not ep.startswith("admin"):
        if session.get("user_role") != "admin":
            cfg = load_config()
            if cfg.get("maintenance_mode"):
                return render_template("maintenance.html", cfg=cfg)


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
        "brand": "#7c3aed", "dark": "#5b21b6", "light": "#ede9fe", "mid": "#a78bfa",
        "dm_brand": "#a78bfa", "dm_dark": "#7c3aed", "dm_light": "#2d1d5e",
        "glow": "rgba(124,58,237,.13)", "glow_h": "rgba(124,58,237,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#120d1b", "dm_bg2": "#181026", "dm_border": "#321d57", "dm_input": "#211535", "dm_hover": "#281941",
    },
    "blue": {
        "brand": "#2563eb", "dark": "#1d4ed8", "light": "#dbeafe", "mid": "#60a5fa",
        "dm_brand": "#60a5fa", "dm_dark": "#2563eb", "dm_light": "#1e2a4a",
        "glow": "rgba(37,99,235,.13)", "glow_h": "rgba(37,99,235,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#080b11", "dm_bg2": "#0c111c", "dm_border": "#172646", "dm_input": "#0f1625", "dm_hover": "#131c30",
    },
    "green": {
        "brand": "#059669", "dark": "#047857", "light": "#d1fae5", "mid": "#34d399",
        "dm_brand": "#34d399", "dm_dark": "#059669", "dm_light": "#0d2e20",
        "glow": "rgba(5,150,105,.13)", "glow_h": "rgba(5,150,105,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#08110e", "dm_bg2": "#0c1c17", "dm_border": "#143d30", "dm_input": "#0e241d", "dm_hover": "#112c23",
    },
    "red": {
        "brand": "#dc2626", "dark": "#b91c1c", "light": "#fee2e2", "mid": "#f87171",
        "dm_brand": "#f87171", "dm_dark": "#dc2626", "dm_light": "#2d0a0a",
        "glow": "rgba(220,38,38,.13)", "glow_h": "rgba(220,38,38,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#110808", "dm_bg2": "#1c0c0c", "dm_border": "#3b1515", "dm_input": "#240e0e", "dm_hover": "#2b1111",
    },
    "orange": {
        "brand": "#ea580c", "dark": "#c2410c", "light": "#ffedd5", "mid": "#fb923c",
        "dm_brand": "#fb923c", "dm_dark": "#ea580c", "dm_light": "#2d1400",
        "glow": "rgba(234,88,12,.13)", "glow_h": "rgba(234,88,12,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#110b08", "dm_bg2": "#1c110c", "dm_border": "#3d2214", "dm_input": "#24160e", "dm_hover": "#2c1a11",
    },
    "pink": {
        "brand": "#db2777", "dark": "#be185d", "light": "#fce7f3", "mid": "#f472b6",
        "dm_brand": "#f472b6", "dm_dark": "#db2777", "dm_light": "#2d0a1e",
        "glow": "rgba(219,39,119,.13)", "glow_h": "rgba(219,39,119,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#11080c", "dm_bg2": "#1c0c13", "dm_border": "#3b1526", "dm_input": "#240e18", "dm_hover": "#2b111d",
    },
    "teal": {
        "brand": "#0d9488", "dark": "#0f766e", "light": "#ccfbf1", "mid": "#2dd4bf",
        "dm_brand": "#2dd4bf", "dm_dark": "#0d9488", "dm_light": "#0a2525",
        "glow": "rgba(13,148,136,.13)", "glow_h": "rgba(13,148,136,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#081110", "dm_bg2": "#0c1c1b", "dm_border": "#143d39", "dm_input": "#0e2422", "dm_hover": "#112c29",
    },
    "indigo": {
        "brand": "#4f46e5", "dark": "#4338ca", "light": "#e0e7ff", "mid": "#818cf8",
        "dm_brand": "#818cf8", "dm_dark": "#4f46e5", "dm_light": "#1a1a45",
        "glow": "rgba(79,70,229,.13)", "glow_h": "rgba(79,70,229,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#0f0e1d", "dm_bg2": "#131129", "dm_border": "#221e59", "dm_input": "#191738", "dm_hover": "#1c1a44",
    },
    "yellow": {
        "brand": "#ca8a04", "dark": "#a16207", "light": "#fef9c3", "mid": "#facc15",
        "dm_brand": "#facc15", "dm_dark": "#ca8a04", "dm_light": "#2a1e00",
        "glow": "rgba(202,138,4,.13)", "glow_h": "rgba(202,138,4,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#110e08", "dm_bg2": "#1c170c", "dm_border": "#3d3014", "dm_input": "#241d0e", "dm_hover": "#2c2311",
    },
    "cyan": {
        "brand": "#0891b2", "dark": "#0e7490", "light": "#cffafe", "mid": "#22d3ee",
        "dm_brand": "#22d3ee", "dm_dark": "#0891b2", "dm_light": "#062535",
        "glow": "rgba(8,145,178,.13)", "glow_h": "rgba(8,145,178,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#080f11", "dm_bg2": "#0c191c", "dm_border": "#14353d", "dm_input": "#0e2024", "dm_hover": "#11262c",
    },
    "rose": {
        "brand": "#e11d48", "dark": "#be123c", "light": "#ffe4e6", "mid": "#fb7185",
        "dm_brand": "#fb7185", "dm_dark": "#e11d48", "dm_light": "#2d0a14",
        "glow": "rgba(225,29,72,.13)", "glow_h": "rgba(225,29,72,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#11080a", "dm_bg2": "#1c0c0f", "dm_border": "#3d141d", "dm_input": "#240e13", "dm_hover": "#2c1117",
    },
    "amber": {
        "brand": "#d97706", "dark": "#b45309", "light": "#fef3c7", "mid": "#fbbf24",
        "dm_brand": "#fbbf24", "dm_dark": "#d97706", "dm_light": "#271900",
        "glow": "rgba(217,119,6,.13)", "glow_h": "rgba(217,119,6,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#110d08", "dm_bg2": "#1c140c", "dm_border": "#3d2a14", "dm_input": "#241a0e", "dm_hover": "#2c1f11",
    },
    "lime": {
        "brand": "#65a30d", "dark": "#4d7c0f", "light": "#ecfccb", "mid": "#a3e635",
        "dm_brand": "#a3e635", "dm_dark": "#65a30d", "dm_light": "#162007",
        "glow": "rgba(101,163,13,.13)", "glow_h": "rgba(101,163,13,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#0d1108", "dm_bg2": "#151c0c", "dm_border": "#2c3d14", "dm_input": "#1b240e", "dm_hover": "#202c11",
    },
    "sky": {
        "brand": "#0284c7", "dark": "#0369a1", "light": "#e0f2fe", "mid": "#38bdf8",
        "dm_brand": "#38bdf8", "dm_dark": "#0284c7", "dm_light": "#062030",
        "glow": "rgba(2,132,199,.13)", "glow_h": "rgba(2,132,199,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#080e11", "dm_bg2": "#0c171c", "dm_border": "#142f3d", "dm_input": "#0e1c24", "dm_hover": "#11222c",
    },
    "violet": {
        "brand": "#6d28d9", "dark": "#5b21b6", "light": "#ede9fe", "mid": "#8b5cf6",
        "dm_brand": "#8b5cf6", "dm_dark": "#6d28d9", "dm_light": "#2a1764",
        "glow": "rgba(109,40,217,.13)", "glow_h": "rgba(109,40,217,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#0b0811", "dm_bg2": "#120c1c", "dm_border": "#24163b", "dm_input": "#170e24", "dm_hover": "#1b112b",
    },
    "slate": {
        "brand": "#475569", "dark": "#334155", "light": "#f1f5f9", "mid": "#94a3b8",
        "dm_brand": "#94a3b8", "dm_dark": "#475569", "dm_light": "#1e2535",
        "glow": "rgba(71,85,105,.13)", "glow_h": "rgba(71,85,105,.30)",
        "brand_text": "#ffffff", "brand_navbar_text": "rgba(255,255,255,.9)",
        "dm_bg": "#0b0c0e", "dm_bg2": "#121316", "dm_border": "#23272d", "dm_input": "#16181c", "dm_hover": "#1b1d22",
    },
}


def hex_to_brand(hex_color: str) -> dict:
    """Compute full brand dict from any hex color string (e.g. '#7c3aed')."""
    import colorsys
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return BRAND_PRESETS["purple"]
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)

    def _hex(*rgb): return "#{:02x}{:02x}{:02x}".format(*[int(c * 255) for c in rgb])
    def _hls(hu, li, sa): return _hex(*colorsys.hls_to_rgb(hu, max(0, min(1, li)), max(0, min(1, sa))))

    dark     = _hls(hue, lightness - 0.15, saturation)
    light    = _hls(hue, min(0.96, lightness + 0.50), min(0.9, saturation))
    mid      = _hls(hue, min(0.80, lightness + 0.18), saturation)
    dm_brand = _hls(hue, min(0.78, lightness + 0.22), saturation)
    dm_dark  = hex_color
    dm_light = _hls(hue, max(0.10, lightness - 0.35), min(0.7, saturation))
    ri, gi, bi = int(r * 255), int(g * 255), int(b * 255)

    # Relative luminance untuk tentukan warna teks (WCAG)
    def _lum(c): return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    luminance = 0.2126 * _lum(r) + 0.7152 * _lum(g) + 0.0722 * _lum(b)
    brand_text        = "#111111" if luminance > 0.35 else "#ffffff"
    brand_navbar_text = "rgba(0,0,0,.80)" if luminance > 0.35 else "rgba(255,255,255,.90)"

    dm_bg     = _hls(hue, max(0.05, lightness - 0.50), min(0.35, saturation * 0.55))
    dm_bg2    = _hls(hue, max(0.08, lightness - 0.47), min(0.40, saturation * 0.60))
    dm_border = _hls(hue, max(0.16, lightness - 0.35), min(0.50, saturation * 0.65))
    dm_input  = _hls(hue, max(0.10, lightness - 0.43), min(0.42, saturation * 0.60))
    dm_hover  = _hls(hue, max(0.12, lightness - 0.40), min(0.44, saturation * 0.60))

    return {
        "brand": hex_color, "dark": dark, "light": light, "mid": mid,
        "dm_brand": dm_brand, "dm_dark": dm_dark, "dm_light": dm_light,
        "glow":   f"rgba({ri},{gi},{bi},.13)",
        "glow_h": f"rgba({ri},{gi},{bi},.30)",
        "brand_text":        brand_text,
        "brand_navbar_text": brand_navbar_text,
        "dm_bg": dm_bg, "dm_bg2": dm_bg2, "dm_border": dm_border,
        "dm_input": dm_input, "dm_hover": dm_hover,
    }


def load_config() -> dict:
    try:
        with open("config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"nama_toko": "Ibra Store", "rekening": [], "kontak_admin": ""}

def _get_website_url() -> str:
    """Auto-detect URL website: config manual → REPLIT_DEV_DOMAIN → kosong."""
    cfg    = load_config()
    manual = cfg.get("website_url", "").strip()
    if manual:
        return manual
    domain = os.environ.get("REPLIT_DEV_DOMAIN", "").strip()
    return f"https://{domain}" if domain else ""

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

def send_telegram_pe(chat_id: int, text: str) -> bool:
    """Kirim pesan Telegram dengan konversi premium emoji (entities)."""
    if not BOT_TOKEN:
        return False
    try:
        try:
            from premium_emoji import build_http_entities
            plain, entities = build_http_entities(text, "Markdown")
        except Exception:
            plain, entities = text, []
        payload: dict = {"chat_id": chat_id}
        if entities:
            payload["text"]     = plain
            payload["entities"] = entities
        else:
            payload["text"]       = text
            payload["parse_mode"] = "Markdown"
        r = httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload, timeout=8
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
    if preset_name == "custom":
        brand = hex_to_brand(cfg.get("custom_hex", "#7c3aed"))
    else:
        brand = BRAND_PRESETS.get(preset_name, BRAND_PRESETS["purple"])
    return {
        "cfg":           cfg,
        "current_user":  current_user(),
        "current_saldo": db_get_saldo(session["user_tid"]) if "user_tid" in session else 0,
        "logo_url":      get_logo_url(),
        "brand":         brand,
        "brand_presets": BRAND_PRESETS,
        "brand_name":    preset_name,
        "web_aktif":     cfg.get("web_aktif", True),
        "website_url":   _get_website_url(),
        "bot_username":  _get_bot_username(),
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
        if not _rl_allowed_bucket(_ip(), _rl_reg_data, REG_RATE_MAX, REG_RATE_WIN):
            flash("Terlalu banyak percobaan registrasi. Coba lagi dalam 1 jam.", "danger")
            return redirect(url_for("register"))
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

        referral_kode_input = request.form.get("referral_kode","").strip().upper() or None
        session["reg_tid"]          = tid
        session["reg_pw_hash"]      = generate_password_hash(pw)
        session["reg_phone"]        = phone
        session["reg_email"]        = email
        session["reg_referral_kode"] = referral_kode_input
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
        role  = "admin" if tid in ADMIN_IDS else "user"
        phone = session.pop("reg_phone", None)
        email = session.pop("reg_email", None)
        web_create_user(tid, None, session.pop("reg_pw_hash"), role,
                        phone=phone, email=email)
        # Generate kode referral untuk user baru
        web_ensure_referral_kode(tid)
        # Simpan referral_by jika ada kode referral
        ref_kode = session.pop("reg_referral_kode", None)
        if ref_kode:
            referrer = web_get_user_by_referral_kode(ref_kode)
            if referrer and int(referrer["telegram_id"]) != tid:
                web_set_referral_by(tid, referrer["telegram_id"])
        session.pop("reg_tid", None)
        flash("Akun berhasil dibuat! Silakan login.", "success")
        return redirect(url_for("login"))
    return render_template("verify.html")


def _do_user_login(tid: int, role: str, force_pw: bool = False):
    """Helper: set sesi user + catat web_session ke DB."""
    token = secrets.token_hex(24)
    session["user_tid"]     = tid
    session["user_role"]    = role
    session["force_pw"]     = force_pw
    session.permanent       = True
    session["_last_active"] = time.time()
    session["_web_token"]   = token
    try:
        ip = _ip()
        ua = request.headers.get("User-Agent", "")[:200]
        web_session_create(tid, token, ip, ua)
    except Exception:
        pass


@app.route("/user/2fa", methods=["GET","POST"])
def user_verify_otp_page():
    """Halaman verifikasi OTP 2FA untuk user biasa."""
    if "user_tid" in session:
        return redirect(url_for("dashboard"))
    if "user_2fa_tid" not in session:
        flash("Silakan login terlebih dahulu.", "warning")
        return redirect(url_for("login"))
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        otp  = request.form.get("otp","").strip()
        tid  = session["user_2fa_tid"]
        if web_verify_otp(tid, otp):
            role   = session.pop("user_2fa_role", "user")
            fpw    = session.pop("user_2fa_fpw", False)
            session.pop("user_2fa_tid", None)
            _do_user_login(tid, role, fpw)
            flash("✅ Verifikasi berhasil. Selamat datang!", "success")
            return redirect(url_for("dashboard"))
        flash("Kode OTP salah atau sudah kedaluwarsa.", "danger")
        return redirect(url_for("user_verify_otp_page"))
    return render_template("admin_2fa.html", for_user=True)


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
        if tid in ADMIN_IDS and role != "admin":
            web_update_role(tid, "admin")
            role = "admin"
        if role == "admin":
            # 2FA — kirim OTP ke Telegram sebelum buka admin panel
            otp  = gen_otp()
            exp  = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
            web_save_otp(tid, otp, exp)
            toko = load_config().get("nama_toko", "Ibra Store")
            send_telegram(
                tid,
                f"🔐 *Login Admin — {toko}*\n\nKode OTP: `{otp}`\nBerlaku 10 menit.\n\n"
                "⚠️ Jika bukan kamu yang login, segera ganti password!"
            )
            session["admin_2fa_tid"]  = tid
            session["admin_2fa_role"] = role
            flash("Kode OTP dikirim ke Telegram kamu. Masukkan untuk masuk ke Admin Panel.", "info")
            return redirect(url_for("admin_verify_otp_page"))
        # User biasa — cek 2FA
        if user.get("dua_fa_aktif"):
            otp  = gen_otp()
            exp  = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
            web_save_otp(tid, otp, exp)
            toko = load_config().get("nama_toko", "Ibra Store")
            ok   = send_telegram(
                tid,
                f"🔐 *Login — {toko}*\n\nKode OTP: `{otp}`\nBerlaku 10 menit.\n\n"
                "Jangan bagikan ke siapapun!"
            )
            if not ok:
                flash("Gagal mengirim OTP ke Telegram. Pastikan bot sudah pernah di-start.", "danger")
                return redirect(url_for("login"))
            session["user_2fa_tid"]  = tid
            session["user_2fa_role"] = role
            session["user_2fa_fpw"]  = bool(user.get("force_password_change"))
            flash("Kode OTP dikirim ke Telegram kamu.", "info")
            return redirect(url_for("user_verify_otp_page"))
        # Login langsung (tanpa 2FA)
        _do_user_login(tid, role, bool(user.get("force_password_change")))
        flash("Selamat datang kembali!", "success")
        return redirect(url_for("dashboard"))
    return render_template("login.html", prefill=request.args.get("id",""))


@app.route("/admin/2fa", methods=["GET","POST"])
def admin_verify_otp_page():
    """Halaman verifikasi OTP 2FA untuk admin."""
    if "user_tid" in session:
        return redirect(url_for("admin"))
    if "admin_2fa_tid" not in session:
        flash("Silakan login terlebih dahulu.", "warning")
        return redirect(url_for("login"))
    if request.method == "POST":
        if not _csrf_ok():
            abort(403)
        otp = request.form.get("otp","").strip()
        tid = session["admin_2fa_tid"]
        if web_verify_otp(tid, otp):
            role = session.pop("admin_2fa_role", "admin")
            session.pop("admin_2fa_tid", None)
            _do_user_login(tid, role, False)
            flash("✅ Verifikasi berhasil. Selamat datang, Admin!", "success")
            return redirect(url_for("admin"))
        flash("Kode OTP salah atau sudah kedaluwarsa.", "danger")
        return redirect(url_for("admin_verify_otp_page"))
    return render_template("admin_2fa.html")


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


def _log_group(text: str) -> None:
    """Kirim log transaksi ke grup admin (LOG_GROUP_ID)."""
    if LOG_GROUP_ID:
        send_telegram(LOG_GROUP_ID, text)


def _stok_alert_web(nama_produk: str, nama_tipe: str, sisa: int):
    """Kirim notifikasi stok rendah ke semua admin via Telegram."""
    msg = (
        f"⚠️ *STOK RENDAH — {nama_produk}*\n"
        f"📦 Tipe: {nama_tipe}\n"
        f"Sisa stok: *{sisa}x* — segera restock!"
    )
    for admin_id in ADMIN_IDS:
        send_telegram(admin_id, msg)
    _log_group(msg)


@app.route("/beli/voucher/check")
def beli_voucher_check():
    """AJAX: cek validitas voucher dan tampilkan nominal diskon."""
    kode = request.args.get("kode","").strip().upper()
    if not kode:
        return jsonify({"valid": False, "pesan": "Kode kosong"})
    cfg = load_config()
    if not cfg.get("voucher_aktif", True):
        return jsonify({"valid": False, "pesan": "Fitur voucher tidak aktif"})
    from db import _get_conn, _lock
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM voucher WHERE kode=? AND aktif=1", (kode,)
        ).fetchone()
        conn.close()
    if not row:
        return jsonify({"valid": False, "pesan": "Kode tidak ditemukan atau tidak aktif"})
    if row["used"] >= row["max_uses"]:
        return jsonify({"valid": False, "pesan": "Kuota voucher sudah habis"})
    uid = str(session.get("user_tid", ""))
    if uid:
        from db import _get_conn, _lock
        with _lock:
            conn = _get_conn()
            already = conn.execute(
                "SELECT 1 FROM voucher_log WHERE kode=? AND user_id=?", (kode, uid)
            ).fetchone()
            conn.close()
        if already:
            return jsonify({"valid": False, "pesan": "Voucher sudah pernah kamu gunakan"})
    return jsonify({"valid": True, "nominal": row["nominal"],
                    "pesan": f"Diskon Rp{row['nominal']:,}"})


@app.route("/beli/<pid>/saldo", methods=["POST"])
@login_required
def beli_saldo(pid):
    user_tid = session["user_tid"]
    if not _rl_allowed(_ip()):
        flash("Terlalu banyak pembelian. Coba lagi dalam 1 jam.", "danger")
        return redirect(url_for("beli", pid=pid))

    tg_kirim     = request.form.get("telegram_id","").strip()
    form_tid     = request.form.get("tid","").strip()
    voucher_kode = request.form.get("voucher_kode","").strip().upper()
    jumlah       = max(1, min(10, int(request.form.get("jumlah","1") or 1)))
    is_first     = db_is_first_purchase(user_tid)

    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod:
            flash("Produk tidak ditemukan.", "danger")
            return redirect(url_for("index"))
        tipe_dict = prod.get("tipe", {})
        tid        = form_tid if form_tid in tipe_dict else (next(iter(tipe_dict), None))
        if not tid:
            flash("Tidak ada tipe tersedia.", "danger")
            return redirect(url_for("beli", pid=pid))
        tipe = tipe_dict[tid]
        akun_list = tipe.get("akun_list", [])
        if len(akun_list) < jumlah:
            flash(f"Stok tidak cukup (tersisa {len(akun_list)} unit).", "danger")
            return redirect(url_for("beli", pid=pid))
        harga = tipe["harga"]

        # Terapkan voucher (diskon hanya untuk satuan pertama)
        diskon = 0
        if voucher_kode:
            cfg_v = load_config()
            if cfg_v.get("voucher_aktif", True):
                v_result = db_use_voucher(voucher_kode, str(user_tid))
                if isinstance(v_result, int):
                    diskon = min(v_result, harga)
                elif v_result == "used":
                    flash("Voucher sudah pernah kamu gunakan.", "warning")
                    return redirect(url_for("beli", pid=pid))
                else:
                    flash("Kode voucher tidak valid atau sudah habis.", "danger")
                    return redirect(url_for("beli", pid=pid))

        harga_bayar = max(0, harga - diskon) * jumlah
        saldo = db_get_saldo(user_tid)
        if saldo < harga_bayar:
            flash(f"Saldo tidak cukup (Rp{saldo:,} < Rp{harga_bayar:,}). Top up dulu.", "danger")
            return redirect(url_for("beli", pid=pid))
        akun_list_beli = [akun_list.pop(0) for _ in range(jumlah)]
        tipe["akun_list"] = akun_list
        tipe["stok"]      = len(akun_list)
        prod["tipe"][tid] = tipe
        raw[pid] = prod
        save_produk_raw(raw)
        sisa_stok = len(akun_list)

    nama_tipe = tipe.get("nama", prod["nama"])
    db_add_saldo(user_tid, -harga_bayar)
    ket_vc = f" [Voucher -{diskon:,}]" if diskon else ""
    trx_id = db_add_riwayat(user_tid, "BELI",
                             f"{prod['nama']} [{nama_tipe}] x{jumlah} (Web/Saldo){ket_vc}", harga_bayar)

    tg_sent = False
    if tg_kirim:
        try:
            waktu_beli = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            akun_lines = ""
            for i, ak in enumerate(akun_list_beli, start=1):
                akun_lines += f"\n{'─'*20}\n🔢 Akun {i}\n👤 `{ak.get('username','')}`\n🔑 `{ak.get('password','')}`"
                if ak.get("extra"): akun_lines += f"\nℹ️ {ak['extra']}"
            msg_beli = (
                f"✅ *PEMBELIAN BERHASIL!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 {prod['nama']} [{nama_tipe}] x{jumlah}\n"
                f"💳 Metode: Web / Saldo\n"
                + (f"🏷 Diskon Voucher: -Rp{diskon:,}\n" if diskon else "")
                + f"💸 Total Dibayar: Rp{harga_bayar:,}\n"
                f"🔖 ID Transaksi: `{trx_id}`\n"
                f"📅 {waktu_beli}\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
                + akun_lines
            )
            tg_sent = send_telegram_pe(int(tg_kirim), msg_beli)
        except Exception:
            pass

    # Log ke grup admin
    _log_group(
        f"🛒 *PENJUALAN BARU*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: `{user_tid}`\n"
        f"📦 {prod['nama']} [{nama_tipe}] x{jumlah}\n"
        f"💳 Metode: Web / Saldo\n"
        f"💸 Total: Rp{harga_bayar:,}"
        + (f" (Voucher -Rp{diskon:,})" if diskon else "")
        + f"\n🔖 TRX: `{trx_id}`"
    )

    cfg_s = load_config()
    # Stok alert
    if cfg_s.get("stok_alert_aktif"):
        stok_min = int(cfg_s.get("stok_alert_min", 2))
        if 0 < sisa_stok <= stok_min:
            _stok_alert_web(prod["nama"], nama_tipe, sisa_stok)

    # Referral trigger
    user_data = web_get_user_by_tid(user_tid)
    if user_data and user_data.get("referral_by") and is_first and cfg_s.get("referral_aktif"):
        referrer_tid = int(user_data["referral_by"])
        bonus = int(cfg_s.get("referral_bonus", 5000))
        if cfg_s.get("referral_konfirmasi", "otomatis") == "otomatis":
            db_add_saldo(referrer_tid, bonus)
            db_add_riwayat(referrer_tid, "REFERRAL", f"Bonus referral (user {user_tid})", bonus)
            send_telegram(referrer_tid,
                f"🎉 *Bonus Referral!*\nKamu mendapat *Rp{bonus:,}* karena teman kamu "
                f"baru saja melakukan pembelian pertama!")
        else:
            lid = db_referral_create(referrer_tid, user_tid, bonus)
            if lid:
                send_telegram(referrer_tid,
                    f"🎉 Ada bonus referral *Rp{bonus:,}* menunggu konfirmasi admin.")

    deskripsi = tipe.get("deskripsi", "").strip()
    item = {"nama": prod["nama"], "harga": harga, "harga_bayar": harga_bayar,
            "diskon": diskon, "tipe": nama_tipe, "jumlah": jumlah}
    return render_template(
        "beli_sukses.html", akun_list=akun_list_beli, item=item,
        trx_id=trx_id, tg_sent=tg_sent, tg_kirim=tg_kirim, metode="Saldo",
        deskripsi=deskripsi,
    )


@app.route("/beli/<pid>/qris", methods=["POST"])
def beli_qris(pid):
    ip = _ip()
    if not _rl_allowed(ip):
        flash("Terlalu banyak pembelian. Coba lagi dalam 1 jam.", "danger")
        return redirect(url_for("beli", pid=pid))
    if not _rl_allowed_bucket(ip, _rl_qris_data, QRIS_RATE_MAX, QRIS_RATE_WIN):
        flash("Terlalu banyak percobaan QRIS. Coba lagi dalam 30 menit.", "danger")
        return redirect(url_for("beli", pid=pid))
    sisa_blok_web = _web_cancel_blocked(ip)
    if sisa_blok_web > 0:
        menit_web = (sisa_blok_web + 59) // 60
        flash(f"Terlalu sering membatalkan QRIS. Coba lagi dalam ±{menit_web} menit.", "danger")
        return redirect(url_for("beli", pid=pid))
    if not QRIS_BASE64:
        flash("QRIS belum dikonfigurasi.", "danger")
        return redirect(url_for("beli", pid=pid))

    tg_kirim     = request.form.get("telegram_id","").strip()
    form_tid     = request.form.get("tid","").strip()
    voucher_kode = request.form.get("voucher_kode","").strip().upper()
    jumlah       = max(1, min(10, int(request.form.get("jumlah","1") or 1)))

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
        if len(tipe.get("akun_list", [])) < jumlah:
            flash(f"Stok tidak cukup (tersisa {len(tipe.get('akun_list',[]))} unit).", "danger")
            return redirect(url_for("beli", pid=pid))
        reserved_akun = [tipe["akun_list"].pop(0) for _ in range(jumlah)]
        tipe["stok"]      = len(tipe["akun_list"])
        prod["tipe"][tid] = tipe
        raw[pid] = prod
        save_produk_raw(raw)

    harga  = tipe["harga"]
    # Validasi voucher tanpa konsumsi (dikonsumsi saat pembayaran terkonfirmasi)
    diskon_vc = 0
    vc_valid  = ""
    if voucher_kode:
        cfg_v = load_config()
        if cfg_v.get("voucher_aktif", True):
            chk = db_check_voucher(voucher_kode, str(session.get("user_tid", "")))
            if isinstance(chk, int):
                diskon_vc = min(chk, harga)
                vc_valid  = voucher_kode

    nominal   = max(0, harga - diskon_vc) * jumlah
    kode_unik = _generate_kode_unik_web(nominal)
    total     = nominal + kode_unik

    if "user_tid" in session:
        buyer_uid = session["user_tid"]
    else:
        # Guest: satu QRIS aktif per IP pada satu waktu
        if not _qris_ip_claim(ip):
            flash("Sudah ada pembayaran QRIS aktif dari jaringan ini. Selesaikan atau tunggu sampai expired.", "danger")
            return redirect(url_for("beli", pid=pid))
        if "guest_uid" not in session:
            session["guest_uid"] = random.randint(10**13, 10**14 - 1)
        buyer_uid = session["guest_uid"]

    db_remove_pending_any_by_user(buyer_uid)
    db_add_pending({
        "user_id":         buyer_uid,
        "metode":          "qris_beli_web",
        "nominal":         nominal,
        "expected_amount": total,
        "kode_unik":       kode_unik,
        "waktu":           datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "produk_id":       pid,
        "jumlah":          jumlah,
        "reserved_akun":   reserved_akun,
        "total_transfer":  total,
        "voucher_kode":    vc_valid,
        "diskon_vc":       diskon_vc,
    })

    session["bq_uid"]    = buyer_uid
    session["bq_pid"]    = pid
    session["bq_tid"]    = tid
    session["bq_total"]  = total
    session["bq_nama"]   = f"{prod['nama']} [{tipe.get('nama','')}]"
    session["bq_harga"]  = harga
    session["bq_jumlah"] = jumlah
    session["bq_tg"]     = tg_kirim
    session["bq_start"]  = int(time.time())

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


@app.route("/beli/cancel", methods=["POST"])
def beli_cancel():
    """Auto-cancel QRIS beli saat user meninggalkan halaman (JS sendBeacon). Stok dikembalikan."""
    uid = session.get("bq_uid")
    ip  = _ip()
    if uid:
        pending = db_get_pending_any_by_user(uid)
        if pending and pending.get("metode") == "qris_beli_web":
            reserved = pending.get("reserved_akun", [])
            pid_r    = pending.get("produk_id")
            tid_r    = session.get("bq_tid")
            if reserved and pid_r and tid_r:
                try:
                    with _purchase_lock, produk_lock():
                        raw  = load_produk_raw()
                        prod = raw.get(pid_r)
                        if prod and tid_r in prod.get("tipe", {}):
                            prod["tipe"][tid_r]["akun_list"] = reserved + prod["tipe"][tid_r].get("akun_list", [])
                            prod["tipe"][tid_r]["stok"]      = len(prod["tipe"][tid_r]["akun_list"])
                            raw[pid_r] = prod
                            save_produk_raw(raw)
                except Exception:
                    pass
            db_remove_pending_any_by_user(uid)
    _web_cancel_record(ip)
    _qris_ip_release(ip)
    for k in ["bq_uid","bq_pid","bq_tid","bq_total","bq_nama","bq_harga","bq_tg","bq_start"]:
        session.pop(k, None)
    return "", 204


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
    jumlah_s = session.get("bq_jumlah", len(reserved) or 1)
    tg_kirim = session.get("bq_tg", "")
    vc_kode  = pending.get("voucher_kode", "")
    diskon_p = pending.get("diskon_vc", 0)
    nominal_p = pending.get("nominal", total)

    db_remove_pending_by_id(pending["id"])

    # Konsumsi voucher
    if vc_kode:
        db_use_voucher(vc_kode, str(session.get("user_tid", uid)))

    ket_vc = f" [Voucher -{diskon_p:,}]" if diskon_p else ""
    if "user_tid" in session:
        trx_id = db_add_riwayat(session["user_tid"], "BELI", f"{nama} x{jumlah_s} (Web/QRIS){ket_vc}", nominal_p)
    else:
        trx_id = gen_trx_id()

    akun_list_r = reserved if reserved else [{}]

    tg_sent = False
    if tg_kirim:
        try:
            waktu_qris = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            akun_lines = ""
            for i, ak in enumerate(akun_list_r, start=1):
                akun_lines += f"\n{'─'*20}\n🔢 Akun {i}\n👤 `{ak.get('username','')}`\n🔑 `{ak.get('password','')}`"
                if ak.get("extra"): akun_lines += f"\nℹ️ {ak['extra']}"
            msg_beli = (
                f"✅ *PEMBELIAN BERHASIL!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 {nama} x{jumlah_s}\n"
                f"💳 Metode: Web / QRIS\n"
                + (f"🏷 Diskon Voucher: -Rp{diskon_p:,}\n" if diskon_p else "")
                + f"💸 Total Dibayar: Rp{nominal_p:,}\n"
                f"🔖 ID Transaksi: `{trx_id}`\n"
                f"📅 {waktu_qris}\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
                + akun_lines
            )
            tg_sent = send_telegram_pe(int(tg_kirim), msg_beli)
        except Exception:
            pass

    _log_group(
        f"🛒 *PENJUALAN BARU*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: `{session.get('user_tid','Guest')}`\n"
        f"📦 {nama} x{jumlah_s}\n"
        f"💳 Metode: Web / QRIS\n"
        f"💸 Total: Rp{nominal_p:,}"
        + (f" (Voucher -Rp{diskon_p:,})" if diskon_p else "")
        + f"\n🔖 TRX: `{trx_id}`"
    )

    # Ambil deskripsi tipe dari produk
    _bq_desc = ""
    try:
        _raw_tmp = load_produk_raw()
        _prod_tmp = _raw_tmp.get(pid, {})
        _bq_tid   = session.get("bq_tid", "")
        _bq_desc  = _prod_tmp.get("tipe", {}).get(_bq_tid, {}).get("deskripsi", "").strip()
    except Exception:
        pass

    session["bs_akun_list"] = akun_list_r
    session["bs_trx"]       = trx_id
    session["bs_item"]    = {"nama": nama, "harga": harga, "jumlah": jumlah_s}
    session["bs_tg_sent"] = tg_sent
    session["bs_tg"]      = tg_kirim
    session["bs_metode"]  = "QRIS"
    session["bs_desc"]    = _bq_desc

    for k in ["bq_uid","bq_pid","bq_total","bq_nama","bq_harga","bq_jumlah","bq_tg","bq_start"]:
        session.pop(k, None)

    return jsonify({"status": "success", "redirect": url_for("beli_sukses")})


@app.route("/beli/sukses")
def beli_sukses():
    akun_list = session.pop("bs_akun_list", None)
    # backward compat: jika ada single akun (dari saldo langsung)
    if akun_list is None:
        akun_list = []
    trx_id    = session.pop("bs_trx", "-")
    item      = session.pop("bs_item", {})
    tg_sent   = session.pop("bs_tg_sent", False)
    tg_kirim  = session.pop("bs_tg", "")
    metode    = session.pop("bs_metode", "")
    deskripsi = session.pop("bs_desc", "")
    if not akun_list:
        return redirect(url_for("index"))
    return render_template(
        "beli_sukses.html", akun_list=akun_list, item=item,
        trx_id=trx_id, tg_sent=tg_sent, tg_kirim=tg_kirim, metode=metode,
        deskripsi=deskripsi,
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
        elif action == "toggle_2fa":
            new_val = 1 if not user.get("dua_fa_aktif") else 0
            web_set_dua_fa(tid, new_val)
            flash(f"Verifikasi 2 Langkah {'diaktifkan' if new_val else 'dinonaktifkan'}.", "success")
            return redirect(url_for("profile"))
        elif action == "logout_session":
            sid = request.form.get("session_id","")
            try:
                web_session_deactivate(int(sid), tid)
                flash("Sesi berhasil diakhiri.", "success")
            except Exception:
                flash("Gagal mengakhiri sesi.", "danger")
            return redirect(url_for("profile"))
        elif action == "logout_all_sessions":
            web_session_deactivate_all(tid, except_token=session.get("_web_token"))
            flash("Semua sesi lain berhasil diakhiri.", "success")
            return redirect(url_for("profile"))
    user = web_get_user_by_tid(tid)
    ref_kode  = web_ensure_referral_kode(tid)
    sessions  = web_session_list(tid)
    ref_list  = db_referral_list(tid)
    my_tickets = db_ticket_list_by_user(tid)
    return render_template("profile.html", user=user, ref_kode=ref_kode,
                           sessions=sessions, ref_list=ref_list,
                           my_tickets=my_tickets,
                           cur_token=session.get("_web_token",""))


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
    if not _validate_image_magic(f.stream):
        flash("File tidak valid. Hanya gambar asli (JPG/PNG/WEBP) yang diperbolehkan.", "danger")
        return redirect(url_for("deposit"))
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
    pending     = [p for p in db_get_all_pending() if not p.get("metode","").startswith("qris")]
    saldo_all   = db_get_all_saldo()
    stats       = db_get_all_statistik()
    bot_users   = db_get_all_bot_users()
    bot_uid_map = {str(u["telegram_id"]): u.get("username") for u in bot_users}
    cfg = load_config()
    from db import _get_conn, _lock
    with _lock:
        conn = _get_conn()
        vouchers    = conn.execute("SELECT * FROM voucher ORDER BY rowid DESC").fetchall()
        ref_pending = conn.execute(
            "SELECT * FROM referral_log WHERE status='pending' ORDER BY id DESC"
        ).fetchall()
        tiket_list  = conn.execute(
            "SELECT * FROM support_tickets ORDER BY id DESC LIMIT 100"
        ).fetchall()
        conn.close()
    return render_template(
        "admin.html",
        pending=pending, saldo_all=saldo_all, stats=stats,
        users_web=web_get_all_users(), produk=load_produk(),
        total_saldo=sum(saldo_all.values()),
        bot_uid_map=bot_uid_map,
        rekap=db_get_rekap_penjualan(),
        daily_sales=db_get_daily_sales(30),
        audit_log=db_get_audit_log(100),
        vouchers=vouchers,
        ref_pending=ref_pending,
        tiket_list=tiket_list,
        cfg=cfg,
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
    db_add_audit_log(session["user_tid"], "KONFIRMASI_DEPOSIT", str(uid), f"Rp{nominal:,} | TRX: {trx_id}")
    _log_group(
        f"💰 *Deposit Dikonfirmasi (Web)*\n"
        f"👤 User: `{uid}`\n"
        f"💵 Rp{nominal:,}\n"
        f"🔖 TRX: `{trx_id}`\n"
        f"👮 Admin: `{session['user_tid']}`"
    )
    flash(f"✅ Rp{nominal:,} dikonfirmasi. TRX: {trx_id}", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reject/<int:uid>", methods=["POST"])
@admin_required
def admin_reject(uid):
    db_remove_pending_any_by_user(uid)
    send_telegram(uid, "❌ Deposit kamu ditolak admin. Hubungi admin untuk info lebih lanjut.")
    db_add_audit_log(session["user_tid"], "TOLAK_DEPOSIT", str(uid), "")
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

    kode_unik = _generate_kode_unik_web(nominal)
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


@app.route("/deposit/cancel", methods=["POST"])
def deposit_cancel():
    """Auto-cancel QRIS deposit saat user meninggalkan halaman (JS sendBeacon)."""
    uid = session.get("dq_uid")
    if uid:
        pending = db_get_pending_any_by_user(uid)
        if pending and pending.get("metode") == "qris_deposit_web":
            db_remove_pending_any_by_user(uid)
    for k in ["dq_uid","dq_total","dq_nominal","dq_kode","dq_start"]:
        session.pop(k, None)
    return "", 204


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
    _log_group(
        f"💰 *Deposit QRIS Otomatis (Web)*\n"
        f"👤 User: `{uid}`\n"
        f"💵 Rp{nominal:,}\n"
        f"🔖 TRX: `{trx_id}`"
    )
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


@app.route("/admin/produk/<pid>/tipe/<tid>/deskripsi", methods=["POST"])
@admin_required
def admin_produk_tipe_deskripsi(pid, tid):
    """Update deskripsi tipe — mendukung teks panjang multi-baris."""
    deskripsi = request.form.get("deskripsi", "").strip()
    with _purchase_lock, produk_lock():
        raw  = load_produk_raw()
        prod = raw.get(pid)
        if not prod or tid not in prod.get("tipe", {}):
            flash("Tipe tidak ditemukan.", "danger")
            return redirect(url_for("admin") + "#tab-produk")
        prod["tipe"][tid]["deskripsi"] = deskripsi
        raw[pid] = prod
        save_produk_raw(raw)
    nama_tipe = raw.get(pid, {}).get("tipe", {}).get(tid, {}).get("nama", tid)
    msg = f"✅ Deskripsi tipe '{nama_tipe}' diperbarui." if deskripsi else f"✅ Deskripsi tipe '{nama_tipe}' dihapus."
    flash(msg, "success")
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
    if not _validate_image_magic(file.stream):
        flash("File tidak valid. Pastikan file adalah gambar asli (JPG/PNG/WEBP).", "danger")
        return redirect(url_for("admin") + "#tab-config")
    dest = os.path.join("static", f"logo.{ext}")
    file.save(dest)
    db_add_audit_log(session["user_tid"], "GANTI_LOGO", "", f"logo.{ext}")
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
    cfg["web_aktif"]             = request.form.get("web_aktif") == "on"
    cfg["maintenance_mode"]      = request.form.get("maintenance_mode") == "on"
    # Stok alert
    cfg["stok_alert_aktif"] = request.form.get("stok_alert_aktif") == "on"
    try:
        cfg["stok_alert_min"] = max(1, int(request.form.get("stok_alert_min","2")))
    except ValueError:
        cfg["stok_alert_min"] = 2
    # Voucher
    cfg["voucher_aktif"] = request.form.get("voucher_aktif") == "on"
    # Referral
    cfg["referral_aktif"] = request.form.get("referral_aktif") == "on"
    try:
        cfg["referral_bonus"] = max(0, int(request.form.get("referral_bonus","5000").replace(".","")))
    except ValueError:
        cfg["referral_bonus"] = 5000
    cfg["referral_konfirmasi"] = "manual" if request.form.get("referral_konfirmasi") == "manual" else "otomatis"
    # Brand color
    brand_color = request.form.get("brand_color", "").strip()
    custom_hex  = request.form.get("custom_hex",  "").strip()
    if brand_color == "custom" and custom_hex.startswith("#"):
        cfg["brand_color"] = "custom"
        cfg["custom_hex"]  = custom_hex
    elif brand_color in BRAND_PRESETS:
        cfg["brand_color"] = brand_color
        cfg.pop("custom_hex", None)
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
        db_add_audit_log(session["user_tid"], "KURANGI_SALDO", str(uid), f"Rp{nominal:,}")
        _log_group(
            f"➖ *Saldo Dikurangi (Web)*\n"
            f"👤 User: `{uid}`\n"
            f"💸 -Rp{nominal:,}\n"
            f"👮 Admin: `{session['user_tid']}`"
        )
        flash(f"✅ Saldo {uid} dikurangi Rp{nominal:,}.", "success")
    else:
        db_add_saldo(uid, nominal)
        trx_id = db_add_riwayat(uid, "DEPOSIT", "Tambah Saldo Manual (Admin)", nominal)
        send_telegram(uid, f"✅ Saldo kamu ditambah *Rp{nominal:,}* oleh admin.\n🔖 TRX: `{trx_id}`")
        db_add_audit_log(session["user_tid"], "TAMBAH_SALDO", str(uid), f"Rp{nominal:,} | TRX: {trx_id}")
        _log_group(
            f"💰 *Saldo Ditambah Manual (Web)*\n"
            f"👤 User: `{uid}`\n"
            f"💵 +Rp{nominal:,}\n"
            f"🔖 TRX: `{trx_id}`\n"
            f"👮 Admin: `{session['user_tid']}`"
        )
        flash(f"✅ Saldo {uid} ditambah Rp{nominal:,}.", "success")
    return redirect(url_for("admin") + "#tab-saldo")


# ─── ADMIN VOUCHER ────────────────────────────────────────────────────────────

@app.route("/admin/voucher/tambah", methods=["POST"])
@admin_required
def admin_voucher_tambah():
    kode     = request.form.get("kode","").strip().upper()
    try:
        nominal  = int(request.form.get("nominal","0").replace(".","").strip())
        max_uses = int(request.form.get("max_uses","1").strip())
    except ValueError:
        flash("Nominal atau max_uses tidak valid.", "danger")
        return redirect(url_for("admin") + "#tab-voucher")
    if not kode or nominal <= 0:
        flash("Kode dan nominal wajib diisi.", "danger")
        return redirect(url_for("admin") + "#tab-voucher")
    ok = db_add_voucher(kode, nominal, max_uses)
    if ok:
        db_add_audit_log(session["user_tid"], "TAMBAH_VOUCHER", kode, f"Rp{nominal:,} x{max_uses}")
        flash(f"✅ Voucher {kode} (Rp{nominal:,}) berhasil ditambahkan.", "success")
    else:
        flash(f"Kode '{kode}' sudah ada.", "danger")
    return redirect(url_for("admin") + "#tab-voucher")


@app.route("/admin/voucher/hapus/<kode>", methods=["POST"])
@admin_required
def admin_voucher_hapus(kode):
    db_delete_voucher(kode)
    db_add_audit_log(session["user_tid"], "HAPUS_VOUCHER", kode)
    flash(f"✅ Voucher {kode} dihapus.", "success")
    return redirect(url_for("admin") + "#tab-voucher")


@app.route("/admin/voucher/toggle/<kode>", methods=["POST"])
@admin_required
def admin_voucher_toggle(kode):
    db_toggle_voucher(kode)
    return redirect(url_for("admin") + "#tab-voucher")


# ─── ADMIN REFERRAL ───────────────────────────────────────────────────────────

@app.route("/admin/referral/approve/<int:log_id>", methods=["POST"])
@admin_required
def admin_referral_approve(log_id):
    row = db_referral_approve(log_id)
    if row:
        db_add_saldo(row["referrer_tid"], row["bonus"])
        db_add_riwayat(row["referrer_tid"], "REFERRAL",
                       f"Bonus referral dikonfirmasi admin (user {row['referred_tid']})", row["bonus"])
        send_telegram(row["referrer_tid"],
            f"✅ Bonus referral *Rp{row['bonus']:,}* telah dikonfirmasi admin dan masuk ke saldo kamu!")
        db_add_audit_log(session["user_tid"], "APPROVE_REFERRAL", str(log_id),
                         f"Rp{row['bonus']:,} → {row['referrer_tid']}")
        flash(f"✅ Referral #{log_id} disetujui.", "success")
    else:
        flash("Referral tidak ditemukan atau sudah diproses.", "danger")
    return redirect(url_for("admin") + "#tab-referral")


@app.route("/admin/referral/reject/<int:log_id>", methods=["POST"])
@admin_required
def admin_referral_reject(log_id):
    db_referral_reject(log_id)
    db_add_audit_log(session["user_tid"], "REJECT_REFERRAL", str(log_id))
    flash(f"Referral #{log_id} ditolak.", "info")
    return redirect(url_for("admin") + "#tab-referral")


# ─── ADMIN TIKET ──────────────────────────────────────────────────────────────

@app.route("/admin/tiket/reply/<int:ticket_id>", methods=["POST"])
@admin_required
def admin_tiket_reply(ticket_id):
    reply = request.form.get("reply","").strip()
    if not reply:
        flash("Balasan tidak boleh kosong.", "danger")
        return redirect(url_for("admin") + "#tab-tiket")
    ticket = db_ticket_get(ticket_id)
    if ticket:
        db_ticket_reply(ticket_id, reply)
        send_telegram(ticket["user_tid"],
            f"💬 *Balasan Support Tiket #{ticket_id}*\n\n{reply}\n\n"
            f"_Pesan kamu: {ticket['pesan'][:100]}..._")
        db_add_audit_log(session["user_tid"], "REPLY_TIKET", str(ticket_id))
        flash(f"✅ Balasan tiket #{ticket_id} terkirim.", "success")
    else:
        flash("Tiket tidak ditemukan.", "danger")
    return redirect(url_for("admin") + "#tab-tiket")


@app.route("/admin/tiket/tutup/<int:ticket_id>", methods=["POST"])
@admin_required
def admin_tiket_tutup(ticket_id):
    db_ticket_close(ticket_id)
    db_add_audit_log(session["user_tid"], "TUTUP_TIKET", str(ticket_id))
    flash(f"Tiket #{ticket_id} ditutup.", "info")
    return redirect(url_for("admin") + "#tab-tiket")


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


@app.route("/admin/notifications")
@admin_required
def admin_notifications():
    """Polling endpoint — jumlah pending deposit manual real-time."""
    pending = [p for p in db_get_all_pending() if not p.get("metode","").startswith("qris")]
    return jsonify({"pending_count": len(pending)})


@app.route("/admin/export/rekap.csv")
@admin_required
def admin_export_csv():
    """Export rekap penjualan + deposit ke CSV."""
    import csv, io
    rekap   = db_get_rekap_penjualan()
    output  = io.StringIO()
    writer  = csv.writer(output)
    # Header
    writer.writerow(["Jenis","Waktu","User ID","Keterangan","Jumlah (Rp)"])
    for r in rekap.get("beli", {}).get("semua", {}).get("rows", []):
        writer.writerow(["BELI", r.get("waktu",""), r.get("user_id",""), r.get("keterangan",""), r.get("jumlah",0)])
    for r in rekap.get("deposit", {}).get("semua", {}).get("rows", []):
        writer.writerow(["DEPOSIT", r.get("waktu",""), r.get("user_id",""), r.get("keterangan",""), r.get("jumlah",0)])
    db_add_audit_log(session["user_tid"], "EXPORT_CSV", "", "rekap.csv")
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"rekap_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
    )


@app.route("/admin/user/<int:tid>/force-pw", methods=["POST"])
@admin_required
def admin_user_force_pw(tid):
    """Paksa user ganti password di login berikutnya."""
    web_set_force_password_change(tid, 1)
    db_add_audit_log(session["user_tid"], "FORCE_PASSWORD_CHANGE", str(tid), "")
    flash(f"✅ User {tid} akan dipaksa ganti password saat login berikutnya.", "success")
    return redirect(url_for("admin") + "#tab-saldo")


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
