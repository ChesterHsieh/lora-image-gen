# ComfyUI on RunPod — GPU + Model Service

把 ComfyUI 當成**遠端 GPU + 模型服務**跑在 RunPod，本機透過 API 連線。
RunPod 只負責算力與模型，UI / client 在本機。

## 架構

```
┌─────────────────┐         HTTPS (RunPod Proxy)        ┌──────────────────────────┐
│   本機 (client) │ ──────────────────────────────────▶ │   RunPod Pod (GPU)         │
│                 │   POST /prompt  (送 workflow)        │   ComfyUI server :8188     │
│  - 瀏覽器 UI    │ ◀────────────────────────────────── │                            │
│  - client 腳本  │   GET  /history /view (拿結果圖)     │   /workspace ← Network Vol │
│  - ComfyUI 桌面 │                                       │     ├── models/            │
└─────────────────┘                                       │     ├── loras/             │
                                                          │     └── outputs/           │
                                                          └──────────────────────────┘
```

重點：**Network Volume 掛在 `/workspace`，模型只下載一次**。Pod 關掉重開、換 GPU，模型與訓練好的 LoRA 都還在。

## 為什麼是 ComfyUI 不是 InvokeAI

InvokeAI 是單體架構（web server + 推論引擎 + model manager 綁在一起），沒有原生的「本機 UI + 遠端 GPU」拆分模式——要遠端就得整包跑在 RunPod，本機只是開瀏覽器連過去。ComfyUI 的 `/prompt` queue API 是乾淨的 client-server 分離，天生符合「遠端只當 GPU + model service」的需求。

## 目錄

| 路徑 | 用途 |
|------|------|
| `launcher/` | 本機一鍵訓練 launcher：讀 `.env` 建 pod、上傳資料、訓練、同步產出到 Google Drive、回收 pod |
| `scripts/` | Pod 端啟動 / 模型下載 / 訓練 / 同步腳本（在 RunPod 上執行） |
| `client/` | 本機端連遠端 ComfyUI API 的腳本 |
| `workflows/` | ComfyUI workflow（API 格式 JSON），給 client 提交用 |
| `.env.example` | launcher 設定範本（API key、Volume、GPU、rclone、訓練超參數） |
| `DEPLOY.md` | 完整部署步驟（從建 Network Volume 到一鍵訓練） |

## 快速開始

完整步驟見 [DEPLOY.md](./DEPLOY.md)，摘要：

1. RunPod 建一個 **Network Volume**（建議 50–100 GB，放模型 + LoRA + 訓練資料）
2. 用 ComfyUI template 開 pod，掛上該 Network Volume 到 `/workspace`
3. 在 pod 上跑 `scripts/setup_volume.sh` 建立目錄結構 + 下載 Dreamshaper XL / VAE
4. 本機設好 `RUNPOD_COMFY_URL`，用 `client/comfy_client.py` 提交 workflow 產圖

## GPU 規格建議

| 用途 | 建議 GPU | VRAM | 備註 |
|------|----------|------|------|
| SDXL 推論 | RTX 4090 / A5000 | 24 GB | turbo 模型 4–8 步，很快 |
| SDXL LoRA 訓練 | A6000 / A100 | 48 / 80 GB | 24GB 也能訓但 batch 受限 |
| 兩者都要 | A6000 (48GB) | 48 GB | 一張卡兼顧，CP 值佳 |

> 訓練可用 Spot / Community Cloud 省錢；推論服務若要長開用 On-Demand 較穩。
