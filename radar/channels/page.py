import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urljoin, urlsplit

from radar.channels.common import Context, failed, finish, load_state, save_state, state_file, to_date
from radar.urls import normalize

# Minified pages mix quoted and unquoted attributes.
_HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""", re.IGNORECASE)


def default_pattern(url: str) -> str:
    """Same site, same path prefix as the list page."""
    parts = urlsplit(url)
    return re.escape(f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}/")


def extract_links(html: str, base: str, pattern: str) -> dict[str, str]:
    """{normalized key: absolute url} for links matching `pattern`, excluding the list page itself."""
    regex, own = re.compile(pattern), normalize(base)
    links = {}
    for groups in _HREF.findall(html):
        url = urljoin(base, "".join(groups).replace("&amp;", "&"))
        key = normalize(url)
        if regex.match(url) and key != own:
            links.setdefault(key, url)
    return links


def collect(channel: dict, ctx: Context, key: str) -> dict:
    """State is {"seen": [key], "pending": {key: {"url", "since"}}}.

    `seen` keeps every judged link, also after it leaves the page. `pending` holds links whose date has
    not been fetched yet; one counts as new once fetched if its date falls in [since, run_date].
    """
    resp = ctx.get(channel["url"])
    if resp.status_code >= 400:
        return failed(f"http_{resp.status_code}")
    links = extract_links(resp.text, channel["url"], channel.get("link_pattern") or default_pattern(channel["url"]))
    if not links:
        return failed("no_links")
    state = load_state(ctx, key)
    if state is not None and not isinstance(state, dict):
        raise ValueError(f"page state {key} is not an object")
    first_run = state is None
    seen = set(state["seen"]) if state else set()
    pending = dict(state["pending"]) if state else {}
    since = ctx.window_start.isoformat()

    new = []
    for link_key, url in links.items():
        if link_key in seen or link_key in pending:
            continue
        if first_run:
            pending[link_key] = {"url": url, "since": since}
        else:
            new.append({"url": url, "key": link_key, "title": "", "page_date": None})
            seen.add(link_key)

    queue = sorted(pending.items(), key=lambda kv: kv[1]["since"])
    queue = queue[: ctx.take_date_fetches(len(queue))]
    with ThreadPoolExecutor(max_workers=8) as pool:
        dates = list(pool.map(lambda kv: ctx.page_date(kv[1]["url"]), queue))
    for (link_key, entry), page_date in zip(queue, dates):
        del pending[link_key]
        seen.add(link_key)
        day = to_date(page_date)
        if day and date.fromisoformat(entry["since"]) <= day <= ctx.run_date:
            new.append({"url": entry["url"], "key": link_key, "title": "", "page_date": page_date,
                        "late": entry["since"] != since})

    save_state(ctx, key, {"seen": sorted(seen), "pending": pending})
    new_keys = {i["key"] for i in new}
    baseline = sum(k in seen and k not in new_keys for k in links)
    return finish(len(links), new, baseline, first_run=first_run, snapshot=state_file(ctx, key).as_posix(),
                  links=sorted(links), pending=len(pending), date_fetches=len(queue))

