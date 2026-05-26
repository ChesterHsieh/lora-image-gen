"""讓測試能以 `launcher.xxx` import 套件。"""
from __future__ import annotations

import sys
from pathlib import Path

# runpod/（launcher/ 的上層）放上 sys.path，讓 `import launcher.config` 可用。
RUNPOD_DIR = Path(__file__).resolve().parents[2]
if str(RUNPOD_DIR) not in sys.path:
    sys.path.insert(0, str(RUNPOD_DIR))
