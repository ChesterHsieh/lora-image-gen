"""從本機透過直接 SSH 操作遠端 pod：上傳資料夾、注入設定並觸發訓練、輪詢完成標記。

RunPod 的 proxy SSH 不支援 scp/rsync，runpodctl 也沒有可遠端觸發的傳檔 / 任意
shell exec，故採直接 SSH（pod 須開 22 port + 註冊本機 public key）。連線資訊
（ip/port）由 RunpodClient.extract_endpoints 解析出的 ssh_command 提供。

訓練參數與 rclone 設定不走 pod 建立時的 env / docker_args——這版 SDK 走舊 GraphQL
且不跳脫字串，含引號 / 換行的值會破壞 mutation。改在 pod 就緒後經 SSH 注入。
"""
from __future__ import annotations

import base64
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 形如 "ssh root@1.2.3.4 -p 12345"
_SSH_RE = re.compile(r"ssh\s+(?P<user>[^@]+)@(?P<host>\S+)\s+-p\s+(?P<port>\d+)")


class TransportError(Exception):
    """SSH 連線資訊無效，或遠端操作失敗。"""


@dataclass(frozen=True)
class SshTarget:
    user: str
    host: str
    port: int
    key_path: Path | None = None

    @classmethod
    def from_ssh_command(cls, ssh_command: str | None, key_path: Path | None = None) -> "SshTarget":
        if not ssh_command:
            raise TransportError("pod 未提供直連 SSH 資訊（需開 22 port 直連 SSH）")
        m = _SSH_RE.search(ssh_command)
        if not m:
            raise TransportError(f"無法解析 SSH 連線指令：{ssh_command!r}")
        return cls(user=m["user"], host=m["host"], port=int(m["port"]), key_path=key_path)

    def _ssh_opts(self) -> list[str]:
        opts = ["-p", str(self.port), "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
        if self.key_path:
            opts += ["-i", str(self.key_path)]
        return opts


@dataclass
class SshTransport:
    """本機 SSH 操作 pod 的實作；以注入的 runner 執行子行程，方便測試。"""

    target: SshTarget
    scripts_dir: Path | None = None  # 本機 runpod/scripts/，kickoff 會 rsync 上 pod
    runner: "Runner" = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.runner is None:
            self.runner = _subprocess_runner

    def kickoff(self, cfg) -> None:
        """把腳本與設定送上 pod、注入訓練參數與 rclone 設定，背景觸發訓練流程。"""
        # 1. 上傳 pod 端腳本
        if self.scripts_dir is not None:
            if not self.upload(self.scripts_dir, "/workspace/runpod/scripts/"):
                raise TransportError("上傳 pod 腳本失敗")
            self._remote("chmod +x /workspace/runpod/scripts/*.sh")

        # 2. rclone 設定用 base64 傳，避開引號 / 換行問題；寫進 pod 的 rclone.conf
        b64 = base64.b64encode(cfg.rclone_drive_config.encode("utf-8")).decode("ascii")
        conf = "$HOME/.config/rclone/rclone.conf"
        if self._remote(
            f"mkdir -p $HOME/.config/rclone && echo {b64} | base64 -d > {conf} && chmod 600 {conf}"
        ).returncode != 0:
            raise TransportError("寫入 rclone 設定失敗")

        # 3. 訓練參數寫成 pod 上的 env 檔（值經 shell 跳脫），由 bootstrap source
        env_lines = "\n".join(
            f"export {k}={shlex.quote(v)}"
            for k, v in {
                "COMFY_VOLUME": "/workspace",
                "TRAIN_CONCEPT": cfg.concept,
                "TRAIN_TRIGGER": cfg.trigger,
                "TRAIN_RANK": str(cfg.rank),
                "TRAIN_ALPHA": str(cfg.alpha),
                "TRAIN_LR": cfg.lr,
                "TRAIN_STEPS": str(cfg.steps),
                "TRAIN_BASE_MODEL": cfg.base_model,
                "GDRIVE_DEST_PATH": cfg.gdrive_dest_path,
            }.items()
        )
        env_b64 = base64.b64encode(env_lines.encode("utf-8")).decode("ascii")
        env_file = "/workspace/training/run.env"
        if self._remote(
            f"mkdir -p /workspace/training && echo {env_b64} | base64 -d > {env_file}"
        ).returncode != 0:
            raise TransportError("寫入訓練參數失敗")

        # 4. 背景觸發 bootstrap（source env 後跑），不阻塞；之後靠 marker 輪詢
        bootstrap = "/workspace/runpod/scripts/pod_bootstrap.sh"
        launch = (
            f"set -a && . {env_file} && set +a && "
            f"nohup bash {bootstrap} > /workspace/training/bootstrap.log 2>&1 &"
        )
        if self._remote(launch).returncode != 0:
            raise TransportError("觸發 bootstrap 失敗")

    def upload(self, source: Path, dest: str) -> bool:
        """把本機資料夾打包經 SSH 傳到 pod 的 dest，回傳是否成功。

        用 tar over ssh（不依賴 rsync——RunPod base image 不一定裝 rsync，但 tar 一定有）。
        """
        if self._remote(f"mkdir -p {dest}").returncode != 0:
            return False
        ssh_opts = " ".join(self.target._ssh_opts())
        target = f"{self.target.user}@{self.target.host}"
        src = str(source).rstrip("/")
        # 本機 tar 打包 stdout → ssh → pod 端 tar 解到 dest。
        # COPYFILE_DISABLE=1 + 排除 ._*：避免 macOS AppleDouble metadata。
        # 解壓 --no-same-owner：避開 macOS 寫入的 uid/gid 在 pod 上無權還原。
        pipeline = (
            f"COPYFILE_DISABLE=1 tar -C {shlex.quote(src)} --exclude='._*' -czf - . | "
            f"ssh {ssh_opts} {target} tar --no-same-owner -xzf - -C {shlex.quote(dest)}"
        )
        return self.runner(["bash", "-c", pipeline]).returncode == 0

    def marker_status(self, concept: str) -> str | None:
        """查 pod 上的完成標記：done / failed / None（尚在進行）。"""
        base = f"/workspace/training/{concept}"
        if self._remote(f"test -f {base}.run.done").returncode == 0:
            return "done"
        if self._remote(f"test -f {base}.run.failed").returncode == 0:
            return "failed"
        return None

    def _remote(self, remote_cmd: str) -> subprocess.CompletedProcess:
        cmd = ["ssh", *self.target._ssh_opts(),
               f"{self.target.user}@{self.target.host}", remote_cmd]
        return self.runner(cmd)


# 子行程執行器：吃 argv，回傳 CompletedProcess（可注入假物件做測試）。
from collections.abc import Callable  # noqa: E402

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _subprocess_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)
