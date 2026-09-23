from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import requests
import subprocess

paths = [
    Path("D:/dev/comfyui/ComfyUI_windows_portable_nvidia_0_30_0/ComfyUI_windows_portable/python_embeded/python.exe"),
    Path("D:/dev/comfyui/1_17_2/python_embeded/python.exe"),
]
for path in paths:
    if path.exists():
        try:
            r = subprocess.run([str(path), "-c", "import torch,transformers; print(torch.__version__,torch.cuda.is_available(),transformers.__version__)"], capture_output=True, text=True, timeout=50)
            print(json.dumps({"python": str(path), "stdout": r.stdout, "stderr": r.stderr[-1000:]}), flush=True)
        except Exception as exc:
            print(str(exc), flush=True)

urls = [
    "https://fapi1.binance.com/fapi/v1/time",
    "https://www.binance.com/fapi/v1/time",
    "https://data.binance.vision/data/futures/um/daily/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-09-01.zip",
    "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1s/BTCUSDT-1s-2026-03.zip",
]
def probe(url):
    try:
        r = requests.get(url, timeout=10)
        return {"url": url, "status": r.status_code, "sample": r.text[:100] if ".zip" not in url else len(r.content)}
    except Exception as exc:
        return {"url": url, "error": str(exc)[:100]}
with ThreadPoolExecutor(4) as pool:
    for result in pool.map(probe, urls):
        print(json.dumps(result), flush=True)
