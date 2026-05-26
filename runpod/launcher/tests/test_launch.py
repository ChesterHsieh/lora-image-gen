"""launcher 主流程測試（task 8.4），mock pod/上傳/輪詢驗證成功與失敗路徑。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from launcher.config import LauncherConfig
from launcher.launch import Launcher, RunOutcome, build_start_command
from launcher.runpod_client import PodEndpoints, PodError, PodHandle


def _config(**overrides) -> LauncherConfig:
    base = LauncherConfig(
        runpod_api_key="k", network_volume_id="vol", data_center_id="EU-RO-1",
        gpu_type="A6000", cloud_type="COMMUNITY",
        rclone_drive_config="[gdrive]", gdrive_dest_path="gdrive:o",
        image="img", container_disk_gb=30, concept="stcklnd", trigger="stcklnd",
        rank=16, alpha=8, lr="1e-4", steps=1500,
        base_model="models/checkpoints/m.safetensors", keep_pod=False,
    )
    return replace(base, **overrides)


class FakeClient:
    def __init__(self, *, create_error=False, ready_error=False):
        self.create_error = create_error
        self.ready_error = ready_error
        self.terminated: list[str] = []

    def create_training_pod(self, **kwargs):
        if self.create_error:
            raise PodError("no capacity")
        return PodHandle(pod_id="pod-1", name=kwargs["name"])

    def wait_until_ready(self, pod_id):
        if self.ready_error:
            raise PodError("timeout")
        return {"id": pod_id, "runtime": {"ports": []}}

    def extract_endpoints(self, pod):
        return PodEndpoints(comfy_url="https://pod-1-8188.proxy.runpod.net",
                            ssh_command="ssh root@1.2.3.4 -p 22")

    def terminate(self, pod_id):
        self.terminated.append(pod_id)


class FakeUploader:
    def __init__(self, count=3):
        self.count = count
        self.called = False

    def upload(self, source, concept):
        self.called = True
        return self.count


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    (tmp_path / "apple.png").write_bytes(b"x")
    (tmp_path / "apple.txt").write_text("stcklnd, apple", encoding="utf-8")
    return tmp_path


def _launcher(config, client, uploader, marker_status):
    """marker_status 為 list，依序回傳模擬輪詢狀態。"""
    states = iter(marker_status)
    return Launcher(
        config=config, client=client, uploader=uploader,
        marker_check=lambda pid, c: next(states),
        log=lambda *_: None, sleep=lambda *_: None,
        poll_interval=0, now=lambda: 0.0,
    )


@pytest.mark.unit
def test_success_terminates_pod(dataset: Path) -> None:
    client = FakeClient()
    l = _launcher(_config(), client, FakeUploader(), [None, "done"])
    result = l.run(dataset)
    assert result.outcome == RunOutcome.SUCCESS
    assert result.pod_terminated is True
    assert client.terminated == ["pod-1"]


@pytest.mark.unit
def test_success_keep_pod_does_not_terminate(dataset: Path) -> None:
    client = FakeClient()
    l = _launcher(_config(keep_pod=True), client, FakeUploader(), ["done"])
    result = l.run(dataset)
    assert result.outcome == RunOutcome.SUCCESS
    assert result.pod_terminated is False
    assert client.terminated == []


@pytest.mark.unit
def test_train_or_sync_failure_keeps_pod(dataset: Path) -> None:
    client = FakeClient()
    l = _launcher(_config(), client, FakeUploader(), ["failed"])
    result = l.run(dataset)
    assert result.outcome == RunOutcome.TRAIN_OR_SYNC_FAILED
    assert result.pod_terminated is False
    assert client.terminated == []  # 失敗不回收，產出留在 Volume


@pytest.mark.unit
def test_poll_timeout_keeps_pod(dataset: Path) -> None:
    client = FakeClient()
    # now 先回 0（建 deadline），之後超過 timeout
    clock = iter([0.0, 0.0, 999999.0, 999999.0])
    l = Launcher(
        config=_config(), client=client, uploader=FakeUploader(),
        marker_check=lambda pid, c: None, log=lambda *_: None,
        sleep=lambda *_: None, poll_interval=0, run_timeout=10,
        now=lambda: next(clock),
    )
    result = l.run(dataset)
    assert result.outcome == RunOutcome.TRAIN_OR_SYNC_FAILED
    assert client.terminated == []


@pytest.mark.unit
def test_pod_create_error_reports_datacenter_hint(dataset: Path) -> None:
    client = FakeClient(create_error=True)
    l = _launcher(_config(), client, FakeUploader(), [])
    result = l.run(dataset)
    assert result.outcome == RunOutcome.POD_ERROR
    assert "資料中心" in result.message


@pytest.mark.unit
def test_invalid_dataset_aborts_before_pod(tmp_path: Path) -> None:
    client = FakeClient()
    uploader = FakeUploader()
    l = _launcher(_config(), client, uploader, [])
    result = l.run(tmp_path / "nonexistent")
    assert result.outcome == RunOutcome.POD_ERROR
    assert uploader.called is False  # 沒建 pod、沒上傳


@pytest.mark.unit
def test_ready_failure_keeps_pod(dataset: Path) -> None:
    client = FakeClient(ready_error=True)
    l = _launcher(_config(), client, FakeUploader(), [])
    result = l.run(dataset)
    assert result.outcome == RunOutcome.POD_ERROR
    assert client.terminated == []


@pytest.mark.unit
def test_upload_failure_keeps_pod(dataset: Path) -> None:
    from launcher.uploader import DatasetError

    class FailingUploader:
        def upload(self, source, concept):
            raise DatasetError("傳輸失敗")

    client = FakeClient()
    l = _launcher(_config(), client, FailingUploader(), [])
    result = l.run(dataset)
    assert result.outcome == RunOutcome.POD_ERROR
    assert client.terminated == []  # 上傳失敗也不回收


@pytest.mark.unit
def test_on_ready_failure_keeps_pod(dataset: Path) -> None:
    client = FakeClient()

    def boom(endpoints):
        raise RuntimeError("無直連 SSH")

    l = Launcher(
        config=_config(), client=client, uploader=FakeUploader(),
        marker_check=lambda pid, c: "done", on_ready=boom,
        log=lambda *_: None, sleep=lambda *_: None, poll_interval=0, now=lambda: 0.0,
    )
    result = l.run(dataset)
    assert result.outcome == RunOutcome.POD_ERROR
    assert client.terminated == []  # 連不上 pod 也不回收


@pytest.mark.unit
def test_on_ready_receives_endpoints(dataset: Path) -> None:
    seen: list = []
    client = FakeClient()
    l = Launcher(
        config=_config(), client=client, uploader=FakeUploader(),
        marker_check=lambda pid, c: "done", on_ready=seen.append,
        log=lambda *_: None, sleep=lambda *_: None, poll_interval=0, now=lambda: 0.0,
    )
    l.run(dataset)
    assert seen and seen[0].ssh_command is not None


@pytest.mark.unit
def test_build_start_command_is_empty() -> None:
    # 訓練改經 SSH 觸發，start command 留空避免破壞 SDK 的 GraphQL。
    assert build_start_command("stcklnd") == ""


@pytest.mark.unit
def test_kickoff_called_on_success(dataset: Path) -> None:
    seen: list = []
    client = FakeClient()
    l = Launcher(
        config=_config(), client=client, uploader=FakeUploader(),
        marker_check=lambda pid, c: "done", kickoff=seen.append,
        log=lambda *_: None, sleep=lambda *_: None, poll_interval=0, now=lambda: 0.0,
    )
    l.run(dataset)
    assert seen and seen[0].concept == "stcklnd"


@pytest.mark.unit
def test_kickoff_failure_keeps_pod(dataset: Path) -> None:
    client = FakeClient()

    def boom(cfg):
        raise RuntimeError("ssh kickoff failed")

    l = Launcher(
        config=_config(), client=client, uploader=FakeUploader(),
        marker_check=lambda pid, c: "done", kickoff=boom,
        log=lambda *_: None, sleep=lambda *_: None, poll_interval=0, now=lambda: 0.0,
    )
    result = l.run(dataset)
    assert result.outcome == RunOutcome.POD_ERROR
    assert client.terminated == []
