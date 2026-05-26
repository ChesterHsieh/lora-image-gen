#!/usr/bin/env bash
# 在 RunPod pod 上執行：用 kohya_ss / sd-scripts 訓練 SDXL LoRA。
# 超參數全由環境變數帶入（launcher 經 pod env 注入），不需改本腳本。
# 前提：Network Volume 掛在 /workspace；資料集已上傳到 /workspace/datasets/<concept>/。
set -euo pipefail

VOL="${COMFY_VOLUME:-/workspace}"
CONCEPT="${TRAIN_CONCEPT:-stcklnd}"

DATASET_DIR="$VOL/datasets/$CONCEPT"
OUTPUT_DIR="$VOL/models/loras"
LOG_DIR="$VOL/training/logs/$CONCEPT"
DONE_MARKER="$VOL/training/$CONCEPT.train.done"     # launcher 輪詢此標記
FAIL_MARKER="$VOL/training/$CONCEPT.train.failed"

BASE_MODEL="$VOL/${TRAIN_BASE_MODEL:-models/checkpoints/dreamshaper_xl_v2_turbo.safetensors}"
RANK="${TRAIN_RANK:-16}"
ALPHA="${TRAIN_ALPHA:-8}"
LR="${TRAIN_LR:-1e-4}"
STEPS="${TRAIN_STEPS:-1500}"
# 訓練解析度。我們的裁切插圖多為 ~339px 小圖（放大到 1024 是之後在 ComfyUI 做的步驟），
# 故用 bucket + 不放大，避免「image size is small」中止。可由 env 覆寫。
RESOLUTION="${TRAIN_RESOLUTION:-512,512}"
MIN_BUCKET="${TRAIN_MIN_BUCKET:-256}"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR" "$VOL/training"
rm -f "$DONE_MARKER" "$FAIL_MARKER"

# 任一步失敗：寫失敗標記後以非零碼結束（launcher 視為訓練失敗）。
fail() {
  echo "訓練失敗：$1" >&2
  echo "$1" > "$FAIL_MARKER"
  exit 1
}

echo "==> 檢查輸入"
[ -d "$DATASET_DIR" ] || fail "找不到資料集目錄：$DATASET_DIR"
[ -f "$BASE_MODEL" ] || fail "找不到 base 模型：$BASE_MODEL（先跑 setup_volume.sh）"

# kohya/sd-scripts 慣例：--train_data_dir 要指「父資料夾」，圖放在名為
# "<重複次數>_<概念>" 的子資料夾裡（重複次數 × 圖數 = 一個 epoch 的步數）。
# 我們的圖直接放在 DATASET_DIR，這裡組出符合慣例的訓練根目錄。
REPEATS="${TRAIN_REPEATS:-10}"
TRAIN_ROOT="$VOL/training/kohya/$CONCEPT"
IMG_SUBDIR="$TRAIN_ROOT/${REPEATS}_${CONCEPT}"
echo "==> 準備 kohya 資料夾結構：$IMG_SUBDIR"
rm -rf "$TRAIN_ROOT"
mkdir -p "$IMG_SUBDIR"
# 用 symlink 把圖與 caption 接進子資料夾（不複製、省空間）
ln -s "$DATASET_DIR"/* "$IMG_SUBDIR"/ 2>/dev/null || true
n_img=$(find "$IMG_SUBDIR" -maxdepth 1 \( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' -o -name '*.webp' \) | wc -l)
[ "$n_img" -gt 0 ] || fail "kohya 子資料夾內沒有圖：$IMG_SUBDIR"
echo "    子資料夾內圖片數：$n_img（重複 $REPEATS 次）"

# sd-scripts 程式碼放 volume（共用、免重 clone）；但 pip 相依裝在 container 的系統
# Python，不在 volume 上——所以每個全新 pod 都要重裝。用「能否 import accelerate」判斷，
# 而非 volume 上的標記檔（標記在 volume 會讓新 pod 誤以為已裝）。
SD_SCRIPTS="${SD_SCRIPTS_DIR:-/workspace/sd-scripts}"
if [ ! -d "$SD_SCRIPTS" ]; then
  echo "==> 取得 sd-scripts"
  git clone --depth 1 https://github.com/kohya-ss/sd-scripts "$SD_SCRIPTS" \
    || fail "clone sd-scripts 失敗"
fi
if ! python -c "import accelerate" >/dev/null 2>&1; then
  echo "==> 安裝 sd-scripts 相依（本 container 尚未裝）"
  # 必須在 sd-scripts 目錄內裝：requirements.txt 末行的 '.' 是裝專案自身，
  # 不 cd 進去會把當前目錄（/root）誤當專案而失敗。
  ( cd "$SD_SCRIPTS" && pip install -q -r requirements.txt ) \
    || fail "安裝 sd-scripts 相依失敗"
fi

echo "==> 開始訓練 SDXL LoRA（concept=$CONCEPT rank=$RANK alpha=$ALPHA lr=$LR steps=$STEPS）"
cd "$SD_SCRIPTS"
# --train_data_dir 指父資料夾 TRAIN_ROOT；caption 採同名 .txt。
python sdxl_train_network.py \
  --pretrained_model_name_or_path "$BASE_MODEL" \
  --train_data_dir "$TRAIN_ROOT" \
  --output_dir "$OUTPUT_DIR" \
  --output_name "$CONCEPT" \
  --resolution "$RESOLUTION" \
  --enable_bucket \
  --min_bucket_reso "$MIN_BUCKET" \
  --max_bucket_reso 1024 \
  --bucket_no_upscale \
  --network_module networks.lora \
  --network_dim "$RANK" \
  --network_alpha "$ALPHA" \
  --learning_rate "$LR" \
  --max_train_steps "$STEPS" \
  --mixed_precision bf16 \
  --save_model_as safetensors \
  --caption_extension .txt \
  --logging_dir "$LOG_DIR" \
  2>&1 | tee "$LOG_DIR/train.log"
# 取 python（pipeline 第一段）的退出碼，而非 tee 的——否則訓練失敗會被誤判成功。
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || fail "sd-scripts 訓練以非零碼結束（exit $rc）"

OUT_FILE="$OUTPUT_DIR/$CONCEPT.safetensors"
[ -f "$OUT_FILE" ] || fail "訓練結束但找不到輸出 LoRA：$OUT_FILE"
echo "==> 訓練完成，輸出：$OUT_FILE"
echo "$OUT_FILE" > "$DONE_MARKER"
