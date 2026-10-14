import calendar
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

WINDOW_DAYS = 7


@dataclass
class Context:
    """What a channel needs from the run: the date, persistent state, and injectable I/O."""

    run_date: date
    state_dir: Path
    get: Callable  # url -> response with status_code, content, text, json()
    page_date: Callable[[str], str | None]
    first_run_date_fetch_max: int = 12

    @property
    def window_start(self) -> date:
        return self.run_date - timedelta(days=WINDOW_DAYS)

    def in_window(self, day: date | None) -> bool:
        return day is not None and self.window_start <= day <= self.run_date


def to_date(value) -> date | None:
    """ISO strings, struct_time (feedparser) or None."""
    if value is None:
        return None
    if hasattr(value, "tm_year"):
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc).date()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def state_file(ctx: Context, key: str) -> Path:
    """`key` is `<kind>/<id>`, e.g. `pages/sunday`."""
    kind, _, name = key.rpartition("/")
    return ctx.state_dir / kind / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.json"


def load_state(ctx: Context, key: str) -> set[str] | None:
    """None means the channel has never run: its first run sets the baseline."""
    path = state_file(ctx, key)
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None


def save_state(ctx: Context, key: str, keys) -> None:
    path = state_file(ctx, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(set(keys)), ensure_ascii=False, indent=0), encoding="utf-8")


def finish(fetched: int, new: list[dict], baseline: int, **extra) -> dict:
    status = "failed" if fetched == 0 else ("updated" if new else "no_update")
    return {"status": status, "fetched": fetched, "new": new, "baseline": baseline, "error": None, **extra}


def failed(error: str) -> dict:
    return {"status": "failed", "fetched": 0, "new": [], "baseline": 0, "error": error}
