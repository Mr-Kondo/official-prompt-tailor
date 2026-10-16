#!/usr/bin/env python3
"""Maintainer tools for official-prompt-tailor (not shipped with the skill): discover / propose / adopt / rereview / review / docs / audit.

discover: list pages in each host's llms.txt or sitemap that look relevant and are not registered or triaged yet.
propose:  turn one candidate URL into a registry "sources" entry that passes the tool's own checks.
adopt:    add the sources and reviewed rules of a spec file to registry.json (a dry run unless --write).
rereview: for the rules whose source changed since they were reviewed (drift), compare the old body with the new one and, with --write,
          renew the review of the rules whose excerpt and surroundings did not change. The old body is never committed.
review:   a person goes through rules at a terminal and records "human:<name>" for the ones they review (interactive terminals only).
docs:     check, or with --write refresh, the numbers and tables in README.md and docs/ that come from registry.json.
audit:    check, from a run and the agent's own transcript, that the skill's procedure was followed and reported straight.
discover, propose and audit only print (discover --mark-seen also records the listed pages as triaged). adopt, rereview and review
edit registry.json, and only adopt and rereview with --write: which rules a page holds and what they mean is judged by whoever writes
the spec (docs/maintenance.md); adopt checks the spec against the live page and records who reviewed. Standard library only.
"""
import argparse
import contextlib
import difflib
import html
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE.parent / ".claude" / "skills" / "official-prompt-tailor" / "scripts"))
import evidence as ev  # noqa: E402

CONFIG, SEEN = HERE / "discovery.json", HERE / "discovery-seen.json"
LINK = re.compile(r"\[([^\]\n]{0,300})\]\((https?://[^)\s]+)\)")  # a title is one short line: an unbounded [^\]]* is quadratic on a body of "["
INDEX_F = {"index_id": (str, 1), "url": (str, 1), "scope": (list, 1), "keywords": (list, 0), "exclude": (list, 0), "format": (str, 0)}
FORMATS = ("llms", "sitemap")  # what the index is: an llms.txt (the default) or a sitemap.xml


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
        if index.get("format", "llms") not in FORMATS:
            raise ev.ContractError(f"index {index['index_id']}: format must be one of {FORMATS}")
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


@contextlib.contextmanager
def response_limit(size):
    """ev.fetch_live refuses an answer over ev.MAX_BYTES (5 MB), which is right for a page. A sitemap of a big site is larger (18.7 MB for
    ai.google.dev, 2026-10), so it is read under a higher limit, for the length of the one call."""
    old, ev.MAX_BYTES = ev.MAX_BYTES, size
    try:
        yield
    finally:
        ev.MAX_BYTES = old


def fetch_index(index, tmp, process=None):
    """(entry, the index's text or None): an llms.txt, or with `process` (sitemap_body) a sitemap. A failed answer is looked at once more, as
    drift does: one answer can be a dropped connection or a CDN node mid-deploy. Each look gets its own directory because fetch_live never
    overwrites a snapshot. A refusal (HTTP 403, a bot check) is reported as it is: nothing here tries to get past it."""
    for look in ("look-1", "look-2"):
        with response_limit(SITEMAP_MAX_BYTES) if process else contextlib.nullcontext():
            e = ev.fetch_live(index_source(index), tmp / look, process)
        if e["status"] == "live":
            return e, ev.read_text(tmp / look / e["body_path"])
        if look == "look-1":
            time.sleep(ev.RETRY_WAIT)
    return e, None


# A sitemap.xml lists a site's pages the way an llms.txt does, for the sites that have no llms.txt. An index of sitemaps points to others.
SITEMAP_TYPES = ("application/xml", "text/xml", "text/plain")
SITEMAP_LOC = re.compile(r"<loc>([^<]*)</loc>", re.I)  # stripped afterwards: `\s*([^<]*?)\s*` backtracks cubically on <loc> + spaces + no closing tag
MAX_CHILD_SITEMAPS = 30  # child sitemaps read per index; more are reported, not read
SITEMAP_MAX_BYTES = 40 * 1024 * 1024  # one sitemap answer; a site's page limit for a sitemap is 50 MB uncompressed


def sitemap_body(src, raw, content_type):
    """fetch_live's `process` for a sitemap: a first line saying which kind it is (`# urlset` or `# sitemapindex`), then each <loc> address.
    The XML is not parsed (a DOCTYPE or an entity is never expanded): only the addresses are read out of it."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    charset = re.search(r"charset=([^;\s]+)", content_type or "", re.I)
    if charset and charset.group(1).strip("\"'").lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise ev.Blocked("unsupported_charset")
    if ctype not in SITEMAP_TYPES:
        raise ev.Blocked(f"unexpected_content_type:{ctype}")
    try:
        text = ev.norm(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ev.Blocked("not_utf8")
    kind = "sitemapindex" if re.search(r"<sitemapindex\b", text) else ("urlset" if re.search(r"<urlset\b", text) else None)
    if kind is None:
        raise ev.Blocked("not_a_sitemap")
    locs = [html.unescape(x.strip()) for x in SITEMAP_LOC.findall(text)]
    if not locs:
        raise ev.Blocked("sitemap_without_locations")
    return "\n".join([f"# {kind}"] + locs) + "\n"


def sitemap_locs(body):
    lines = body.split("\n")
    return lines[0][2:], [x for x in lines[1:] if x]


def sitemap_pages(index, locs):
    """key -> (title, https url) for the pages of the index's own host. A sitemap has no titles: the last path segment stands for one."""
    host, pages = page_key(index["url"]).split("/", 1)[0], {}
    for url in locs:
        key = page_key(url)
        if urllib.parse.urlsplit(url).scheme in ("http", "https") and key.split("/", 1)[0] == host:
            known = pages.get(key)
            if known is None or ("?" in known[1] and "?" not in url):  # one page in many languages (?hl=de): the plain address stands for it
                pages[key] = (re.sub(r"[-_]+", " ", urllib.parse.unquote(key.rsplit("/", 1)[-1])), re.sub(r"^http://", "https://", url))
    return pages


def read_sitemap(index, tmp):
    """(entry, pages, problems, skipped): the index's own sitemap and, if it lists sitemaps, each of those that is https on the same host
    (at most MAX_CHILD_SITEMAPS). What should have been read and was not is named in `problems`; the pages of what could be read are still
    returned. `skipped` holds the children left alone on purpose: another host's sitemap is not ours to list from."""
    e, text = fetch_index(index, tmp / "top", sitemap_body)
    if text is None:
        return e, {}, [], []
    kind, locs = sitemap_locs(text)
    if kind == "urlset":
        return e, sitemap_pages(index, locs), [], []
    host, problems, pages = page_key(index["url"]).split("/", 1)[0], [], {}
    children = list(dict.fromkeys(locs))
    same = [u for u in children if ev.url_key(u) is not None and "#" not in u and page_key(u).split("/", 1)[0] == host]
    skipped = [u for u in children if u not in same]
    if len(same) > MAX_CHILD_SITEMAPS:
        problems.append(f"{len(same) - MAX_CHILD_SITEMAPS} child sitemaps beyond the first {MAX_CHILD_SITEMAPS} were not read")
    for n, url in enumerate(same[:MAX_CHILD_SITEMAPS], 1):
        ce, ctext = fetch_index({**index, "index_id": f"{index['index_id']}-{n}", "url": url}, tmp / f"child-{n}", sitemap_body)
        shutil.rmtree(tmp / f"child-{n}", ignore_errors=True)  # the answer (up to 40 MB, twice) is in ctext now: thirty of them would not fit on a small runner
        if ctext is None:
            problems.append(f"{url}: {ce['error']}")
            continue
        ckind, clocs = sitemap_locs(ctext)
        if ckind != "urlset":
            problems.append(f"{url}: a sitemap index inside a sitemap index is not followed")
            continue
        for key, page in sitemap_pages(index, clocs).items():
            pages.setdefault(key, page)
    return e, pages, problems, skipped


def discover(mark_seen=False):
    reg, _ = ev.load_registry()
    cfg, triaged = load_config(), set(load_seen())
    skip_keys = {page_key(u) for s in reg["sources"] for u in s["allowed_urls"]} | triaged
    rows = []
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        for index in cfg["indexes"]:
            if index.get("format", "llms") == "sitemap":
                e, pages, problems, skipped = read_sitemap(index, tmp)
                shown = "; ".join(problems[:3]) + (f" (and {len(problems) - 3} more)" if len(problems) > 3 else "")
                row = {"index_id": index["index_id"], "status": "partial" if problems and e["status"] == "live" else e["status"],
                       "error": e["error"] or shown or None, "pages": 0, "candidates": [], "skipped": skipped}
            else:
                e, text = fetch_index(index, tmp)
                pages = index_pages(index, text) if text is not None else {}
                row = {"index_id": index["index_id"], "status": e["status"], "error": e["error"], "pages": 0, "candidates": []}
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
        if extraction == "markdown-v1":  # v2 is v1 minus the query of signed images: when that changes the body, v1 would drift every hour
            try:
                v2 = ev.process_body({**base, "extraction": "markdown-v2"}, raw, content_type)
            except ev.Blocked:
                v2 = body
            if v2 != body:
                extraction, body = "markdown-v2", v2
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


# ---------------------------------------------------------------- adopt
SPEC_F = {"schema_version": (int, 1), "reviewer_id": (str, 1), "sources": (list, 1)}
SPEC_SOURCE_F = {**ev.SRC_F, "rules": (list, 1)}  # a registry source, and the rules read from it
SPEC_RULE_F = {"rule_id": (str, 1), "target_ids": (list, 1), "surfaces": (list, 1), "models": (list, 1), "purposes": (list, 1),
               "preconditions": (list, 1), "exclusions": (list, 1), "source_excerpt": (str, 1), "meaning": (str, 1),
               "allowed_change": (str, 1), "kind": (str, 0)}


def dump_registry(reg):
    """The registry's own layout: one compact record per line, the three lists on lines of their own."""
    one = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    lines = ["{", '"schema_version":%d,' % reg["schema_version"]]
    for key, last in (("sources", False), ("targets", False), ("rules", True)):
        lines += ['"%s":[' % key, ",\n".join(one(x) for x in reg[key]), "]" if last else "],"]
    lines.append("}")
    return "\n".join(lines) + "\n"


def routing_inputs(reg):
    """Every input that can be routed differently: each target and purpose, with each surface and model the registry names, and unknown."""
    named = lambda key: {v for x in reg["sources"] + reg["rules"] for v in x[key]}  # noqa: E731
    surfaces = named("surfaces") | {s for t in reg["targets"] for c in t["conditional_sources"] for s in c["surfaces"]}
    for target, purpose, surface, model in itertools.product([t["target_id"] for t in reg["targets"]], ev.PURPOSES,
                                                             ["unknown"] + sorted(surfaces - {"all"}), ["unknown"] + sorted(named("models") - {"all"})):
        yield {"target_id": target, "purpose": purpose, "target_surface": surface, "model_id": model}


def route_gaps(reg):
    """Where routing and rules disagree, over every routing input: ({(target_id, source_id): [input, ...]}, [rule_id, ...]).
    The first holds the inputs for which a source is selected and none of its rules applies: verify asks for a read of every selected
    source, so that page is read for nothing. The second holds the rules that apply to no input."""
    view = {**reg, "rules": [{**r, "review_status": "reviewed"} for r in reg["rules"]]}  # whether a rule is reviewed is another check
    reads, usable = {}, set()
    for inp in routing_inputs(view):
        chosen = ev.select_sources(view, inp)
        applicable = ev._applicable(view, inp, {"selected_sources": chosen})
        usable |= {r["rule_id"] for r in applicable}
        for sid in set(chosen) - {r["source_id"] for r in applicable}:
            reads.setdefault((inp["target_id"], sid), []).append(inp)
    return reads, sorted({r["rule_id"] for r in reg["rules"]} - usable)


def routing_problems(reg):
    reads, unused = route_gaps(reg)
    problems = []
    for (tid, sid), inputs in sorted(reads.items()):
        purposes = [p for p in ev.PURPOSES if any(i["purpose"] == p for i in inputs)]
        problems.append(f"target {tid}: source {sid} is fetched but none of its rules applies, for purposes {purposes} ({len(inputs)} inputs, "
                        f"e.g. surface {inputs[0]['target_surface']}, model {inputs[0]['model_id']}): route it only where its rules apply")
    return problems + [f"rule {rid} applies to no input: no route fetches its source for its targets, purposes, surfaces and models" for rid in unused]


def registry_problems(reg):
    """What the repository requires of a registry beyond validate_registry: no source without a route or a rule, a source fetched only
    where one of its rules applies and every rule usable by some input, every rule reviewed and complete, API advice kept to the API
    surface, and a model's guide grounding only that model's rules."""
    sources = {s["source_id"]: s for s in reg["sources"]}
    routed = {i for t in reg["targets"] for i in t["required_sources"] + [x for c in t["conditional_sources"] for x in c["source_ids"]]}
    problems = [f"source {i} is not routed by any target" for i in sorted(set(sources) - routed)]
    problems += [f"source {i} has no rule" for i in sorted(set(sources) - {r["source_id"] for r in reg["rules"]})]
    problems += routing_problems(reg)
    for r in reg["rules"]:
        s, rid = sources[r["source_id"]], r["rule_id"]
        if r["review_status"] != "reviewed":
            problems.append(f"rule {rid} is not reviewed")
        if not (r["preconditions"] and r["exclusions"]):
            problems.append(f"rule {rid} lacks preconditions or exclusions")
        if s["surfaces"] == ["api"] and r["surfaces"] != ["api"]:
            problems.append(f"rule {rid}: advice from an API-only source must be limited to the api surface")
        if s["models"] != ["all"] and not set(r["models"]) <= set(s["models"]):
            problems.append(f"rule {rid}: models outside those of its source")
    return problems


def observe(source, looks=6):
    """(body, sha256) once two live looks agree, among at most `looks`: one answer can be a CDN node mid-deploy or a dropped
    connection. A look that fails is skipped, not counted as a change."""
    seen = []
    for look in range(looks):
        with tempfile.TemporaryDirectory() as t:
            e = ev.fetch_live(source, Path(t))
            seen.append((e["body_sha256"], ev.read_text(Path(t) / e["body_path"])) if e["status"] == "live" else (None, e["error"]))
        if seen[-1][0] and seen[-1][0] in [h for h, _ in seen[:-1]]:
            return seen[-1][1], seen[-1][0]
        if look < looks - 1:
            time.sleep(ev.RETRY_WAIT)
    raise ev.Unreadable(f"{source['source_id']}: no two live looks agree: {[(h or 'x')[:8] for h, _ in seen]} {seen[-1][1] if not seen[-1][0] else ''}")


def adopt_rule(spec, source, body, sha, reviewer, now):
    """One rule of the spec as a reviewed registry rule, after checking it against the live body."""
    ev.shape(spec, SPEC_RULE_F, "spec rule")
    rid = spec["rule_id"]
    ev.safe_id(rid, "rule_id")
    for key in ("target_ids", "surfaces", "models", "purposes", "preconditions", "exclusions"):
        ev.strs(spec[key], f"rule {rid}.{key}", True)
    if spec.get("kind", "official") not in ("official", "inference"):
        raise ev.ContractError(f"rule {rid}: kind must be official or inference")
    if not set(spec["target_ids"]) <= set(source["target_ids"]) or not set(spec["purposes"]) <= set(ev.PURPOSES):
        raise ev.ContractError(f"rule {rid}: a target outside its source's targets, or an unknown purpose")
    excerpt = spec["source_excerpt"]
    if not excerpt or len(excerpt) > ev.MAX_EXCERPT:
        raise ev.ContractError(f"rule {rid}: source_excerpt must be 1 to {ev.MAX_EXCERPT} characters")
    if excerpt not in body:
        raise ev.ContractError(f"rule {rid}: source_excerpt is not in the live body of {source['source_id']}")
    return {"rule_id": rid, "source_id": source["source_id"], "target_ids": spec["target_ids"], "surfaces": spec["surfaces"],
            "models": spec["models"], "kind": spec.get("kind", "official"), "purposes": spec["purposes"],
            "preconditions": spec["preconditions"], "exclusions": spec["exclusions"], "source_body_sha256": sha,
            "source_excerpt": excerpt, "reviewed_at": now, "reviewer_id": reviewer, "meaning": spec["meaning"],
            "source_locator": excerpt, "allowed_change": spec["allowed_change"], "review_status": "reviewed"}


def wire(reg, source, rules):
    """Route the source from each target its rules name, for the purposes those rules use and the source's own surfaces,
    so that a purpose without a rule from this source does not fetch it. -> the target ids wired."""
    purposes = {}
    for r in rules:
        for t in r["target_ids"]:
            purposes.setdefault(t, set()).update(r["purposes"])
    for tid, used in sorted(purposes.items()):
        target = next(t for t in reg["targets"] if t["target_id"] == tid)
        listed = ["all"] if used >= set(ev.PURPOSES) else [p for p in ev.PURPOSES if p in used]
        for c in target["conditional_sources"]:
            if set(c["purposes"]) == set(listed) and set(c["surfaces"]) == set(source["surfaces"]):
                if source["source_id"] not in c["source_ids"]:
                    c["source_ids"].append(source["source_id"])
                break
        else:
            target["conditional_sources"].append({"purposes": listed, "surfaces": list(source["surfaces"]), "source_ids": [source["source_id"]]})
    return sorted(purposes)


class Loaded(dict):
    """The registry as load_for_edit read it, with the bytes it was read from: replace_registry refuses to write over a file that has changed since."""
    raw = b""


def load_for_edit():
    """The registry, after checking that dump_registry would write it back byte for byte: an edit then changes only what it means to change."""
    reg, _ = ev.load_registry()
    raw = ev.REGISTRY_PATH.read_bytes()
    if dump_registry(reg).encode("utf-8") != raw:
        raise ev.ContractError("registry.json is not in the layout dump_registry writes (one compact record per line): put it back first")
    loaded = Loaded(reg)
    loaded.raw = raw
    return loaded


def checked_registry_text(reg):
    """The registry as dump_registry writes it, once validate_registry and registry_problems accept it."""
    ev.validate_registry(reg)
    problems = registry_problems(reg)
    if problems:
        raise ev.ContractError("; ".join(problems))
    return dump_registry(reg).encode("utf-8")


def replace_registry(text, loaded):
    """Write `text` over registry.json, if the file is still what `loaded` was read from: a look at the network or a person's typing takes minutes,
    and an edit made meanwhile would otherwise be undone without a word."""
    if ev.REGISTRY_PATH.read_bytes() != loaded.raw:
        raise ev.ContractError("registry.json changed since it was read; nothing was written")
    tmp = ev.REGISTRY_PATH.with_name(ev.REGISTRY_PATH.name + ".tmp")
    tmp.write_bytes(text)
    os.replace(tmp, ev.REGISTRY_PATH)
    loaded.raw = text


def adopt(spec_path, write=False):
    reg = load_for_edit()
    spec = ev.load_json(spec_path)
    ev.shape(spec, SPEC_F, "spec")
    if spec["schema_version"] != 1 or not spec["reviewer_id"].strip():
        raise ev.ContractError("spec: schema_version must be 1 and reviewer_id must say who reviewed")
    if spec["reviewer_id"].strip().lower().startswith(ev.HUMAN_REVIEWER):
        raise ev.ContractError("spec: reviewer_id must not start with human: (adopt does not record a person's review; `review` does, from a terminal)")
    if not spec["sources"]:
        raise ev.ContractError("spec: no sources")
    now, rows = ev.iso(ev.utcnow()), []
    for entry in spec["sources"]:
        ev.shape(entry, SPEC_SOURCE_F, "spec source")
        source = {k: v for k, v in entry.items() if k != "rules"}
        if not entry["rules"]:
            raise ev.ContractError(f"{source['source_id']}: a source without a rule cannot be watched by drift")
        for url in source["allowed_urls"]:
            check_request(reg, url, source["target_ids"], source["source_id"])
        body, sha = observe(source)
        rules = [adopt_rule(r, source, body, sha, spec["reviewer_id"], now) for r in entry["rules"]]
        reg["sources"].append(source)
        reg["rules"].extend(rules)
        rows.append({"source_id": source["source_id"], "body_sha256": sha, "extraction": source["extraction"],
                     "rules": [r["rule_id"] for r in rules], "wired": wire(reg, source, rules)})
    text = checked_registry_text(reg)
    if write:
        replace_registry(text, reg)
    return {"schema_version": 1, "written": write, "reviewer_id": spec["reviewer_id"], "sources": rows,
            "registry": {"sources": len(reg["sources"]), "rules": len(reg["rules"]), "bytes": len(text)}}


# ---------------------------------------------------------------- rereview
# The bodies a rule was reviewed against are kept here, so that when the page changes there is an old body to compare with. Local only:
# a page's full text is not committed (copyright), and outputs/ is not tracked (.gitignore). A clone that never ran rereview has none; the
# snapshots of past runs under outputs/ (or --old) are looked at too.
STORE = ROOT / "outputs" / "reviewed-bodies"
CONTEXT_LINES = 5  # lines before and after the excerpt that must also be unchanged for a rule to be renewed without a person reading the page
# Words that, added to the section of an excerpt (outside the lines around it), may change what the rule means: a person reads them before renewal.
RISK_WORDS = re.compile(r"deprecat|no longer|obsolete|legacy|removed|discontinu|not recommended|do not use|don't use|instead of|非推奨|廃止|使用しない|使わない|推奨されません", re.I)
MACHINE_REVIEWER = "tools/maintain.py rereview (excerpt and surrounding lines unchanged; mechanical; not human)"
LEDGER, LEDGER_F = HERE / "human-reviews.json", {"rule_id": (str, 1), "reviewer_id": (str, 1), "reviewed_at": (str, 1), "source_body_sha256": (str, 1)}


def stored_body(source_id, sha, dirs=()):
    """The body of source_id whose SHA-256 is sha, or None: from the store, then from the snapshots of past runs under outputs/ and `dirs`."""
    found = [STORE / source_id / f"{sha}.txt"]
    for d in [ROOT / "outputs", *map(Path, dirs)]:
        found += sorted(d.glob(f"**/snapshots/{source_id}/body.txt"))
    for path in found:
        try:
            raw = path.read_bytes()
            if ev.sha_bytes(raw) == sha:
                return raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return None


def keep_body(source_id, sha, body):
    """Keep the body a rule is reviewed against (only if it really has that SHA-256). -> whether a file was written."""
    path = STORE / source_id / f"{sha}.txt"
    if path.exists() or ev.sha_bytes(body.encode("utf-8")) != sha:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return True


def keep(only=(), prune=True):
    """Keep, for every source, the body its rules were reviewed against, as long as the page still has it; drop kept bodies that no rule refers to.
    Run on a schedule, this means the old body exists on the day a page changes (rereview needs it then, not before). Writes nothing but STORE."""
    reg = ev.load_registry()[0]
    ids = {s["source_id"] for s in reg["sources"]}
    if set(only) - ids:
        raise ev.ContractError(f"unknown source ids {sorted(set(only) - ids)}")
    wanted = {}
    for r in reg["rules"]:
        wanted.setdefault(r["source_id"], set()).add(r["source_body_sha256"])
    rows, kept = [], 0
    for sid, o in ev._observe_live(reg["sources"], only).items():
        if o.body is None:
            status = "not_fetched"
        elif o.sha256 not in wanted.get(sid, ()):
            status = "page_changed"
        else:
            fresh = keep_body(sid, o.sha256, o.body)
            kept, status = kept + fresh, "kept" if fresh else "already_kept"
        rows.append({"source_id": sid, "status": status, "error": o.error})
    pruned = 0
    if prune and not only and STORE.is_dir():
        for path in sorted(STORE.glob("*/*.txt")):
            if path.stem not in wanted.get(path.parent.name, ()):
                path.unlink()
                pruned += 1
        for d in sorted(STORE.glob("*")):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    missing = [{"source_id": sid, "source_body_sha256": sha} for sid, shas in sorted(wanted.items()) if not only or sid in only
               for sha in sorted(shas) if not (STORE / sid / f"{sha}.txt").is_file()]
    return {"schema_version": 1, "checked_at": ev.iso(ev.utcnow()), "verdict": "COMPLETE" if not missing else "PARTIAL",
            "bodies_kept": kept, "bodies_pruned": pruned, "without_old_body": missing, "sources": rows}


def clip(line, width=160):
    return line if len(line) <= width else line[:width - 1] + "…"


def neighbourhood(body, excerpt, context):
    """The excerpt's place in a body: its line numbers, the lines around it, the heading above it and how often it occurs. None if it is not there."""
    at = body.find(excerpt)
    if at < 0:
        return None
    lines = body.split("\n")
    first = body.count("\n", 0, at)
    last = first + excerpt.count("\n")
    heading = next((lines[i] for i in range(first, -1, -1) if lines[i].startswith("#")), "")
    return {"at": [first + 1, last + 1], "heading": heading, "count": body.count(excerpt), "lines": lines[max(0, first - context):last + context + 1]}


def section_lines(body, excerpt):
    """The lines of the section the excerpt is in: from the heading above it to the next heading of the same or a higher level."""
    lines = body.split("\n")
    first = body.count("\n", 0, body.find(excerpt))
    start = next((i for i in range(first, -1, -1) if lines[i].startswith("#")), 0)
    level = len(lines[start]) - len(lines[start].lstrip("#")) if lines[start].startswith("#") else 0
    end = next((i for i in range(first + 1, len(lines)) if lines[i].startswith("#") and len(lines[i]) - len(lines[i].lstrip("#")) <= level), len(lines))
    return lines[start:end]


def section_risks(old_body, new_body, excerpt):
    """Lines added to the excerpt's section that hold a risk word. The window of `context` lines misses them when they sit further away."""
    old, new = set(section_lines(old_body, excerpt)), section_lines(new_body, excerpt)
    return [clip(x) for x in new if x not in old and RISK_WORDS.search(x)]


def judge(rule, new_body, old_body, context):
    """How a drifted rule stands. class: excerpt_missing (it cannot be renewed) | old_body_unavailable | context_changed (a person reads the diff)
    | context_unchanged (the excerpt, `context` lines either side, the heading above it and its number of occurrences are the same in the old
    and the new body: the page changed somewhere else) | section_risk (as context_unchanged, but a line with a risk word was added to the excerpt's section,
    outside the window: a person reads it)."""
    new = neighbourhood(new_body, rule["source_excerpt"], context)
    if new is None:
        return {"class": "excerpt_missing", "lines": None}
    if old_body is None:
        return {"class": "old_body_unavailable", "lines": new["at"]}
    old = neighbourhood(old_body, rule["source_excerpt"], context)
    if old is not None and all(old[k] == new[k] for k in ("lines", "heading", "count")):
        risks = section_risks(old_body, new_body, rule["source_excerpt"])
        if risks:
            return {"class": "section_risk", "lines": new["at"], "added": risks[:10]}
        return {"class": "context_unchanged", "lines": new["at"]}
    shown = lambda n: ([n["heading"]] if n else []) + (n["lines"] if n else [])  # noqa: E731
    diff = list(difflib.unified_diff(shown(old), shown(new), "old", "new", lineterm="", n=1))
    return {"class": "context_changed", "lines": new["at"], "diff": [clip(x) for x in diff[:40]] if old else ["(the excerpt is not in the old body)"]}


def body_hunks(old, new, limit=20):
    """The places where two bodies differ, line by line: {"count": n, "hunks": [first `limit`, each side cut to 8 lines]}."""
    a, b = old.split("\n"), new.split("\n")
    hunks = [{"old_lines": [i1 + 1, i2], "new_lines": [j1 + 1, j2], "removed": [clip(x) for x in a[i1:i2][:8]], "added": [clip(x) for x in b[j1:j2][:8]]}
             for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal"]
    return {"count": len(hunks), "hunks": hunks[:limit]}


def look_all(reg, only):
    """(observed, rule states) as drift sees them: every source once, and the ones in doubt (a changed rule, no answer) once more, because
    one answer can be a CDN node mid-deploy or a dropped connection."""
    observed = ev._observe_live(reg["sources"], only)
    states = ev._rule_states(reg, observed)
    doubtful = {s["source_id"] for s in states if s["state"] in ("changed", "excerpt_missing")} | {k for k, o in observed.items() if o.body is None}
    if doubtful:
        time.sleep(ev.RETRY_WAIT)
        observed.update(ev._observe_live([s for s in reg["sources"] if s["source_id"] in doubtful], ()))
        states = ev._rule_states(reg, observed)
    return observed, states


def agent_reviewer(text):
    """The reviewer_id rereview writes. A person's review is recorded by `review`, never by this command."""
    if text is None:
        return MACHINE_REVIEWER
    if text.strip().startswith(ev.HUMAN_REVIEWER) or "not human" not in text:
        raise ev.ContractError('--reviewer must say it is not a person (…; not human): rereview never records a human review, `review` does')
    return text.strip()


def rereview(only=(), rule_ids=(), run=None, old_dirs=(), context=CONTEXT_LINES, accept=(), reviewer=None, write=False):
    reg = load_for_edit() if write else ev.load_registry()[0]
    rules = {r["rule_id"]: r for r in reg["rules"]}
    unknown = sorted((set(rule_ids) | set(accept)) - set(rules))
    if unknown:
        raise ev.ContractError(f"unknown rules {unknown}")
    if accept and reviewer is None:
        raise ev.ContractError("--accept needs --reviewer: say who judged that the rule still holds (…; not human)")
    reviewer = agent_reviewer(reviewer)
    only = set(only) | {rules[r]["source_id"] for r in [*rule_ids, *accept]}
    if only - {s["source_id"] for s in reg["sources"]}:
        raise ev.ContractError(f"unknown source ids {sorted(only - {s['source_id'] for s in reg['sources']})}")
    if run:
        observed = ev._observe_run(Path(run), only)
        states = ev._rule_states(reg, observed)
    else:
        observed, states = look_all(reg, only)
    kept = sum(keep_body(sid, o.sha256, o.body) for sid, o in observed.items()
               if o.body is not None and any(r["source_id"] == sid and r["source_body_sha256"] == o.sha256 for r in reg["rules"]))
    olds, rows = {}, []
    for st in states:
        if st["state"] not in ("changed", "excerpt_missing") or (rule_ids and st["rule_id"] not in rule_ids):
            continue
        rule, o = rules[st["rule_id"]], observed[st["source_id"]]
        key = (rule["source_id"], rule["source_body_sha256"])
        if key not in olds:
            olds[key] = stored_body(*key, old_dirs)
        row = {"rule_id": rule["rule_id"], "source_id": rule["source_id"], "state": st["state"], **judge(rule, o.body, olds[key], context)}
        row["renewable"] = row["class"] == "context_unchanged" or (rule["rule_id"] in accept and row["class"] in ("context_changed", "old_body_unavailable", "section_risk"))
        row["renewed"] = False
        rows.append(row)
    sources = []
    for sid, o in observed.items():
        mine = [r for r in rows if r["source_id"] == sid]
        if not mine and o.body is not None:
            continue
        entry = {"source_id": sid, "status": o.status, "error": o.error, "body_sha256": o.sha256}
        if mine:
            old_shas = sorted({rules[r["rule_id"]]["source_body_sha256"] for r in mine})
            entry["changes"] = [{"from_sha256": s, **body_hunks(olds[(sid, s)], o.body)} if olds[(sid, s)] is not None
                                else {"from_sha256": s, "old_body": "unavailable"} for s in old_shas]
        sources.append(entry)
    now = ev.iso(ev.utcnow())
    if write and any(r["renewable"] for r in rows):
        for row in rows:
            if row["renewable"]:
                o = observed[row["source_id"]]
                rules[row["rule_id"]].update(source_body_sha256=o.sha256, reviewed_at=now, reviewer_id=reviewer)
                row["renewed"] = True
        replace_registry(checked_registry_text(reg), reg)
        kept += sum(keep_body(r["source_id"], observed[r["source_id"]].sha256, observed[r["source_id"]].body) for r in rows if r["renewed"])
    pending = [r for r in rows if not r["renewed"]]
    if pending:
        verdict = "NEEDS_REVIEW" if any(not r["renewable"] for r in pending) else "READY_TO_STAMP"
    elif rows:
        verdict = "STAMPED"
    else:
        verdict = "INCOMPLETE" if any(o.body is None for o in observed.values()) else "CURRENT"
    return {"schema_version": 1, "checked_at": now, "mode": "run" if run else "live", "context_lines": context, "verdict": verdict,
            "reviewer_id": reviewer if write else None, "written": bool(write and any(r["renewed"] for r in rows)),
            "bodies_kept": kept, "sources": sources, "rules": rows}


# ---------------------------------------------------------------- review (a person, at a terminal)
AUTOMATION_ENV = ("CI", "GITHUB_ACTIONS", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")  # set by CI and by Claude Code; a person's own shell has none of them


def interactive():
    """A terminal on both ends, in a process that is not run by CI or by an agent. This checks how the process was started, not who is typing:
    a pty can be driven by a program, which the ledger and the tests around it do not prevent either (docs/trust-boundary.md)."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not any(os.environ.get(k) for k in AUTOMATION_ENV)


def human_ledger():
    """The reviews `review` recorded, oldest first. A rule may carry a human reviewer_id only if the ledger has the same review
    (tests/test_official_prompt_tailor.py): an agent that edits registry.json by hand does not leave one."""
    entries = ev.load_json(LEDGER) if LEDGER.exists() else []
    if not isinstance(entries, list):
        raise ev.ContractError(f"{LEDGER.name}: list expected")
    for e in entries:
        ev.shape(e, LEDGER_F, f"{LEDGER.name} entry")
    return entries


def rule_card(rule, source, number, total, body, sha, context):
    near = neighbourhood(body, rule["source_excerpt"], context) if body is not None else None
    lines = [f"━━ [{number}/{total}] {rule['rule_id']} ({rule['kind']}) ━━", f"資料: {source['source_id']}  {source['allowed_urls'][0]}",
             f"対象: {', '.join(rule['target_ids'])} / surfaces: {', '.join(rule['surfaces'])} / models: {', '.join(rule['models'])} / purposes: {', '.join(rule['purposes'])}",
             "使える前提:"] + [f"  - {x}" for x in rule["preconditions"]] + ["使えない場合:"] + [f"  - {x}" for x in rule["exclusions"]]
    lines += [f"意味: {rule['meaning']}", f"許される変更: {rule['allowed_change']}", f"引用: {rule['source_excerpt']}"]
    if near is None:
        lines.append("本文: 引用が本文にありません(取得できないか、本文が変わっています)。記録できません。")
    else:
        lines.append(f"本文の{near['at'][0]}〜{near['at'][1]}行目と、前後 {context} 行" +
                     ("(審査したときと同じ本文)" if sha == rule["source_body_sha256"] else "(審査のあとで本文が変わっています)") + ":")
        lines += [f"  {n:5d}  {x}" for n, x in enumerate(near["lines"], max(1, near["at"][0] - context))]
    return "\n".join(lines)


def review(rule_ids=(), only=(), run=None, context=CONTEXT_LINES, limit=None, ask=input, say=print):
    """Show each rule with the page it cites; for the ones whose name the reviewer types, record human:<name> against the body they saw."""
    if not interactive():
        raise ev.Unreadable("review needs an interactive terminal (stdin and stdout are terminals, and CI is not set): a person's review is not written from a script or an agent's tool call")
    reg = load_for_edit()
    srcs = {s["source_id"]: s for s in reg["sources"]}
    unknown = sorted((set(rule_ids) - {r["rule_id"] for r in reg["rules"]}) | (set(only) - set(srcs)))
    if unknown:
        raise ev.ContractError(f"unknown rule or source ids {unknown}")
    chosen = [r for r in reg["rules"] if r["review_status"] == "reviewed" and not ev.human_reviewed(r)
              and (not rule_ids or r["rule_id"] in rule_ids) and (not only or r["source_id"] in only)][:limit]
    seen, recorded, skipped, quit_ = {}, [], 0, False
    for number, rule in enumerate(chosen, 1):
        sid = rule["source_id"]
        if sid not in seen:
            try:
                if run:
                    o = ev._observe_run(Path(run), {sid}).get(sid)
                    seen[sid] = (o.body, o.sha256) if o and o.body is not None else (None, None)
                else:
                    body, sha = observe(srcs[sid])
                    seen[sid] = (body, sha)
            except ev.Unreadable as e:
                say(f"{sid}: 本文を取得できません({e})")
                seen[sid] = (None, None)
        body, sha = seen[sid]
        say(rule_card(rule, srcs[sid], number, len(chosen), body, sha, context))
        if body is None or rule["source_excerpt"] not in body:
            skipped += 1
            continue
        try:
            name = ask("この規則を審査した人の名前(空: 飛ばす / q: 終了): ").strip()
            if name and name != "q" and ask(f"human:{name} を {rule['rule_id']} に書きます。よければ y: ").strip().lower() not in ("y", "yes"):
                name = ""
        except EOFError:
            name = "q"
        if name == "q":
            quit_ = True
            break
        if not name or len(name) > 80:
            skipped += 1
            say("飛ばしました。" if not name else "名前は 80 文字までです。飛ばしました。")
            continue
        before = {k: rule[k] for k in ("source_body_sha256", "reviewed_at", "reviewer_id")}
        rule.update(source_body_sha256=sha, reviewed_at=ev.iso(ev.utcnow()), reviewer_id=ev.HUMAN_REVIEWER + name)
        try:
            text = checked_registry_text(reg)
        except ev.ContractError:
            rule.update(before)
            raise
        old_entries = human_ledger()  # the ledger first: a rule with a human reviewer_id and no entry would fail the guard, an entry without a rule would not
        entries = old_entries + [{"rule_id": rule["rule_id"], "reviewer_id": rule["reviewer_id"], "reviewed_at": rule["reviewed_at"], "source_body_sha256": sha}]
        LEDGER.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            replace_registry(text, reg)
        except Exception:
            rule.update(before)
            LEDGER.write_text(json.dumps(old_entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise
        keep_body(sid, sha, body)
        recorded.append(rule["rule_id"])
        say(f"記録しました: {rule['rule_id']} ← {rule['reviewer_id']}")
    return {"schema_version": 1, "recorded": recorded, "skipped": skipped, "quit": quit_, "remaining": len(chosen) - len(recorded) - skipped}


# ---------------------------------------------------------------- docs
README_PATH, TARGETS_PATH, TRUST_PATH = ROOT / "README.md", ROOT / "docs" / "targets.md", ROOT / "docs" / "trust-boundary.md"
ROUTING_LABELS = {"chatgpt": "ChatGPT Chat", "chatgpt-work": "ChatGPT Work", "codex": "Codex", "claude": "Claude チャット",
                  "claude-cowork": "Claude Cowork", "claude-code": "Claude Code", "gemini": "Gemini Apps(通常・Gems・Apps skill)",
                  "antigravity": "Antigravity", "gemini-agent-app": "Gemini Apps の Tasks", "gemini-cli": "Gemini CLI",
                  "google-adk-agent": "Google ADK のエージェント"}
COUNT_LABELS = {**ROUTING_LABELS, "gemini": "Gemini Apps", "google-adk-agent": "Google ADK"}
VENDORS = {"openai": ["chatgpt", "chatgpt-work", "codex"], "anthropic": ["claude", "claude-cowork", "claude-code"],
           "google": ["gemini", "antigravity", "gemini-agent-app", "gemini-cli", "google-adk-agent"]}
BLOCK = re.compile(r"(<!-- generated:(?P<name>[a-z-]+) begin[^\n]*-->\n)(?P<body>.*?)(<!-- generated:(?P=name) end -->)", re.S)


def _ids(xs):
    return "、".join(f"`{x}`" for x in xs)


def routing_table(reg, targets):
    """What each target fetches, from select_sources: the always-fetched sources, then what a purpose adds, then what a surface adds."""
    surfaces = sorted({s for t in reg["targets"] for c in t["conditional_sources"] for s in c["surfaces"]} - {"all"})
    rows = ["| 利用先 | `target_id` | 常に取得 | 条件付き |", "|---|---|---|---|"]
    for tid in targets:
        pick = lambda purpose, surface: ev.select_sources(reg, {"target_id": tid, "purpose": purpose, "target_surface": surface, "model_id": "unknown"})  # noqa: E731
        required = next(t for t in reg["targets"] if t["target_id"] == tid)["required_sources"]
        cells, by_purpose = [], {}
        for p in ev.PURPOSES:
            by_purpose.setdefault(tuple(i for i in pick(p, "unknown") if i not in required), []).append(p)
        cells += ["・".join(f"`{p}`" for p in ps) + ": " + _ids(extra) for extra, ps in by_purpose.items() if extra]
        for surface in surfaces:
            added = {}
            for p in ev.PURPOSES:
                added.setdefault(tuple(i for i in pick(p, surface) if i not in pick(p, "unknown")), []).append(p)
            cells += [f"`{surface}`" + ("" if len(ps) == len(ev.PURPOSES) else "(" + "・".join(f"`{p}`" for p in ps) + ")") + ": " + _ids(add)
                      for add, ps in added.items() if add]
        rows.append(f"| {ROUTING_LABELS[tid]} | `{tid}` | {_ids(required) or 'なし'} | {'<br>'.join(cells) or 'なし'} |")
    return "\n".join(rows) + "\n"


def count_table(reg):
    """Reviewed official rules a purpose can use per target, when the surface and the model are unknown."""
    rows = ["| 利用先 | " + " | ".join(ev.PURPOSES) + " |", "|---|" + "---|" * len(ev.PURPOSES)]
    for tid in ev.TARGET_IDS:
        cells = []
        for p in ev.PURPOSES:
            chosen = ev.select_sources(reg, {"target_id": tid, "purpose": p, "target_surface": "unknown", "model_id": "unknown"})
            cells.append("資料なし" if not chosen else str(sum(
                1 for r in reg["rules"] if r["review_status"] == "reviewed" and r["kind"] == "official" and r["source_id"] in chosen
                and tid in r["target_ids"] and p in r["purposes"] and ev.in_list(r["surfaces"], "unknown") and ev.in_list(r["models"], "unknown"))))
        rows.append(f"| {COUNT_LABELS[tid]} | " + " | ".join(cells) + " |")
    return "\n".join(rows) + "\n"


def model_table(reg):
    """Per model: the sources fetched only for it (its guide, and any other source whose rules name it), and the rules that name it."""
    rows = ["| `model_id` | 取得される、モデル限定の資料 | 使える規則 |", "|---|---|---|"]
    for m in sorted({m for s in reg["sources"] for m in s["models"]} - {"all"}):
        rows.append(f"| `{m}` | {_ids(s['source_id'] for s in reg['sources'] if m in s['models'])} | {sum(1 for r in reg['rules'] if m in r['models'])} 件 |")
    return "\n".join(rows) + "\n"


def doc_blocks(reg):
    missing = (set(ev.TARGET_IDS) - set(ROUTING_LABELS)) | (set(ev.TARGET_IDS) - {t for ts in VENDORS.values() for t in ts})
    if missing:
        raise ev.ContractError(f"add {sorted(missing)} to ROUTING_LABELS and VENDORS in tools/maintain.py")
    blocks = {f"routing-{vendor}": routing_table(reg, targets) for vendor, targets in VENDORS.items()}
    return {**blocks, "rule-counts": count_table(reg), "model-guides": model_table(reg)}


def inline_numbers(reg):
    """(file, pattern, values): numbers written into prose. The pattern's groups are the numbers, in order."""
    return [(README_PATH, re.compile(r"資料\((\d+)\)・対象\((\d+)\)・審査済み規則\((\d+)\)"),
             (len(reg["sources"]), len(reg["targets"]), sum(1 for r in reg["rules"] if r["review_status"] == "reviewed"))),
            (TARGETS_PATH, re.compile(r"次の節の (\d+) 種"), (len(reg["targets"]),)),
            (TARGETS_PATH, re.compile(r"モデルを限定した規則は (\d+) 件です"), (sum(1 for r in reg["rules"] if r["models"] != ["all"]),)),
            (TRUST_PATH, re.compile(r"人の審査は (\d+) 件です"), (sum(1 for r in reg["rules"] if ev.human_reviewed(r)),))]


def fill_numbers(pattern, text, values):
    def sub(m):
        out, last = [], 0
        for i, value in enumerate(values, 1):
            a, b = m.start(i) - m.start(), m.end(i) - m.start()
            out += [m.group(0)[last:a], str(value)]
            last = b
        return "".join(out) + m.group(0)[last:]
    return pattern.sub(sub, text)


def replace_blocks(text, blocks):
    """-> (text with each generated block rewritten, problems). A block the document lacks, or one it has that is not known, is a problem."""
    seen, problems = set(), []

    def sub(m):
        if m.group("name") not in blocks:
            problems.append(f"unknown generated block {m.group('name')}")
            return m.group(0)
        seen.add(m.group("name"))
        return m.group(1) + blocks[m.group("name")] + m.group(4)
    new = BLOCK.sub(sub, text)
    return new, problems + [f"generated block {n} is missing" for n in blocks if n not in seen]


def docs(write=False):
    reg, _ = ev.load_registry()
    paths = (README_PATH, TARGETS_PATH, TRUST_PATH)
    try:
        old = {p: ev.norm(p.read_bytes().decode("utf-8")) for p in paths}
    except FileNotFoundError as e:
        raise ev.Unreadable(f"missing document: {e.filename}")
    new, problems = dict(old), []
    for path, pattern, values in inline_numbers(reg):
        if pattern.search(new[path]):
            new[path] = fill_numbers(pattern, new[path], values)
        else:
            problems.append(f"{path.name}: no text matches {pattern.pattern}")
    new[TARGETS_PATH], more = replace_blocks(new[TARGETS_PATH], doc_blocks(reg))
    stale = [p.name for p in paths if new[p] != old[p]]
    if write and not problems + more:
        for p in paths:
            if new[p] != old[p]:
                p.write_bytes(new[p].encode("utf-8"))
    return {"schema_version": 1, "written": bool(write and stale and not problems + more), "stale": stale, "problems": problems + more}


# ---------------------------------------------------------------- audit
SCALE = {"参照検証": "PASS_LIVE|PASS_CACHE|FAIL|INCOMPLETE", "意味審査": "NOT_REVIEWED|INCOMPLETE|PASS|FAIL",
         "効果": "IMPROVED_ON_TEST_SET|REGRESSED|NO_CLEAR_IMPROVEMENT|NOT_EVALUATED|INCOMPLETE"}
NEGATED = re.compile(r"ない|なく|ません|不|未")  # 「検証済みではなく候補」も否定
LITERAL = re.compile(r"`[^`\n]+`|[A-Za-z_][A-Za-z0-9_./:\-]*[A-Za-z0-9_]|\d+(?:\.\d+)*")


def codex_transcript_texts(rows):
    prompts, answer = [], ""
    for _, record in rows:
        if not isinstance(record, dict) or record.get("type") != "response_item":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        blocks = payload.get("content")
        if not isinstance(blocks, list):
            raise ev.ContractError("Codex message content must be a list")
        texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") in ("input_text", "output_text")]
        if any(not isinstance(text, str) for text in texts):
            raise ev.ContractError("Codex message text must be a string")
        text = "\n".join(texts)
        if payload.get("role") == "user":
            prompts.append(text)
        elif payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
            answer = text
    return "\n".join(prompts), answer


def transcript_texts(path, host="claude-code"):
    """User request and final answer from the host's own transcript, without reasoning blocks."""
    rows, own = ev.read_transcript(path)
    if host == "codex":
        return codex_transcript_texts(rows)
    prompt = answer = ""
    handed = False  # once the report is handed back, text after it is not the answer
    for _, d in rows:
        msg = d.get("message") if isinstance(d, dict) else None
        if not isinstance(msg, dict) or (d.get("isSidechain") and not own):
            continue
        blocks = [{"type": "text", "text": msg["content"]}] if isinstance(msg.get("content"), str) else (msg.get("content") or [])
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if text.strip() and d.get("type") == "user" and not prompt:
            prompt = text
        elif d.get("type") == "assistant":
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "SubagentHandback" and isinstance(b.get("input", {}).get("message"), str):
                    answer, handed = b["input"]["message"], True
                elif b.get("type") == "text" and b.get("text", "").strip() and not handed:
                    answer = b["text"]
    return prompt, answer


def expand_variables(command):
    """The command with the shell variables it assigns itself filled in (`S=…/evidence.py; python3 $S drift`), and a `for c in a b; do … $c …`
    loop written out once per word: the audit looks for the commands that were run, not for how they were spelled."""
    values = {k: v.strip("'\"") for k, v in re.findall(r"(?:^|[;&|\s])([A-Za-z_]\w*)=(\S+?);?(?=\s|$)", command)}
    text = command
    for var, words in re.findall(r"\bfor\s+([A-Za-z_]\w*)\s+in\s+([^;\n]+?)\s*;", command):
        text += "\n" + "\n".join(re.sub(r"\$\{?%s\}?" % var, w, command) for w in words.split())
    return re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: values.get(m.group(1), m.group(0)), text)


def squash(text):
    return "\n".join(line.rstrip() for line in ev.norm(text).strip().split("\n") if line.strip())


def pasted_report(run, answer, host_log=None):
    """(PASS | WARN | FAIL, detail): does the answer hold the sources report as the script prints it now? The format-mark line is not
    compared (a model may drop an invisible line). A report in an older format (a lower mark, or none before the mark existed) cannot be compared."""
    plain = lambda text: squash(ev.FORMAT_MARK.sub("", text))  # noqa: E731
    if plain(ev.render_sources(ev.sources_report(run, host_log))) in plain(answer):
        return "PASS", "the answer holds the sources --markdown report as the script prints it"
    mark = ev.FORMAT_MARK.search(answer)
    pasted = int(mark.group(1)) if mark else (1 if re.search(r"^# 見た資料: ", answer, re.M) else None)
    if pasted is not None and pasted != ev.SOURCES_FORMAT:
        return "WARN", f"the answer holds a report in format {pasted}; the script prints format {ev.SOURCES_FORMAT}, so the two cannot be compared"
    return "FAIL", "the answer does not hold the sources --markdown report as the script prints it"


def audit(run, transcript):
    """Did the agent that made this run follow the skill's procedure and report it honestly? Rule-based, from the run's files and the
    agent's own transcript; what the optimised prompt means is not judged. -> {"verdict": PASS|FAIL, "checks": [{id, status, detail}]}"""
    run = Path(run)
    seen = ev.sources_report(run, transcript)  # consistency of the run, and the reads the transcript shows
    host = ev.load_json(run / "input.json")["execution_host"]
    try:
        if host not in ev.LOG_ADAPTER_HOSTS:
            raise ev.ContractError(f"no audit log adapter for {host}")
        events = ev.parse_host_log(host, transcript)
        prompt, answer = transcript_texts(transcript, host)
    except ev.ContractError as error:
        return {"schema_version": 1, "run_id": run.name, "verdict": "FAIL", "sources_verdict": seen["verdict"],
                "checks": [{"id": "host_log_valid", "status": "FAIL", "detail": str(error)}]}
    if host == "codex":
        events = [dict(e, name="Bash", input={"command": "\n".join(c for c in e["input"]["cmds"] if c)})
                  if e["kind"] == "use" and e["name"] == "CodexExec" else e for e in events]
    checks = []

    def check(cid, ok, detail, level="FAIL"):
        checks.append({"id": cid, "status": "PASS" if ok else level, "detail": detail})

    bash = [(e["seq"], expand_variables(str(e["input"].get("command", "")))) for e in events if e["kind"] == "use" and e["name"] == "Bash"]
    ran = lambda name: [s for s, c in bash if re.search(r"evidence\.py\s+" + name + r"\b", c)]  # noqa: E731
    gen_paths = [run / "optimized.md", run / "recommendations.json"]
    writes = [e["seq"] for e in events if e["kind"] == "use" and (
        (e["name"] in ("Write", "Edit") and ev._resolves_to(e["input"].get("file_path"), gen_paths))
        or (e["name"] == "Bash" and any(g.name in str(e["input"].get("command", "")) for g in gen_paths)))]
    first_write = min(writes) if writes else None
    fetch, drift, rules, verify, sources = (ran(n) for n in ("fetch", "drift", "rules", "verify", "sources"))
    where = f"(record lines: fetch {fetch}, drift {drift}, rules {rules}, first write {first_write}, verify {verify}, sources {sources})"
    check("fetched", bool(fetch), where)
    check("wrote_the_result", first_write is not None, where)
    check("drift_before_writing", bool(drift) and first_write is not None and min(drift) < first_write, where)
    check("rules_before_writing", bool(rules) and first_write is not None and min(rules) < first_write, where)
    check("verified_after_writing", bool(verify) and first_write is not None and max(verify) > first_write and (run / "verification.json").is_file(),
          where + (" verification.json is there" if (run / "verification.json").is_file() else " verification.json is missing"))
    first_verify = min((v for v in verify if first_write is not None and v > first_write), default=None)  # a later verify may re-check; the report only needs one before it
    check("sources_after_verify", first_verify is not None and any(s > first_verify for s in sources), where)
    unread = [r["source_id"] for r in seen["selected"] if r["read"]["status"] != "observed"]  # verify asks for a read of every selected source
    if seen["selected"]:
        check("selected_bodies_were_read", not unread,
              f"not shown by a Read before the first write: {unread}" if unread else "every selected source was shown by a Read before the first write")
    else:  # an input none of whose routes applies selects no source (SKILL.md, "資料が 0 件"): there is nothing to read
        checks.append({"id": "selected_bodies_were_read", "status": "NA", "detail": "no source was selected for this input, so there was nothing to read"})
    own_logs = [p.strip("'\"") for _, c in bash for p in re.findall(r"--host-log[ =]+(\S+)", c)]
    check("no_log_the_user_did_not_give", all(p in prompt for p in own_logs), f"--host-log used with {[p for p in own_logs if p not in prompt]}")
    whole = [e for e in events if e["kind"] == "use" and (
        (e["name"] == "Read" and str(e["input"].get("file_path", "")).endswith("registry.json") and not e["input"].get("limit"))
        or (e["name"] == "Bash" and re.search(r"\b(cat|less|more)\s+[^|;&]*registry\.json", str(e["input"].get("command", "")))))]
    check("registry_not_read_whole", not whole, "registry.json was read whole" if whole else "registry.json was not read whole")

    values = {label: (re.search(label + r"\s*[:\uff1a]\s*[*`_\s]*(" + alt + r")(?![A-Z_])", answer) or [None, None])[1] for label, alt in SCALE.items()}
    check("answer_has_three_lines", None not in values.values(), f"read {values}")
    load = lambda name: ev.load_json(run / name) if (run / name).is_file() else None  # noqa: E731
    review, review_run, evaluation = load("semantic-review.json"), load("semantic-verification.json"), load("evaluation.json")
    reviewed = review is not None and review["status"] != "NOT_REVIEWED" and review_run is not None
    # A record that is missing says "not done": no review that assess ran is NOT_REVIEWED, no evaluation is NOT_EVALUATED.
    want = {"参照検証": (load("verification.json") or {}).get("verdict"),
            "意味審査": (review_run.get("verdict") if reviewed else None) or "NOT_REVIEWED",
            "効果": (evaluation or {}).get("status") or "NOT_EVALUATED"}
    off = {k: (values[k], w) for k, w in want.items() if w is not None and values[k] != w}
    check("answer_matches_the_records", not off, f"(answer, record): {off}" if off else "the three lines equal the run's records")
    sentence = lambda m: re.split(r"[。\n]", answer[m.end():], maxsplit=1)[0][:16]  # noqa: E731  # a "not" in the next sentence does not deny this one
    said = lambda words: [m.group(0) + sentence(m) for m in re.finditer(words, answer) if not NEGATED.search(sentence(m))]  # noqa: E731
    if values["参照検証"] in ("PASS_LIVE", "PASS_CACHE") and values["意味審査"] == "PASS":  # may be adopted; an improvement still needs a measurement
        claims = [] if want["効果"] == "IMPROVED_ON_TEST_SET" else said("改善済み|実測済み")
        check("no_overclaim", not claims, f"improvement claimed without a measurement: {claims}" if claims else "an adoptable proposal; no unmeasured improvement is claimed")
    else:
        claims = said("検証済み|改善済み|実測済み")
        check("no_overclaim", "候補" in answer and not claims, f"claims: {claims}" if claims else "marked as a candidate, nothing called verified or improved")
    optimized = ev.norm((run / "optimized.md").read_text(encoding="utf-8")).strip() if (run / "optimized.md").is_file() else None
    check("copy_prompt_in_answer", optimized is not None and optimized in ev.norm(answer), "optimized.md appears in the answer as it is")
    source_commands = [c for _, c in bash if re.search(r"evidence\.py\s+sources\b", c)]
    report_log = transcript if source_commands and "--host-log" in source_commands[-1] else None
    status, detail = pasted_report(run, answer, report_log)
    check("sources_report_pasted_as_printed", status == "PASS", detail, level=status)
    original = ev.norm((run / "original.md").read_text(encoding="utf-8")) if (run / "original.md").is_file() else ""
    lost = sorted(t for t in {x.strip("`") for x in LITERAL.findall(original)} if optimized is None or t not in optimized)
    check("original_literals_kept", not lost, f"not in optimized.md: {lost}" if lost else "commands, numbers and names of the original are all kept", level="WARN")
    return {"schema_version": 1, "run_id": run.name, "verdict": "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS",
            "sources_verdict": seen["verdict"], "checks": checks}


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
    a = sub.add_parser("adopt")
    a.add_argument("--spec", required=True)
    a.add_argument("--write", action="store_true")
    rr = sub.add_parser("rereview")
    rr.add_argument("--source", action="append", default=[])
    rr.add_argument("--rule", action="append", default=[])
    rr.add_argument("--run")
    rr.add_argument("--old", action="append", default=[])
    rr.add_argument("--context", type=int, default=CONTEXT_LINES)
    rr.add_argument("--accept", action="append", default=[])
    rr.add_argument("--reviewer")
    rr.add_argument("--write", action="store_true")
    kp = sub.add_parser("keep")
    kp.add_argument("--source", action="append", default=[])
    kp.add_argument("--no-prune", action="store_true")
    rv = sub.add_parser("review")
    rv.add_argument("--source", action="append", default=[])
    rv.add_argument("--rule", action="append", default=[])
    rv.add_argument("--run")
    rv.add_argument("--context", type=int, default=CONTEXT_LINES)
    rv.add_argument("--limit", type=int)
    dd = sub.add_parser("docs")
    dd.add_argument("--write", action="store_true")
    au = sub.add_parser("audit")
    au.add_argument("--run", required=True)
    au.add_argument("--transcript", required=True)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # the JSON holds Japanese text; Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "discover":
            rep = discover(args.mark_seen)
            found = sum(len(row["candidates"]) for row in rep["indexes"])
            return ev._emit("discover", 0 if rep["verdict"] == "NONE" else 2, rep, f"{rep['verdict']} {found} candidates")
        if args.cmd == "adopt":
            rep = adopt(args.spec, args.write)
            n = sum(len(s["rules"]) for s in rep["sources"])
            return ev._emit("adopt", 0, rep, f"{len(rep['sources'])} sources, {n} rules, " + ("written" if rep["written"] else "dry run: nothing written"))
        if args.cmd == "rereview":
            rep = rereview(args.source, args.rule, args.run, args.old, args.context, args.accept, args.reviewer, args.write)
            n = lambda cls: sum(1 for r in rep["rules"] if r["class"] == cls)  # noqa: E731
            return ev._emit("rereview", 0 if rep["verdict"] in ("CURRENT", "STAMPED") else 2, rep,
                            f"{rep['verdict']} {len(rep['rules'])} rules (unchanged around the excerpt {n('context_unchanged')}, changed {n('context_changed')}, "
                            f"no old body {n('old_body_unavailable')}, excerpt gone {n('excerpt_missing')}), " + ("written" if rep["written"] else "nothing written"))
        if args.cmd == "keep":
            rep = keep(args.source, not args.no_prune)
            return ev._emit("keep", 0 if rep["verdict"] == "COMPLETE" else 2, rep,
                            f"{rep['verdict']} kept {rep['bodies_kept']}, pruned {rep['bodies_pruned']}, no old body for {len(rep['without_old_body'])}")
        if args.cmd == "review":
            rep = review(args.rule, args.source, args.run, args.context, args.limit)
            return ev._emit("review", 0, rep, f"{len(rep['recorded'])} recorded, {rep['skipped']} skipped, {rep['remaining']} not looked at")
        if args.cmd == "audit":
            rep = audit(args.run, args.transcript)
            bad = [c["id"] for c in rep["checks"] if c["status"] == "FAIL"]
            return ev._emit("audit", 0 if rep["verdict"] == "PASS" else 1, rep, rep["verdict"] + (f" {bad}" if bad else "")
                            + (f", warnings {[c['id'] for c in rep['checks'] if c['status'] == 'WARN']}" if any(c["status"] == "WARN" for c in rep["checks"]) else ""))
        if args.cmd == "docs":
            rep = docs(args.write)
            code = 1 if rep["problems"] else (2 if rep["stale"] and not rep["written"] else 0)
            return ev._emit("docs", code, rep, "problems: " + "; ".join(rep["problems"]) if rep["problems"] else
                            ("out of date: " + ", ".join(rep["stale"]) + " (run docs --write)" if code == 2 else
                             "refreshed: " + ", ".join(rep["stale"]) if rep["written"] else "up to date"))
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
