import os
import time

import requests
from ddgs import DDGS

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")


def search(query: str, backend: str, max_results: int) -> list[dict]:
    """One query against the last week; returns [{url, title, snippet}]."""
    if backend == "searxng":
        resp = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "time_range": "week", "language": "en"},
            timeout=60,
        )
        resp.raise_for_status()
        rows = resp.json().get("results", [])
        return [
            {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("content", "")}
            for r in rows
        ][:max_results]
    if backend == "ddgs":
        rows = DDGS().text(query, timelimit="w", max_results=max_results) or []
        return [{"url": r.get("href", ""), "title": r.get("title", ""), "snippet": r.get("body", "")} for r in rows]
    raise ValueError(f"unknown backend {backend!r}")


def search_with_retry(query: str, backend: str, max_results: int, retries: int) -> dict:
    """Never raises: the error type is part of the measurement."""
    error = None
    for attempt in range(retries + 1):
        try:
            return {"results": search(query, backend, max_results), "error": None, "attempts": attempt + 1}
        except Exception as exc:
            error = type(exc).__name__
            time.sleep(2 ** attempt)
    return {"results": [], "error": error, "attempts": retries + 1}
