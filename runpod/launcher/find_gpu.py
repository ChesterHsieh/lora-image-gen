"""掃描 RunPod：找出「能建 Network Volume 的區 × 有貨 × 低於預算」的 GPU。

挑訓練卡的兩個硬限制：
1. 只有部分資料中心支援 Network Volume（storageSupport=True）——pod 掛 volume 會被
   鎖在 volume 所在資料中心，所以卡必須在「能建 volume」的區才用得上。
2. 庫存 High/Medium 才搶得穩；Low 常在下單瞬間被搶走（靠 launcher 重試）。

用法（在 lora-image-gen/runpod/ 下）：
    ../.venv/bin/python -m launcher.find_gpu --env .env --max-price 1.0 --min-vram 24
    ../.venv/bin/python -m launcher.find_gpu --env .env --min-stock medium   # 只看 Medium 以上
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .config import load_config

# storageSupport=True 的區會在執行時動態查詢，不寫死。
# 適合 SDXL LoRA 訓練的候選卡（>=20GB；清單外的卡不在掃描範圍）。
# 注意：只列「PyTorch 穩定版支援（sm_90 以下）」的卡。Blackwell（RTX PRO Blackwell /
# RTX 50xx / B200，sm_120）與其他過新架構雖常 High 庫存，但 pod base image 的 PyTorch
# 跑不起來（CUDA error: no kernel image available），故不列入。
TRAINING_GPUS = [
    "NVIDIA RTX 6000 Ada Generation",
    "NVIDIA RTX 5000 Ada Generation",
    "NVIDIA RTX 4000 Ada Generation",
    "NVIDIA A40",
    "NVIDIA RTX A6000",
    "NVIDIA RTX A5000",
    "NVIDIA RTX A4500",
    "NVIDIA L4",
    "NVIDIA L40",
    "NVIDIA L40S",
    "NVIDIA GeForce RTX 4090",
    "NVIDIA GeForce RTX 3090",
    "NVIDIA A100 80GB PCIe",
]

_STOCK_ORDER = {"High": 0, "Medium": 1, "Low": 2}


@dataclass(frozen=True)
class GpuOffer:
    stock: str
    price: float
    data_center_id: str
    vram_gb: int
    gpu_id: str

    def __str__(self) -> str:
        return f"{self.stock:6} | ${self.price:<5} | {self.data_center_id:9} | {self.vram_gb}GB | {self.gpu_id}"


def storage_data_centers(gq) -> list[str]:
    """回傳支援 Network Volume 的資料中心 ID。"""
    resp = gq.run_graphql_query("{ dataCenters { id storageSupport } }")
    return [d["id"] for d in resp["data"]["dataCenters"] if d.get("storageSupport")]


def scan(gq, *, max_price: float, min_vram: int, min_stock: str) -> list[GpuOffer]:
    """掃描，回傳符合條件的 GPU offer，依庫存>價格排序。"""
    allowed_stock = {s for s, o in _STOCK_ORDER.items() if o <= _STOCK_ORDER[min_stock]}
    dcs = storage_data_centers(gq)
    offers: list[GpuOffer] = []
    for dc in dcs:
        for gpu in TRAINING_GPUS:
            q = (
                '{ gpuTypes(input:{id:"%s"}) { memoryInGb securePrice '
                'lowestPrice(input:{gpuCount:1,dataCenterId:"%s"}){stockStatus uninterruptablePrice} } }'
                % (gpu, dc)
            )
            try:
                rows = gq.run_graphql_query(q)["data"]["gpuTypes"]
            except Exception:
                continue
            if not rows:
                continue
            x = rows[0]
            lp = x.get("lowestPrice")
            if not lp or lp.get("stockStatus") not in allowed_stock:
                continue
            price = lp.get("uninterruptablePrice") or x.get("securePrice")
            vram = x.get("memoryInGb") or 0
            if price is None or price > max_price or vram < min_vram:
                continue
            offers.append(GpuOffer(lp["stockStatus"], price, dc, vram, gpu))
    offers.sort(key=lambda o: (_STOCK_ORDER.get(o.stock, 9), o.price))
    return offers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="找能建 volume 的區裡有貨的訓練 GPU")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--max-price", type=float, default=1.0, help="每小時上限（USD）")
    parser.add_argument("--min-vram", type=int, default=24, help="最低 VRAM (GB)")
    parser.add_argument("--min-stock", choices=["high", "medium", "low"], default="low",
                        help="最低庫存等級（high 最嚴）")
    args = parser.parse_args(argv)

    import runpod
    from runpod.api import graphql as gq

    cfg = load_config(args.env)
    runpod.api_key = cfg.runpod_api_key

    offers = scan(gq, max_price=args.max_price, min_vram=args.min_vram,
                  min_stock=args.min_stock.capitalize())
    if not offers:
        print("（沒有符合條件的 GPU；放寬 --max-price / --min-vram / --min-stock 再試）")
        return 1
    print(f"=== 能建 volume + 有貨 + <=${args.max_price}/hr + >={args.min_vram}GB ===")
    for o in offers:
        print("  " + str(o))
    best = offers[0]
    print(f"\n建議：在 {best.data_center_id} 建 volume，GPU={best.gpu_id}（{best.stock} 庫存）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
