#!/bin/bash
# ============================================================
# SETUP FROM GITHUB - Untuk Replit Project Baru (Kosong)
# ============================================================
# Jalankan sekali di Shell Replit setelah set secrets:
#   GH_TOKEN  = GitHub Personal Access Token
#   GH_REPO   = github.com/username/repo.git
#
# Cara pakai:
#   bash setup_from_github.sh
# ============================================================

set -e

GH_TOKEN="${GH_TOKEN:-}"
GH_REPO="${GH_REPO:-}"

echo "================================================"
echo "  Setup dari GitHub ke Replit"
echo "================================================"

# Validasi secrets
if [ -z "$GH_TOKEN" ]; then
    echo "[ERROR] GH_TOKEN belum diset di Secrets/Environment Variables!"
    echo "        Masuk ke tab 'Secrets' di Replit, tambahkan GH_TOKEN"
    exit 1
fi

if [ -z "$GH_REPO" ]; then
    echo "[ERROR] GH_REPO belum diset di Secrets/Environment Variables!"
    echo "        Contoh nilai: github.com/username/nama-repo.git"
    exit 1
fi

REPO_URL="https://${GH_TOKEN}@${GH_REPO}"

echo "[1/4] Inisialisasi git..."
git init 2>/dev/null || true
git config user.email "replit-bot@auto-sync.local"
git config user.name "Replit Auto Sync"

echo "[2/4] Menambahkan remote GitHub..."
if git remote get-url origin &>/dev/null; then
    git remote set-url origin "$REPO_URL"
else
    git remote add origin "$REPO_URL"
fi

echo "[3/4] Mengambil data dari GitHub..."
git fetch origin main --depth=1

echo "[4/4] Menerapkan file dari GitHub..."
git checkout -f origin/main
git reset --hard origin/main

echo ""
echo "================================================"
echo "  SELESAI! Semua file dari GitHub sudah ada."
echo "================================================"
echo ""
echo "Langkah selanjutnya:"
echo "  1. Install dependencies:"
echo "     pip install -r requirements.txt"
echo "  2. Tambahkan semua secrets lainnya di tab Secrets"
echo "  3. Tekan tombol Run"
echo ""
