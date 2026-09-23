"""Read-only probes of public archives; never submits exchange orders."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import datetime as dt
import json
import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "binance_march_klines": "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-03.zip",
    "binance_august_klines": "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-08.zip",
    "binance_september_klines": "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09-22.zip",
    "binance_mark": "https://data.binance.vision/data/futures/um/monthly/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2026-03.zip",
    "binance_funding": "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-03.zip",
    "binance_aggtrades": "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2026-03-01.zip",
    "binance_exchange_info": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "cryptocompare_history": "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC&lTs=1775001599",
    "bitcoinmagazine_wp": "https://bitcoinmagazine.com/wp-json/wp/v2/posts?after=2026-03-01T00:00:00&before=2026-09-23T00:00:00&per_page=2&_fields=id,date_gmt,modified_gmt,link,title,excerpt",
    "bitcoincom_wp": "https://news.bitcoin.com/wp-json/wp/v2/posts?after=2026-03-01T00:00:00&before=2026-09-23T00:00:00&per_page=2&_fields=id,date_gmt,modified_gmt,link,title,excerpt",
    "gdelt_march": "https://api.gdeltproject.org/api/v2/doc/doc?query=bitcoin&mode=artlist&format=json&maxrecords=2&startdatetime=20260301000000&enddatetime=20260302000000",
}


def probe(item):
    name, url = item
    try:
        with requests.get(url, timeout=25, stream=True, headers={"User-Agent": "JevMeshResearch/0.1 (public historical research)"}) as response:
            result = {"name": name, "url": url, "status": response.status_code,
                      "content_length": response.headers.get("Content-Length"),
                      "content_type": response.headers.get("Content-Type")}
            if name == "binance_exchange_info" and response.status_code == 200:
                payload = response.json()
                result["symbol"] = next((s for s in payload.get("symbols", []) if s["symbol"] == "BTCUSDT"), None)
            elif ".zip" not in url:
                body = response.text
                result["sample"] = body[:1200]
                if response.status_code == 200:
                    dest = ROOT / "data" / "audit" / f"probe_{name}.txt"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(body, encoding="utf-8")
            return result
    except Exception as exc:
        return {"name": name, "url": url, "error": str(exc)}


if __name__ == "__main__":
    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(probe, SOURCES.items()):
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    target = ROOT / "data" / "audit" / "source_probes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"queried_at": dt.datetime.now(dt.timezone.utc).isoformat(), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
