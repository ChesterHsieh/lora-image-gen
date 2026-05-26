#!/usr/bin/env python3
"""批次裁切：把整張卡牌截圖裁出「中央插圖」，丟掉邊框 / 卡名 / icon。

用固定比例裁切（不靠影像偵測），先用 --preview 校準一次比例，確認後再批次跑。
為什麼要裁：訓練「插圖畫風」時，若把卡框一起餵進去，模型會把米色邊框、圓角、
數字 icon 也當成風格學進去。所以只留中央插圖。

只依賴 Pillow，無其他相依。

校準（先看一張裁出來對不對）：
    python crop_cards.py --in raw_cards --out cropped --preview

調比例後批次裁全部：
    python crop_cards.py --in raw_cards --out cropped \\
        --top 0.18 --bottom 0.62 --left 0.10 --right 0.90
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

# 預設裁切框（相對比例，0~1）。這是常見卡面的「合理起點」：
# 插圖大致在卡的上半中央。實際請用 --preview 校準到你的素材。
DEFAULTS = {"top": 0.16, "bottom": 0.60, "left": 0.08, "right": 0.92}

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def crop_box(w: int, h: int, frac: dict[str, float]) -> tuple[int, int, int, int]:
    """把相對比例換算成像素裁切框 (left, top, right, bottom)。"""
    left = int(w * frac["left"])
    right = int(w * frac["right"])
    top = int(h * frac["top"])
    bottom = int(h * frac["bottom"])
    if right <= left or bottom <= top:
        raise ValueError(f"裁切框無效：left={left} right={right} top={top} bottom={bottom}")
    return left, top, right, bottom


def iter_images(src: Path):
    for p in sorted(src.iterdir()):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            yield p


def main() -> int:
    parser = argparse.ArgumentParser(description="固定比例批次裁切卡牌中央插圖")
    parser.add_argument("--in", dest="src", type=Path, required=True, help="原始卡牌截圖資料夾")
    parser.add_argument("--out", dest="dst", type=Path, required=True, help="裁切輸出資料夾")
    parser.add_argument("--top", type=float, default=DEFAULTS["top"], help="上緣比例 0~1")
    parser.add_argument("--bottom", type=float, default=DEFAULTS["bottom"], help="下緣比例 0~1")
    parser.add_argument("--left", type=float, default=DEFAULTS["left"], help="左緣比例 0~1")
    parser.add_argument("--right", type=float, default=DEFAULTS["right"], help="右緣比例 0~1")
    parser.add_argument("--preview", action="store_true",
                        help="只處理第一張並輸出，供校準比例；不批次跑")
    parser.add_argument("--square", action="store_true",
                        help="裁完再置中補成正方形（白底），方便 SDXL bucketing")
    args = parser.parse_args()

    frac = {"top": args.top, "bottom": args.bottom, "left": args.left, "right": args.right}
    for k, v in frac.items():
        if not 0.0 <= v <= 1.0:
            print(f"錯誤：--{k} 必須在 0~1 之間，得到 {v}", file=__import__("sys").stderr)
            return 2

    if not args.src.is_dir():
        print(f"錯誤：找不到輸入資料夾 {args.src}", file=__import__("sys").stderr)
        return 2

    args.dst.mkdir(parents=True, exist_ok=True)
    images = list(iter_images(args.src))
    if not images:
        print(f"警告：{args.src} 裡沒有可處理的圖片（支援 {sorted(IMG_EXTS)}）")
        return 1

    if args.preview:
        images = images[:1]
        print(f"==> 預覽模式：只裁第一張 {images[0].name}，校準後拿掉 --preview 再跑全部")

    count = 0
    for path in images:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            box = crop_box(im.width, im.height, frac)
            cropped = im.crop(box)

            if args.square:
                side = max(cropped.width, cropped.height)
                canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
                canvas.paste(cropped, ((side - cropped.width) // 2,
                                       (side - cropped.height) // 2), cropped)
                cropped = canvas

            # 統一輸出 PNG（保留透明 / 無壓縮 artifact）
            out_path = args.dst / f"{path.stem}.png"
            cropped.convert("RGBA").save(out_path)
            print(f"    {path.name}  {im.size} -> crop{box} -> {out_path.name} {cropped.size}")
            count += 1

    print(f"==> 完成，裁切 {count} 張到 {args.dst}")
    if args.preview:
        print("    打開看看：插圖有沒有完整、卡框/卡名/數字有沒有切乾淨。")
        print("    沒切好就調 --top/--bottom/--left/--right 再 preview 一次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
