"""crop_cards 的測試，對應 card-illustration-cropping spec 的各 scenario。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

import crop_cards


@pytest.mark.unit
class TestCropBox:
    """Requirement: 固定比例批次裁切 / 裁切框無效。"""

    def test_translates_fractions_to_pixels(self, default_frac: dict[str, float]) -> None:
        # 500x700 套用預設比例
        box = crop_cards.crop_box(500, 700, default_frac)
        assert box == (40, 112, 460, 420)

    def test_raises_when_right_le_left(self) -> None:
        with pytest.raises(ValueError, match="裁切框無效"):
            crop_cards.crop_box(500, 700, {"top": 0.1, "bottom": 0.6, "left": 0.9, "right": 0.2})

    def test_raises_when_bottom_le_top(self) -> None:
        with pytest.raises(ValueError, match="裁切框無效"):
            crop_cards.crop_box(500, 700, {"top": 0.7, "bottom": 0.2, "left": 0.1, "right": 0.9})


@pytest.mark.integration
class TestBatchCrop:
    """Requirement: 固定比例批次裁切（端到端走 main）。"""

    def _run_via_argv(self, args: list[str]) -> int:
        old = sys.argv
        sys.argv = ["crop_cards.py", *args]
        try:
            return crop_cards.main()
        finally:
            sys.argv = old

    def test_crops_all_images_to_central_illustration(
        self, raw_cards_dir: Path, tmp_path: Path, default_frac: dict[str, float],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = tmp_path / "cropped"
        rc = self._run_via_argv([
            "--in", str(raw_cards_dir), "--out", str(out),
            "--top", str(default_frac["top"]), "--bottom", str(default_frac["bottom"]),
            "--left", str(default_frac["left"]), "--right", str(default_frac["right"]),
        ])
        assert rc == 0
        outputs = sorted(p.name for p in out.glob("*.png"))
        assert outputs == ["apple.png", "villager.png"]
        # 裁出的尺寸 = 預設裁切框大小（420x308），且印出原尺寸/裁切框/輸出尺寸
        with Image.open(out / "apple.png") as im:
            assert im.size == (420, 308)
        captured = capsys.readouterr().out
        assert "(500, 700)" in captured and "420, 308" in captured

    def test_square_pads_to_square_canvas(
        self, raw_cards_dir: Path, tmp_path: Path, default_frac: dict[str, float],
    ) -> None:
        out = tmp_path / "cropped_sq"
        rc = self._run_via_argv([
            "--in", str(raw_cards_dir), "--out", str(out),
            "--top", str(default_frac["top"]), "--bottom", str(default_frac["bottom"]),
            "--left", str(default_frac["left"]), "--right", str(default_frac["right"]),
            "--square",
        ])
        assert rc == 0
        with Image.open(out / "apple.png") as im:
            assert im.width == im.height == 420  # 較大邊為 420

    def test_no_square_keeps_aspect_ratio(
        self, raw_cards_dir: Path, tmp_path: Path, default_frac: dict[str, float],
    ) -> None:
        out = tmp_path / "cropped_rect"
        self._run_via_argv([
            "--in", str(raw_cards_dir), "--out", str(out),
            "--top", str(default_frac["top"]), "--bottom", str(default_frac["bottom"]),
            "--left", str(default_frac["left"]), "--right", str(default_frac["right"]),
        ])
        with Image.open(out / "apple.png") as im:
            assert im.size == (420, 308)  # 非正方形，保留長寬比

    def test_preview_processes_only_first_image(
        self, raw_cards_dir: Path, tmp_path: Path, default_frac: dict[str, float],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = tmp_path / "preview"
        rc = self._run_via_argv([
            "--in", str(raw_cards_dir), "--out", str(out), "--preview",
            "--top", str(default_frac["top"]), "--bottom", str(default_frac["bottom"]),
            "--left", str(default_frac["left"]), "--right", str(default_frac["right"]),
        ])
        assert rc == 0
        assert sorted(p.name for p in out.glob("*.png")) == ["apple.png"]  # 只第一張
        assert "預覽模式" in capsys.readouterr().out


@pytest.mark.integration
class TestCropValidation:
    """Requirement: 比例值域 / 輸入驗證與空資料夾處理。"""

    def _run_via_argv(self, args: list[str]) -> int:
        old = sys.argv
        sys.argv = ["crop_cards.py", *args]
        try:
            return crop_cards.main()
        finally:
            sys.argv = old

    def test_ratio_out_of_range_exits_nonzero_no_output(
        self, raw_cards_dir: Path, tmp_path: Path,
    ) -> None:
        out = tmp_path / "bad"
        rc = self._run_via_argv([
            "--in", str(raw_cards_dir), "--out", str(out), "--top", "1.5",
        ])
        assert rc == 2
        assert not list(out.glob("*.png")) if out.exists() else True

    def test_missing_source_dir_exits_nonzero(self, tmp_path: Path) -> None:
        rc = self._run_via_argv([
            "--in", str(tmp_path / "nope"), "--out", str(tmp_path / "o"),
        ])
        assert rc == 2

    def test_empty_source_warns_and_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = self._run_via_argv(["--in", str(empty), "--out", str(tmp_path / "o")])
        assert rc == 1
        assert "沒有可處理的圖片" in capsys.readouterr().out
