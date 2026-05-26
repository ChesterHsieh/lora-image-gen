#!/usr/bin/env python3
"""產 caption：依「卡名 → 物件描述」對照表，為每張裁好的圖產出 .txt caption。

設計理念（風格 LoRA 的鐵律）：
  - caption 描述「畫的是什麼物件」→ 讓物件變成可被 prompt 控制的變數。
  - caption 不描述「畫風」→ 把「怎麼畫」留給 LoRA 學，這才是風格綁定的來源。
  - 每張 caption 開頭加一個觸發詞（模型不認識的字），用來召喚風格。

來源用一份 CSV（你自己整理）：
    filename,description
    apple.png,a red apple
    villager.png,a villager standing
    house.png,a wooden house

工具會：
  1. 把描述裡常見的「風格形容詞」自動剝除（flat, cute, cartoon, crayon...），
     避免不小心把畫風寫進 caption。
  2. 在前面加觸發詞，輸出與圖同名的 .txt（kohya / ComfyUI 通用格式）。

用法：
    python make_captions.py --images cropped --csv cards.csv --trigger mystyle
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# 描述「畫風」的詞 —— 出現在 caption 裡會稀釋風格綁定，自動剝除。
# 比對時忽略大小寫，整詞比對。
STYLE_WORDS = {
    "flat", "cute", "cartoon", "cartoonish", "crayon", "doodle", "minimalist",
    "minimalistic", "simple", "childish", "childlike", "hand-drawn", "handdrawn",
    "illustration", "illustrated", "drawing", "drawn", "sketch", "stylized",
    "vector", "pixel", "painterly", "colorful", "bright", "cheerful", "whimsical",
}

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def strip_style_words(text: str) -> tuple[str, list[str]]:
    """從描述中剝除風格形容詞，回傳 (清理後文字, 被剝掉的詞)。"""
    removed: list[str] = []

    def repl(match: re.Match) -> str:
        word = match.group(0)
        if word.lower() in STYLE_WORDS:
            removed.append(word)
            return ""
        return word

    cleaned = re.sub(r"[A-Za-z][A-Za-z-]*", repl, text)
    # 收拾多餘空白與標點
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;])", r"\1", cleaned)
    cleaned = re.sub(r"^[,\s]+|[,\s]+$", "", cleaned)
    return cleaned.strip(), removed


def load_descriptions(csv_path: Path) -> dict[str, str]:
    """讀 CSV，回傳 {stem: description}。鍵用不含副檔名的檔名。"""
    mapping: dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "filename" not in reader.fieldnames \
                or "description" not in reader.fieldnames:
            raise ValueError("CSV 需含表頭 filename,description")
        for row in reader:
            fname = (row.get("filename") or "").strip()
            desc = (row.get("description") or "").strip()
            if not fname:
                continue
            mapping[Path(fname).stem] = desc
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="為裁好的圖產 caption .txt")
    parser.add_argument("--images", type=Path, required=True, help="裁好的插圖資料夾")
    parser.add_argument("--csv", type=Path, required=True,
                        help="卡名→描述對照表（表頭 filename,description）")
    parser.add_argument("--trigger", default="mystyle",
                        help="觸發詞，建議用模型不認識的字（預設 mystyle）")
    parser.add_argument("--keep-style-words", action="store_true",
                        help="不剝除風格形容詞（預設會剝，除非你很清楚自己在做什麼）")
    parser.add_argument("--dry-run", action="store_true", help="只印出不寫檔")
    args = parser.parse_args()

    if not args.images.is_dir():
        print(f"錯誤：找不到圖片資料夾 {args.images}", file=sys.stderr)
        return 2
    if not args.csv.is_file():
        print(f"錯誤：找不到 CSV {args.csv}", file=sys.stderr)
        return 2

    try:
        descriptions = load_descriptions(args.csv)
    except ValueError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2

    images = sorted(p for p in args.images.iterdir()
                    if p.is_file() and p.suffix.lower() in IMG_EXTS)
    if not images:
        print(f"警告：{args.images} 裡沒有圖片", file=sys.stderr)
        return 1

    written, missing, all_removed = 0, [], set()
    for img in images:
        desc = descriptions.get(img.stem)
        if desc is None:
            missing.append(img.stem)
            desc = ""  # 沒對到描述就只放觸發詞

        if args.keep_style_words:
            body, removed = desc, []
        else:
            body, removed = strip_style_words(desc)
        all_removed.update(w.lower() for w in removed)

        caption = f"{args.trigger}, {body}".rstrip(", ").strip() if body else args.trigger
        txt_path = img.with_suffix(".txt")

        if args.dry_run:
            print(f"    {img.name}  ->  {caption}")
        else:
            txt_path.write_text(caption + "\n", encoding="utf-8")
            written += 1

    print(f"==> {'(dry-run) ' if args.dry_run else ''}處理 {len(images)} 張圖")
    if not args.dry_run:
        print(f"    寫出 {written} 個 .txt 到 {args.images}")
    if all_removed:
        print(f"    自動剝除的風格詞：{', '.join(sorted(all_removed))}")
        print("    （這些是「畫風」，不該寫進 caption，否則風格綁定會變弱）")
    if missing:
        print(f"    ⚠ 有 {len(missing)} 張在 CSV 找不到描述，只給了觸發詞：")
        print(f"      {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
