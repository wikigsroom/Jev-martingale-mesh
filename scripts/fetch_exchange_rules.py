"""Read-only public API snapshots. Never uses account credentials or private APIs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import datetime as dt
import requests
import argparse

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--proxy', default=None, help='Optional HTTP proxy; no system proxy settings are modified')
args = parser.parse_args()
proxy = {'http': args.proxy, 'https': args.proxy} if args.proxy else None
sources = {
    "exchange_info": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "public_brackets": "https://www.binance.com/bapi/futures/v1/friendly/future/common/brackets",
    "funding_september": "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime=1788220800000&endTime=1790035200000&limit=1000",
}
def fetch(pair):
    name, url = pair
    try:
        r = requests.get(url, proxies=proxy, timeout=20)
        r.raise_for_status()
        data = r.json()
        out = root / f"data/audit/{name}.json"
        out.write_text(json.dumps({"url": url, "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "data": data}, indent=2), encoding="utf-8")
        if name == 'funding_september':
            (root / 'data/raw/binance/funding_september_rest.json').write_text(json.dumps(data), encoding='utf-8')
        if name == "exchange_info":
            print(json.dumps(next(s for s in data["symbols"] if s["symbol"] == "BTCUSDT")), flush=True)
        else:
            print(name, str(data)[:500], flush=True)
    except Exception as exc:
        print(name, str(exc)[:150], flush=True)
with ThreadPoolExecutor(3) as pool:
    list(pool.map(fetch, sources.items()))
