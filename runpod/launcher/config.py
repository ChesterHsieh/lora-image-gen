"""載入並驗證啟動 RunPod 訓練所需的設定與機密。

從 .env 讀值，檢查必要鍵齊全，缺項時以 ConfigError 中止。
機密（API key、rclone 設定）標記為 secret，永不寫入日誌或 repr。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path


class ConfigError(Exception):
    """設定缺漏或無效。訊息只含鍵名，不含機密值。"""


# 必要鍵：缺任一即拒絕啟動。rclone 設定另外驗證（見 load_config）。
REQUIRED_KEYS: tuple[str, ...] = (
    "RUNPOD_API_KEY",
    "RUNPOD_NETWORK_VOLUME_ID",
    "RUNPOD_DATA_CENTER_ID",
    "RUNPOD_GPU_TYPE",
    "GDRIVE_DEST_PATH",
)

# 視為機密的鍵：不可出現在日誌 / repr。
SECRET_KEYS: frozenset[str] = frozenset(
    {"RUNPOD_API_KEY", "RCLONE_DRIVE_CONFIG", "RCLONE_DRIVE_CONFIG_B64"}
)


def parse_env_file(path: Path) -> dict[str, str]:
    """把 .env 解析成 dict。支援 KEY=VALUE、# 註解、空行；不展開變數。"""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class LauncherConfig:
    """一次訓練啟動所需的完整設定。機密欄位的 repr 會被遮蔽。"""

    runpod_api_key: str = field(metadata={"secret": True})
    network_volume_id: str
    data_center_id: str
    gpu_type: str
    cloud_type: str
    rclone_drive_config: str = field(metadata={"secret": True})
    gdrive_dest_path: str
    image: str
    container_disk_gb: int
    concept: str
    trigger: str
    rank: int
    alpha: int
    lr: str
    steps: int
    base_model: str
    keep_pod: bool

    @property
    def gpu_types(self) -> list[str]:
        """GPU 候選清單：RUNPOD_GPU_TYPE 可用逗號分隔多款，依序嘗試。"""
        return [g.strip() for g in self.gpu_type.split(",") if g.strip()]

    def __repr__(self) -> str:  # 避免機密被印進日誌 / 例外追蹤
        parts = []
        for f in fields(self):
            if f.metadata.get("secret"):
                value = "***" if getattr(self, f.name) else "(empty)"
            else:
                value = getattr(self, f.name)
            parts.append(f"{f.name}={value!r}")
        return f"LauncherConfig({', '.join(parts)})"


def _missing_required(values: dict[str, str]) -> list[str]:
    """回傳缺漏（不存在或空字串）的必要鍵，保持 REQUIRED_KEYS 的順序。"""
    return [k for k in REQUIRED_KEYS if not values.get(k, "").strip()]


def _resolve_rclone_config(values: dict[str, str]) -> str:
    """取 rclone 設定：優先 base64（單行、最穩），否則用單行原文。

    .env 逐行解析無法存多行值，所以多行的 rclone.conf 必須用 RCLONE_DRIVE_CONFIG_B64
    （整段 conf 的 base64）傳入；RCLONE_DRIVE_CONFIG 僅適用真的單行的情況。
    """
    import base64
    import binascii

    b64 = values.get("RCLONE_DRIVE_CONFIG_B64", "").strip()
    if b64:
        try:
            return base64.b64decode(b64).decode("utf-8")
        except (binascii.Error, ValueError) as exc:
            raise ConfigError(f"RCLONE_DRIVE_CONFIG_B64 不是有效的 base64：{exc}") from exc
    raw = values.get("RCLONE_DRIVE_CONFIG", "").strip()
    if raw:
        return raw
    raise ConfigError(
        "設定缺少 rclone 設定：請提供 RCLONE_DRIVE_CONFIG_B64（rclone.conf 的 base64，建議）"
        " 或單行的 RCLONE_DRIVE_CONFIG"
    )


def load_config(env_path: Path) -> LauncherConfig:
    """讀 .env、驗證必要鍵、組出設定；缺漏時拋 ConfigError（只列鍵名）。"""
    if not env_path.exists():
        raise ConfigError(f"找不到設定檔：{env_path}（可從 .env.example 複製）")

    values = parse_env_file(env_path)
    missing = _missing_required(values)
    if missing:
        raise ConfigError("設定缺少必要鍵：" + ", ".join(missing))

    return LauncherConfig(
        runpod_api_key=values["RUNPOD_API_KEY"],
        network_volume_id=values["RUNPOD_NETWORK_VOLUME_ID"],
        data_center_id=values["RUNPOD_DATA_CENTER_ID"],
        gpu_type=values["RUNPOD_GPU_TYPE"],
        cloud_type=values.get("RUNPOD_CLOUD_TYPE", "SECURE").strip().upper(),
        rclone_drive_config=_resolve_rclone_config(values),
        gdrive_dest_path=values["GDRIVE_DEST_PATH"],
        image=values.get("RUNPOD_IMAGE", "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"),
        container_disk_gb=int(values.get("RUNPOD_CONTAINER_DISK_GB", "30")),
        concept=values.get("TRAIN_CONCEPT", "stcklnd"),
        trigger=values.get("TRAIN_TRIGGER", "stcklnd"),
        rank=int(values.get("TRAIN_RANK", "16")),
        alpha=int(values.get("TRAIN_ALPHA", "8")),
        lr=values.get("TRAIN_LR", "1e-4"),
        steps=int(values.get("TRAIN_STEPS", "1500")),
        base_model=values.get("TRAIN_BASE_MODEL", "models/checkpoints/dreamshaper_xl_v2_turbo.safetensors"),
        keep_pod=values.get("KEEP_POD", "false").strip().lower() in {"1", "true", "yes"},
    )
