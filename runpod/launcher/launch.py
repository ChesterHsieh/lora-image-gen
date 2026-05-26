"""一鍵在 RunPod 上跑一次 SDXL LoRA 訓練的主流程。

ephemeral 流程：載入設定 → 建 pod → 上傳資料 → 觸發訓練 → 輪詢完成
→ 依結果決定是否回收 pod。失敗或同步失敗時不回收 pod（產出留在 Volume）。

用法：
    python -m launcher.launch --env .env --dataset ../dataset_prep/cropped
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import ConfigError, LauncherConfig, load_config
from .runpod_client import PodEndpoints, PodError, PodHandle, RunpodClient
from .uploader import DatasetError, DatasetUploader


class RunOutcome(Enum):
    SUCCESS = "success"
    TRAIN_OR_SYNC_FAILED = "train_or_sync_failed"
    POD_ERROR = "pod_error"


@dataclass(frozen=True)
class RunResult:
    outcome: RunOutcome
    pod_id: str | None
    pod_terminated: bool
    message: str


# 輪詢 pod 端流程狀態：回 "done" / "failed" / None（尚在進行）。
MarkerCheck = Callable[[str, str], str | None]


def build_start_command(concept: str) -> str:
    """docker start command 留空：pod 用 template 預設開機（含 ComfyUI）。

    訓練不從這裡觸發——RunPod 這版 SDK 走舊 GraphQL 且不跳脫字串，含引號 / 換行的
    指令會破壞 mutation 語法。改在 pod 就緒後經 SSH 上傳腳本並觸發 pod_bootstrap.sh。
    """
    return ""


@dataclass
class Launcher:
    """編排一次訓練的完整 ephemeral 流程。"""

    config: LauncherConfig
    client: RunpodClient
    uploader: DatasetUploader
    marker_check: MarkerCheck
    kickoff: Callable[[LauncherConfig], None] = lambda _cfg: None
    on_ready: Callable[[PodEndpoints], None] = lambda _ep: None
    log: Callable[[str], None] = print
    poll_interval: float = 30.0
    run_timeout: float = 6 * 3600.0
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic

    def run(self, dataset: Path) -> RunResult:
        cfg = self.config

        # 先在本機驗證資料集，避免白開 pod。
        try:
            from .uploader import validate_dataset

            validate_dataset(dataset)
        except DatasetError as exc:
            return RunResult(RunOutcome.POD_ERROR, None, True, f"資料集無效：{exc}")

        # 建立 pod。
        try:
            pod = self.client.create_training_pod(
                name=f"lora-train-{cfg.concept}",
                image=cfg.image,
                gpu_types=cfg.gpu_types,
                network_volume_id=cfg.network_volume_id,
                data_center_id=cfg.data_center_id,
                container_disk_gb=cfg.container_disk_gb,
                env={},  # 訓練參數 / rclone 設定改經 SSH 注入，避免破壞 SDK 的 GraphQL
                start_command=build_start_command(cfg.concept),
                cloud_type=cfg.cloud_type,
            )
        except PodError as exc:
            return RunResult(
                RunOutcome.POD_ERROR, None, True,
                f"建立 pod 失敗：{exc}。請確認 Network Volume 與 pod 在同一資料中心"
                f"（{cfg.data_center_id}）、GPU 型別可用。",
            )

        self.log(f"==> 已建立 pod：{pod.pod_id}")

        # 等就緒並印出除錯連線資訊。
        try:
            ready = self.client.wait_until_ready(pod.pod_id)
        except PodError as exc:
            return self._finish_keep_pod(pod, f"pod 未就緒：{exc}")
        endpoints = self.client.extract_endpoints(ready)
        self._print_debug_endpoints(endpoints)

        # pod 就緒，把連線資訊交給上傳 / 輪詢的接點（直連 SSH）。
        try:
            self.on_ready(endpoints)
        except Exception as exc:  # noqa: BLE001 — 連線資訊無效屬可預期失敗
            return self._finish_keep_pod(pod, f"無法建立與 pod 的連線：{exc}")

        # 上傳資料集。
        try:
            count = self.uploader.upload(dataset, cfg.concept)
            self.log(f"==> 已上傳資料集：{count} 張圖")
        except DatasetError as exc:
            return self._finish_keep_pod(pod, f"資料集上傳失敗：{exc}")

        # 經 SSH 上傳 pod 腳本 + 注入訓練參數與 rclone 設定，並觸發訓練流程。
        try:
            self.kickoff(cfg)
            self.log("==> 已在 pod 上觸發訓練流程")
        except Exception as exc:  # noqa: BLE001
            return self._finish_keep_pod(pod, f"觸發訓練失敗：{exc}")

        # 輪詢 pod 端訓練+同步流程的完成標記。
        outcome = self._poll_run(pod)
        if outcome != RunOutcome.SUCCESS:
            return self._finish_keep_pod(
                pod, "訓練或同步失敗——pod 已保留，產出仍在 Network Volume 可手動取回。",
                outcome=outcome,
            )

        # 成功：依設定決定回收。
        if cfg.keep_pod:
            return self._finish_keep_pod(pod, "訓練成功，已依設定保留 pod。", outcome=RunOutcome.SUCCESS)
        self.client.terminate(pod.pod_id)
        self.log(f"==> 訓練成功，已回收 pod {pod.pod_id}。")
        return RunResult(RunOutcome.SUCCESS, pod.pod_id, True, "訓練成功，產出已同步至 Google Drive。")

    def _poll_run(self, pod: PodHandle) -> RunOutcome:
        deadline = self.now() + self.run_timeout
        while True:
            status = self.marker_check(pod.pod_id, self.config.concept)
            if status == "done":
                return RunOutcome.SUCCESS
            if status == "failed":
                return RunOutcome.TRAIN_OR_SYNC_FAILED
            if self.now() >= deadline:
                self.log("==> 輪詢逾時，視為失敗。")
                return RunOutcome.TRAIN_OR_SYNC_FAILED
            self.sleep(self.poll_interval)

    def _print_debug_endpoints(self, endpoints: PodEndpoints) -> None:
        self.log("==> 除錯介面：")
        self.log(f"    ComfyUI: {endpoints.comfy_url or '(尚未就緒)'}")
        self.log(f"    SSH    : {endpoints.ssh_command or '(尚未就緒，可到 RunPod Connect 頁取得)'}")

    def _finish_keep_pod(
        self, pod: PodHandle, message: str, *, outcome: RunOutcome = RunOutcome.POD_ERROR
    ) -> RunResult:
        self.log(f"==> {message}")
        self.log(f"==> pod 未回收，記得自行回收以免持續計費：runpodctl remove pod {pod.pod_id}")
        return RunResult(outcome, pod.pod_id, False, message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在 RunPod 上跑一次 SDXL LoRA 訓練")
    parser.add_argument("--env", type=Path, default=Path(".env"), help=".env 設定檔路徑")
    parser.add_argument("--dataset", type=Path, required=True, help="本機訓練資料集目錄（圖 + 同名 .txt）")
    parser.add_argument("--ssh-key", help="連 pod 用的 SSH 私鑰路徑（預設用 ssh 自動選；需先把 public key 註冊到 RunPod）")
    parser.add_argument("--create-retries", type=int, default=20,
                        help="配不到 GPU 容量時的重試次數（預設 20）")
    parser.add_argument("--retry-wait", type=float, default=15.0,
                        help="每次重試前等待秒數（預設 15）")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.env)
    except ConfigError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        return 2

    import runpod

    from .transport import SshTarget, SshTransport

    runpod.api_key = cfg.runpod_api_key
    client = RunpodClient(
        api=runpod,
        create_retries=args.create_retries,
        create_retry_wait=args.retry_wait,
        log=print,
    )

    # transport 的 SSH 目標要等 pod 就緒才知道；用一個 holder 在 on_ready 時填入。
    holder: dict[str, SshTransport] = {}
    key_path = Path(args.ssh_key).expanduser() if args.ssh_key else None
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"

    def on_ready(endpoints: PodEndpoints) -> None:
        target = SshTarget.from_ssh_command(endpoints.ssh_command, key_path=key_path)
        holder["transport"] = SshTransport(target=target, scripts_dir=scripts_dir)

    def transfer(source: Path, dest: str) -> bool:
        return holder["transport"].upload(source, dest)

    def kickoff(c: LauncherConfig) -> None:
        holder["transport"].kickoff(c)

    def marker_check(pod_id: str, concept: str) -> str | None:
        return holder["transport"].marker_status(concept)

    uploader = DatasetUploader(transfer=transfer)
    launcher = Launcher(
        config=cfg, client=client, uploader=uploader,
        marker_check=marker_check, kickoff=kickoff, on_ready=on_ready,
    )
    result = launcher.run(args.dataset)
    print(f"\n結果：{result.message}")
    return 0 if result.outcome == RunOutcome.SUCCESS else 1


if __name__ == "__main__":
    raise SystemExit(main())
