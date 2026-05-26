"""make_captions 的測試，對應 style-caption-generation spec 的各 scenario。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import make_captions


def _write_csv(path: Path, rows: str) -> Path:
    path.write_text(rows, encoding="utf-8")
    return path


def _run_via_argv(args: list[str]) -> int:
    old = sys.argv
    sys.argv = ["make_captions.py", *args]
    try:
        return make_captions.main()
    finally:
        sys.argv = old


@pytest.mark.unit
class TestStripStyleWords:
    """Requirement: 自動剝除畫風描述詞。"""

    def test_strips_style_words_case_insensitive(self) -> None:
        cleaned, removed = make_captions.strip_style_words("a Cute red apple Flat illustration")
        assert cleaned == "a red apple"
        assert {w.lower() for w in removed} == {"cute", "flat", "illustration"}

    def test_keeps_object_words(self) -> None:
        cleaned, removed = make_captions.strip_style_words("a wooden house")
        assert cleaned == "a wooden house"
        assert removed == []

    def test_whole_word_match_only(self) -> None:
        # "scary" 含 "car" 但不該被當成 card 之類整詞剝除
        cleaned, _ = make_captions.strip_style_words("a scary monster")
        assert cleaned == "a scary monster"


@pytest.mark.unit
class TestLoadDescriptions:
    """Requirement: 依對照表產生 caption（CSV 解析）。"""

    def test_missing_header_raises(self, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path / "bad.csv", "name,desc\napple,a red apple\n")
        with pytest.raises(ValueError, match="filename,description"):
            make_captions.load_descriptions(csv_path)

    def test_key_strips_extension(self, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path / "c.csv",
                              "filename,description\napple.png,a red apple\n")
        mapping = make_captions.load_descriptions(csv_path)
        assert mapping == {"apple": "a red apple"}

    def test_blank_filename_row_skipped(self, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path / "c.csv",
                              "filename,description\n,orphan desc\napple,a red apple\n")
        mapping = make_captions.load_descriptions(csv_path)
        assert mapping == {"apple": "a red apple"}  # 空 filename 列被略過


@pytest.mark.integration
class TestCaptionGeneration:
    """Requirement: 觸發詞前綴 / 缺描述警告 / 乾跑（端到端走 main）。"""

    def test_matched_description_writes_caption(
        self, cropped_dir_with_images: Path, tmp_path: Path,
    ) -> None:
        csv_path = _write_csv(tmp_path / "cards.csv",
                              "filename,description\n"
                              "apple,a cute red apple flat illustration\n"
                              "villager,a simple cartoon villager standing\n")
        rc = _run_via_argv(["--images", str(cropped_dir_with_images),
                            "--csv", str(csv_path), "--trigger", "stcklnd"])
        assert rc == 0
        # 風格詞被剝除，只留觸發詞 + 物件描述
        assert (cropped_dir_with_images / "apple.txt").read_text(encoding="utf-8").strip() \
            == "stcklnd, a red apple"
        assert (cropped_dir_with_images / "villager.txt").read_text(encoding="utf-8").strip() \
            == "stcklnd, a villager standing"

    def test_custom_trigger_word(
        self, cropped_dir_with_images: Path, tmp_path: Path,
    ) -> None:
        csv_path = _write_csv(tmp_path / "cards.csv",
                              "filename,description\napple,a red apple\n")
        _run_via_argv(["--images", str(cropped_dir_with_images),
                       "--csv", str(csv_path), "--trigger", "myxyz"])
        caption = (cropped_dir_with_images / "apple.txt").read_text(encoding="utf-8")
        assert caption.startswith("myxyz,")

    def test_missing_description_falls_back_to_trigger_only(
        self, cropped_dir_with_images: Path, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # tree 在圖裡但不在 CSV
        csv_path = _write_csv(tmp_path / "cards.csv",
                              "filename,description\napple,a red apple\n")
        rc = _run_via_argv(["--images", str(cropped_dir_with_images),
                            "--csv", str(csv_path), "--trigger", "stcklnd"])
        assert rc == 0
        tree_caption = (cropped_dir_with_images / "tree.txt").read_text(encoding="utf-8")
        assert tree_caption.strip() == "stcklnd"
        out = capsys.readouterr().out
        assert "找不到描述" in out and "tree" in out

    def test_keep_style_words_flag(
        self, cropped_dir_with_images: Path, tmp_path: Path,
    ) -> None:
        csv_path = _write_csv(tmp_path / "cards.csv",
                              "filename,description\napple,a cute red apple\n")
        _run_via_argv(["--images", str(cropped_dir_with_images), "--csv", str(csv_path),
                       "--trigger", "stcklnd", "--keep-style-words"])
        assert "cute" in (cropped_dir_with_images / "apple.txt").read_text(encoding="utf-8")

    def test_dry_run_writes_nothing(
        self, cropped_dir_with_images: Path, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        csv_path = _write_csv(tmp_path / "cards.csv",
                              "filename,description\napple,a red apple\n")
        rc = _run_via_argv(["--images", str(cropped_dir_with_images),
                            "--csv", str(csv_path), "--dry-run"])
        assert rc == 0
        assert not list(cropped_dir_with_images.glob("*.txt"))  # 沒寫任何 txt
        assert "dry-run" in capsys.readouterr().out

    def test_missing_csv_exits_nonzero(
        self, cropped_dir_with_images: Path, tmp_path: Path,
    ) -> None:
        rc = _run_via_argv(["--images", str(cropped_dir_with_images),
                            "--csv", str(tmp_path / "nope.csv")])
        assert rc == 2

    def test_missing_images_dir_exits_nonzero(self, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path / "cards.csv",
                              "filename,description\napple,a red apple\n")
        rc = _run_via_argv(["--images", str(tmp_path / "nope"), "--csv", str(csv_path)])
        assert rc == 2

    def test_missing_header_via_main_exits_nonzero(
        self, cropped_dir_with_images: Path, tmp_path: Path,
    ) -> None:
        csv_path = _write_csv(tmp_path / "bad.csv", "name,desc\napple,a red apple\n")
        rc = _run_via_argv(["--images", str(cropped_dir_with_images), "--csv", str(csv_path)])
        assert rc == 2

    def test_empty_images_dir_exits_nonzero(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        csv_path = _write_csv(tmp_path / "cards.csv",
                              "filename,description\napple,a red apple\n")
        rc = _run_via_argv(["--images", str(empty), "--csv", str(csv_path)])
        assert rc == 1
