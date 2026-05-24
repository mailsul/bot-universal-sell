#!/bin/bash
# Auto-push ke Gitea setiap kali dijalankan
# Dipanggil otomatis oleh runner.py saat startup

GITEA_TOKEN="${GITEA_TOKEN:-}"
GITEA_REPO="${GITEA_REPO:-}"

if [ -z "$GITEA_TOKEN" ] || [ -z "$GITEA_REPO" ]; then
    echo "[gitea_sync] SKIP: GITEA_TOKEN atau GITEA_REPO belum diset"
    exit 0
fi

# Bentuk URL dengan token
REPO_URL="https://${GITEA_TOKEN}@${GITEA_REPO}"

# Hapus lock file yang tersisa kalau ada
rm -f .git/config.lock .git/index.lock .git/HEAD.lock 2>/dev/null || true

# Setup git identity
git config user.email "replit-bot@auto-sync.local" 2>/dev/null || true
git config user.name "Replit Auto Sync" 2>/dev/null || true

# Set remote
if git remote get-url gitea-auto &>/dev/null; then
    git remote set-url gitea-auto "$REPO_URL"
else
    git remote add gitea-auto "$REPO_URL"
fi

# Untrack folder & file yang seharusnya diabaikan (kalau masih ter-track git)
UNTRACK_LIST=(
    "attached_assets"
    "backup_db"
    "logs"
)
for item in "${UNTRACK_LIST[@]}"; do
    if git ls-files --error-unmatch "$item" &>/dev/null 2>&1 || git ls-files "$item" | grep -q .; then
        echo "[gitea_sync] Untrack: $item"
        git rm -r --cached "$item" 2>/dev/null || true
    fi
done

# Untrack semua file .zip yang ter-track
TRACKED_ZIPS=$(git ls-files "*.zip" 2>/dev/null)
if [ -n "$TRACKED_ZIPS" ]; then
    echo "[gitea_sync] Untrack zip files..."
    git rm --cached *.zip 2>/dev/null || true
fi

# Untrack semua file .db yang ter-track
TRACKED_DBS=$(git ls-files "*.db" 2>/dev/null)
if [ -n "$TRACKED_DBS" ]; then
    echo "[gitea_sync] Untrack db files..."
    git rm --cached *.db 2>/dev/null || true
fi

# Tambah semua perubahan
git add -A

# Cek apakah ada perubahan baru
if git diff --cached --quiet; then
    echo "[gitea_sync] Tidak ada perubahan baru, skip commit."
else
    TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    git commit -m "Auto-sync: ${TIMESTAMP}" && echo "[gitea_sync] Commit: ${TIMESTAMP}"
fi

# Unshallow jika repo adalah shallow clone
if [ -f .git/shallow ]; then
    echo "[gitea_sync] Shallow repo terdeteksi, unshallow dulu..."
    git fetch --unshallow 2>/dev/null || git fetch --depth=2147483647 2>/dev/null || true
fi

# Push ke Gitea
echo "[gitea_sync] Push ke Gitea..."
if git push gitea-auto HEAD:refs/heads/main --force 2>&1; then
    echo "[gitea_sync] Push berhasil!"
else
    echo "[gitea_sync] Push gagal (cek GITEA_TOKEN / GITEA_REPO)"
fi
