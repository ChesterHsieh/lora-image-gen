"""共用 fixtures：讓 dataset_prep/ 的腳本可被 import，並產合成測試素材。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

# 把 dataset_prep/（tests/ 的上層）放上 sys.path，讓測試能 import 兩支腳本
DATASET_PREP_DIR = Path(__file__).resolve().parent.parent
if str(DATASET_PREP_DIR) not in sys.path:
    sys.path.insert(0, str(DATASET_PREP_DIR))


def _make_fake_card(path: Path, object_color: tuple[int, int, int],
                    name: str, size: tuple[int, int] = (500, 700)) -> None:
    """畫一張假卡：米色框 + 中央插圖白底區 + 中央物件 + 左上 icon + 底部卡名。

    版面對應 crop_cards 的預設裁切框可切出中央物件、排除 icon 與卡名。
    """
    w, h = size
    img = Image.new("RGB", size, (235, 225, 200))  # 米色卡框
    draw = ImageDraw.Draw(img)
    draw.rectangle([int(w * 0.08), int(h * 0.16), int(w * 0.92), int(h * 0.60)],
                   fill=(255, 255, 255))                 # 插圖白底區
    draw.ellipse([int(w * 0.34), int(h * 0.26), int(w * 0.66), int(h * 0.49)],
                 fill=object_color)                       # 中央物件
    draw.rectangle([int(w * 0.06), int(h * 0.04), int(w * 0.18), int(h * 0.13)],
                   fill=(255, 200, 0))                     # 左上角 icon（應被切掉）
    draw.text((int(w * 0.36), int(h * 0.86)), name.upper(), fill=(0, 0, 0))  # 底部卡名
    img.save(path)


@pytest.fixture
def raw_cards_dir(tmp_path: Path) -> Path:
    """產兩張規格一致的假卡到 raw/ 並回傳該目錄。"""
    raw = tmp_path / "raw"
    raw.mkdir()
    _make_fake_card(raw / "apple.png", (200, 40, 40), "apple")
    _make_fake_card(raw / "villager.png", (60, 120, 200), "villager")
    return raw


@pytest.fixture
def cropped_dir_with_images(tmp_path: Path) -> Path:
    """產幾張裁好的方圖（模擬 crop 輸出），供打標測試使用。"""
    cropped = tmp_path / "cropped"
    cropped.mkdir()
    for name, color in [("apple", (200, 40, 40)),
                        ("villager", (60, 120, 200)),
                        ("tree", (0, 160, 0))]:
        Image.new("RGB", (420, 420), color).save(cropped / f"{name}.png")
    return cropped


@pytest.fixture
def default_frac() -> dict[str, float]:
    return {"top": 0.16, "bottom": 0.60, "left": 0.08, "right": 0.92}
