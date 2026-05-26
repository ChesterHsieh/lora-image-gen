---
name: run-lora-image-gen
description: Run / launch / train the Stacklands-style SDXL LoRA on RunPod GPU, end to end — find & grab a GPU, preflight-check setup (RunPod API key, rclone→Google Drive, dataset), train the LoRA, sync results to Google Drive, and open the remote ComfyUI to try the trained LoRA. Use when asked to train the LoRA, run a RunPod GPU job, grab/rent a GPU, check what's not set up yet, or screenshot/open the ComfyUI to test generated images.
---

# Run: lora-image-gen (Stacklands LoRA on RunPod)

Train an SDXL LoRA on a **remote RunPod GPU**, sync the result to **Google
Drive**, and drive the **remote ComfyUI** to try it. There is no local GPU
app — the "app" is a remote pod, driven from your machine by the Python
launcher under `runpod/launcher/` plus shell scripts under `runpod/scripts/`.

All paths below are relative to this unit (`lora-image-gen/`). Run Python with
the project venv `.venv/bin/python`. The launcher modules are run from
**`runpod/`** as `python -m launcher.<mod>` (that dir is the import root).

> **Hard constraints learned the hard way** (see Gotchas for why):
> - Mounting a Network Volume **requires Secure Cloud** (`RUNPOD_CLOUD_TYPE=SECURE`).
> - A volume **locks the pod to its data center**; only `storageSupport=True`
>   DCs can host a volume. So the GPU must be in stock **in the volume's DC**.
> - Use a **PyTorch-supported GPU** (sm_90 or lower: A100/H100, RTX 4090,
>   A6000/A40/L40S…). **Blackwell** (RTX PRO Blackwell / RTX 50xx, sm_120) is
>   often High-stock but the pod's PyTorch can't run on it.

## Prerequisites

```bash
# Python deps for the launcher (into the project's existing uv venv)
uv pip install -r runpod/requirements.txt --python .venv/bin/python
# Local tools used to drive the pod (macOS): ssh/tar/curl are built in.
# rclone is only needed locally once, to generate the Drive token (step 2).
brew install rclone        # macOS; skip if already installed
```

You also need, one time:
- A **RunPod API key** (Console → Settings → API Keys).
- Your **SSH public key registered** in RunPod (Console → Settings → SSH
  Public Keys) — the launcher rsync/ssh's into the pod over direct SSH.
- An **rclone Google Drive remote** configured locally (`rclone config`,
  name it `gdrive`), then encode it into `.env` (step 2).

## Setup: the `.env`

```bash
cp runpod/.env.example runpod/.env   # then fill values; .env is gitignored
```

Required keys: `RUNPOD_API_KEY`, `RUNPOD_NETWORK_VOLUME_ID`,
`RUNPOD_DATA_CENTER_ID`, `RUNPOD_GPU_TYPE` (comma-separated candidates ok),
`GDRIVE_DEST_PATH`, and the rclone config — **as base64** (multi-line configs
break `.env` line parsing):

```bash
# from lora-image-gen/runpod/ — encode your local [gdrive] block into .env
python3 - <<'PY'
import base64, configparser, io, pathlib
cp = configparser.ConfigParser(); cp.read(pathlib.Path.home()/".config/rclone/rclone.conf")
buf = io.StringIO(); buf.write("[gdrive]\n")
for k,v in cp["gdrive"].items(): buf.write(f"{k} = {v}\n")
b64 = base64.b64encode(buf.getvalue().encode()).decode()
print("RCLONE_DRIVE_CONFIG_B64=" + b64)   # paste this line into runpod/.env
PY
```
Set `RUNPOD_CLOUD_TYPE=SECURE`. Training dataset = `dataset_prep/cropped/`
(paired `*.png` + same-name `*.txt` caption; produced by
`dataset_prep/crop_cards.py` + `make_captions.py`, trigger word `stcklnd`).

## Run (agent path)

All four capabilities are driven by committed scripts. Run from `runpod/`.

### 1. Find a GPU & grab it (decide where)

```bash
cd runpod
# scan: storage-capable DCs × in-stock training GPUs (PyTorch-compatible) under budget
../.venv/bin/python -m launcher.find_gpu --env .env --max-price 1.0 --min-vram 24 --min-stock low
# pick a DC + GPU from the output, then create ONE volume there (deletes others):
../.venv/bin/python -m launcher.volume_admin --env .env ensure-single --dc <DC> --size 60 --name lora-vol
# put the printed VOLUME_ID + DC + GPU into .env (RUNPOD_NETWORK_VOLUME_ID / _DATA_CENTER_ID / _GPU_TYPE)
```

### 2. Preflight — what's not set up yet

```bash
../.venv/bin/python -m launcher.preflight --env .env --dataset ../dataset_prep/cropped
```
Checks RunPod API key + volume, rclone→Drive config, and dataset; prints a
fix hint for each `❌`. Proceed only when it says "全部就緒".

### 3. Train (launch → train → sync to Drive → recycle)

```bash
../.venv/bin/python -u -m launcher.launch --env .env --dataset ../dataset_prep/cropped --create-retries 40
```
Grabs a candidate GPU (retries while stock is Low), creates the pod, prints
**ComfyUI URL + SSH command**, uploads the dataset (tar-over-ssh), runs
kohya `sdxl_train_network` on the pod (downloads the base model into the
volume the first time only), then **syncs the LoRA + logs to Google Drive**
(`sync_outputs.sh`). On success the pod is recycled (unless `KEEP_POD=true`);
on failure the pod is **kept** so outputs on the volume can be recovered.

Poll the pod yourself by SSH (judge by which process is running, not by
fragile pgrep keywords): `ps aux | grep [s]dxl_train_network`, done marker
`/workspace/training/<concept>.run.done`, fail marker `...run.failed`.

### 4. Open the remote ComfyUI to try the LoRA (safe, not exposed)

```bash
# pass the SSH command that launch printed (NOT the proxy URL — that's unauthenticated)
bash scripts/open_ui.sh 'ssh root@<ip> -p <port>'
```
Installs/starts ComfyUI on the pod **bound to 127.0.0.1** and prints a guide:
open an SSH tunnel (`ssh ... -N -L 8188:127.0.0.1:8188 root@<ip>`), browse
`http://localhost:8188`, load the `stcklnd_lora` workflow, edit the positive
prompt (keep the `stcklnd,` trigger), Run. Output images land in
`/workspace/outputs` on the pod.

## Gotchas (battle scars from a real end-to-end run)

- **`There are no longer any instances available` while stock shows High** —
  you're querying/grabbing the wrong **cloud type**. The card was Secure-only;
  `RUNPOD_CLOUD_TYPE` defaulted to Community. Set `SECURE`.
- **CUDA error: no kernel image is available** — the GPU is **Blackwell
  (sm_120)**, newer than the pod's PyTorch (sm_90). Pick a 4090/A6000/A40/L40S.
  `find_gpu.py` already excludes Blackwell from its candidate list.
- **RunPod's `<podid>-8188.proxy.runpod.net` is unauthenticated** — anyone
  with the URL can use your GPU / read pod files. `open_ui.sh` binds ComfyUI
  to `127.0.0.1` and you reach it via SSH tunnel instead. Never leave the
  proxy port open.
- **rsync isn't on the pod base image** — upload uses **tar over ssh**. macOS
  tar also injects AppleDouble (`._*`) and ownership; the pipeline uses
  `COPYFILE_DISABLE=1 ... --exclude='._*'` locally and `tar --no-same-owner`
  on the pod.
- **kohya needs a parent dir** — `--train_data_dir` must point at the PARENT
  of a `<repeats>_<concept>/` subfolder, not at the images. `train_lora.sh`
  builds this structure with symlinks.
- **`| tee` hides the exit code** — `python ... | tee log` returns tee's 0
  even when training failed. `train_lora.sh` reads `${PIPESTATUS[0]}`.
- **Small training images** — the cropped illustrations are ~339px (upscaling
  to 1024 is a later ComfyUI step). Training uses `--enable_bucket
  --bucket_no_upscale --resolution 512` so kohya doesn't reject them.
- **Pip deps live in the container, not the volume** — a fresh pod must
  reinstall. `train_lora.sh` checks `python -c "import accelerate"` rather
  than a marker file on the volume.
- **`.env` can't hold multi-line values** — rclone config goes in as
  `RCLONE_DRIVE_CONFIG_B64` (single-line base64), decoded by `config.py`.

## Troubleshooting

- `find_gpu` prints "（空）" → loosen `--min-stock low` / raise `--max-price`
  / lower `--min-vram`. If a whole DC is dry, pick another storage-capable DC
  and rebuild the volume there with `volume_admin ensure-single`.
- `preflight` says volume DC ≠ configured DC → the pod can't mount it; rebuild
  the volume in the configured DC (or fix `RUNPOD_DATA_CENTER_ID`).
- Training stuck at `steps: 0/1500` then fails → read
  `/workspace/training/logs/<concept>/train.log` tail over SSH; it's usually
  one of the Gotchas above (CUDA/sm, image size, missing module).
- Forgot which pod is billing → `runpodctl get pod` or RunPod Console;
  recycle with `runpod.terminate_pod('<id>')` (the launcher prints the id).

## Tests

```bash
cd runpod/launcher && ../../.venv/bin/python -m pytest -q   # 57 unit tests (mocked)
```

## The drivers (committed)

- `runpod/launcher/find_gpu.py` — scan storage-capable DCs for in-stock,
  PyTorch-compatible, sub-budget GPUs.
- `runpod/launcher/volume_admin.py` — list/create/delete Network Volumes via
  REST; `ensure-single` keeps exactly one.
- `runpod/launcher/preflight.py` — readiness check (API/volume/rclone/dataset).
- `runpod/launcher/launch.py` — the end-to-end ephemeral run (grab→train→sync→recycle).
- `runpod/scripts/open_ui.sh` — install + localhost-bind ComfyUI on the pod, print tunnel guide.
- `runpod/scripts/{pod_bootstrap,train_lora,sync_outputs,setup_volume,start_comfy}.sh` — pod-side steps.
- `runpod/workflows/stcklnd_lora_ui.json` — loadable ComfyUI workflow with the LoRA pipeline.
