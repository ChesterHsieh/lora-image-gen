#!/usr/bin/env python3
"""本機端連遠端 RunPod ComfyUI 的 client。

把一個 ComfyUI API 格式的 workflow JSON 提交到遠端 server，等它跑完，
再把產出的圖片抓回本機。RunPod 只負責 GPU + 模型，這支腳本跑在本機。

環境變數：
    RUNPOD_COMFY_URL   遠端 ComfyUI 的 base URL（從 RunPod Connect 頁面取得，
                       例如 https://abc123-8188.proxy.runpod.net）

用法：
    export RUNPOD_COMFY_URL="https://<pod-id>-8188.proxy.runpod.net"
    python comfy_client.py ../workflows/sdxl_turbo_txt2img.json \\
        --out ./out --positive "a cinematic photo of a fox in snow"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ComfyClient:
    """極簡 ComfyUI HTTP client（只用標準函式庫，無第三方相依）。"""

    def __init__(self, base_url: str, timeout: int = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _get(self, path: str) -> bytes:
        req = urllib.request.Request(f"{self.base_url}{path}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def queue_prompt(self, workflow: dict) -> str:
        """提交 workflow，回傳 prompt_id。"""
        result = self._post("/prompt", {"prompt": workflow, "client_id": self.client_id})
        return result["prompt_id"]

    def wait(self, prompt_id: str, poll: float = 1.5) -> dict:
        """輪詢 /history 直到該 prompt 完成，回傳它的 history 條目。"""
        while True:
            raw = self._get(f"/history/{prompt_id}")
            history = json.loads(raw)
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(poll)

    def fetch_image(self, filename: str, subfolder: str, folder_type: str) -> bytes:
        params = urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": folder_type}
        )
        return self._get(f"/view?{params}")


def patch_prompt(workflow: dict, positive: str | None, negative: str | None,
                 seed: int | None) -> dict:
    """把 CLI 傳入的 prompt / seed 寫進 workflow。

    依賴 workflow JSON 裡用 _meta.title 標好的節點：
    "Positive Prompt" / "Negative Prompt" 的 CLIPTextEncode，以及 KSampler。
    workflows/ 下提供的範本都已標好。
    """
    for node in workflow.values():
        title = node.get("_meta", {}).get("title", "")
        if title == "Positive Prompt" and positive is not None:
            node["inputs"]["text"] = positive
        elif title == "Negative Prompt" and negative is not None:
            node["inputs"]["text"] = negative
        elif node.get("class_type") == "KSampler" and seed is not None:
            node["inputs"]["seed"] = seed
    return workflow


def main() -> int:
    parser = argparse.ArgumentParser(description="提交 workflow 到遠端 RunPod ComfyUI")
    parser.add_argument("workflow", type=Path, help="ComfyUI API 格式 workflow JSON")
    parser.add_argument("--url", default=os.environ.get("RUNPOD_COMFY_URL"),
                        help="遠端 ComfyUI base URL（預設讀 RUNPOD_COMFY_URL）")
    parser.add_argument("--out", type=Path, default=Path("./out"), help="圖片存放目錄")
    parser.add_argument("--positive", help="正向 prompt（覆寫 workflow）")
    parser.add_argument("--negative", help="負向 prompt（覆寫 workflow）")
    parser.add_argument("--seed", type=int, help="固定 seed（覆寫 workflow）")
    args = parser.parse_args()

    if not args.url:
        print("錯誤：未設定 RUNPOD_COMFY_URL，也沒給 --url", file=sys.stderr)
        return 2

    workflow = json.loads(args.workflow.read_text())
    workflow = patch_prompt(workflow, args.positive, args.negative, args.seed)

    client = ComfyClient(args.url)
    print(f"==> 提交到 {client.base_url}")
    prompt_id = client.queue_prompt(workflow)
    print(f"==> prompt_id = {prompt_id}，等待產圖…")

    entry = client.wait(prompt_id)
    args.out.mkdir(parents=True, exist_ok=True)

    saved = 0
    for node_output in entry.get("outputs", {}).values():
        for img in node_output.get("images", []):
            data = client.fetch_image(img["filename"], img.get("subfolder", ""), img["type"])
            dest = args.out / img["filename"]
            dest.write_bytes(data)
            print(f"==> 已存：{dest}")
            saved += 1

    if saved == 0:
        print("警告：沒有抓到任何圖片，檢查 workflow 是否含 SaveImage 節點。", file=sys.stderr)
        return 1
    print(f"==> 完成，共 {saved} 張。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
