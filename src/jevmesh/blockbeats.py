"""Client for the public BlockBeats newsflash feed."""
from datetime import datetime, timezone
import hashlib
import html
import hmac
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests

SITE_URL = "https://www.theblockbeats.info/newsflash"
PAGE_LIMIT = 50
USER_AGENT = "JevMeshHistoricalReplay/1.0"


def _clean_text(value):
    value = re.sub(r"<br\s*/?>|</p\s*>|</div\s*>", " ", str(value or ""), flags=re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def normalize_item(item, start_seconds, end_seconds):
    try:
        added_at = int(item["add_time"])
        item_id = int(item["id"])
    except (KeyError, TypeError, ValueError):
        return None
    if not start_seconds <= added_at < end_seconds:
        return None
    article_id = item.get("article_id")
    timestamp_ms = added_at * 1000
    url = str(item.get("url") or "").strip()
    if not url and article_id:
        url = f"https://www.theblockbeats.info/flash/{article_id}"
    try:
        is_premium = int(item.get("is_premium", 0)) != 0
    except (TypeError, ValueError):
        is_premium = False
    return {
        "event_id": f"blockbeats:{item_id}",
        "source": "blockbeats",
        "url": url,
        "title": _clean_text(item.get("title")),
        "excerpt": _clean_text(item.get("content") or item.get("abstract")),
        "published_ms": timestamp_ms,
        "modified_ms": timestamp_ms,
        "available_ms": timestamp_ms,
        "availability_basis": "BlockBeats newsflash add_time; historical first-receipt time unavailable",
        "article_id": article_id,
        "is_premium": is_premium,
    }


def _client_config(session):
    response = session.get(SITE_URL, timeout=(15, 45))
    response.raise_for_status()
    page = response.text
    base_match = re.search(r'baseURL:"([^"]+)"', page)
    script_paths = re.findall(r'<script\b[^>]*\bsrc=["\']([^"\']+)', page, flags=re.I)
    app_path = next((path for path in script_paths if re.search(r"/_nuxt/app\.[^/]+\.js", path)), None)
    if not base_match or not app_path:
        raise RuntimeError("Could not locate the BlockBeats public API configuration")
    app_response = session.get(urljoin(SITE_URL, app_path), timeout=(15, 45))
    app_response.raise_for_status()
    app_script = app_response.text
    key_match = re.search(r'APP_KEY:\s*"([^"]+)"', app_script)
    secret_match = re.search(r'APP_SECRET:\s*"([^"]+)"', app_script)
    if not key_match or not secret_match:
        raise RuntimeError("Could not locate the BlockBeats web client signing configuration")
    base_url = base_match.group(1).replace(r"\u002F", "/").rstrip("/")
    return base_url, key_match.group(1), secret_match.group(1)


def _canonical_query(params):
    return "&".join(
        f"{key}={unquote(str(params[key]))}" for key in sorted(params)
    )


def _signed_headers(method, path, params, app_key, app_secret, timestamp_ms, nonce):
    query = _canonical_query(params) if method.upper() == "GET" else ""
    message = f"{method.upper()}|{path}|{timestamp_ms}|{nonce}|{query}"
    signature = hmac.new(app_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "User-Agent": USER_AGENT,
        "Referer": SITE_URL,
        "X-App-Key": app_key,
        "X-Timestamp": str(timestamp_ms),
        "X-Nonce": nonce,
        "X-Signature": signature,
        "X-Encrypt": "false",
    }


def _request_page(session, base_url, app_key, app_secret, page, cursor, limit):
    params = {
        "detective": "-2",
        "end_time": str(cursor),
        "ios": "-2",
        "limit": str(limit),
        "page": str(page),
    }
    path = urlsplit(base_url).path.rstrip("/") + "/newsflash/list"
    url = base_url + "/newsflash/list?" + "&".join(
        f"{quote(key)}={quote(params[key])}" for key in sorted(params)
    )
    headers = _signed_headers(method="GET", path=path, params=params, app_key=app_key,
                              app_secret=app_secret,
                              timestamp_ms=int(time.time() * 1000), nonce=secrets.token_hex(8))
    response = session.get(url, headers=headers, timeout=(15, 60))
    response.raise_for_status()
    if response.headers.get("X-Encrypted", "false").lower() == "true":
        raise RuntimeError("BlockBeats returned an encrypted payload despite requesting plaintext")
    payload = response.json()
    if payload.get("code") != 0 or not isinstance(payload.get("data", {}).get("list"), list):
        raise RuntimeError(f"BlockBeats newsflash request failed: {payload.get('message') or payload.get('msg')}")
    return payload


def _write_json_atomic(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (2 ** attempt))


def fetch_history(start_utc, end_utc, raw_dir, pause_seconds=0.15, progress=None):
    start_time = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    end_time = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)
    start_seconds = int(start_time.timestamp())
    end_seconds = int(end_time.timestamp())
    if start_seconds >= end_seconds:
        raise ValueError("BlockBeats history start must precede end")
    raw_dir = Path(raw_dir)
    pages_dir = raw_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    state_path = raw_dir / "fetch_state.json"
    request_spec = {"start": start_utc, "end_exclusive": end_utc, "limit": PAGE_LIMIT,
                    "ios": -2, "detective": -2}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("request") != request_spec:
            raise ValueError("Existing BlockBeats page cache belongs to a different date range")
    else:
        if any(pages_dir.glob("page_*.json")):
            raise ValueError("BlockBeats page cache exists without its checkpoint")
        state = {"request": request_spec, "next_page": 1, "cursor": end_seconds,
                 "complete": False, "pages_fetched": 0}
        _write_json_atomic(state_path, state)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    base_url, app_key, app_secret = _client_config(session)
    while not state["complete"]:
        page = int(state["next_page"])
        cursor = int(state["cursor"])
        page_path = pages_dir / f"page_{page:06d}.json"
        if page_path.exists():
            payload = json.loads(page_path.read_text(encoding="utf-8"))
        else:
            for attempt in range(5):
                try:
                    payload = _request_page(session, base_url, app_key, app_secret, page, cursor, PAGE_LIMIT)
                    break
                except (requests.RequestException, ValueError, RuntimeError):
                    if attempt == 4:
                        raise
                    time.sleep(min(2 ** attempt, 20))
            _write_json_atomic(page_path, payload)
        items = payload["data"]["list"]
        state["pages_fetched"] = page
        state["next_page"] = page + 1
        if not items:
            state["complete"] = True
        else:
            times = [int(item["add_time"]) for item in items if item.get("add_time") is not None]
            if not times:
                raise RuntimeError(f"BlockBeats page {page} has no usable timestamps")
            oldest = min(times)
            if oldest >= cursor:
                raise RuntimeError(f"BlockBeats pagination made no time progress on page {page}")
            state["cursor"] = oldest
            if oldest < start_seconds:
                state["complete"] = True
        _write_json_atomic(state_path, state)
        if progress and (page == 1 or page % 25 == 0 or state["complete"]):
            progress({"page": page, "cursor": state["cursor"], "complete": state["complete"]})
        if not state["complete"] and pause_seconds > 0:
            time.sleep(pause_seconds)
    rows = {}
    for page in range(1, int(state["pages_fetched"]) + 1):
        page_path = pages_dir / f"page_{page:06d}.json"
        payload = json.loads(page_path.read_text(encoding="utf-8"))
        for item in payload["data"]["list"]:
            row = normalize_item(item, start_seconds, end_seconds)
            if row is not None:
                rows[row["event_id"]] = row
    result = sorted(rows.values(), key=lambda row: (row["available_ms"], row["event_id"]))
    audit = {
        "source": "BlockBeats public newsflash feed",
        "source_url": SITE_URL,
        "api_url": base_url + "/newsflash/list",
        "start": start_utc,
        "end_exclusive": end_utc,
        "page_limit": PAGE_LIMIT,
        "pages_fetched": int(state["pages_fetched"]),
        "events": len(result),
        "availability_basis": "BlockBeats newsflash add_time; historical first-receipt time unavailable",
        "content_basis": "Public newsflash API snapshot; historical content snapshots unavailable",
        "premium_events": sum(row["is_premium"] for row in result),
        "event_sha256": hashlib.sha256(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
    }
    return result, audit
