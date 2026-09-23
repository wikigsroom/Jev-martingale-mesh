"""Public data acquisition, provenance, UTC alignment, and fail-closed coverage."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import calendar
import datetime as dt
import hashlib
import html
import io
import json
import re
import time
import zipfile

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = "https://data.binance.vision/data/futures/um"
UTC = dt.timezone.utc


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def get(url, attempts=3, timeout=30):
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "JevMeshResearch/0.1"})
            if response.status_code in (404, 401, 403):
                return response
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt + 1 == attempts:
                raise
            time.sleep(1 + attempt)


def archive_job(kind, period, interval="1m"):
    frequency = "monthly" if len(period) == 7 else "daily"
    if kind in ("klines", "markPriceKlines"):
        relative = f"{frequency}/{kind}/BTCUSDT/{interval}/BTCUSDT-{interval}-{period}.zip"
    else:
        relative = f"{frequency}/{kind}/BTCUSDT/BTCUSDT-{kind}-{period}.zip"
    return {"kind": kind, "period": period, "url": f"{ARCHIVE}/{relative}",
            "path": ROOT / "data" / "raw" / "binance" / relative}


def download_archive(job):
    path = job["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {k: str(v) for k, v in job.items()}
    try:
        if not path.exists():
            response = get(job["url"])
            receipt["status"] = response.status_code
            if response.status_code != 200:
                return receipt
            temporary = path.with_suffix(".partial")
            temporary.write_bytes(response.content)
            temporary.replace(path)
        receipt.update(status=200, size=path.stat().st_size,
                       sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        checksum_path = path.with_suffix(path.suffix + ".CHECKSUM")
        if not checksum_path.exists():
            checksum = get(job["url"] + ".CHECKSUM", attempts=2)
            if checksum.status_code == 200:
                checksum_path.write_text(checksum.text, encoding="utf-8")
        if checksum_path.exists():
            expected = checksum_path.read_text(encoding="utf-8").split()[0]
            receipt["checksum_valid"] = expected == receipt["sha256"]
            if not receipt["checksum_valid"]:
                raise ValueError(f"Checksum mismatch: {path}")
        else:
            receipt["checksum_valid"] = None
    except Exception as exc:
        receipt["error"] = str(exc)
    return receipt


def download_market(end_date="2026-09-22"):
    end = dt.date.fromisoformat(end_date)
    jobs = []
    for month in range(3, end.month + 1):
        period = f"2026-{month:02d}"
        complete = month < end.month or end.day == calendar.monthrange(2026, month)[1]
        periods = [period] if complete else [f"{period}-{day:02d}" for day in range(1, end.day + 1)]
        for kind in ("klines", "markPriceKlines"):
            jobs.extend(archive_job(kind, p) for p in periods)
        # Funding history is published in monthly packages; current month may be unavailable.
        jobs.append(archive_job("fundingRate", period))
    receipts = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(download_archive, j) for j in jobs]):
            item = future.result()
            receipts.append(item)
            print(json.dumps({k: item.get(k) for k in ("kind", "period", "status", "size", "checksum_valid", "error")}), flush=True)
    write_json(ROOT / "data/audit/market_downloads.json", {"queried_at": dt.datetime.now(UTC).isoformat(), "receipts": receipts})
    return receipts


def read_archive_csv(path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        with archive.open(name) as handle:
            frame = pd.read_csv(handle, header=None)
    # Futures archives use headers, but some historical objects do not.
    numeric_time = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
    frame = frame[numeric_time.notna()].copy()
    frame[frame.columns[0]] = pd.to_numeric(frame.iloc[:, 0])
    return frame


def prepare_market():
    files = ROOT / "data/raw/binance"
    result = {}
    for kind in ("klines", "markPriceKlines"):
        frames = []
        for path in sorted(files.glob(f"*/{kind}/BTCUSDT/1m/*.zip")):
            frame = read_archive_csv(path)
            frame = frame.iloc[:, :6]
            frame.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            for col in frame:
                frame[col] = pd.to_numeric(frame[col], errors="raise")
            frames.append(frame)
        if not frames:
            raise ValueError(f"No downloaded {kind}")
        frame = pd.concat(frames).sort_values("timestamp").drop_duplicates("timestamp")
        frame["timestamp"] = frame.timestamp.astype("int64")
        frame = frame.set_index("timestamp")
        if frame.index.max() > 10**14:
            raise ValueError("Unexpected microsecond futures timestamps")
        result[kind] = frame
    joined = result["klines"].join(result["markPriceKlines"].add_prefix("mark_"), how="inner")
    # Never bridge missing dates. Main study stops at the first missing minute.
    wanted_start = int(dt.datetime(2026, 3, 1, tzinfo=UTC).timestamp() * 1000)
    joined = joined.loc[joined.index >= wanted_start]
    if joined.index[0] != wanted_start:
        raise ValueError("March 1 start is missing")
    differences = np.diff(joined.index.to_numpy())
    gaps = np.where(differences != 60_000)[0]
    gap_records = [{"after": int(joined.index[i]), "before": int(joined.index[i + 1])} for i in gaps]
    if len(gaps):
        joined = joined.iloc[:int(gaps[0]) + 1]
    funding_rows = []
    for path in sorted(files.glob("*/fundingRate/BTCUSDT/*.zip")):
        f = read_archive_csv(path)
        if f.shape[1] < 3:
            raise ValueError("Unexpected funding schema")
        for row in f.itertuples(index=False, name=None):
            funding_rows.append((int(row[0]), float(row[2])))
    rest_file = files / "funding_september_rest.json"
    if rest_file.exists():
        for row in json.loads(rest_file.read_text(encoding="utf-8")):
            funding_rows.append((int(row["fundingTime"]), float(row["fundingRate"])))
    funding = pd.DataFrame(funding_rows, columns=["timestamp", "rate"]).drop_duplicates("timestamp").sort_values("timestamp")
    out = ROOT / "data/processed"
    out.mkdir(parents=True, exist_ok=True)
    joined.to_pickle(out / "market_1m.pkl")
    funding.to_pickle(out / "funding.pkl")
    audit = {"bars": len(joined), "start": pd.to_datetime(joined.index[0], unit="ms", utc=True).isoformat(),
             "end_exclusive": pd.to_datetime(joined.index[-1] + 60_000, unit="ms", utc=True).isoformat(),
             "gaps": gap_records, "funding_records": len(funding),
             "funding_last": pd.to_datetime(funding.timestamp.max(), unit="ms", utc=True).isoformat() if len(funding) else None,
             "price_min": float(joined.low.min()), "price_max": float(joined.high.max()),
             "market_sha256": hashlib.sha256((out / "market_1m.pkl").read_bytes()).hexdigest()}
    write_json(ROOT / "data/audit/market_coverage.json", audit)
    print(json.dumps(audit, indent=2), flush=True)
    return joined, funding


def strip_html(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def download_news():
    base = "https://bitcoinmagazine.com/wp-json/wp/v2/posts"
    query = "?after=2026-03-01T00:00:00&before=2026-09-23T00:00:00&per_page=100&_fields=id,date_gmt,modified_gmt,link,title,excerpt"
    out = ROOT / "data/raw/news/bitcoinmagazine"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        path = out / f"page_{page:03d}.json"
        if path.exists():
            bundle = json.loads(path.read_text(encoding="utf-8"))
        else:
            response = get(base + query + f"&page={page}")
            response.raise_for_status()
            bundle = {"url": response.url, "retrieved_at": dt.datetime.now(UTC).isoformat(),
                      "total_pages": int(response.headers.get("X-WP-TotalPages", 1)), "items": response.json()}
            write_json(path, bundle)
        total_pages = bundle["total_pages"]
        for item in bundle["items"]:
            published = pd.Timestamp(item["date_gmt"], tz="UTC")
            modified = pd.Timestamp(item["modified_gmt"], tz="UTC")
            # Revisions are not backdated: latest available text is gated at last modification.
            available = max(published, modified)
            rows.append({"event_id": f"bitcoinmagazine:{item['id']}", "source": "bitcoinmagazine",
                         "url": item["link"], "title": strip_html(item["title"]["rendered"]),
                         "excerpt": strip_html(item.get("excerpt", {}).get("rendered", "")),
                         "published_ms": int(published.timestamp() * 1000),
                         "modified_ms": int(modified.timestamp() * 1000),
                         "available_ms": int(available.timestamp() * 1000),
                         "availability_basis": "max(published,modified); historic first-receipt unknown"})
        print(f"news page {page}/{total_pages}, records={len(rows)}", flush=True)
        page += 1
        time.sleep(0.35)
    frame = pd.DataFrame(rows).drop_duplicates("event_id").sort_values("available_ms")
    frame = frame[frame.available_ms < int(pd.Timestamp("2026-09-23", tz="UTC").timestamp() * 1000)]
    target = ROOT / "data/processed/news.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_json(target, orient="records", lines=True, force_ascii=False)
    audit = {"records": len(frame), "pages": total_pages, "source": base,
             "monthly_published_counts": frame.groupby(pd.to_datetime(frame.published_ms, unit="ms", utc=True).dt.strftime("%Y-%m")).size().to_dict(),
             "timestamp_limitation": "Publisher timestamps, not observed live arrival. Last-modified gating is conservative but does not prove historical availability or unchanged text.",
             "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    write_json(ROOT / "data/audit/news_coverage.json", audit)
    print(json.dumps(audit, indent=2), flush=True)
    return frame
