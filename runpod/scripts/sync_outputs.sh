#!/usr/bin/env bash
# 在 RunPod pod 上執行：用 rclone 把訓練產出同步到 Google Drive。
# 非互動授權——rclone 設定（含 OAuth token）由 launcher 經環境變數 RCLONE_DRIVE_CONFIG 注入。
#
# RCLONE_DRIVE_CONFIG 的內容：本機 `rclone config` 建好 drive remote 後，
# 從 rclone.conf 取出該 remote 的區塊（含 [remote] 標頭那段），整段塞進 .env。
# 取得 token 的步驟見 runpod/DEPLOY.md。
set -euo pipefail

VOL="${COMFY_VOLUME:-/workspace}"
CONCEPT="${TRAIN_CONCEPT:-stcklnd}"
DEST="${GDRIVE_DEST_PATH:?未設定 GDRIVE_DEST_PATH}"

LORA_DIR="$VOL/models/loras"
LOG_DIR="$VOL/training/logs/$CONCEPT"

# rclone 設定來源（擇一）：
#   1) launcher 的 transport.kickoff 已把 rclone.conf 寫到 ~/.config/rclone/rclone.conf
#   2) 或經環境變數 RCLONE_DRIVE_CONFIG 注入（這裡還原成 rclone.conf）
RCLONE_CONF="$HOME/.config/rclone/rclone.conf"
if [ -n "${RCLONE_DRIVE_CONFIG:-}" ]; then
  mkdir -p "$(dirname "$RCLONE_CONF")"
  printf '%s\n' "$RCLONE_DRIVE_CONFIG" > "$RCLONE_CONF"
  chmod 600 "$RCLONE_CONF"
fi
if [ ! -s "$RCLONE_CONF" ]; then
  echo "同步失敗：找不到 rclone 設定（$RCLONE_CONF 不存在，也未注入 RCLONE_DRIVE_CONFIG）" >&2
  exit 1
fi

command -v rclone >/dev/null 2>&1 || { echo "==> 安裝 rclone"; curl -fsSL https://rclone.org/install.sh | bash; }

echo "==> 同步 LoRA 權重 → $DEST/$CONCEPT/loras"
rclone copy "$LORA_DIR" "$DEST/$CONCEPT/loras" --include "$CONCEPT*.safetensors" --progress \
  || { echo "同步失敗：LoRA 權重" >&2; exit 1; }

if [ -d "$LOG_DIR" ]; then
  echo "==> 同步訓練 log → $DEST/$CONCEPT/logs"
  rclone copy "$LOG_DIR" "$DEST/$CONCEPT/logs" --progress \
    || { echo "同步失敗：訓練 log" >&2; exit 1; }
fi

echo "==> 同步完成：$DEST/$CONCEPT"
