"""設定載入與驗證測試（task 8.1）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from launcher.config import (
    REQUIRED_KEYS,
    ConfigError,
    LauncherConfig,
    load_config,
    parse_env_file,
)

FULL_ENV = """
# 註解行
RUNPOD_API_KEY=secret-key-123
RUNPOD_NETWORK_VOLUME_ID=vol-abc
RUNPOD_DATA_CENTER_ID=EU-RO-1
RUNPOD_GPU_TYPE=NVIDIA RTX A6000
RCLONE_DRIVE_CONFIG=[gdrive]
GDRIVE_DEST_PATH=gdrive:lora-outputs

TRAIN_CONCEPT=mycard
TRAIN_RANK=32
KEEP_POD=true
"""


def _write_env(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.mark.unit
def test_parse_env_skips_comments_and_blanks(tmp_path: Path) -> None:
    env = _write_env(tmp_path, "A=1\n# comment\n\nB = 2 \nNOEQUALS\n")
    values = parse_env_file(env)
    assert values == {"A": "1", "B": "2"}
    assert "NOEQUALS" not in values


@pytest.mark.unit
def test_load_config_full(tmp_path: Path) -> None:
    cfg = load_config(_write_env(tmp_path, FULL_ENV))
    assert cfg.runpod_api_key == "secret-key-123"
    assert cfg.network_volume_id == "vol-abc"
    assert cfg.concept == "mycard"
    assert cfg.rank == 32
    assert cfg.keep_pod is True
    # 未指定的有預設
    assert cfg.alpha == 8
    assert cfg.trigger == "stcklnd"
    assert cfg.cloud_type == "SECURE"  # 預設 SECURE（掛 volume 必須）


@pytest.mark.unit
def test_rclone_config_b64_decoded(tmp_path: Path) -> None:
    import base64
    conf = "[gdrive]\ntype = drive\ntoken = {\"x\":1}"
    b64 = base64.b64encode(conf.encode()).decode()
    content = FULL_ENV.replace("RCLONE_DRIVE_CONFIG=[gdrive]",
                               f"RCLONE_DRIVE_CONFIG_B64={b64}")
    cfg = load_config(_write_env(tmp_path, content))
    assert cfg.rclone_drive_config == conf  # 多行還原無誤


@pytest.mark.unit
def test_missing_rclone_config_raises(tmp_path: Path) -> None:
    content = "\n".join(ln for ln in FULL_ENV.splitlines()
                        if not ln.startswith("RCLONE_DRIVE_CONFIG"))
    with pytest.raises(ConfigError, match="rclone"):
        load_config(_write_env(tmp_path, content))


@pytest.mark.unit
def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="找不到設定檔"):
        load_config(tmp_path / "nope.env")


@pytest.mark.unit
@pytest.mark.parametrize("drop", list(REQUIRED_KEYS))
def test_missing_required_key_lists_name(tmp_path: Path, drop: str) -> None:
    lines = [ln for ln in FULL_ENV.splitlines() if not ln.startswith(drop + "=")]
    with pytest.raises(ConfigError) as exc:
        load_config(_write_env(tmp_path, "\n".join(lines)))
    assert drop in str(exc.value)


@pytest.mark.unit
def test_empty_value_counts_as_missing(tmp_path: Path) -> None:
    content = FULL_ENV.replace("RUNPOD_API_KEY=secret-key-123", "RUNPOD_API_KEY=")
    with pytest.raises(ConfigError, match="RUNPOD_API_KEY"):
        load_config(_write_env(tmp_path, content))


@pytest.mark.unit
def test_repr_hides_secrets(tmp_path: Path) -> None:
    cfg = load_config(_write_env(tmp_path, FULL_ENV))
    text = repr(cfg)
    assert "secret-key-123" not in text  # API key 明文不外洩
    assert "[gdrive]" not in text        # rclone 設定明文不外洩
    assert "***" in text
    # 非機密欄位照常可見
    assert "vol-abc" in text


@pytest.mark.unit
def test_keep_pod_falsey_values(tmp_path: Path) -> None:
    content = FULL_ENV.replace("KEEP_POD=true", "KEEP_POD=no")
    cfg = load_config(_write_env(tmp_path, content))
    assert cfg.keep_pod is False
