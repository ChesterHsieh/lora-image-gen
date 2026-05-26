---
name: check-dataset
description: Check whether the LoRA training dataset is complete and good enough to train — verifies paired image+caption files exist, warns on too-few images, orphan files (image without .txt or .txt without image), empty captions, and captions whose first word isn't the trigger. Use when asked to check/validate the training data, see if the dataset is ready, or before launching a training run. The same check also runs as step [4/4] of /run-lora-image-gen's preflight.
---

# Check: training dataset readiness

Verify the prepared dataset (cropped images + same-name `.txt` captions) is
complete and good enough to train a style LoRA. This wraps the
`launcher.check_dataset` module — the *same* logic preflight uses for its
`[4/4] 訓練資料集` step, so running this stand-alone and running preflight
agree.

Run the project venv from `runpod/`:

```bash
cd runpod
../.venv/bin/python -m launcher.check_dataset --dataset ../dataset_prep/cropped
```

Optional flags:
- `--trigger <word>` — the trigger word each caption should start with
  (default `mystyle`; match whatever you passed to `make_captions.py`).
- `--min-images <n>` — warn when fewer than N paired images (default `15`).

## What it checks

**Hard errors (exit 1 — don't train):**
- Dataset folder missing / not a directory.
- No paired image+`.txt` at all.
- A caption file that is **empty**.

**Warnings (exit 0 — usable, but fix for better results):**
- Fewer paired images than `--min-images` (style LoRAs need enough variety).
- An image with no same-name `.txt` (silently ignored by training → list shown).
- A `.txt` with no matching image (orphan, can delete → list shown).
- A caption whose first word isn't the trigger (dilutes style binding → list shown).

Offending filenames are printed (up to 10 each) so the fix is obvious: run
`dataset_prep/crop_cards.py` to crop, `make_captions.py` to (re)caption.

## How to interpret for the user

- **exit 0, no warnings** → "資料集完備，可以訓練。"
- **exit 0, warnings** → usable; relay each warning and recommend fixing before
  spending GPU time.
- **exit 1** → not ready; relay the ❌ lines and the fix hint, do **not**
  proceed to launch.

## Relation to the full run

This is the dataset slice of [/run-lora-image-gen](../run-lora-image-gen/SKILL.md).
For the end-to-end run (find GPU → train → sync → ComfyUI), use that skill;
preflight there calls this same check, so you don't need to run both — this
skill is for checking the data *on its own* without touching RunPod.
