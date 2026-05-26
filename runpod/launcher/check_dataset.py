"""訓練資料完備檢查：在「成對檔案存在」之上，再驗數量、落單檔、caption 品質。

設計為純函式（`check()` 回傳結構化結果，可單測 / 可重用），preflight 直接 import
它來呈現結果；也提供 CLI 入口讓 `/check-dataset` skill 單獨跑。

這是比 uploader.validate_dataset() 更細的一層：validate_dataset 只擋「連一組成對
都沒有」的硬錯（launch 上傳前的最後防線）；本模組多抓「圖太少 / 漏打標 / caption
沒觸發詞」這類會讓訓練品質變差、但不致於跑不起來的軟問題。

用法（在 lora-image-gen/runpod/ 下）：
    ../.venv/bin/python -m launcher.check_dataset --dataset ../dataset_prep/cropped
    ../.venv/bin/python -m launcher.check_dataset --dataset ../dataset_prep/cropped --trigger mystyle --min-images 15
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})

# 風格 LoRA 的經驗門檻：低於此張數通常學不穩，出 WARN（非 error，仍可硬跑）。
DEFAULT_MIN_IMAGES = 15
# make_captions.py 的預設觸發詞，兩邊保持一致。
DEFAULT_TRIGGER = "mystyle"


@dataclass(frozen=True)
class DatasetReport:
    """資料集檢查結果。errors 非空 = 不該開訓練；warnings 為品質提醒（可放行）。"""

    pairs: tuple[Path, ...] = ()              # 有對應 .txt 的圖片
    images_without_caption: tuple[Path, ...] = ()
    captions_without_image: tuple[Path, ...] = ()
    empty_captions: tuple[Path, ...] = ()     # caption 檔內容為空
    missing_trigger: tuple[Path, ...] = ()    # caption 開頭沒觸發詞
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pair_count(self) -> int:
        return len(self.pairs)

    @property
    def ok(self) -> bool:
        return not self.errors


def _captions_in(source: Path) -> set[str]:
    return {p.stem for p in source.iterdir() if p.suffix.lower() == ".txt"}


def check(
    source: Path,
    *,
    trigger: str = DEFAULT_TRIGGER,
    min_images: int = DEFAULT_MIN_IMAGES,
) -> DatasetReport:
    """檢查資料集完備性，回傳 DatasetReport（不拋例外，問題收進 errors/warnings）。"""
    if not source.exists() or not source.is_dir():
        return DatasetReport(errors=[f"資料集來源不存在或不是資料夾：{source}"])

    images = sorted(p for p in source.iterdir()
                    if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTS)
    caption_stems = _captions_in(source)
    image_stems = {p.stem for p in images}

    pairs = tuple(p for p in images if p.stem in caption_stems)
    images_without_caption = tuple(p for p in images if p.stem not in caption_stems)
    captions_without_image = tuple(
        source / f"{stem}.txt" for stem in sorted(caption_stems - image_stems)
    )

    empty_captions: list[Path] = []
    missing_trigger: list[Path] = []
    trigger_lower = trigger.strip().lower()
    for img in pairs:
        txt = img.with_suffix(".txt")
        body = txt.read_text(encoding="utf-8").strip()
        if not body:
            empty_captions.append(txt)
        elif trigger_lower and not body.lower().lstrip().startswith(trigger_lower):
            missing_trigger.append(txt)

    errors: list[str] = []
    warnings: list[str] = []

    if not pairs:
        errors.append(
            f"資料集 {source} 不含任何成對的圖與同名 .txt caption；"
            "請先完成 crop_cards.py 與 make_captions.py"
        )
    if empty_captions:
        errors.append(f"{len(empty_captions)} 個 caption 是空的（會讓那幾張只剩觸發詞或全空）")

    if pairs and len(pairs) < min_images:
        warnings.append(
            f"成對圖只有 {len(pairs)} 張，少於建議的 {min_images} 張；"
            "風格 LoRA 圖太少容易學不穩，建議多備一些"
        )
    if images_without_caption:
        warnings.append(
            f"{len(images_without_caption)} 張圖沒有同名 .txt（會被訓練忽略）：跑 make_captions.py 補打標"
        )
    if captions_without_image:
        warnings.append(f"{len(captions_without_image)} 個 .txt 找不到對應的圖（落單，可刪）")
    if missing_trigger:
        warnings.append(
            f"{len(missing_trigger)} 個 caption 開頭不是觸發詞 '{trigger}'；"
            "觸發詞不在開頭會稀釋風格綁定"
        )

    return DatasetReport(
        pairs=pairs,
        images_without_caption=images_without_caption,
        captions_without_image=captions_without_image,
        empty_captions=tuple(empty_captions),
        missing_trigger=tuple(missing_trigger),
        errors=errors,
        warnings=warnings,
    )


def print_report(report: DatasetReport) -> None:
    """把 DatasetReport 以 ✅/❌/⚠️ 印出（preflight 與 CLI 共用）。"""
    OK, BAD, WARN = "✅", "❌", "⚠️"
    if report.pair_count:
        print(f"  {OK} {report.pair_count} 組成對的圖 + 同名 .txt caption")
    for msg in report.errors:
        print(f"  {BAD} {msg}")
    for msg in report.warnings:
        print(f"  {WARN} {msg}")

    def _names(paths: tuple[Path, ...], limit: int = 10) -> str:
        shown = ", ".join(p.name for p in paths[:limit])
        return shown + (" ..." if len(paths) > limit else "")

    if report.images_without_caption:
        print(f"     漏打標的圖：{_names(report.images_without_caption)}")
    if report.captions_without_image:
        print(f"     落單的 .txt：{_names(report.captions_without_image)}")
    if report.missing_trigger:
        print(f"     開頭缺觸發詞：{_names(report.missing_trigger)}")
    if report.empty_captions:
        print(f"     空 caption：{_names(report.empty_captions)}")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：檢查並印出結果，回傳 exit code（0 = 可訓練，1 = 未就緒）。"""
    parser = argparse.ArgumentParser(description="訓練資料完備檢查（數量 / 落單 / caption 品質）")
    parser.add_argument("--dataset", type=Path, default=Path("../dataset_prep/cropped"),
                        help="裁切+打標後的資料夾（圖與同名 .txt）")
    parser.add_argument("--trigger", default=DEFAULT_TRIGGER,
                        help=f"caption 該以哪個觸發詞開頭（預設 {DEFAULT_TRIGGER}）")
    parser.add_argument("--min-images", type=int, default=DEFAULT_MIN_IMAGES,
                        help=f"成對圖低於此數出警告（預設 {DEFAULT_MIN_IMAGES}）")
    args = parser.parse_args(argv)

    print(f"=== 訓練資料完備檢查：{args.dataset} ===")
    report = check(args.dataset, trigger=args.trigger, min_images=args.min_images)
    print_report(report)

    print("\n=== 結果 ===")
    if report.ok and not report.warnings:
        print("✅ 資料集完備，可以訓練。")
        return 0
    if report.ok:
        print("⚠️ 資料集可用，但有上面的品質提醒，建議補齊後再訓練。")
        return 0
    print("❌ 資料集尚未就緒，依上面指引補齊後再跑。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
