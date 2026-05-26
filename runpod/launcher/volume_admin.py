"""RunPod Network Volume 管理（REST API）。

官方 Python SDK 不支援建立 / 刪除 Network Volume，但 REST API 有
（POST/GET/DELETE https://rest.runpod.io/v1/networkvolumes）。本模組封裝這些
操作，並提供「確保同一時間只有一個 volume」的便利流程。

用法（在 lora-image-gen/runpod/ 下）：
    ../.venv/bin/python -m launcher.volume_admin list
    ../.venv/bin/python -m launcher.volume_admin create --dc EU-RO-1 --size 60 --name lora-vol
    ../.venv/bin/python -m launcher.volume_admin delete --id <vol-id>
    # 在指定 DC 建一個新 volume，並刪除其他所有 volume（確保唯一）：
    ../.venv/bin/python -m launcher.volume_admin ensure-single --dc EU-RO-1 --size 60 --name lora-vol
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import load_config

REST_BASE = "https://rest.runpod.io/v1"


class VolumeError(Exception):
    """Network Volume 操作失敗。"""


@dataclass
class VolumeApi:
    """RunPod Network Volume 的 REST 客戶端（只用標準函式庫）。"""

    api_key: str
    base: str = REST_BASE
    timeout: int = 30

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise VolumeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc

    def list(self) -> list[dict]:
        return self._request("GET", "/networkvolumes") or []

    def create(self, *, name: str, data_center_id: str, size_gb: int) -> dict:
        return self._request(
            "POST", "/networkvolumes",
            {"name": name, "dataCenterId": data_center_id, "size": size_gb},
        )

    def delete(self, volume_id: str) -> None:
        self._request("DELETE", f"/networkvolumes/{volume_id}")


def _fmt(v: dict) -> str:
    return f"{v.get('id')} | dc={v.get('dataCenterId')} | size={v.get('size')}GB | name={v.get('name')}"


def ensure_single(api: VolumeApi, *, name: str, data_center_id: str, size_gb: int,
                  log=print) -> dict:
    """在指定 DC 建一個新 volume，並刪掉其餘所有 volume，確保同時只有一個。

    若指定 DC 已有同名 volume 則沿用、不重建。回傳保留的 volume。
    """
    existing = api.list()
    keep = next(
        (v for v in existing if v.get("dataCenterId") == data_center_id and v.get("name") == name),
        None,
    )
    if keep is None:
        log(f"==> 在 {data_center_id} 建立 volume：{name}（{size_gb}GB）")
        keep = api.create(name=name, data_center_id=data_center_id, size_gb=size_gb)
        log(f"==> 已建立：{_fmt(keep)}")
    else:
        log(f"==> 沿用既有：{_fmt(keep)}")

    for v in existing:
        if v.get("id") != keep.get("id"):
            log(f"==> 刪除其他 volume：{_fmt(v)}")
            api.delete(v["id"])
    return keep


def _api_from_env(env_path: Path) -> VolumeApi:
    cfg = load_config(env_path)
    return VolumeApi(api_key=cfg.runpod_api_key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RunPod Network Volume 管理")
    parser.add_argument("--env", type=Path, default=Path(".env"), help=".env 路徑（取 API key）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出所有 Network Volume")

    p_create = sub.add_parser("create", help="建立一個 Network Volume")
    p_create.add_argument("--dc", required=True, help="資料中心 ID（如 EU-RO-1）")
    p_create.add_argument("--size", type=int, default=60, help="容量 GB")
    p_create.add_argument("--name", default="lora-vol", help="volume 名稱")

    p_del = sub.add_parser("delete", help="刪除指定 Network Volume")
    p_del.add_argument("--id", required=True, help="volume id")

    p_single = sub.add_parser("ensure-single", help="在指定 DC 建一個，刪掉其餘所有")
    p_single.add_argument("--dc", required=True)
    p_single.add_argument("--size", type=int, default=60)
    p_single.add_argument("--name", default="lora-vol")

    args = parser.parse_args(argv)
    api = _api_from_env(args.env)

    if args.cmd == "list":
        vols = api.list()
        if not vols:
            print("（沒有任何 Network Volume）")
        for v in vols:
            print(_fmt(v))
        return 0

    if args.cmd == "create":
        v = api.create(name=args.name, data_center_id=args.dc, size_gb=args.size)
        print("已建立：" + _fmt(v))
        print("VOLUME_ID=" + v.get("id", ""))
        return 0

    if args.cmd == "delete":
        api.delete(args.id)
        print(f"已刪除：{args.id}")
        return 0

    if args.cmd == "ensure-single":
        v = ensure_single(api, name=args.name, data_center_id=args.dc, size_gb=args.size)
        print("\n保留的 volume：" + _fmt(v))
        print("VOLUME_ID=" + v.get("id", ""))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
