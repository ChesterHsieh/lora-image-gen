"""直連 SSH transport 測試：解析連線、tar 上傳、查完成標記。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from launcher.transport import SshTarget, SshTransport, TransportError


def _ok(cmd):
    return subprocess.CompletedProcess(cmd, 0, "", "")


def _fail(cmd):
    return subprocess.CompletedProcess(cmd, 1, "", "")


@pytest.mark.unit
def test_parse_ssh_command() -> None:
    t = SshTarget.from_ssh_command("ssh root@1.2.3.4 -p 12345")
    assert (t.user, t.host, t.port) == ("root", "1.2.3.4", 12345)


@pytest.mark.unit
def test_parse_none_raises() -> None:
    with pytest.raises(TransportError, match="未提供直連 SSH"):
        SshTarget.from_ssh_command(None)


@pytest.mark.unit
def test_parse_garbage_raises() -> None:
    with pytest.raises(TransportError, match="無法解析"):
        SshTarget.from_ssh_command("connect via web terminal")


@pytest.mark.unit
def test_upload_success_runs_mkdir_then_tar(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd):
        calls.append(cmd)
        return _ok(cmd)

    t = SshTransport(SshTarget("root", "1.2.3.4", 22), runner=runner)
    assert t.upload(tmp_path, "/workspace/datasets/x/") is True
    # 第一個是遠端 mkdir，第二個是 tar over ssh（不依賴 rsync）
    assert any("mkdir -p" in " ".join(c) for c in calls)
    assert any("tar -C" in " ".join(c) and "ssh" in " ".join(c) for c in calls)


@pytest.mark.unit
def test_upload_mkdir_failure_returns_false(tmp_path: Path) -> None:
    t = SshTransport(SshTarget("root", "h", 22), runner=_fail)
    assert t.upload(tmp_path, "/dest") is False


@pytest.mark.unit
def test_upload_transfer_failure_returns_false(tmp_path: Path) -> None:
    seq = iter([_ok, _fail])  # mkdir 成功、tar 失敗

    def runner(cmd):
        return next(seq)(cmd)

    t = SshTransport(SshTarget("root", "h", 22), runner=runner)
    assert t.upload(tmp_path, "/dest") is False


@pytest.mark.unit
def test_marker_status_done() -> None:
    def runner(cmd):
        return _ok(cmd) if "run.done" in " ".join(cmd) else _fail(cmd)

    t = SshTransport(SshTarget("root", "h", 22), runner=runner)
    assert t.marker_status("stcklnd") == "done"


@pytest.mark.unit
def test_marker_status_failed() -> None:
    def runner(cmd):
        return _ok(cmd) if "run.failed" in " ".join(cmd) else _fail(cmd)

    t = SshTransport(SshTarget("root", "h", 22), runner=runner)
    assert t.marker_status("stcklnd") == "failed"


@pytest.mark.unit
def test_marker_status_pending() -> None:
    t = SshTransport(SshTarget("root", "h", 22), runner=_fail)
    assert t.marker_status("stcklnd") is None


@pytest.mark.unit
def test_key_path_added_to_ssh_opts() -> None:
    t = SshTarget("root", "h", 22, key_path=Path("/k.pem"))
    opts = t._ssh_opts()
    assert "-i" in opts and "/k.pem" in opts


class _Cfg:
    """kickoff 用到的最小設定假物件。"""
    rclone_drive_config = "[gdrive]\ntype = drive\ntoken = {\"a\":1}"
    concept = "stcklnd"
    trigger = "stcklnd"
    rank = 16
    alpha = 8
    lr = "1e-4"
    steps = 1500
    base_model = "models/checkpoints/m.safetensors"
    gdrive_dest_path = "gdrive:out"


@pytest.mark.unit
def test_kickoff_uploads_writes_config_and_launches(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(cmd):
        calls.append(" ".join(cmd))
        return _ok(cmd)

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    t = SshTransport(SshTarget("root", "h", 22), scripts_dir=scripts, runner=runner)
    t.kickoff(_Cfg())

    joined = "\n".join(calls)
    assert any("tar -C" in c for c in calls)                      # 上傳腳本（tar over ssh）
    assert "base64 -d" in joined and "rclone.conf" in joined      # 寫 rclone 設定
    assert "run.env" in joined                                    # 寫訓練參數
    assert "pod_bootstrap.sh" in joined and "nohup" in joined     # 背景觸發


@pytest.mark.unit
def test_kickoff_rclone_write_failure_raises(tmp_path: Path) -> None:
    # 讓 rclone.conf 寫入那步失敗
    def runner(cmd):
        s = " ".join(cmd)
        if "rclone.conf" in s:
            return _fail(cmd)
        return _ok(cmd)

    t = SshTransport(SshTarget("root", "h", 22), scripts_dir=None, runner=runner)
    with pytest.raises(TransportError, match="rclone"):
        t.kickoff(_Cfg())
