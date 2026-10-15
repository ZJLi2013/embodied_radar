"""Scope judgment: one DeepSeek call per candidate, on its fields and the text fetched from its page."""

import argparse
import copy
import gzip
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import trafilatura

from radar import config

TEXT_LIMIT = 6000
DECISIONS = {"keep", "reject", "needs_review"}
OUTPUT_KEYS = {"decision", "reason", "evidence_quote", "team"}
HTTP_RETRY_BUDGET = 3
# requests' read timeout counts gaps between bytes; DeepSeek keep-alive bytes defeat it, so cap each call's wall time.
CALL_DEADLINE_SECONDS = 120
UA = {"User-Agent": "Mozilla/5.0 (compatible; embodied-radar/0.1)"}
DIRECTION = {"brain": "大脑", "data_sim": "数据 / 仿真"}

SYSTEM = """你是具身机器人技术雷达的筛选员。判断一个候选条目是否属于下面定义的范围。
只依据给出的条目字段与正文判断；不要凭团队名、网址或常识推断正文里没有的内容。

decision 取值：
- keep：正文表明它是范围 include 中的技术工作（模型、算法、数据、仿真、基础设施的发布或研究）；正文披露了新技术内容的产品发布也算。
- reject：正文表明它属于 exclude 或 business（正文没有新技术内容的融资、合作、人事、市场、活动、营销），或与机器人 / 具身环境无关。
- needs_review：给出的内容不足以判断（例如正文缺失或只有标题）。

只输出一个 JSON 对象，恰好包含这四个键：
{"decision": "keep|reject|needs_review", "reason": "一句话理由", "evidence_quote": "从条目字段或正文原样摘出的一句话，不超过 200 字符；needs_review 且无可引用内容时为空字符串", "team": "团队 id；正文能确认是清单中哪个团队的工作时填写，否则为 null"}"""


def evidence_url(item: dict) -> str:
    """Where the readable text of an item lives: README / card for repos, the page itself otherwise."""
    parts = item["key"].split("/")
    if parts[0] == "github.com" and len(parts) >= 3:
        return f"https://api.github.com/repos/{parts[1]}/{parts[2]}/readme"
    if parts[0] == "huggingface.co" and len(parts) >= 3 and parts[1] not in ("blog", "papers"):
        return f"https://huggingface.co/{'/'.join(parts[1:])}/raw/main/README.md"
    return item["url"]


def slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_")[:120]


def fetch_evidence(item: dict, evidence_dir: Path, get=None) -> dict:
    """Fetch once (retry once), keep the raw response, the extracted text and their hashes."""
    url = evidence_url(item)
    headers = {**UA}
    if url.startswith("https://api.github.com"):
        headers["Accept"] = "application/vnd.github.raw+json"
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    get = get or (lambda u: requests.get(u, headers=headers, timeout=30))
    meta = {"key": item["key"], "request_url": url, "attempts": 0, "status": None, "error": None}
    body = b""
    for attempt in (1, 2):
        meta["attempts"] = attempt
        meta["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            resp = get(url)
        except requests.RequestException as exc:
            meta["error"] = type(exc).__name__
            continue
        meta["status"], body = resp.status_code, resp.content
        meta["error"] = None if resp.status_code < 400 else f"http_{resp.status_code}"
        if meta["error"] is None:
            break

    is_markdown = url.startswith("https://api.github.com") or url.endswith("README.md")
    text = ""
    if body and meta["error"] is None:
        html_or_md = body.decode("utf-8", errors="replace")
        text = html_or_md if is_markdown else (trafilatura.extract(html_or_md, url=url) or "")
    name = slug(item["key"])
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"{name}.raw.gz").write_bytes(gzip.compress(body))
    (evidence_dir / f"{name}.txt").write_text(text, encoding="utf-8")
    meta |= {"raw_file": f"{name}.raw.gz", "text_file": f"{name}.txt", "raw_sha256": hashlib.sha256(body).hexdigest(),
             "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text_chars": len(text)}
    (evidence_dir / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def teams_block(entities: list[dict]) -> str:
    return "\n".join(f"- {e['id']}: {e['name']}（{DIRECTION[e['direction']]}）" for e in entities)


def scope_block(scope: dict) -> str:
    return "\n".join(f"{k}:\n" + "\n".join(f"  - {v}" for v in scope[k]) for k in ("include", "exclude", "business"))


def build_messages(item: dict, text: str, fetch_error: str | None, scope: dict, entities: list[dict]) -> list[dict]:
    visible = text[:TEXT_LIMIT]
    if fetch_error:
        body = f"（正文抓取失败：{fetch_error}）"
    elif not visible.strip():
        body = "（正文为空）"
    else:
        body = visible
    sources = ", ".join(f"{s['entity']}/{s['type']}" for s in item.get("sources", []))
    user = (f"范围：\n{scope_block(scope)}\n\n团队清单：\n{teams_block(entities)}\n\n候选条目：\n"
            f"url: {item['url']}\ntitle: {item.get('title') or '（空）'}\n"
            f"description: {item.get('description') or '（空）'}\n来源渠道: {sources}\n\n"
            f"正文（前 {TEXT_LIMIT} 字符；{'已截断，原文 ' + str(len(text)) + ' 字符' if len(text) > TEXT_LIMIT else '未截断'}）：\n{body}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse_output(content: str) -> dict:
    """Raises ValueError for anything but a JSON object with exactly the four keys and a known decision."""
    data = json.loads(content)
    if not isinstance(data, dict) or set(data) != OUTPUT_KEYS:
        raise ValueError(f"keys {sorted(data) if isinstance(data, dict) else type(data).__name__} != {sorted(OUTPUT_KEYS)}")
    if data["decision"] not in DECISIONS:
        raise ValueError(f"decision {data['decision']!r}")
    return data


def call_with_deadline(fn, seconds: float):
    """A hung call is abandoned on a daemon thread and reported as a timeout."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise requests.Timeout(f"no answer within {seconds}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def run(items_path: Path, evidence_dir: Path, out_dir: Path, domain_dir: Path, provider, model: str) -> dict:
    domain = config.load(domain_dir)
    items = [json.loads(l) for l in items_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    calls_dir = out_dir / "calls"
    calls_dir.mkdir(parents=True, exist_ok=True)
    params = {"model": model, "temperature": 0.0, "max_tokens": 400, "response_format": {"type": "json_object"}}
    retries_left, results, usage = HTTP_RETRY_BUDGET, [], {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    seen_models = set()

    for item in items:
        meta = json.loads((evidence_dir / f"{slug(item['key'])}.json").read_text(encoding="utf-8"))
        text = (evidence_dir / meta["text_file"]).read_text(encoding="utf-8")
        messages = build_messages(item, text, meta["error"], domain.raw["scope"], domain.entities)
        name = slug(item["key"])
        (calls_dir / f"{name}.request.json").write_text(
            json.dumps({**params, "messages": messages}, ensure_ascii=False, indent=1), encoding="utf-8")
        row = {"key": item["key"], "url": item["url"], "sources": item.get("sources", []),
               "request_file": f"calls/{name}.request.json", "response_file": None, "error": None}
        while True:
            usage["requests"] += 1
            # A fresh copy per call: an abandoned call that answers late must not overwrite this call's response.
            call = copy.copy(provider)
            call.last_response = None
            try:
                content = call_with_deadline(lambda: call.chat(
                    messages, model=model, temperature=params["temperature"], max_tokens=params["max_tokens"],
                    response_format=params["response_format"]), CALL_DEADLINE_SECONDS)
            except requests.RequestException as exc:
                if retries_left > 0:
                    retries_left -= 1
                    time.sleep(2)
                    continue
                row["error"] = f"http:{type(exc).__name__}"
            except ValueError as exc:  # truncated or empty answer from the provider
                row["error"] = f"provider:{exc}"
            break
        raw = call.last_response
        if raw is not None:
            (calls_dir / f"{name}.response.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
            row["response_file"] = f"calls/{name}.response.json"
            seen_models.add(raw.get("model"))
            for k in ("prompt_tokens", "completion_tokens"):
                usage[k] += raw.get("usage", {}).get(k, 0)
        if row["error"] is None:
            try:
                row |= parse_output(content)
            except (ValueError, json.JSONDecodeError) as exc:
                row["error"] = f"parse:{exc}"
        results.append(row)

    summary = {
        "model_requested": model, "models_reported": sorted(m for m in seen_models if m), "params": params,
        # LF-normalized: a Windows checkout of the same commit has CRLF line endings.
        "inputs": {"items_sha256": hashlib.sha256(items_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                   "evidence_dir": evidence_dir.as_posix()},
        "usage": usage, "http_retries_used": HTTP_RETRY_BUDGET - retries_left,
        "errors": sum(r["error"] is not None for r in results), "results": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def make_provider():
    from llm_provider.deepseek_provider import DeepSeekProvider

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    return DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"], default_model=model, max_retries=0), model


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge whether candidates are in scope")
    parser.add_argument("--items", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--domain", default="domains/embodied")
    args = parser.parse_args()
    provider, model = make_provider()
    s = run(Path(args.items), Path(args.evidence), Path(args.out), Path(args.domain), provider, model)
    print(json.dumps({k: s[k] for k in ("models_reported", "usage", "http_retries_used", "errors")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
