"""把本機訓練資料集（圖 + 同名 caption）驗證後送上 pod。

驗證為純函式（可單測）；實際傳輸為可注入的 callable，方便測試與
日後替換傳輸方式（runpodctl send / scp / rsync）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})


class DatasetError(Exception):
    """資料集來源不存在或不含成對的圖與 caption。"""


@dataclass(frozen=True)
class DatasetSummary:
    """資料集驗證結果。"""

    pairs: tuple[Path, ...]  # 有對應 .txt 的圖片路徑

    @property
    def image_count(self) -> int:
        return len(self.pairs)


def validate_dataset(source: Path) -> DatasetSummary:
    """檢查來源存在、且含至少一組「圖 + 同名 .txt」。

    來源不存在或無成對檔案時拋 DatasetError，呼叫端據此中止、不啟動訓練。
    """
    if not source.exists() or not source.is_dir():
        raise DatasetError(f"資料集來源不存在或不是資料夾：{source}")

    paired = sorted(
        p
        for p in source.iterdir()
        if p.suffix.lower() in SUPPORTED_IMAGE_EXTS and p.with_suffix(".txt").exists()
    )
    if not paired:
        raise DatasetError(
            f"資料集 {source} 不含任何成對的圖與同名 .txt caption；"
            "請先完成 crop_cards.py 與 make_captions.py"
        )
    return DatasetSummary(pairs=tuple(paired))


# 傳輸函式：把本機資料夾送到 pod 的目標路徑。回傳是否成功。
TransferFn = Callable[[Path, str], bool]


@dataclass
class DatasetUploader:
    """驗證資料集並上傳到 pod 的 /workspace/datasets/<concept>/。"""

    transfer: TransferFn

    def upload(self, source: Path, concept: str) -> int:
        """驗證後上傳，回傳上傳的圖片數；驗證失敗拋 DatasetError。"""
        summary = validate_dataset(source)
        dest = f"/workspace/datasets/{concept}/"
        if not self.transfer(source, dest):
            raise DatasetError(f"資料集上傳失敗：{source} -> {dest}")
        return summary.image_count
