"""集中封裝與 RunPod 平台的所有互動。

launcher 其他部分只透過這裡建 pod / 查狀態 / 回收，日後若要從官方
Python SDK 換成 REST API 或 runpodctl，只需改這個模組。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

VOLUME_MOUNT_PATH = "/workspace"
COMFY_PORT = 8188

# RunPod 在沒有可用機器時的錯誤訊息特徵（用來判斷該重試而非放棄）。
_NO_CAPACITY_HINTS = ("no longer any instances", "no instances available", "not enough free")


class PodError(Exception):
    """建立 / 查詢 / 回收 pod 失敗。"""


def _is_no_capacity(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _NO_CAPACITY_HINTS)


class RunpodApi(Protocol):
    """RunPod SDK 介面的最小子集，方便測試以假物件替換。"""

    def create_pod(self, **kwargs) -> dict: ...
    def get_pod(self, pod_id: str) -> dict: ...
    def terminate_pod(self, pod_id: str) -> None: ...


@dataclass(frozen=True)
class PodHandle:
    """一個已建立的 pod 的不可變參照。"""

    pod_id: str
    name: str


@dataclass(frozen=True)
class PodEndpoints:
    """pod 對外服務的連線資訊（就緒後才有值）。"""

    comfy_url: str | None
    ssh_command: str | None


@dataclass
class RunpodClient:
    """RunPod 官方 SDK 的薄封裝。"""

    api: RunpodApi
    poll_interval: float = 10.0
    ready_timeout: float = 600.0
    create_retries: int = 1          # 配不到容量時的重試次數（1 = 不重試）
    create_retry_wait: float = 15.0  # 每次重試前等待秒數
    sleep: "Callable[[float], None]" = time.sleep
    log: "Callable[[str], None]" = lambda _m: None

    def create_training_pod(
        self,
        *,
        name: str,
        image: str,
        gpu_types: list[str],
        network_volume_id: str,
        data_center_id: str,
        container_disk_gb: int,
        env: dict[str, str],
        start_command: str,
        cloud_type: str = "SECURE",
    ) -> PodHandle:
        """以既有 Network Volume 建立帶 GPU 的訓練 pod。

        gpu_types 為候選清單，每輪重試依序嘗試、任一有貨就搶。volume 在建立時掛到
        /workspace；開 ComfyUI(http) 與 SSH(tcp) port；env 與訓練啟動指令一併注入。
        cloud_type 可選 SECURE / COMMUNITY。
        """
        base_kwargs = dict(
            name=name,
            image_name=image,
            cloud_type=cloud_type,
            data_center_id=data_center_id,
            network_volume_id=network_volume_id,
            volume_mount_path=VOLUME_MOUNT_PATH,
            container_disk_in_gb=container_disk_gb,
            ports=f"{COMFY_PORT}/http,22/tcp",
            start_ssh=True,
            support_public_ip=True,
            env=env,
        )
        # 只在非空時傳 docker_args：空字串會讓 SDK 送出破壞語法的 dockerArgs: ""。
        if start_command:
            base_kwargs["docker_args"] = start_command

        candidates = gpu_types or []
        if not candidates:
            raise PodError("未指定任何 GPU 型別")

        # 容量不足時重試：每輪依序試每款候選 GPU，任一有貨就搶。
        attempts = max(1, self.create_retries)
        for i in range(1, attempts + 1):
            for gpu in candidates:
                try:
                    result = self.api.create_pod(gpu_type_id=gpu, **base_kwargs)
                except Exception as exc:  # noqa: BLE001 — 需判斷是否為容量問題
                    if _is_no_capacity(exc):
                        continue  # 換下一款候選 GPU
                    raise PodError(f"建立 pod 失敗（{gpu}）：{exc}") from exc
                pod_id = result.get("id")
                if not pod_id:
                    raise PodError(f"建立 pod 失敗：回應未含 pod id（{result!r}）")
                self.log(f"==> 搶到 {gpu}")
                return PodHandle(pod_id=pod_id, name=name)
            # 這輪所有候選都沒貨
            if i < attempts:
                self.log(
                    f"==> 第 {i}/{attempts} 次：候選 {candidates} 都配不到，"
                    f"{self.create_retry_wait:.0f}s 後重試…"
                )
                self.sleep(self.create_retry_wait)
        raise PodError(
            f"試 {attempts} 輪仍配不到候選 GPU {candidates}（{data_center_id} 容量不足）"
        )

    def get_status(self, pod_id: str) -> dict:
        """查 pod 當前狀態（原始 SDK 回應）。"""
        pod = self.api.get_pod(pod_id)
        if pod is None:
            raise PodError(f"查不到 pod：{pod_id}")
        return pod

    def wait_until_ready(self, pod_id: str, *, now=time.monotonic, sleep=time.sleep) -> dict:
        """輪詢直到 pod 的 runtime 就緒；逾時拋 PodError。"""
        deadline = now() + self.ready_timeout
        while True:
            pod = self.get_status(pod_id)
            if pod.get("runtime"):
                return pod
            if now() >= deadline:
                raise PodError(
                    f"pod {pod_id} 在 {self.ready_timeout:.0f}s 內未就緒（runtime 仍為空）"
                )
            sleep(self.poll_interval)

    def extract_endpoints(self, pod: dict) -> PodEndpoints:
        """從 pod 狀態取出 ComfyUI proxy URL 與 SSH 連線指令。"""
        pod_id = pod.get("id", "")
        comfy_url = f"https://{pod_id}-{COMFY_PORT}.proxy.runpod.net" if pod_id else None

        ssh_command = None
        runtime = pod.get("runtime") or {}
        for port in runtime.get("ports", []) or []:
            if port.get("privatePort") == 22 and port.get("isIpPublic"):
                ip = port.get("ip")
                public_port = port.get("publicPort")
                if ip and public_port:
                    ssh_command = f"ssh root@{ip} -p {public_port}"
                    break
        return PodEndpoints(comfy_url=comfy_url, ssh_command=ssh_command)

    def terminate(self, pod_id: str) -> None:
        """回收 pod。"""
        self.api.terminate_pod(pod_id)
