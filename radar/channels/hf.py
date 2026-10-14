from radar.channels.common import Context, failed, finish, to_date
from radar.urls import normalize

API = "https://huggingface.co/api"
PAGES = {"models": "https://huggingface.co/{id}", "datasets": "https://huggingface.co/datasets/{id}"}


def collect(channel: dict, ctx: Context, key: str) -> dict:
    org = channel["org"]
    fetched, new = 0, []
    for kind in PAGES:
        resp = ctx.get(f"{API}/{kind}?author={org}&sort=createdAt&direction=-1&limit=30")
        if resp.status_code >= 400:
            return failed(f"{kind}_http_{resp.status_code}")
        items = resp.json()
        if not isinstance(items, list):
            return failed(f"{kind}_unexpected_payload")
        fetched += len(items)
        for item in items:
            created = to_date(item.get("createdAt") or item.get("lastModified"))
            if ctx.in_window(created):
                url = PAGES[kind].format(id=item["id"])
                new.append({"url": url, "key": normalize(url), "title": item["id"], "kind": kind,
                            "published": created.isoformat()})
    return finish(fetched, new, fetched - len(new))
