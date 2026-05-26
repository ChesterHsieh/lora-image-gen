# lora-image-gen — 一鍵在 RunPod 上訓練畫風 LoRA

在**租來的雲端 GPU（RunPod）**上訓練一個 SDXL LoRA，把訓練好的權重自動同步到你的
**Google Drive**，再用遠端 ComfyUI 試產圖——本機不需要顯卡。

目前範例案例：重現 **Stacklands 卡牌插圖畫風**（觸發詞 `stcklnd`）。換成你自己的畫風，
只要換訓練圖即可。

> 💡 沒有 RunPod 帳號的話，用這個連結註冊：
> **https://runpod.io?ref=tcp8hlfo**

---

## 這個 repo 幫你做什麼（skill 簡介）

整套流程已固化成一個 Claude Code skill：**`/run-lora-image-gen`**
（定義在 [.claude/skills/run-lora-image-gen/SKILL.md](.claude/skills/run-lora-image-gen/SKILL.md)）。
它把「租 GPU → 訓練 → 存檔 → 驗證」拆成四個各自可跑的工具，**每一步都在真實 RunPod 上跑通過**：

| 你想做的事 | 工具 | 一句話 |
| --- | --- | --- |
| **1. 找 GPU 並決定在哪租** | `launcher/find_gpu.py` + `launcher/volume_admin.py` | 掃「能放硬碟的機房 × 有貨 × 你顯卡跑得動 × 低於預算」的 GPU，幫你在選定機房建一顆唯一的雲端硬碟 |
| **2. 檢查還有什麼沒設定好** | `launcher/preflight.py` | 一鍵體檢：API key、Google Drive 設定、訓練圖片格式，每個沒過的都給你修法 |
| **3. 訓練 + 自動存到 Drive** | `launcher/launch.py` | 一行指令：搶卡 → 開機 → 上傳圖 → 訓練 → 把 LoRA 同步進你的 Google Drive → 關機停止計費 |
| **4. 開遠端 UI 試產圖** | `scripts/open_ui.sh` | 在雲端機器開 ComfyUI（安全、只有你連得到），附文字導引，改個 prompt 就能產圖驗證畫風 |

在裝了 Claude Code 的環境裡，直接打 `/run-lora-image-gen`，它會載入完整操作指引。

---

## 無腦上手（從零到第一張圖）

> 整個過程你只需要：一個 RunPod 帳號、一個 Google 帳號、一台能跑 Python 的電腦（不用顯卡）。

### 第 0 步：註冊 + 裝工具

1. 註冊 RunPod：**https://runpod.io?ref=tcp8hlfo**，並儲值一點點（訓練一次約 $0.3~1）。
2. 本機裝 [`uv`](https://github.com/astral-sh/uv)（Python 環境管理）和 [`rclone`](https://rclone.org/)：
   ```bash
   brew install uv rclone     # macOS；其他系統見各自官網
   ```
3. 取得這個 repo 並建環境：
   ```bash
   git clone https://github.com/ChesterHsieh/lora-image-gen.git
   cd lora-image-gen
   uv venv && uv pip install -r runpod/requirements.txt --python .venv/bin/python
   ```

### 第 1 步：拿三把鑰匙，填進 `.env`

```bash
cp runpod/.env.example runpod/.env     # .env 不會進版控（含機密）
```

打開 `runpod/.env` 填：

1. **RunPod API key** → RunPod Console → Settings → API Keys，貼到 `RUNPOD_API_KEY`。
2. **SSH 公鑰** → 把你本機 `~/.ssh/id_ed25519.pub` 內容貼到 RunPod Console → Settings →
   SSH Public Keys（沒有就先 `ssh-keygen -t ed25519`）。launcher 靠它連進機器傳檔。
3. **Google Drive（rclone）** → 本機跑 `rclone config` 建一個叫 `gdrive` 的 Google Drive
   remote（一路選預設、瀏覽器授權），然後把它轉成單行貼進 `.env`：
   ```bash
   cd runpod
   python3 - <<'PY'
   import base64, configparser, io, pathlib
   cp = configparser.ConfigParser(); cp.read(pathlib.Path.home()/".config/rclone/rclone.conf")
   buf = io.StringIO(); buf.write("[gdrive]\n")
   for k,v in cp["gdrive"].items(): buf.write(f"{k} = {v}\n")
   print("RCLONE_DRIVE_CONFIG_B64=" + base64.b64encode(buf.getvalue().encode()).decode())
   PY
   # 把印出來那行貼進 runpod/.env，並設 GDRIVE_DEST_PATH=gdrive:lora-outputs
   ```

> ⚠️ `RUNPOD_CLOUD_TYPE` 維持 `SECURE`（掛雲端硬碟必須）。

### 第 2 步：放你的訓練圖

把圖片放進 `dataset_prep/`，用內附工具裁切、自動產生說明文字（caption）。每張圖配一個同名
`.txt`，開頭是觸發詞。詳見 [dataset_prep/README.md](dataset_prep/README.md)。
（想直接試 Stacklands 範例：照 dataset_prep 的步驟備好 `cropped/` 即可。）

### 第 3 步：選機器、建雲端硬碟

```bash
cd runpod
# 看現在哪裡有合適又便宜的 GPU（能放硬碟的機房 × 有貨 × 跑得動 × <$1/hr）
../.venv/bin/python -m launcher.find_gpu --env .env --max-price 1.0 --min-vram 24 --min-stock low
# 照建議在某機房建一顆 60GB 雲端硬碟（會自動刪掉其他的，保持只有一顆）
../.venv/bin/python -m launcher.volume_admin --env .env ensure-single --dc <機房> --size 60 --name lora-vol
# 把上面印出的 VOLUME_ID、機房、GPU 名稱填回 .env 的對應欄位
```

### 第 4 步：體檢 → 訓練

```bash
# 體檢：API / Drive / 圖片有沒有都備好（會逐項打勾或給你修法）
../.venv/bin/python -m launcher.preflight --env .env --dataset ../dataset_prep/cropped

# 全部就緒就開訓（搶卡→訓練→同步 Drive→關機，全自動）
../.venv/bin/python -u -m launcher.launch --env .env --dataset ../dataset_prep/cropped --create-retries 40
```

跑完，訓練好的 LoRA 會出現在你的 **Google Drive `lora-outputs/` 資料夾**裡。
（GPU 缺貨時 launcher 會自動重試搶；訓練或同步失敗時不會關機，方便你檢查。）

### 第 5 步（可選）：開 ComfyUI 試產圖

launcher 跑完會印出一行 SSH 指令，拿它開遠端 UI：

```bash
bash scripts/open_ui.sh 'ssh root@<ip> -p <port>'      # 用 launcher 印出的那行
```

照螢幕上的文字導引：開 SSH 隧道 → 瀏覽器開 `http://localhost:8188` → 載入 `stcklnd_lora`
workflow → 改 prompt（開頭保留觸發詞）→ 產圖。**用完記得關機停止計費**（指引裡有指令）。

---

## 省錢與安全小抄

- **GPU 按秒計費**：launcher 預設訓完自動關機（`KEEP_POD=false`）。手動開的 pod 記得自己關。
- **雲端硬碟按容量計費**（約 $0.05–0.07/GB/月）：不用時 `volume_admin delete` 刪掉。
  它存著基礎模型，留著的話下次同機房訓練就不用重下。
- **機密只在本機**：`.env`（含 API key、Drive token）已被 `.gitignore` 排除，不會進版控。
- **ComfyUI 不裸奔**：`open_ui.sh` 把 UI 綁在機器本機，只能透過 SSH 隧道連——別直接開
  RunPod 的 proxy 網址，那個沒密碼、等於對全網開放。

## 目錄結構

| 目錄 | 用途 |
| --- | --- |
| `runpod/launcher/` | 本機端 Python 工具：找卡 / 管硬碟 / 體檢 / 一鍵訓練 |
| `runpod/scripts/` | 雲端機器上跑的腳本：下模型 / 訓練 / 同步 Drive / 開 ComfyUI |
| `runpod/workflows/` | ComfyUI workflow（含套好 LoRA 的 `stcklnd_lora_ui.json`） |
| `dataset_prep/` | 訓練圖前處理：裁切 + 自動打標 |
| `.claude/skills/` | `/run-lora-image-gen` skill 定義 |

技術細節與架構決策見 [runpod/README.md](runpod/README.md) 與 [runpod/DEPLOY.md](runpod/DEPLOY.md)。

## 授權與素材聲明

程式碼為個人學習 / 研究用途。Stacklands 美術版權屬 Sokpop Collective；用其素材訓練僅供
個人研究，公開分享或商用需另行評估。
