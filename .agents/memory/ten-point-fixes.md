---
name: 10-poin-fixes
description: Ringkasan 10 poin fix yang telah diimplementasi pada session ini
---

1. Error handler: filter BadRequest "message can't be deleted" di error_handler (tidak kirim pesan error ke user); handle_produk_detail delete di-wrap try/except
2. QRIS cancel: handle_cancel_beli_qris sekarang delete pesan QRIS (query.message.delete) lalu kirim pesan baru; emoji 🔄 di QRIS button pakai emoji_char parameter
3. LOG_GROUP format: semua _notify_group di-redesign dengan separator ━━━ dan label lebih formal (PENJUALAN BARU, DEPOSIT MASUK, dll)
4. Purchase success (bot): text_akun di _proses_beli_saldo dan proses_mutasi qris_beli redesign — tampilkan Metode (Saldo/QRIS)
5. Pin QRIS: proses_mutasi qris_beli sekarang capture return message dan pin_chat_message setelah kirim akun
6. Stok alert ke LOG_GROUP: kedua path (saldo dan QRIS beli) sekarang juga panggil _notify_group untuk stok rendah
7. Voucher count: db_use_voucher dibungkus try/except + logging.error + conn.close() di finally
8. Web purchase message: redesign dengan format sama seperti bot, tampilkan voucher info
9. Web premium emoji: tambah send_telegram_pe() di web.py (pakai build_http_entities dari premium_emoji.py)
10. Rate limit: bot 5x cancel/expired → block 30 menit (_bot_cancel_record/blocked); web guest 3x → block 30 menit (_web_cancel_record/blocked); check di handle_beli_qris + handle_deposit (bot) dan beli_qris route (web)

**Why:** User melaporkan 10 bug/peningkatan yang mengganggu UX dan kejelasan admin log.
**How to apply:** Perubahan tersebar di main.py, web.py, db.py. Rate limit in-memory reset jika bot/web restart.
