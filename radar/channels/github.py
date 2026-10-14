from radar.channels.common import Context, failed, finish, to_date
from radar.urls import normalize

API = "https://api.github.com"


def collect(channel: dict, ctx: Context, key: str) -> dict:
    org = channel["org"]
    resp = ctx.get(f"{API}/orgs/{org}/repos?sort=created&direction=desc&per_page=30")
    if resp.status_code == 404:
        resp = ctx.get(f"{API}/users/{org}/repos?sort=created&direction=desc&per_page=30")
    if resp.status_code >= 400:
        return failed(f"http_{resp.status_code}")
    repos = resp.json()
    if not isinstance(repos, list):
        return failed("unexpected_payload")

    new = [
        {"url": r["html_url"], "key": normalize(r["html_url"]), "title": r["name"],
         "description": r.get("description") or "", "published": r["created_at"][:10]}
        for r in repos if ctx.in_window(to_date(r.get("created_at")))
    ]
    return finish(len(repos), new, len(repos) - len(new))
