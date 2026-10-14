import re
from concurrent.futures import ThreadPoolExecutor
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
    resp = ctx.get(channel["url"])
    if resp.status_code >= 400:
        return failed(f"http_{resp.status_code}")
    links = extract_links(resp.text, channel["url"], channel.get("link_pattern") or default_pattern(channel["url"]))
    snapshot = load_state(ctx, key)

    new, baseline, capped = [], 0, 0
    if snapshot is not None:
        for link_key, url in links.items():
            if link_key in snapshot:
                baseline += 1
            else:
                new.append({"url": url, "key": link_key, "title": "", "page_date": None})
    else:
        # First run: only a link whose own page carries a date inside the window counts as new.
        ordered = list(links.items())
        to_fetch = ordered[: ctx.first_run_date_fetch_max]
        capped = len(ordered) - len(to_fetch)
        with ThreadPoolExecutor(max_workers=8) as pool:
            dates = list(pool.map(lambda item: ctx.page_date(item[1]), to_fetch))
        for (link_key, url), page_date in zip(to_fetch, dates):
            if ctx.in_window(to_date(page_date)):
                new.append({"url": url, "key": link_key, "title": "", "page_date": page_date})
            else:
                baseline += 1
        baseline += capped

    save_state(ctx, key, links)
    return finish(len(links), new, baseline, first_run=snapshot is None,
                  snapshot=state_file(ctx, key).as_posix(), date_fetch_capped=capped)
