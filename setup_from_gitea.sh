#!/bin/bash
# ============================================================
# SETUP FROM GITEA - Untuk Replit Project Baru (Kosong)
# ============================================================
# Jalankan sekali di Shell Replit setelah set secrets:
#   GITEA_TOKEN  = Gitea Access Token
#   GITEA_REPO   = gitea-maraazn069.flixprem.org/username/repo.git
#
# Cara pakai:
#   bash setup_from_gitea.sh
# ============================================================

set -e

GITEA_TOKEN="${GITEA_TOKEN:-}"
GITEA_REPO="${GITEA_REPO:-}"

echo "================================================"
echo "  Setup dari Gitea ke Replit"
echo "================================================"

# Validasi secrets
if [ -z "$GITEA_TOKEN" ]; then
    echo "[ERROR] GITEA_TOKEN belum diset di Secrets/Environment Variables!"
    echo "        Masuk ke tab 'Secrets' di Replit, tambahkan GITEA_TOKEN"
    echo "        (Buat di Gitea: Settings → Applications → Access Tokens)"
    exit 1
fi

if [ -z "$GITEA_REPO" ]; then
    echo "[ERROR] GITEA_REPO belum diset di Secrets/Environment Variables!"
    echo "        Contoh nilai: gitea-maraazn069.flixprem.org/username/nama-repo.git"
    exit 1
fi

REPO_URL="https://${GITEA_TOKEN}@${GITEA_REPO}"

echo "[1/4] Inisialisasi git..."
git init 2>/dev/null || true
git config user.email "replit-bot@auto-sync.local"
git config user.name "Replit Auto Sync"

echo "[2/4] Menambahkan remote Gitea..."
if git remote get-url origin &>/dev/null; then
    git remote set-url origin "$REPO_URL"
else
    git remote add origin "$REPO_URL"
fi

echo "[3/4] Mengambil data dari Gitea..."
git fetch origin main --depth=1

echo "[4/4] Menerapkan file dari Gitea..."
git checkout -f origin/main
git reset --hard origin/main

echo ""
echo "================================================"
echo "  SELESAI! Semua file dari Gitea sudah ada."
echo "================================================"
echo ""
echo "Langkah selanjutnya:"
echo "  1. Install dependencies:"
echo "     pip install -r requirements.txt"
echo "  2. Tambahkan semua secrets lainnya di tab Secrets"
echo "  3. Tekan tombol Run"
echo ""
