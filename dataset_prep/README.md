# 資料準備：Stacklands 風格 LoRA

把 Stacklands 卡牌截圖變成可訓練的「插圖畫風」資料集。
目標是學**插圖畫風**，不是整張卡，所以要去掉卡框 / 卡名 / icon，只留中央插圖。

## 流程總覽

```
1. 手動蒐集卡牌截圖   →   raw_cards/
        ↓  crop_cards.py（固定比例裁切，去框）
2. 中央插圖           →   cropped/
        ↓  （放大不在這做：之後在 ComfyUI 用 AI upscaler 補到 1024）
3. 整理 cards.csv（卡名→物件描述，從 wiki 抄）
        ↓  make_captions.py（產 caption，自動剝除風格詞、加觸發詞）
4. 每張圖配一個 .txt   →   cropped/ 內，圖與 txt 同名
        ↓
5. 整個 cropped/ 就是訓練資料集
```

> ⚠️ **蒐集這步無法全自動**：Fandom 擋自動抓取，且大量爬圖有版權 / ToS 風險。
> 請手動把卡牌圖存到 `raw_cards/`。版權注意事項見 [../runpod/DEPLOY.md](../runpod/DEPLOY.md)：
> 個人學習 / 研究 OK，公開分享或商用要另外評估（美術屬 Sokpop Collective）。

## 工具

### 1. `crop_cards.py` — 固定比例裁切

先用 `--preview` 校準裁切框（只裁第一張，打開看對不對），確認後拿掉 `--preview` 跑全部。

```bash
# 校準：看第一張裁出來，卡框/卡名/icon 有沒有切乾淨
python crop_cards.py --in raw_cards --out cropped --preview

# 調整四邊比例直到滿意（值是相對比例 0~1）
python crop_cards.py --in raw_cards --out cropped --preview \
    --top 0.16 --bottom 0.60 --left 0.08 --right 0.92

# 校準好，批次裁全部（--square 置中補白成正方形，方便 SDXL 處理）
python crop_cards.py --in raw_cards --out cropped \
    --top 0.16 --bottom 0.60 --left 0.08 --right 0.92 --square
```

前提：素材是**規格一致**的整張卡截圖（同一比例），固定比例才裁得準。
若尺寸雜亂，分批校準不同比例再合併。

### 2. `make_captions.py` — 產 caption

先把卡名與描述整理成 `cards.csv`（表頭 `filename,description`，見 `cards.csv.example`）。
`filename` 對應 `cropped/` 裡的圖檔名（可含或不含副檔名）。

```bash
# dry-run：先看 caption 會長怎樣、哪些風格詞被剝掉、哪些圖缺描述
python make_captions.py --images cropped --csv cards.csv --trigger stcklnd --dry-run

# 確認後實際寫出 .txt
python make_captions.py --images cropped --csv cards.csv --trigger stcklnd
```

**它幫你守住兩條風格 LoRA 鐵律：**
- 每張 caption 開頭加觸發詞（預設 `stcklnd`，用模型不認識的字召喚風格）。
- 自動剝除描述「畫風」的詞（flat / cute / cartoon / crayon / illustration…），
  因為 caption 描述畫風會稀釋風格綁定。要保留就加 `--keep-style-words`。

## 整理 cards.csv 的訣竅

- 描述只寫**「畫的是什麼物件」**，例如 `a red apple`、`a villager standing`。
- **不要寫畫風**（工具會自動剝，但源頭就別寫更乾淨）。
- 從 wiki 的卡片描述抄物件名詞即可，把功能 / 數值敘述刪掉。
- 主題盡量雜（人 / 動物 / 植物 / 物件 / 建築），避免風格跟單一主題綁死。

## 資料夾（皆不入版控，見 ../.gitignore）

| 路徑 | 內容 |
|------|------|
| `raw_cards/` | 你手動蒐集的整張卡牌截圖（自己建） |
| `cropped/` | 裁切後的插圖 + 同名 caption .txt（訓練資料集） |
| `cards.csv` | 卡名→物件描述對照表（自己建，參考 .example） |

## 測試

兩支工具有 pytest 測試套件（`tests/`），涵蓋裁切框換算、square 補白、值域 / 空資料夾錯誤、
caption 產生、畫風詞剝除、缺描述回退、dry-run 等情境。用專用 venv 跑（與其他子專案隔離）：

```bash
# 在 lora-image-gen/ 建一次專用 venv（uv）
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python pytest coverage pillow

# 在 dataset_prep/ 跑測試 + 覆蓋率（設定見 .coveragerc / pytest.ini）
cd dataset_prep
../.venv/bin/python -m coverage run -m pytest tests/ -q
../.venv/bin/python -m coverage report
```

目前 25 passed、覆蓋率 100%（排除 `__main__` 入口守衛）。
