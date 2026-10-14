#!/usr/bin/env python3
"""Maintainer tools for official-prompt-tailor (not shipped with the skill): discover / propose.

discover: list pages in each host's llms.txt that look relevant and are not registered or triaged yet.
propose:  turn one candidate URL into a registry "sources" entry that passes the tool's own checks.
Both only print (discover --mark-seen also records the listed pages as triaged). Neither edits registry.json:
adding a source and reviewing its rules stays a human decision (docs/maintenance.md). Standard library only.
"""
import argparse
import json
import re
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / ".claude" / "skills" / "official-prompt-tailor" / "scripts"))
import evidence as ev  # noqa: E402

CONFIG, SEEN = HERE / "discovery.json", HERE / "discovery-seen.json"
LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
INDEX_F = {"index_id": (str, 1), "url": (str, 1), "scope": (list, 1), "keywords": (list, 0), "exclude": (list, 0)}


def page_key(url):
    """host/path without www., scheme, query, trailing slash, .md and /index: llms.txt and the registry spell one page differently."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host + (re.sub(r"(/index)?(\.md)?/?$", "", p.path) or "/")


def load_config():
    cfg = ev.load_json(CONFIG)
    ev.shape(cfg, {"schema_version": (int, 1), "keywords": (list, 1), "indexes": (list, 1)}, "discovery")
    ev.strs(cfg["keywords"], "keywords", True)
    for index in cfg["indexes"]:
        ev.shape(index, INDEX_F, "discovery.index")
        ev.safe_id(index["index_id"], "index_id")
        if ev.url_key(index["url"]) is None or "#" in index["url"]:
            raise ev.ContractError(f"index {index['index_id']}: url must be https without a fragment")
        ev.strs(index["scope"], "scope")
        if any(not s.startswith("/") for s in index["scope"]):
            raise ev.ContractError(f"index {index['index_id']}: scope entries must start with '/'")
        for key in ("keywords", "exclude"):
            ev.strs(index.get(key, []), key)
            try:
                re.compile("|".join(index.get(key, [])))
            except re.error as e:
                raise ev.ContractError(f"index {index['index_id']}.{key}: bad regex ({e})")
    ev.uniq([index["index_id"] for index in cfg["indexes"]], "index_id")
    return cfg


def load_seen():
    if not SEEN.exists():
        return []
    seen = ev.load_json(SEEN)
    ev.strs(seen, "seen")
    return seen


def index_source(index):
    """The registry-shaped source that lets ev.fetch_live fetch an llms.txt with all of its checks."""
    return {"source_id": index["index_id"], "allowed_urls": [index["url"]], "target_ids": [], "surfaces": ["all"],
            "models": ["all"], "category": "index", "extraction": "markdown-v1", "body_locators": []}


def index_pages(index, body):
    """key -> (title, https url) for the pages of the index's own host; links to other hosts are not ours to list."""
    host, pages = page_key(index["url"]).split("/", 1)[0], {}
    for title, url in LINK.findall(body):
        key = page_key(url)
        if key.split("/", 1)[0] == host:
            pages.setdefault(key, (title.strip(), re.sub(r"^http://", "https://", url)))
    return pages


def candidates(index, default_keywords, pages, skip_keys):
    """Pages in scope that match a keyword and are neither registered nor triaged (skip_keys)."""
    want = re.compile("|".join(index.get("keywords") or default_keywords), re.I)
    skip = re.compile("|".join(index["exclude"]), re.I) if index.get("exclude") else None
    scope = [s if s.endswith("/") else s + "/" for s in index["scope"]]
    found = []
    for key, (title, url) in sorted(pages.items()):
        path = "/" + key.split("/", 1)[1]
        text = f"{title} {path}"
        in_scope = not scope or any((path + "/").startswith(s) for s in scope)  # "/skills/" must also match the page "/skills"
        if in_scope and want.search(text) and not (skip and skip.search(text)) and key not in skip_keys:
            found.append({"title": title, "url": url, "key": key})
    return found


def fetch_index(index, tmp):
    """(entry, llms.txt text or None). A failed answer is looked at once more, as drift does: one answer can be a dropped
    connection or a CDN node mid-deploy. Each look gets its own directory because fetch_live never overwrites a snapshot."""
    for look in ("look-1", "look-2"):
        e = ev.fetch_live(index_source(index), tmp / look)
        if e["status"] == "live":
            return e, ev.read_text(tmp / look / e["body_path"])
        if look == "look-1":
            time.sleep(ev.RETRY_WAIT)
    return e, None


def discover(mark_seen=False):
    reg, _ = ev.load_registry()
    cfg, triaged = load_config(), set(load_seen())
    skip_keys = {page_key(u) for s in reg["sources"] for u in s["allowed_urls"]} | triaged
    rows = []
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        for index in cfg["indexes"]:
            e, text = fetch_index(index, tmp)
            row = {"index_id": index["index_id"], "status": e["status"], "error": e["error"], "pages": 0, "candidates": []}
            if text is not None:
                pages = index_pages(index, text)
                row["pages"], row["candidates"] = len(pages), candidates(index, cfg["keywords"], pages, skip_keys)
            rows.append(row)
    listed = {c["key"] for row in rows for c in row["candidates"]}
    if mark_seen:
        SEEN.write_text(json.dumps(sorted(triaged | listed), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if listed:
        verdict = "CANDIDATES"
    else:
        verdict = "INCOMPLETE" if any(row["status"] != "live" for row in rows) else "NONE"
    return {"schema_version": 1, "checked_at": ev.iso(ev.utcnow()), "verdict": verdict, "indexes": rows,
            "marked_seen": len(listed) if mark_seen else 0}


def check_request(reg, url, targets, source_id):
    """Raise ContractError unless this URL may be fetched and proposed. Returns the source_id to use."""
    registered = {page_key(u) for s in reg["sources"] for u in s["allowed_urls"]}
    hosts = {urllib.parse.urlsplit(u).hostname for s in reg["sources"] for u in s["allowed_urls"]}
    hosts |= {urllib.parse.urlsplit(i["url"]).hostname for i in load_config()["indexes"]}
    if ev.url_key(url) is None or "#" in url or urllib.parse.urlsplit(url).hostname not in hosts:
        raise ev.ContractError("url must be https, have no fragment, and be on a host the registry or discovery config already uses")
    if not targets or set(targets) - set(ev.TARGET_IDS):
        raise ev.ContractError(f"--target must be one or more of {ev.TARGET_IDS}")
    if page_key(url) in registered:
        raise ev.ContractError("that page is already registered")
    sid = source_id or "new-" + re.sub(r"[^A-Za-z0-9]+", "-", page_key(url)).strip("-")[:58]
    ev.safe_id(sid, "source_id")
    if sid in {s["source_id"] for s in reg["sources"]}:
        raise ev.ContractError(f"source_id already exists: {sid}")
    return sid


def headings(body):
    found = [re.sub(r"^#+\s*", "", ln).strip() for ln in body.split("\n") if ln.startswith("#")]
    return [h for h in dict.fromkeys(found) if 3 <= len(h) <= 80]


def fit_extraction(base, raw, content_type):
    """The first extraction that accepts the page, with body_locators from its headings: (entry, body, rejected)."""
    rejected = {}
    for extraction in ev.EXTRACTIONS:
        try:
            body = ev.process_body({**base, "extraction": extraction}, raw, content_type)
        except ev.Blocked as b:
            rejected[extraction] = b.code
            continue
        found = headings(body)
        if len(found) < 2:
            rejected[extraction] = "too_few_headings"
            continue
        entry = {**base, "extraction": extraction, "body_locators": [found[0], found[len(found) // 2]]}
        ev.process_body(entry, raw, content_type)  # the same check fetch and verify run
        ev.shape(entry, ev.SRC_F, "source")
        return entry, body, rejected
    return None, None, rejected


def propose(url, targets, source_id=None, surfaces=("all",), category="product-feature"):
    reg, _ = ev.load_registry()
    base = {"source_id": check_request(reg, url, targets, source_id), "allowed_urls": [url], "target_ids": list(targets),
            "surfaces": list(surfaces), "models": ["all"], "category": category, "extraction": "markdown-v1",
            "body_locators": []}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        first = ev.fetch_live(base, tmp / "a")  # fetched once; every extraction is then tried on the same bytes
        if first["content_type"] is None:
            return {"ok": False, "error": first["error"], "tried": {}}
        entry, body, tried = fit_extraction(base, (tmp / "a" / first["raw_path"]).read_bytes(), first["content_type"])
        if entry is None:
            return {"ok": False, "error": "no_extraction_fits", "tried": tried}
        sha = ev.sha_bytes(body.encode("utf-8"))
        stable = ev.fetch_live(entry, tmp / "b")["body_sha256"] == sha
    return {"ok": True, "stable": stable, "title": entry["body_locators"][0], "body_sha256": sha, "source": entry,
            "tried": tried, "next": "targets への配線、rules の追加、審査は人が行う(docs/maintenance.md)"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="maintain.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--mark-seen", action="store_true")
    p = sub.add_parser("propose")
    p.add_argument("--url", required=True)
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--surface", action="append", default=[])
    p.add_argument("--source-id")
    p.add_argument("--category", default="product-feature")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # the JSON holds Japanese text; Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "discover":
            rep = discover(args.mark_seen)
            found = sum(len(row["candidates"]) for row in rep["indexes"])
            return ev._emit("discover", 0 if rep["verdict"] == "NONE" else 2, rep, f"{rep['verdict']} {found} candidates")
        rep = propose(args.url, args.target, args.source_id, args.surface or ["all"], args.category)
        good = rep["ok"] and rep["stable"]
        return ev._emit("propose", 0 if good else 2, rep, "ok" if good else "needs attention: " + str(rep.get("error") or "unstable body"))
    except ev.ContractError as e:
        print(f"{args.cmd}: contract violation: {e}", file=sys.stderr)
        return 1
    except ev.Unreadable as e:
        print(f"{args.cmd}: not runnable: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
