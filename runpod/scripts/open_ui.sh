#!/usr/bin/env bash
# 在「已跑完訓練的 pod」上開 ComfyUI，安全地讓你連線試用訓好的 LoRA。
#
# 重點：ComfyUI 綁 127.0.0.1（不對 RunPod proxy 裸奔），你從本機用 SSH 隧道連，
# 只有你連得到。RunPod 的 proxy URL 無認證、等於對全網開放，故不用。
#
# 用法（在本機 lora-image-gen/runpod/ 下）：
#   bash scripts/open_ui.sh 'ssh root@<ip> -p <port>'
# 其中 SSH 指令就是 launcher 啟動時印出的那行（除錯介面）。
set -euo pipefail

SSH_CMD="${1:?用法：open_ui.sh 'ssh root@<ip> -p <port>'（用 launcher 印出的 SSH 指令）}"
# 解析 ssh root@HOST -p PORT
HOST=$(echo "$SSH_CMD" | sed -E 's/.*@([^ ]+).*/\1/')
PORT=$(echo "$SSH_CMD" | sed -E 's/.*-p ([0-9]+).*/\1/')
SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes"

echo "==> 目標 pod：$HOST:$PORT"
echo "==> 在 pod 上安裝 / 啟動 ComfyUI（綁 127.0.0.1，不對外）…"

# 在 pod 上跑：clone ComfyUI（若無）→ 裝相依 → 指向 volume 模型/LoRA → 綁 localhost 啟動
ssh $SSH_OPTS -p "$PORT" "root@$HOST" 'bash -s' <<'REMOTE'
set -e
COMFY=/workspace/ComfyUI
[ -d "$COMFY" ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI "$COMFY"
cd "$COMFY"
python -c "import comfy" >/dev/null 2>&1 || pip install -q -r requirements.txt
cat > "$COMFY/extra_model_paths.yaml" <<YAML
runpod_volume:
    base_path: /workspace/
    checkpoints: models/checkpoints
    vae: models/vae
    loras: models/loras
YAML
# 停掉任何裸奔的舊實例，改綁 127.0.0.1
pkill -f "main.py.*--listen 0.0.0.0" 2>/dev/null || true
pkill -f "main.py.*--listen 127.0.0.1" 2>/dev/null || true
sleep 2
nohup python main.py --listen 127.0.0.1 --port 8188 --output-directory /workspace/outputs \
  > /workspace/training/comfy.log 2>&1 &
for i in $(seq 1 30); do
  curl -s -o /dev/null http://127.0.0.1:8188/ && { echo "ComfyUI 已就緒（127.0.0.1:8188）"; break; }
  sleep 2
done
REMOTE

cat <<GUIDE

==========================================================
 ComfyUI 已在 pod 上啟動（只綁 pod 本機，未對外裸奔）
==========================================================

【步驟 1】另開一個終端機，建 SSH 隧道（這個視窗要保持開著）：

    ssh $SSH_OPTS -p $PORT -N -L 8188:127.0.0.1:8188 root@$HOST

【步驟 2】瀏覽器打開：

    http://localhost:8188

  （只有你本機連得到；RunPod 的 proxy 網址沒開，不會對全網裸奔）

【步驟 3】載入現成 workflow 試用你的 LoRA：
  - 左上 Workflow → Open → 選 stcklnd_lora（已含 LoRA pipeline）
  - 改「Positive Prompt」節點，開頭保留觸發詞，例如：
        stcklnd, a wooden axe, white background
  - 想更像畫風：把 Load LoRA 節點的 strength 調高（0.9 → 1.1~1.3）
  - 一次產多張不同 seed：Empty Latent 的 batch_size 設 4 +
        KSampler 的 control_after_generate 設 randomize
  - 按 Run（Ctrl+Enter）產圖

【用完】回收 pod 省 GPU 費（LoRA 已在 Drive，砍 pod 不影響）：
    ../.venv/bin/python -c "import runpod,sys; \\
      from launcher.config import load_config; from pathlib import Path; \\
      runpod.api_key=load_config(Path('.env')).runpod_api_key; \\
      runpod.terminate_pod('<POD_ID>')"
==========================================================
GUIDE
