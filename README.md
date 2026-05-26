# lora-image-gen — 一鍵在 RunPod 上訓練畫風 LoRA

本機不需要顯卡。把你的圖丟進去，訓練好的 LoRA 自動存進你的 Google Drive。

整個流程由 Claude Code 的 `/run-lora-image-gen` skill 驅動 —— 找 GPU、體檢、訓練、同步、開 ComfyUI 全都在 Claude Code 裡跑，你只要按 permission 放行，不用自己跳出去敲指令。

---

## 怎麼開始

在這個 repo 裡開 Claude Code，然後跑：

```text
/run-lora-image-gen
```

Claude 會帶你完成整個流程，包含第一次的環境設定。過程中它會問你、或請你去做下面這幾件**只有你本人能做**的事：

| 一次性設定 | 你要做什麼 |
| -------- | -------- |
| **RunPod 帳號** | 註冊並儲值 $1–2（租 GPU 按秒計費，一次約 $0.3–1）。用這個連結：[runpod.io?ref=tcp8hlfo](https://runpod.io?ref=tcp8hlfo)，拿到 **API key** |
| **SSH 公鑰** | 把 `~/.ssh/id_ed25519.pub` 貼進 RunPod Console（沒有就先 `ssh-keygen -t ed25519`） |
| **Google Drive 授權** | 跑一次 `rclone config` 建一個叫 `gdrive` 的 remote（瀏覽器 OAuth，需要你本人點同意） |
| **訓練圖** | 把你的卡牌截圖放進 `dataset_prep/`（你手上才有的素材） |

其餘的事——裝依賴、填 `.env`、找卡、建雲端硬碟、preflight、訓練、同步、開 ComfyUI——skill 都會在 Claude Code 裡幫你跑，你按 permission 放行即可。

跑完，LoRA 出現在你的 **Google Drive `lora-outputs/`** 資料夾。

---

## 省錢小抄

- **GPU 按秒計費**：訓完預設自動關機（`KEEP_POD=false`）。
- **雲端硬碟按容量計費**（約 $0.05–0.07/GB/月）：不用時請 Claude 跑 `volume_admin delete` 刪掉。
- **機密只在本機**：`.env` 已被 `.gitignore` 排除。
- **ComfyUI 不裸奔**：只透過 SSH 隧道連，不要直接開 RunPod proxy 網址（沒密碼）。

## 目錄結構

| 目錄 | 用途 |
| ---- | ---- |
| `runpod/launcher/` | 本機端工具：找卡 / 管硬碟 / 體檢 / 一鍵訓練 |
| `runpod/scripts/` | 雲端跑的腳本：下模型 / 訓練 / 同步 Drive / 開 ComfyUI |
| `runpod/workflows/` | ComfyUI workflow |
| `dataset_prep/` | 訓練圖前處理：裁切 + 自動打標 |
| `.claude/skills/` | `/run-lora-image-gen` skill 定義（流程的事實來源） |

技術細節見 [runpod/README.md](runpod/README.md) 與 [runpod/DEPLOY.md](runpod/DEPLOY.md)；資料前處理見 [dataset_prep/README.md](dataset_prep/README.md)。

## 授權與素材聲明

程式碼為個人學習 / 研究用途。用任何遊戲 / 作品的美術素材訓練前，請先確認其授權；公開分享或商用需另行評估。
