# 部署指南：ComfyUI on RunPod（GPU + Model Service）

從零到「本機連遠端產圖」與「在 RunPod 上訓練 LoRA」的完整步驟。
架構說明見 [README.md](./README.md)。

---

## 階段 1：建立 Network Volume（持久化模型存放）

Network Volume 是 pod 之間共享、pod 關掉也不會消失的儲存。模型只下載一次。

1. RunPod Console → **Storage** → **Network Volume** → **+ New Network Volume**
2. 選一個 **資料中心（Data Center）**——記住它，**pod 必須開在同一個資料中心**才能掛載
3. 容量建議：
   - 只做推論：**30 GB**（SDXL checkpoint ~7GB + VAE + 幾個 LoRA）
   - 含訓練：**60–100 GB**（再加訓練資料集、訓練快取、產出的多個 LoRA）
4. 命名，例如 `lora-imggen-vol`，建立

> 💡 Network Volume 是按容量計費（約 $0.05–0.07/GB/月），即使沒開 pod 也會收。用不到時可縮容或刪除。

---

## 階段 2：開 Pod（掛上 Volume + ComfyUI template）

1. RunPod Console → **Pods** → **Deploy**
2. **GPU**：依用途選（見 README 的規格表）
   - 推論：RTX 4090 / A5000（24GB）
   - 訓練：A6000（48GB）較穩
3. **Network Volume**：選剛建立的 `lora-imggen-vol`，掛載點填 `/workspace`
   （⚠️ 必須選在 Volume 同一個資料中心，否則清單裡不會出現）
4. **Template**：搜尋 **ComfyUI**（社群有多個現成 template，如 `runpod/comfyui` 或 ashleykza 的版本）
5. 確認 **HTTP Ports** 有開 **8188**（ComfyUI 預設 port）
6. Deploy，等 pod 起來

---

## 階段 3：初始化 Volume + 下載模型（在 Pod 上做一次）

開 pod 的 **Web Terminal** 或用 SSH 連進去，把本資料夾的 scripts 弄上去執行。

把腳本貼上去最快的方式（在 pod terminal）：

```bash
# 在 pod 上，從本機 scp 或直接 git clone 你的 repo；這裡假設已取得 scripts/
cd /workspace
bash setup_volume.sh      # 建目錄結構 + 下載 Dreamshaper XL / VAE 到 Network Volume
```

`setup_volume.sh` 會在 `/workspace/models/{checkpoints,vae,loras,...}` 建好結構並下載基礎模型。
模型寫進 Network Volume，**之後換 pod 都不用重下**。

> 若 Civitai 模型需要 token，把 `setup_volume.sh` 裡的下載 URL 換成你自己的來源，或先在本機下載再 scp 上去（見階段 6）。

---

## 階段 4：啟動 ComfyUI 並取得連線 URL

多數 ComfyUI template 開機就自動跑 ComfyUI 了。若要讓它讀 Network Volume 上的模型，跑：

```bash
cd /workspace
bash start_comfy.sh       # 寫好 extra_model_paths.yaml 指向 Volume，並啟動 server
```

啟動後：
1. RunPod Console → 該 pod → **Connect**
2. 找 **HTTP Service → port 8188**，複製那個 URL，形如
   `https://<pod-id>-8188.proxy.runpod.net`

這個 URL 就是本機 client 要連的位址。

---

## 階段 5：本機連遠端產圖

本機（這個 repo 所在的電腦）：

```bash
cd lora-image-gen/runpod
export RUNPOD_COMFY_URL="https://<pod-id>-8188.proxy.runpod.net"

# 用現有的 Dreamshaper XL Turbo 產圖
python client/comfy_client.py workflows/sdxl_turbo_txt2img.json \
  --out ./out \
  --positive "a cinematic photo of a red fox in fresh snow, soft light" \
  --seed 42
```

圖片會被抓回本機的 `./out`。client 只用 Python 標準函式庫，不用裝額外套件。

> 也可以直接開瀏覽器到那個 proxy URL，用 ComfyUI 的網頁 UI（UI 在遠端 render）。
> 或用 **ComfyUI 桌面版** 設定遠端 server。client 腳本則適合做批次 / 自動化 / 實驗紀錄。

套用訓練好的 LoRA：把 LoRA 放到 `/workspace/models/loras/`，用
`workflows/sdxl_turbo_lora_txt2img.json`，改裡面的 `lora_name` 即可。

---

## 階段 6：把本機現有模型傳上去（可選）

你截圖裡的 Dreamshaper XL / VAE 若已在本機，不想重下，可直接傳上 Network Volume：

```bash
# 需先在 pod 設定 SSH（RunPod Connect 頁有 SSH 指令與 port）
scp -P <ssh-port> /path/to/DreamShaperXL.safetensors \
  root@<pod-ip>:/workspace/models/checkpoints/
```

或用 `runpodctl send` / `receive` 在無 SSH 設定時傳檔。

---

## 階段 7：一鍵訓練 LoRA（launcher 自動化）

訓練不再手動點 Console，改用 `launcher/`：讀一個 `.env` 就建 pod、上傳資料、訓練、
同步產出到 Google Drive、依設定回收 pod（ephemeral）。架構決策見 [README.md](./README.md)。

### 7.1 一次性前置

```bash
# 把官方 runpod SDK 裝進 lora-image-gen 既有的 uv venv
uv pip install -r runpod/requirements.txt --python .venv/bin/python

# 複製設定範本並填值
cp runpod/.env.example runpod/.env
```

`.env` 必填：`RUNPOD_API_KEY`、`RUNPOD_NETWORK_VOLUME_ID`、`RUNPOD_DATA_CENTER_ID`、
`RUNPOD_GPU_TYPE`、`RCLONE_DRIVE_CONFIG`、`GDRIVE_DEST_PATH`。缺任一 launcher 會列出鍵名並中止。

### 7.2 取得 rclone → Google Drive 的設定（路線 A：user OAuth）

pod 是 headless（無瀏覽器），授權要在**本機**做，再把設定（含 token）注入 pod：

```bash
brew install rclone                       # 本機裝 rclone
rclone config                             # 新建一個 remote：
                                          #   name 填 gdrive，storage 選 Google Drive，
                                          #   scope 選 drive.file，auto config 選 y（會開瀏覽器授權）
rclone lsd gdrive:                        # 驗證能連
rclone config file                        # 印出 rclone.conf 路徑
```

打開 `rclone.conf`，把 `[gdrive]` 那整段（含標頭那幾行）原樣貼進 `.env` 的
`RCLONE_DRIVE_CONFIG=`（多行可保留，或用 `\n` 接成單行）。`GDRIVE_DEST_PATH` 設成
`gdrive:lora-outputs` 之類。

> 建議自己申請 Google OAuth client_id（Cloud Console → 啟用 Drive API → 建 Desktop OAuth client，
> consent screen 設 Published），rclone config 時填入，避免共用 app 的 token 約 7 天就過期。
> 細節見 rclone 官方 "Making your own client_id"。

### 7.3 註冊 SSH public key（上傳資料 / 輪詢完成都靠它）

launcher 從本機**直連 SSH** 操作 pod：用 `rsync` 上傳資料集、用 `ssh ... test -f`
輪詢 pod 上的完成標記。（RunPod 的 proxy SSH 不支援 scp/rsync，runpodctl 也沒有可遠端
觸發的傳檔或任意 shell exec，所以走直連 SSH。）建 pod 時已開 22 port + `start_ssh=True`，
你只需把本機 public key 註冊到 RunPod：

```bash
# 沒有 key 就先產一把
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519

# 把 public key 貼到 RunPod Console → Settings → SSH Public Keys
cat ~/.ssh/id_ed25519.pub
```

> 直連 SSH 需要 pod 有對外 IP:port 映射到內部 22。launcher 會從 RunPod 解析該位址；
> 若某 pod 只給 proxy SSH（無公開 22 映射），rsync 會失敗——換個 pod / GPU 重開即可。

### 7.4 啟動訓練

```bash
cd lora-image-gen/runpod
../.venv/bin/python -m launcher.launch --env .env --dataset ../dataset_prep/cropped
# 若 ssh 沒自動選對私鑰，加 --ssh-key ~/.ssh/id_ed25519
```

launcher 會：建 pod → 印出 **ComfyUI proxy URL 與 SSH 連線指令**（除錯用）→ 用 rsync 上傳
`dataset_prep/cropped/` → pod 端跑 `pod_bootstrap.sh`（訓練 → 同步）→ SSH 輪詢完成標記 →
成功則回收 pod、產出已在 Google Drive。

- 資料集上 `/workspace/datasets/<concept>/`，輸出 LoRA 寫 `/workspace/models/loras/<concept>.safetensors`。
- **訓練或同步失敗時 pod 不回收**，產出仍在 Network Volume，可 SSH 進去取或重跑同步。
- 要保留 pod 除錯：`.env` 設 `KEEP_POD=true`（記得自行回收以免持續計費）。
- 訓完的 LoRA 共享同一 Volume，可直接用 ComfyUI（階段 5）載入產圖，肉眼驗證風格。

---

## 成本與生命週期小抄

| 動作 | 說明 |
|------|------|
| 用完關 pod | **Stop** 省 GPU 費用，但 container disk 會清空；模型在 Network Volume 仍在 |
| 換更大/更小 GPU | 關 pod 重開，掛同一個 Volume，模型不用重下 |
| 長期不用 | 刪 pod；Network Volume 要留就留（持續收容量費），不留就刪 |
| 訓練省錢 | 用 Spot / Community Cloud；推論服務要長開用 On-Demand |
