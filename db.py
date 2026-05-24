"""
db.py — SQLite helper untuk Ibra Store Bot
Menggantikan: saldo.json, pending_deposit.json, riwayat.json, statistik.json
produk.json tetap dikelola di main.py (struktur terlalu kompleks untuk SQL).
"""

import sqlite3
import json
import os
import threading
import random
import string
import logging
from datetime import datetime

log = logging.getLogger(__name__)

DB_FILE = "store.db"
_lock   = threading.Lock()


# ─── KONEKSI ──────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ─── INIT & MIGRASI ───────────────────────────────────────────────────────────

def init_db():
    """Buat tabel jika belum ada, lalu migrasi dari JSON jika perlu."""
    with _lock:
        conn = _get_conn()
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS saldo (
            user_id TEXT PRIMARY KEY,
            amount  INTEGER NOT NULL DEFAULT 0
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            username        TEXT,
            metode          TEXT NOT NULL DEFAULT 'manual',
            nominal         INTEGER NOT NULL DEFAULT 0,
            expected_amount INTEGER DEFAULT 0,
            kode_unik       INTEGER DEFAULT 0,
            waktu           TEXT NOT NULL,
            cek_count       INTEGER DEFAULT 3,
            produk_id       TEXT,
            jumlah          INTEGER DEFAULT 1,
            reserved_akun   TEXT    DEFAULT '[]',
            bukti_path      TEXT,
            total_transfer  INTEGER DEFAULT 0
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS riwayat (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            tipe       TEXT NOT NULL,
            keterangan TEXT,
            jumlah     INTEGER NOT NULL DEFAULT 0,
            waktu      TEXT NOT NULL,
            trx_id     TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS statistik (
            user_id TEXT PRIMARY KEY,
            jumlah  INTEGER DEFAULT 0,
            nominal INTEGER DEFAULT 0
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS voucher (
            kode      TEXT PRIMARY KEY,
            nominal   INTEGER NOT NULL DEFAULT 0,
            max_uses  INTEGER NOT NULL DEFAULT 1,
            used      INTEGER NOT NULL DEFAULT 0,
            aktif     INTEGER NOT NULL DEFAULT 1,
            dibuat    TEXT NOT NULL DEFAULT ''
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS voucher_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            kode      TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            waktu     TEXT NOT NULL
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS rating (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            trx_id     TEXT NOT NULL,
            produk     TEXT NOT NULL,
            bintang    INTEGER NOT NULL DEFAULT 5,
            waktu      TEXT NOT NULL
        )""")

        # Migrasi kolom baru jika belum ada
        for col_sql in [
            "ALTER TABLE pending ADD COLUMN tipe_id TEXT",
            "ALTER TABLE pending ADD COLUMN qris_msg_id INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass

        conn.commit()
        conn.close()

    _migrate_from_json()


def _migrate_from_json():
    """One-time: salin data dari JSON ke SQLite. Skip kalau sudah ada data."""
    with _lock:
        conn = _get_conn()
        existing = conn.execute("SELECT COUNT(*) FROM saldo").fetchone()[0]
        conn.close()
    if existing > 0:
        return

    log.info("🔄 Migrasi data JSON → SQLite (satu kali)...")

    # saldo.json
    if os.path.exists("saldo.json"):
        try:
            with open("saldo.json") as f:
                data = json.load(f)
            with _lock:
                conn = _get_conn()
                for uid, amt in data.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO saldo (user_id, amount) VALUES (?,?)",
                        (str(uid), int(amt))
                    )
                conn.commit()
                conn.close()
            log.info(f"  ✅ saldo: {len(data)} user")
        except Exception as e:
            log.error(f"  ❌ saldo gagal: {e}")

    # riwayat.json
    if os.path.exists("riwayat.json"):
        try:
            with open("riwayat.json") as f:
                data = json.load(f)
            count = 0
            with _lock:
                conn = _get_conn()
                for uid, entries in data.items():
                    for e in entries:
                        conn.execute(
                            "INSERT INTO riwayat (user_id,tipe,keterangan,jumlah,waktu,trx_id) "
                            "VALUES (?,?,?,?,?,?)",
                            (str(uid), e.get("tipe",""), e.get("keterangan",""),
                             e.get("jumlah",0), e.get("waktu",""), None)
                        )
                        count += 1
                conn.commit()
                conn.close()
            log.info(f"  ✅ riwayat: {count} entri")
        except Exception as e:
            log.error(f"  ❌ riwayat gagal: {e}")

    # statistik.json
    if os.path.exists("statistik.json"):
        try:
            with open("statistik.json") as f:
                data = json.load(f)
            with _lock:
                conn = _get_conn()
                for uid, s in data.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO statistik (user_id,jumlah,nominal) VALUES (?,?,?)",
                        (str(uid), s.get("jumlah",0), s.get("nominal",0))
                    )
                conn.commit()
                conn.close()
            log.info(f"  ✅ statistik: {len(data)} user")
        except Exception as e:
            log.error(f"  ❌ statistik gagal: {e}")

    # pending_deposit.json
    if os.path.exists("pending_deposit.json"):
        try:
            with open("pending_deposit.json") as f:
                data = json.load(f)
            if data:
                with _lock:
                    conn = _get_conn()
                    for p in data:
                        conn.execute("""
                        INSERT INTO pending
                          (user_id,username,metode,nominal,expected_amount,kode_unik,
                           waktu,cek_count,produk_id,jumlah,reserved_akun,bukti_path,total_transfer)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            p.get("user_id"), p.get("username"),
                            p.get("metode","manual"), p.get("nominal",0),
                            p.get("expected_amount",0), p.get("kode_unik",0),
                            p.get("waktu", datetime.now().strftime("%d/%m/%Y %H:%M:%S")),
                            p.get("cek_count",3), p.get("produk_id"), p.get("jumlah",1),
                            json.dumps(p.get("reserved_akun",[])),
                            p.get("bukti_path"), p.get("total_transfer",0)
                        ))
                    conn.commit()
                    conn.close()
                log.info(f"  ✅ pending: {len(data)} entri")
        except Exception as e:
            log.error(f"  ❌ pending gagal: {e}")

    log.info("✅ Migrasi selesai.")


# ─── HELPER ───────────────────────────────────────────────────────────────────

def _generate_trx_id() -> str:
    """Format: TRX-YYYYMMDD-XXXXXX"""
    date  = datetime.now().strftime("%Y%m%d")
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(random.choices(chars, k=6))
    return f"TRX-{date}-{suffix}"


def _row_to_dict(row) -> dict:
    d = dict(row)
    ra = d.get("reserved_akun")
    if ra is not None:
        try:
            d["reserved_akun"] = json.loads(ra)
        except Exception:
            d["reserved_akun"] = []
    return d


# ─── SALDO ────────────────────────────────────────────────────────────────────

def db_get_saldo(uid) -> int:
    uid = str(uid)
    with _lock:
        conn = _get_conn()
        row  = conn.execute("SELECT amount FROM saldo WHERE user_id=?", (uid,)).fetchone()
        conn.close()
    return int(row["amount"]) if row else 0


def db_add_saldo(uid, delta: int) -> int:
    """Tambah (atau kurangi jika negatif) saldo. Return saldo baru."""
    uid = str(uid)
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO saldo (user_id,amount) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET amount=amount+?",
            (uid, max(0, delta), delta)
        )
        conn.commit()
        new = conn.execute("SELECT amount FROM saldo WHERE user_id=?", (uid,)).fetchone()
        conn.close()
    return int(new["amount"]) if new else 0


def db_set_saldo(uid, amount: int) -> int:
    uid = str(uid)
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO saldo (user_id,amount) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET amount=?",
            (uid, amount, amount)
        )
        conn.commit()
        conn.close()
    return amount


def db_get_all_saldo() -> dict:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT user_id, amount FROM saldo").fetchall()
        conn.close()
    return {r["user_id"]: int(r["amount"]) for r in rows}


# ─── PENDING ──────────────────────────────────────────────────────────────────

def db_get_all_pending() -> list:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM pending").fetchall()
        conn.close()
    return [_row_to_dict(r) for r in rows]


def db_get_pending_by_user(uid) -> dict | None:
    """Cari pending QRIS aktif milik user (metode = qris atau qris_beli)."""
    with _lock:
        conn = _get_conn()
        row  = conn.execute(
            "SELECT * FROM pending WHERE user_id=? AND metode LIKE 'qris%'",
            (int(uid),)
        ).fetchone()
        conn.close()
    return _row_to_dict(row) if row else None


def db_get_pending_any_by_user(uid) -> dict | None:
    """Cari pending apapun milik user (termasuk manual)."""
    with _lock:
        conn = _get_conn()
        row  = conn.execute(
            "SELECT * FROM pending WHERE user_id=?",
            (int(uid),)
        ).fetchone()
        conn.close()
    return _row_to_dict(row) if row else None


def db_add_pending(data: dict) -> int:
    """Insert pending entry. Return new row id."""
    with _lock:
        conn  = _get_conn()
        cur   = conn.execute("""
        INSERT INTO pending
          (user_id,username,metode,nominal,expected_amount,kode_unik,
           waktu,cek_count,produk_id,tipe_id,jumlah,reserved_akun,bukti_path,total_transfer)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data.get("user_id"), data.get("username"),
            data.get("metode","manual"), data.get("nominal",0),
            data.get("expected_amount",0), data.get("kode_unik",0),
            data.get("waktu", datetime.now().strftime("%d/%m/%Y %H:%M:%S")),
            data.get("cek_count",3), data.get("produk_id"), data.get("tipe_id"),
            data.get("jumlah",1),
            json.dumps(data.get("reserved_akun",[])),
            data.get("bukti_path"), data.get("total_transfer",0)
        ))
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
    return new_id


def db_update_pending_msg_id(uid, msg_id: int):
    """Simpan message_id pesan QRIS untuk dihapus nanti."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE pending SET qris_msg_id=? WHERE user_id=? AND metode LIKE 'qris%'",
            (msg_id, int(uid))
        )
        conn.commit()
        conn.close()


def db_remove_pending_by_user(uid):
    """Hapus pending QRIS milik user."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "DELETE FROM pending WHERE user_id=? AND metode LIKE 'qris%'",
            (int(uid),)
        )
        conn.commit()
        conn.close()


def db_remove_pending_any_by_user(uid):
    """Hapus semua pending milik user (termasuk manual)."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM pending WHERE user_id=?", (int(uid),))
        conn.commit()
        conn.close()


def db_update_pending_cek_count(uid, count: int):
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE pending SET cek_count=? WHERE user_id=? AND metode LIKE 'qris%'",
            (count, int(uid))
        )
        conn.commit()
        conn.close()


def db_remove_pending_by_id(pid: int):
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM pending WHERE id=?", (pid,))
        conn.commit()
        conn.close()


# ─── RIWAYAT ─────────────────────────────────────────────────────────────────

def db_add_riwayat(uid, tipe: str, keterangan: str, jumlah: int) -> str:
    """Tambah entri riwayat. Return trx_id yang di-generate."""
    uid    = str(uid)
    trx_id = _generate_trx_id()
    waktu  = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO riwayat (user_id,tipe,keterangan,jumlah,waktu,trx_id) "
            "VALUES (?,?,?,?,?,?)",
            (uid, tipe, keterangan, jumlah, waktu, trx_id)
        )
        conn.commit()
        conn.close()
    if tipe == "BELI":
        db_update_statistik(uid, jumlah)
    return trx_id


def db_get_riwayat(uid, limit: int = 10) -> list:
    uid = str(uid)
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM riwayat WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit)
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


# ─── STATISTIK ────────────────────────────────────────────────────────────────

def db_update_statistik(uid, nominal: int):
    uid = str(uid)
    with _lock:
        conn = _get_conn()
        conn.execute("""
        INSERT INTO statistik (user_id,jumlah,nominal) VALUES (?,1,?)
        ON CONFLICT(user_id) DO UPDATE SET jumlah=jumlah+1, nominal=nominal+?
        """, (uid, nominal, nominal))
        conn.commit()
        conn.close()


def db_get_statistik_user(uid) -> dict:
    uid = str(uid)
    with _lock:
        conn = _get_conn()
        row  = conn.execute("SELECT * FROM statistik WHERE user_id=?", (uid,)).fetchone()
        conn.close()
    if row:
        return {"jumlah": row["jumlah"], "nominal": row["nominal"]}
    return {"jumlah": 0, "nominal": 0}


def db_get_all_statistik() -> dict:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM statistik").fetchall()
        conn.close()
    return {r["user_id"]: {"jumlah": r["jumlah"], "nominal": r["nominal"]} for r in rows}


# ─── WEB AUTH ─────────────────────────────────────────────────────────────────

def db_add_bot_user(telegram_id: int, username: str = None):
    """Simpan/update user yang sudah start bot."""
    with _lock:
        conn = _get_conn()
        conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_users (
            telegram_id INTEGER PRIMARY KEY,
            username    TEXT,
            first_seen  TEXT NOT NULL
        )""")
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        conn.execute("""
        INSERT INTO bot_users (telegram_id, username, first_seen)
        VALUES (?,?,?)
        ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username
        """, (int(telegram_id), username, now))
        conn.commit()
        conn.close()


def db_get_all_bot_users() -> list[dict]:
    """Ambil semua user yang sudah start bot."""
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute("SELECT telegram_id, username FROM bot_users").fetchall()
        except Exception:
            rows = []
        conn.close()
    return [dict(r) for r in rows]


def init_web_tables():
    with _lock:
        conn = _get_conn()
        conn.execute("""
        CREATE TABLE IF NOT EXISTS web_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id   INTEGER UNIQUE NOT NULL,
            username      TEXT,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            created_at    TEXT NOT NULL,
            phone         TEXT,
            email         TEXT
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS web_otp (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            otp_code    TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            used        INTEGER DEFAULT 0
        )""")
        # Migrasi: tambah kolom phone/email ke tabel lama jika belum ada
        for col in ("phone", "email"):
            try:
                conn.execute(f"ALTER TABLE web_users ADD COLUMN {col} TEXT")
            except Exception:
                pass
        # Audit log
        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_tid  INTEGER NOT NULL,
            aksi       TEXT NOT NULL,
            target     TEXT    DEFAULT '',
            detail     TEXT    DEFAULT '',
            waktu      TEXT    NOT NULL
        )""")
        # Migrasi: force_password_change flag
        try:
            conn.execute("ALTER TABLE web_users ADD COLUMN force_password_change INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.commit()
        conn.close()


def web_get_user_by_tid(telegram_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row  = conn.execute("SELECT * FROM web_users WHERE telegram_id=?", (int(telegram_id),)).fetchone()
        conn.close()
    return dict(row) if row else None


def web_create_user(telegram_id: int, username, password_hash: str, role: str = "user",
                    phone: str = None, email: str = None) -> int:
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    email_norm = email.lower().strip() if email else None
    with _lock:
        conn = _get_conn()
        cur  = conn.execute(
            "INSERT INTO web_users (telegram_id,username,password_hash,role,created_at,phone,email) "
            "VALUES (?,?,?,?,?,?,?)",
            (int(telegram_id), username, password_hash, role, now, phone, email_norm)
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
    return new_id


def web_get_user_by_email(email: str) -> dict | None:
    email_norm = email.lower().strip() if email else ""
    if not email_norm:
        return None
    with _lock:
        conn = _get_conn()
        row  = conn.execute("SELECT * FROM web_users WHERE LOWER(email)=?", (email_norm,)).fetchone()
        conn.close()
    return dict(row) if row else None


def web_get_user_by_phone(phone: str) -> dict | None:
    if not phone:
        return None
    with _lock:
        conn = _get_conn()
        row  = conn.execute("SELECT * FROM web_users WHERE phone=?", (phone.strip(),)).fetchone()
        conn.close()
    return dict(row) if row else None


def web_get_user_by_identifier(identifier: str) -> dict | None:
    """Cari user by email, phone, atau telegram_id (backward compat)."""
    if not identifier:
        return None
    u = web_get_user_by_email(identifier)
    if u:
        return u
    u = web_get_user_by_phone(identifier)
    if u:
        return u
    try:
        u = web_get_user_by_tid(int(identifier))
    except (ValueError, TypeError):
        pass
    return u


def web_update_profile(telegram_id: int, phone: str = None, email: str = None):
    with _lock:
        conn = _get_conn()
        if phone is not None:
            conn.execute("UPDATE web_users SET phone=? WHERE telegram_id=?", (phone, int(telegram_id)))
        if email is not None:
            conn.execute("UPDATE web_users SET email=? WHERE telegram_id=?",
                         (email.lower().strip(), int(telegram_id)))
        conn.commit()
        conn.close()


def web_update_password(telegram_id: int, password_hash: str):
    with _lock:
        conn = _get_conn()
        conn.execute("UPDATE web_users SET password_hash=? WHERE telegram_id=?",
                     (password_hash, int(telegram_id)))
        conn.commit()
        conn.close()


def web_update_role(telegram_id: int, role: str):
    with _lock:
        conn = _get_conn()
        conn.execute("UPDATE web_users SET role=? WHERE telegram_id=?",
                     (role, int(telegram_id)))
        conn.commit()
        conn.close()


def web_get_all_users() -> list:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM web_users ORDER BY id DESC").fetchall()
        conn.close()
    return [dict(r) for r in rows]


def web_save_otp(telegram_id: int, otp_code: str, expires_at: str):
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM web_otp WHERE telegram_id=?", (int(telegram_id),))
        conn.execute(
            "INSERT INTO web_otp (telegram_id,otp_code,expires_at,used) VALUES (?,?,?,0)",
            (int(telegram_id), otp_code, expires_at)
        )
        conn.commit()
        conn.close()


def db_get_rekap_penjualan() -> dict:
    """Rekap penjualan BELI dan DEPOSIT dari tabel riwayat."""
    now        = datetime.now()
    today_str  = now.strftime("%d/%m/%Y")
    month_str  = f"/{now.strftime('%m/%Y')}"

    def _parse(rows):
        r = [dict(x) for x in rows]
        return {"count": len(r), "total": sum(x["jumlah"] for x in r), "rows": r[:20]}

    with _lock:
        conn = _get_conn()
        beli_today  = conn.execute(
            "SELECT * FROM riwayat WHERE tipe='BELI' AND waktu LIKE ? ORDER BY id DESC",
            (f"{today_str}%",)).fetchall()
        beli_month  = conn.execute(
            "SELECT * FROM riwayat WHERE tipe='BELI' AND waktu LIKE ? ORDER BY id DESC",
            (f"%{month_str}%",)).fetchall()
        beli_all_r  = conn.execute(
            "SELECT * FROM riwayat WHERE tipe='BELI' ORDER BY id DESC LIMIT 500").fetchall()
        beli_agg    = conn.execute(
            "SELECT COUNT(*) as c, COALESCE(SUM(jumlah),0) as s FROM riwayat WHERE tipe='BELI'").fetchone()
        dep_today   = conn.execute(
            "SELECT * FROM riwayat WHERE tipe='DEPOSIT' AND waktu LIKE ? ORDER BY id DESC",
            (f"{today_str}%",)).fetchall()
        dep_month   = conn.execute(
            "SELECT * FROM riwayat WHERE tipe='DEPOSIT' AND waktu LIKE ? ORDER BY id DESC",
            (f"%{month_str}%",)).fetchall()
        dep_agg     = conn.execute(
            "SELECT COUNT(*) as c, COALESCE(SUM(jumlah),0) as s FROM riwayat WHERE tipe='DEPOSIT'").fetchone()
        conn.close()

    _bulan = ["","Januari","Februari","Maret","April","Mei","Juni",
              "Juli","Agustus","September","Oktober","November","Desember"]
    return {
        "beli": {
            "hari_ini":  _parse(beli_today),
            "bulan_ini": _parse(beli_month),
            "semua":     {"count": beli_agg["c"], "total": beli_agg["s"],
                          "rows": [dict(r) for r in beli_all_r]},
        },
        "deposit": {
            "hari_ini":  _parse(dep_today),
            "bulan_ini": _parse(dep_month),
            "semua":     {"count": dep_agg["c"], "total": dep_agg["s"]},
        },
        "tanggal": f"{now.day} {_bulan[now.month]} {now.year}",
        "bulan":   f"{_bulan[now.month]} {now.year}",
    }


def web_verify_otp(telegram_id: int, otp_code: str) -> bool:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn  = _get_conn()
        row   = conn.execute(
            "SELECT * FROM web_otp WHERE telegram_id=? AND otp_code=? AND used=0 AND expires_at > ?",
            (int(telegram_id), otp_code, now)
        ).fetchone()
        if row:
            conn.execute("UPDATE web_otp SET used=1 WHERE id=?", (row["id"],))
            conn.commit()
        conn.close()
    return row is not None


def db_add_audit_log(admin_tid: int, aksi: str, target: str = "", detail: str = "") -> None:
    """Catat aksi admin ke audit_log."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO audit_log (admin_tid, aksi, target, detail, waktu) VALUES (?,?,?,?,?)",
            (int(admin_tid), aksi, target or "", detail or "", now)
        )
        conn.commit()
        conn.close()


def db_get_audit_log(limit: int = 200) -> list:
    """Ambil daftar audit log terbaru."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def db_get_daily_sales(days: int = 30) -> list:
    """Data penjualan BELI harian N hari terakhir — untuk grafik Chart.js."""
    from datetime import timedelta as _td
    result = []
    now = datetime.now()
    with _lock:
        conn = _get_conn()
        for i in range(days - 1, -1, -1):
            dt      = now - _td(days=i)
            day_str = dt.strftime("%d/%m/%Y")
            row     = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(jumlah), 0) AS s "
                "FROM riwayat WHERE tipe='BELI' AND waktu LIKE ?",
                (f"{day_str}%",)
            ).fetchone()
            result.append({
                "tanggal": dt.strftime("%-d %b"),
                "count":   int(row["c"]),
                "total":   int(row["s"]),
            })
        conn.close()
    return result


def web_set_force_password_change(telegram_id: int, flag: int = 1) -> None:
    """Aktifkan / nonaktifkan flag paksa ganti password."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE web_users SET force_password_change=? WHERE telegram_id=?",
            (flag, int(telegram_id))
        )
        conn.commit()
        conn.close()


# ─── VOUCHER ──────────────────────────────────────────────────────────────────

def db_add_voucher(kode: str, nominal: int, max_uses: int = 1) -> bool:
    """Buat voucher baru. Return False jika kode sudah ada."""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO voucher (kode, nominal, max_uses, used, aktif, dibuat) VALUES (?,?,?,0,1,?)",
                (kode.upper(), int(nominal), int(max_uses), datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            )
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()


def db_get_all_vouchers() -> list:
    """Ambil semua voucher (untuk panel admin)."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM voucher ORDER BY rowid DESC").fetchall()
        conn.close()
    return [dict(r) for r in rows]


def db_use_voucher(kode: str, user_id: str) -> str | int:
    """
    Pakai voucher. Return:
      - int (nominal)   → berhasil
      - "invalid"       → kode tidak ada / tidak aktif / sudah habis
      - "used"          → user ini sudah pernah pakai kode ini
    """
    kode = kode.upper().strip()
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM voucher WHERE kode=? AND aktif=1", (kode,)
        ).fetchone()
        if not row:
            conn.close()
            return "invalid"
        if row["used"] >= row["max_uses"]:
            conn.close()
            return "invalid"
        # Cek apakah user sudah pernah pakai
        already = conn.execute(
            "SELECT id FROM voucher_log WHERE kode=? AND user_id=?", (kode, str(user_id))
        ).fetchone()
        if already:
            conn.close()
            return "used"
        # Tandai pakai
        conn.execute("UPDATE voucher SET used=used+1 WHERE kode=?", (kode,))
        conn.execute(
            "INSERT INTO voucher_log (kode, user_id, waktu) VALUES (?,?,?)",
            (kode, str(user_id), datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        )
        conn.commit()
        nominal = int(row["nominal"])
        conn.close()
    return nominal


def db_delete_voucher(kode: str) -> bool:
    """Hapus voucher dari DB."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM voucher WHERE kode=?", (kode.upper(),))
        conn.commit()
        conn.close()
    return True


# ─── RATING ───────────────────────────────────────────────────────────────────

def db_add_rating(user_id: str, trx_id: str, produk: str, bintang: int) -> None:
    """Simpan rating dari user setelah pembelian."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO rating (user_id, trx_id, produk, bintang, waktu) VALUES (?,?,?,?,?)",
            (str(user_id), str(trx_id), produk, int(bintang),
             datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        )
        conn.commit()
        conn.close()


def db_get_ratings(produk: str = None, limit: int = 50) -> list:
    """Ambil rating, bisa filter per produk."""
    with _lock:
        conn = _get_conn()
        if produk:
            rows = conn.execute(
                "SELECT * FROM rating WHERE produk LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{produk}%", limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rating ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.close()
    return [dict(r) for r in rows]
