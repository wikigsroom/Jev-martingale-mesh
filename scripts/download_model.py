from pathlib import Path
import json
import os
import time
root = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from huggingface_hub import snapshot_download
path = snapshot_download("C-Tianyu/NanoJev", revision="unified-games-v1",
                         local_dir=root / "models/nanojev-unified",
                         allow_patterns=["best.safetensors", "config.json", "backbone_config/*", "tokenizer/*", "source/scripts/train_toy_decisions.py", "LICENSE", "README.md"],
                         max_workers=3, token=False)
print(json.dumps({"checkpoint": path}), flush=True)
