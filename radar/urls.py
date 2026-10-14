import re
from urllib.parse import urlsplit

_ARXIV = re.compile(r"^arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$")


def normalize(url: str) -> str:
    """Canonical `host/path` used for dedupe and gold matching."""
    parts = urlsplit(url.strip() if "://" in url else "https://" + url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    key = f"{host}{parts.path}".rstrip("/")
    match = _ARXIV.match(key)
    if match:
        return f"arxiv.org/abs/{match.group(1)}"
    return key
