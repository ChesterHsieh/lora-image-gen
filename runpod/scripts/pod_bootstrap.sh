#!/usr/bin/env bash
# pod 開機後由 docker start command 觸發的總流程：
#   1. 背景啟動 ComfyUI（除錯用，port 8188）
#   2. 訓練 LoRA
#   3. 訓練成功才把產出同步到 Google Drive
#   4. 寫總完成 / 失敗標記供本機 launcher 輪詢
#
# 前提：repo 的 runpod/scripts/ 已放到 pod 上（launcher 上傳或 git clone），
# 且各超參數 / rclone 設定已由 pod env 注入。
set -uo pipefail

VOL="${COMFY_VOLUME:-/workspace}"
CONCEPT="${TRAIN_CONCEPT:-stcklnd}"
SCRIPTS_DIR="${SCRIPTS_DIR:-$(cd "$(dirname "$0")" && pwd)}"

RUN_DONE="$VOL/training/$CONCEPT.run.done"       # launcher 輪詢：整體成功
RUN_FAILED="$VOL/training/$CONCEPT.run.failed"   # launcher 輪詢：整體失敗
mkdir -p "$VOL/training"
rm -f "$RUN_DONE" "$RUN_FAILED"

# 新 volume 第一次用：若缺 base 模型，先建目錄結構 + 下載（一次性）。
BASE_MODEL_PATH="$VOL/${TRAIN_BASE_MODEL:-models/checkpoints/dreamshaper_xl_v2_turbo.safetensors}"
if [ ! -f "$BASE_MODEL_PATH" ]; then
  echo "==> 偵測到新 volume 缺 base 模型，先跑 setup_volume.sh 下載（一次性）"
  if ! bash "$SCRIPTS_DIR/setup_volume.sh"; then
    echo "run failed at setup" > "$RUN_FAILED"
    echo "==> 下載 base 模型失敗，停止流程。" >&2
    exit 1
  fi
fi

# 背景開 ComfyUI 當除錯介面；失敗不影響訓練本身。
echo "==> 背景啟動 ComfyUI（除錯用）"
nohup bash "$SCRIPTS_DIR/start_comfy.sh" > "$VOL/training/comfy.log" 2>&1 &

echo "==> 開始訓練"
if ! bash "$SCRIPTS_DIR/train_lora.sh"; then
  echo "run failed at training" > "$RUN_FAILED"
  echo "==> 訓練失敗，停止流程（不同步）。" >&2
  exit 1
fi

echo "==> 同步產出到 Google Drive"
if ! bash "$SCRIPTS_DIR/sync_outputs.sh"; then
  echo "run failed at sync" > "$RUN_FAILED"
  echo "==> 同步失敗。產出仍在 Network Volume，可手動取回。" >&2
  exit 1
fi

echo "ok" > "$RUN_DONE"
echo "==> 全部完成。"
