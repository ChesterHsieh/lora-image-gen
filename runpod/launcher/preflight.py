"""啟動訓練前的就緒檢查：RunPod API key、rclone 設定、SSH key、訓練資料集。

逐項檢查並給「還沒設置就怎麼弄」的指引；全部通過才適合跑 launch。
用法（在 lora-image-gen/runpod/ 下）：
    ../.venv/bin/python -m launcher.preflight --env .env --dataset ../dataset_prep/cropped
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .uploader import DatasetError, validate_dataset

OK = "✅"
BAD = "❌"
WARN = "⚠️"


def _check_config(env_path: Path) -> tuple[bool, object]:
    print(f"\n[1/4] 設定檔 {env_path}")
    try:
        cfg = load_config(env_path)
    except ConfigError as exc:
        print(f"  {BAD} {exc}")
        print(f"     → 從 .env.example 複製成 .env 並填值：cp {env_path.parent}/.env.example {env_path}")
        return False, None
    print(f"  {OK} 必要鍵齊全（DC={cfg.data_center_id}, GPU 候選 {len(cfg.gpu_types)} 款, cloud={cfg.cloud_type}）")
    if cfg.cloud_type != "SECURE":
        print(f"  {WARN} cloud_type={cfg.cloud_type}；掛 Network Volume 需 SECURE，建議設 RUNPOD_CLOUD_TYPE=SECURE")
    return True, cfg


def _check_runpod_api(cfg) -> bool:
    print("\n[2/4] RunPod API key")
    try:
        import runpod
        runpod.api_key = cfg.runpod_api_key
        user = runpod.get_user()  # 任一需授權的呼叫，驗證 key 有效
    except Exception as exc:  # noqa: BLE001
        print(f"  {BAD} API key 無效或無法連線：{str(exc)[:120]}")
        print("     → RunPod Console → Settings → API Keys 產生，填入 .env 的 RUNPOD_API_KEY")
        return False
    print(f"  {OK} API key 有效（user id={user.get('id', '?') if isinstance(user, dict) else '?'}）")
    # 確認 volume 存在且在設定的 DC
    try:
        from .volume_admin import VolumeApi
        vols = VolumeApi(api_key=cfg.runpod_api_key).list()
        match = next((v for v in vols if v.get("id") == cfg.network_volume_id), None)
        if not match:
            print(f"  {BAD} 找不到 Network Volume {cfg.network_volume_id}")
            print("     → 用 volume_admin ensure-single 建一個（見 find_gpu 選好區後）")
            return False
        if match.get("dataCenterId") != cfg.data_center_id:
            print(f"  {BAD} volume 在 {match.get('dataCenterId')} 但設定 DC 是 {cfg.data_center_id}（pod 掛不上）")
            return False
        print(f"  {OK} Network Volume {cfg.network_volume_id}（{match.get('dataCenterId')}, {match.get('size')}GB）")
    except Exception as exc:  # noqa: BLE001
        print(f"  {WARN} 無法列出 volume：{str(exc)[:100]}")
    return True


def _check_rclone(cfg) -> bool:
    print("\n[3/4] rclone → Google Drive 設定")
    rc = cfg.rclone_drive_config
    if not rc or "token" not in rc:
        print(f"  {BAD} rclone 設定缺 token")
        print("     → 本機 rclone config 建 drive remote，取 [gdrive] 區塊轉 base64 填 RCLONE_DRIVE_CONFIG_B64")
        return False
    remote = cfg.gdrive_dest_path.split(":")[0] if ":" in cfg.gdrive_dest_path else ""
    print(f"  {OK} rclone 設定含 token；同步目的地 {cfg.gdrive_dest_path}")
    if remote and f"[{remote}]" not in rc:
        print(f"  {WARN} GDRIVE_DEST_PATH 的 remote 名 '{remote}' 與設定區塊不符，確認一致")
    return True


def _check_dataset(dataset: Path) -> bool:
    print(f"\n[4/4] 訓練資料集 {dataset}")
    try:
        summary = validate_dataset(dataset)
    except DatasetError as exc:
        print(f"  {BAD} {exc}")
        print("     → 圖片放 dataset_prep/，跑 crop_cards.py 裁切、make_captions.py 產同名 .txt caption")
        return False
    print(f"  {OK} {summary.image_count} 組成對的圖 + 同名 .txt caption")
    print("     提醒：圖為小尺寸插圖時，訓練用 bucket（train_lora.sh 已處理）；要更精緻可先 upscale 到 1024")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="訓練前就緒檢查")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--dataset", type=Path, default=Path("../dataset_prep/cropped"))
    args = parser.parse_args(argv)

    print("=== RunPod LoRA 訓練：就緒檢查 ===")
    ok_cfg, cfg = _check_config(args.env)
    results = [ok_cfg]
    if ok_cfg:
        results.append(_check_runpod_api(cfg))
        results.append(_check_rclone(cfg))
    results.append(_check_dataset(args.dataset))

    print("\n=== 結果 ===")
    if all(results):
        print(f"{OK} 全部就緒，可跑：../.venv/bin/python -m launcher.launch --env {args.env} --dataset {args.dataset}")
        return 0
    print(f"{BAD} 有項目未就緒，依上面指引補齊後再跑。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
