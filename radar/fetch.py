import json

import requests
import trafilatura

UA = {"User-Agent": "Mozilla/5.0 (compatible; embodied-radar/0.1)"}
THIN_CHARS = 500


def fetch(url: str) -> dict:
    """Main text and declared publication date; `status` is ok / thin / timeout / http_4xx / http_5xx / error."""
    try:
        resp = requests.get(url, headers=UA, timeout=30)
    except requests.Timeout:
        return {"status": "timeout", "chars": 0, "date": None}
    except requests.RequestException as exc:
        return {"status": "error", "chars": 0, "date": None, "detail": type(exc).__name__}
    if resp.status_code >= 400:
        return {"status": f"http_{resp.status_code // 100}xx", "chars": 0, "date": None, "code": resp.status_code}
    doc = trafilatura.extract(resp.text, url=resp.url, output_format="json", with_metadata=True)
    data = json.loads(doc) if doc else {}
    chars = len(data.get("text") or "")
    return {"status": "ok" if chars >= THIN_CHARS else "thin", "chars": chars, "date": data.get("date")}
