#!/usr/bin/env bash
# 在 RunPod pod 上執行：把 ComfyUI 指向 Network Volume 的模型目錄並啟動 server。
# 用法：bash start_comfy.sh
# 啟動後，從 RunPod 的 "Connect" 頁面取得 port 8188 的 HTTPS proxy URL，本機用那個 URL 連線。
set -euo pipefail

VOL="${COMFY_VOLUME:-/workspace}"
COMFY_DIR="${COMFY_DIR:-/workspace/ComfyUI}"   # 多數 RunPod ComfyUI template 裝在這
PORT="${COMFY_PORT:-8188}"

if [ ! -d "$COMFY_DIR" ]; then
  echo "找不到 ComfyUI 目錄：$COMFY_DIR" >&2
  echo "請確認用的是 ComfyUI template，或設定 COMFY_DIR 環境變數指到正確路徑。" >&2
  exit 1
fi

# 用 extra_model_paths.yaml 讓 ComfyUI 讀 Network Volume 上的模型，
# 這樣模型與 ComfyUI 程式碼分離，pod 重建也不用搬模型。
cat > "$COMFY_DIR/extra_model_paths.yaml" <<YAML
runpod_volume:
    base_path: $VOL/
    checkpoints: models/checkpoints
    vae: models/vae
    loras: models/loras
    controlnet: models/controlnet
    clip_vision: models/clip_vision
    upscale_models: models/upscale_models
YAML
echo "==> 已寫入 $COMFY_DIR/extra_model_paths.yaml，指向 $VOL"

cd "$COMFY_DIR"
echo "==> 啟動 ComfyUI（listen 0.0.0.0:$PORT，輸出存到 $VOL/outputs）"
# --listen 讓 RunPod proxy 連得到；--output-directory 把產圖存到 Network Volume
exec python main.py \
  --listen 0.0.0.0 \
  --port "$PORT" \
  --output-directory "$VOL/outputs"
