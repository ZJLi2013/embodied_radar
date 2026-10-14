import feedparser

from radar.channels.common import Context, failed, finish, load_state, save_state, to_date
from radar.urls import normalize


def collect(channel: dict, ctx: Context, key: str) -> dict:
    resp = ctx.get(channel["url"])
    if resp.status_code >= 400:
        return failed(f"http_{resp.status_code}")
    entries = [e for e in feedparser.parse(resp.content).entries if e.get("link")]
    state = load_state(ctx, key)
    seen = None if state is None else set(state)

    new, baseline, keys = [], 0, []
    for e in entries:
        url_key = normalize(e["link"])
        keys.append(url_key)
        published = to_date(e.get("published_parsed") or e.get("updated_parsed"))
        # Undated entries have no window: they are new only once a previous run has seen the feed.
        is_new = ctx.in_window(published) if published else (seen is not None and url_key not in seen)
        if is_new:
            new.append({"url": e["link"], "key": url_key, "title": e.get("title", ""),
                        "published": published.isoformat() if published else None})
        else:
            baseline += 1
    save_state(ctx, key, sorted(set(keys)))
    return finish(len(entries), new, baseline, first_run=seen is None)
