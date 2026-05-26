"""資料集驗證與上傳測試（task 8.2）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from launcher.uploader import (
    DatasetError,
    DatasetUploader,
    validate_dataset,
)


def _make_pair(d: Path, name: str, ext: str = ".png") -> None:
    (d / f"{name}{ext}").write_bytes(b"fake-image")
    (d / f"{name}.txt").write_text("stcklnd, an apple", encoding="utf-8")


@pytest.mark.unit
def test_validate_missing_source(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="不存在"):
        validate_dataset(tmp_path / "nope")


@pytest.mark.unit
def test_validate_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="不含任何成對"):
        validate_dataset(tmp_path)


@pytest.mark.unit
def test_validate_image_without_caption_is_unpaired(tmp_path: Path) -> None:
    (tmp_path / "lonely.png").write_bytes(b"x")  # 無同名 .txt
    with pytest.raises(DatasetError):
        validate_dataset(tmp_path)


@pytest.mark.unit
def test_validate_counts_only_pairs(tmp_path: Path) -> None:
    _make_pair(tmp_path, "apple")
    _make_pair(tmp_path, "tree", ext=".jpg")
    (tmp_path / "orphan.png").write_bytes(b"x")  # 無 caption，不算
    summary = validate_dataset(tmp_path)
    assert summary.image_count == 2


@pytest.mark.unit
def test_upload_success_returns_count(tmp_path: Path) -> None:
    _make_pair(tmp_path, "apple")
    captured: dict = {}

    def transfer(source: Path, dest: str) -> bool:
        captured["source"] = source
        captured["dest"] = dest
        return True

    uploader = DatasetUploader(transfer=transfer)
    count = uploader.upload(tmp_path, "stcklnd")
    assert count == 1
    assert captured["dest"] == "/workspace/datasets/stcklnd/"


@pytest.mark.unit
def test_upload_transfer_failure_raises(tmp_path: Path) -> None:
    _make_pair(tmp_path, "apple")
    uploader = DatasetUploader(transfer=lambda s, d: False)
    with pytest.raises(DatasetError, match="上傳失敗"):
        uploader.upload(tmp_path, "stcklnd")
