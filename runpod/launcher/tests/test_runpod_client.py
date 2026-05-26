"""RunPod SDK 薄封裝測試（task 8.3），以假 API 驗證 pod 生命週期。"""
from __future__ import annotations

import pytest

from launcher.runpod_client import (
    COMFY_PORT,
    VOLUME_MOUNT_PATH,
    PodError,
    RunpodClient,
)


class FakeApi:
    """記錄呼叫參數的假 RunPod API。"""

    def __init__(self, *, create_result=None, pod_states=None):
        self.create_result = create_result if create_result is not None else {"id": "pod-1"}
        self.pod_states = list(pod_states or [])
        self.create_kwargs = None
        self.terminated: list[str] = []

    def create_pod(self, **kwargs):
        self.create_kwargs = kwargs
        return self.create_result

    def get_pod(self, pod_id):
        if self.pod_states:
            return self.pod_states.pop(0)
        return {"id": pod_id, "runtime": {"ports": []}}

    def terminate_pod(self, pod_id):
        self.terminated.append(pod_id)


@pytest.mark.unit
def test_create_pod_passes_volume_and_ports() -> None:
    api = FakeApi()
    client = RunpodClient(api=api)
    handle = client.create_training_pod(
        name="lora-train-x", image="img", gpu_types=["A6000"],
        network_volume_id="vol-1", data_center_id="EU-RO-1",
        container_disk_gb=30, env={"K": "V"}, start_command="bash run.sh",
    )
    assert handle.pod_id == "pod-1"
    kw = api.create_kwargs
    assert kw["gpu_type_id"] == "A6000"
    assert kw["network_volume_id"] == "vol-1"
    assert kw["volume_mount_path"] == VOLUME_MOUNT_PATH
    assert kw["cloud_type"] == "SECURE"
    assert f"{COMFY_PORT}/http" in kw["ports"]
    assert "22/tcp" in kw["ports"]
    assert kw["docker_args"] == "bash run.sh"


@pytest.mark.unit
def test_empty_start_command_omits_docker_args() -> None:
    # 空 start_command 不應送 docker_args（會讓 SDK 產生破壞語法的 dockerArgs: ""）。
    api = FakeApi()
    RunpodClient(api=api).create_training_pod(
        name="x", image="i", gpu_types=["g"], network_volume_id="v",
        data_center_id="d", container_disk_gb=10, env={}, start_command="",
    )
    assert "docker_args" not in api.create_kwargs


class _CapacityApi:
    """前 fail_times 次拋無容量錯，之後成功。"""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def create_pod(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("There are no longer any instances available")
        return {"id": "pod-ok"}

    def get_pod(self, pod_id):
        return {"id": pod_id, "runtime": {"ports": []}}

    def terminate_pod(self, pod_id):
        pass


def _create(client: RunpodClient):
    return client.create_training_pod(
        name="x", image="i", gpu_types=["RTX 3090"], network_volume_id="v",
        data_center_id="EU-CZ-1", container_disk_gb=10, env={}, start_command="",
    )


@pytest.mark.unit
def test_retry_succeeds_after_capacity_frees() -> None:
    api = _CapacityApi(fail_times=2)
    waits: list[float] = []
    client = RunpodClient(api=api, create_retries=5, create_retry_wait=1, sleep=waits.append)
    handle = _create(client)
    assert handle.pod_id == "pod-ok"
    assert api.calls == 3
    assert waits == [1, 1]  # 失敗兩次、各等一次


@pytest.mark.unit
def test_retry_exhausted_raises_capacity_error() -> None:
    api = _CapacityApi(fail_times=99)
    client = RunpodClient(api=api, create_retries=3, create_retry_wait=0, sleep=lambda _: None)
    with pytest.raises(PodError, match="容量不足"):
        _create(client)
    assert api.calls == 3


@pytest.mark.unit
def test_second_candidate_used_when_first_out_of_stock() -> None:
    # 第一款配不到、第二款有貨：同一輪內就該搶到第二款，不需等待。
    class TwoGpuApi:
        def __init__(self):
            self.tried: list[str] = []

        def create_pod(self, **kwargs):
            gpu = kwargs["gpu_type_id"]
            self.tried.append(gpu)
            if gpu == "RTX 4090":
                raise RuntimeError("no instances available")
            return {"id": "pod-ok"}

    api = TwoGpuApi()
    waits: list[float] = []
    client = RunpodClient(api=api, create_retries=3, create_retry_wait=5, sleep=waits.append)
    handle = client.create_training_pod(
        name="x", image="i", gpu_types=["RTX 4090", "RTX 3090"],
        network_volume_id="v", data_center_id="EU-CZ-1",
        container_disk_gb=10, env={}, start_command="",
    )
    assert handle.pod_id == "pod-ok"
    assert api.tried == ["RTX 4090", "RTX 3090"]  # 第一款失敗、立刻試第二款
    assert waits == []  # 同輪搶到，不需等待


@pytest.mark.unit
def test_non_capacity_error_not_retried() -> None:
    class BadApi(_CapacityApi):
        def create_pod(self, **kwargs):
            self.calls += 1
            raise RuntimeError("invalid gpu type")

    api = BadApi(fail_times=0)
    client = RunpodClient(api=api, create_retries=5, create_retry_wait=0, sleep=lambda _: None)
    with pytest.raises(PodError, match="建立 pod 失敗"):
        _create(client)
    assert api.calls == 1  # 非容量錯不重試


@pytest.mark.unit
def test_create_pod_without_id_raises() -> None:
    client = RunpodClient(api=FakeApi(create_result={"error": "no capacity"}))
    with pytest.raises(PodError, match="未含 pod id"):
        client.create_training_pod(
            name="x", image="i", gpu_types=["g"], network_volume_id="v",
            data_center_id="d", container_disk_gb=10, env={}, start_command="c",
        )


@pytest.mark.unit
def test_wait_until_ready_polls_then_returns() -> None:
    api = FakeApi(pod_states=[
        {"id": "pod-1", "runtime": None},
        {"id": "pod-1", "runtime": {"ports": []}},
    ])
    slept: list[float] = []
    client = RunpodClient(api=api, poll_interval=5)
    ready = client.wait_until_ready("pod-1", sleep=slept.append)
    assert ready["runtime"] is not None
    assert slept == [5]  # 第一次未就緒睡一輪


@pytest.mark.unit
def test_wait_until_ready_times_out() -> None:
    api = FakeApi(pod_states=[{"id": "pod-1", "runtime": None}] * 5)
    clock = iter([0, 0, 100, 100, 100])
    client = RunpodClient(api=api, poll_interval=1, ready_timeout=10)
    with pytest.raises(PodError, match="未就緒"):
        client.wait_until_ready("pod-1", now=lambda: next(clock), sleep=lambda _: None)


@pytest.mark.unit
def test_get_status_none_raises() -> None:
    class NoneApi(FakeApi):
        def get_pod(self, pod_id):
            return None

    with pytest.raises(PodError, match="查不到 pod"):
        RunpodClient(api=NoneApi()).get_status("pod-1")


@pytest.mark.unit
def test_extract_endpoints_builds_comfy_url_and_ssh() -> None:
    client = RunpodClient(api=FakeApi())
    pod = {
        "id": "pod-xyz",
        "runtime": {"ports": [
            {"privatePort": 22, "isIpPublic": True, "ip": "1.2.3.4", "publicPort": 12345},
            {"privatePort": COMFY_PORT, "isIpPublic": True, "ip": "1.2.3.4", "publicPort": 8188},
        ]},
    }
    ep = client.extract_endpoints(pod)
    assert ep.comfy_url == f"https://pod-xyz-{COMFY_PORT}.proxy.runpod.net"
    assert ep.ssh_command == "ssh root@1.2.3.4 -p 12345"


@pytest.mark.unit
def test_extract_endpoints_no_ssh_when_no_public_port() -> None:
    client = RunpodClient(api=FakeApi())
    pod = {"id": "pod-xyz", "runtime": {"ports": []}}
    ep = client.extract_endpoints(pod)
    assert ep.comfy_url is not None
    assert ep.ssh_command is None


@pytest.mark.unit
def test_terminate_calls_api() -> None:
    api = FakeApi()
    RunpodClient(api=api).terminate("pod-1")
    assert api.terminated == ["pod-1"]
