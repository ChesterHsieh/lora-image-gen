#!/usr/bin/env bash
# 在 RunPod pod 上執行：建立 Network Volume 的目錄結構，並下載基礎模型。
# 前提：Network Volume 已掛載在 /workspace。
# 用法：bash setup_volume.sh
set -euo pipefail

VOL="${COMFY_VOLUME:-/workspace}"
MODELS="$VOL/models"

echo "==> 使用 Volume 根目錄：$VOL"
if ! mountpoint -q "$VOL" 2>/dev/null; then
  echo "警告：$VOL 似乎不是掛載點，確認 Network Volume 是否掛到這裡再繼續。" >&2
fi

# ComfyUI 標準模型目錄結構
echo "==> 建立目錄結構"
mkdir -p \
  "$MODELS/checkpoints" \
  "$MODELS/vae" \
  "$MODELS/loras" \
  "$MODELS/controlnet" \
  "$MODELS/clip_vision" \
  "$MODELS/upscale_models" \
  "$VOL/outputs" \
  "$VOL/datasets" \
  "$VOL/training"

# 下載工具：優先用 hf（huggingface_hub），沒有就退回 wget
download() {  # download <url> <dest>
  local url="$1" dest="$2"
  if [ -f "$dest" ]; then
    echo "    已存在，跳過：$dest"
    return
  fi
  echo "    下載：$dest"
  wget -q --show-progress -O "$dest" "$url"
}

echo "==> 下載基礎模型（Dreamshaper XL v2 Turbo + SDXL VAE fp16-fix）"
# Dreamshaper XL v2 Turbo (DPM++ SDE) — Civitai / HuggingFace 鏡像
# 注意：Civitai 連結可能需要 token；以下用 HuggingFace 公開鏡像為主，必要時改成你自己的來源。
download \
  "https://huggingface.co/Lykon/dreamshaper-xl-v2-turbo/resolve/main/DreamShaperXL_Turbo_v2_1.safetensors" \
  "$MODELS/checkpoints/dreamshaper_xl_v2_turbo.safetensors"

download \
  "https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors" \
  "$MODELS/vae/sdxl-vae-fp16-fix.safetensors"

echo "==> 完成。目前模型清單："
find "$MODELS" -type f \( -name '*.safetensors' -o -name '*.ckpt' \) -exec ls -lh {} \; | awk '{print $5, $NF}'
