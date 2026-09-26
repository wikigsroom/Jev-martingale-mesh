import hashlib
import hmac

from jevmesh.blockbeats import _canonical_query, _signed_headers, normalize_item


def test_normalize_public_newsflash_item():
    row = normalize_item({
        "id": 12,
        "article_id": 34,
        "add_time": 1772323200,
        "title": "<b>比特币</b> 消息",
        "content": "<p>BlockBeats&nbsp;快讯</p>",
        "is_premium": "0",
    }, 1772323200, 1772409600)

    assert row["event_id"] == "blockbeats:12"
    assert row["url"] == "https://www.theblockbeats.info/flash/34"
    assert row["title"] == "比特币 消息"
    assert row["excerpt"] == "BlockBeats 快讯"
    assert row["available_ms"] == 1772323200000
    assert row["is_premium"] is False


def test_normalize_excludes_out_of_range_newsflash_items():
    row = normalize_item({"id": 1, "add_time": 1772409600}, 1772323200, 1772409600)

    assert row is None


def test_signed_headers_match_canonical_query_signature():
    params = {"page": "2", "limit": "50", "ios": "-2", "end_time": "1772323200", "detective": "-2"}
    timestamp = "1790000000000"
    nonce = "0123456789abcdef"
    secret = "example-secret"
    headers = _signed_headers("GET", "/v2/newsflash/list", params, "example-app", secret,
                              timestamp, nonce)
    canonical = "&".join(f"{key}={params[key]}" for key in sorted(params))
    message = f"GET|/v2/newsflash/list|{timestamp}|{nonce}|{canonical}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    assert _canonical_query(params) == canonical
    assert headers["X-Signature"] == expected
    assert headers["X-Encrypt"] == "false"
