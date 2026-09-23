from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jevmesh.data import archive_job, download_archive, prepare_market, write_json, ROOT
jobs = [archive_job(k, "2026-06-29") for k in ("klines", "markPriceKlines")]
with ThreadPoolExecutor(2) as pool:
    receipts = list(pool.map(download_archive, jobs))
write_json(ROOT / "data/audit/market_repairs.json", receipts)
print(json.dumps(receipts, default=str, indent=2), flush=True)
prepare_market()
