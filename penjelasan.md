# 📁 Penjelasan File & Folder — Ibra Store

## 📂 Folder

| Folder | Fungsi |
|--------|--------|
| `__pycache__/` | Cache Python otomatis saat menjalankan kode. Bisa diabaikan, dibuat ulang sendiri. |
| `.agents/` | Konfigurasi internal Replit Agent. Jangan diubah manual. |
| `.git/` | Riwayat version control (Git). Menyimpan semua perubahan kode sejak awal. |
| `.local/` | Data lokal agent Replit (session, skill, log). Jangan diubah manual. |
| `akun_dikirim/` | Backup file `.txt` berisi akun yang sudah dikirim ke pembeli (per transaksi). |
| `attached_assets/` | File gambar/aset yang diunggah saat chat dengan agent. Tidak diakses web server. |
| `backups/` | Backup data penting (produk, saldo, dll) yang dibuat saat startup atau migrasi. |
| `bukti/` | Foto bukti transfer yang diunggah user saat deposit via Transfer Manual. |
| `static/` | File statis yang disajikan web server: CSS, JS, gambar produk, logo toko, QR QRIS. |
| `templates/` | Template HTML untuk semua halaman website (Jinja2). |

---

## 📄 File Utama

| File | Fungsi |
|------|--------|
| `main.py` | **Bot Telegram** — inti bot (~3000 baris). Berisi semua handler: pesan, pembelian, deposit, riwayat, admin, premium emoji. |
| `web.py` | **Website Flask** (~1700 baris). Semua routing web: toko publik, login, dashboard user, admin panel, API deposit. |
| `db.py` | **Database helper** — semua fungsi baca/tulis SQLite. Saldo, riwayat transaksi, user web, pending deposit, statistik. |
| `premium_emoji.py` | Konversi emoji biasa → custom emoji animasi Telegram Premium. Dipakai di pesan bot. |
| `produk_lock.py` | File mutex (kunci) untuk `produk.json` — mencegah 2 user membeli akun yang sama secara bersamaan (race condition). |
| `qris_helper.py` | Generate QR Code QRIS + encode base64 untuk ditampilkan di halaman deposit web. |
| `emojis.txt` | Peta 1387+ emoji ke `custom_emoji_id` Telegram untuk fitur premium emoji. |

---

## 📄 File Template (HTML)

| File | Fungsi |
|------|--------|
| `templates/base.html` | Layout dasar semua halaman: navbar, footer, CSS vars tema warna, dark/light mode toggle. |
| `templates/index.html` | Halaman toko publik — katalog produk + saldo user. |
| `templates/admin.html` | Panel admin — dashboard, rekap penjualan, kelola produk, saldo, konfigurasi toko. |
| `templates/login.html` | Halaman login web. |
| `templates/register.html` | Halaman pendaftaran web (jika tersedia). |
| `templates/dashboard.html` | Dashboard user setelah login — riwayat, saldo, profil. |
| `templates/beli.html` | Halaman pembelian produk via web (flow: pilih tipe → konfirmasi → bayar). |
| `templates/deposit.html` | Halaman deposit saldo via web (Transfer Manual / QRIS). |
| `templates/riwayat.html` | Halaman riwayat transaksi user. |

---

## 📄 File Data

| File | Fungsi |
|------|--------|
| `store.db` | **Database SQLite utama** — tabel: `saldo`, `riwayat`, `pending`, `statistik`, `bot_users`, `web_users`, `web_otp`. |
| `produk.json` | Data semua produk: nama, deskripsi, harga, tipe-tipe, dan stok akun per tipe. |
| `config.json` | Konfigurasi toko: nama, rekening, kontak admin, warna tema, QRIS, toggle fitur. |
| `produk.lock` | File kunci mutex (dibuat otomatis saat ada transaksi aktif, dihapus setelahnya). |
| `pending_deposit.json` | *(Legacy)* — sudah digantikan tabel `pending` di SQLite. Masih dibaca saat startup untuk migrasi. |
| `riwayat.json` / `saldo.json` / `statistik.json` | *(Legacy)* — data lama sebelum migrasi ke SQLite. Dibaca sekali saat startup lalu data dipindahkan ke DB. |

---

## ⚙️ File Konfigurasi Project

| File | Fungsi |
|------|--------|
| `pyproject.toml` | Konfigurasi project Python + dependensi (library yang dipakai). |
| `.replit` | Konfigurasi Replit: workflow, port, bahasa, perintah run. |
| `replit.md` | Catatan preferensi dan overview project untuk agent Replit. |
| `penjelasan.md` | File ini — penjelasan lengkap semua file dan folder. |
| `.gitignore` | Daftar file yang tidak di-track oleh Git (misal: `*.db`, `config.json`, `*.log`). |
| `README.md` | Dokumentasi umum project. |

---

## 🗂️ Struktur `static/`

```
static/
├── produk_img/     # Gambar produk yang diupload admin
├── logo/           # Logo toko aktif (dipakai di navbar + bot /start)
└── qris/           # File QR Code QRIS untuk deposit
```

---

## 🗂️ Struktur `templates/`

```
templates/
├── base.html        # Layout dasar (navbar, footer, CSS vars)
├── index.html       # Halaman toko publik
├── admin.html       # Panel admin (dashboard, produk, saldo, config)
├── login.html       # Login web
├── dashboard.html   # Dashboard user
├── beli.html        # Pembelian via web
├── deposit.html     # Deposit via web
└── riwayat.html     # Riwayat transaksi user
```

---

## 🔄 Alur Kerja Sistem

```
Telegram Bot (main.py)
    ↓ baca/tulis
Database (db.py → store.db)
    ↑ baca/tulis
Website Flask (web.py)
    ↓ render
Templates HTML (templates/)
    ↓ aset
Static Files (static/)
```

- **Bot** dan **Web** berbagi satu database (`store.db`) dan satu `config.json`
- `produk.json` diakses keduanya (dengan mutex `produk_lock.py`)
- `config.json` dibaca setiap request untuk selalu up-to-date
