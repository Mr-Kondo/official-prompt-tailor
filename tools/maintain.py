#!/usr/bin/env python3
"""Maintainer tools for official-prompt-tailor (not shipped with the skill): discover / propose / adopt / docs / audit.

discover: list pages in each host's llms.txt that look relevant and are not registered or triaged yet.
propose:  turn one candidate URL into a registry "sources" entry that passes the tool's own checks.
adopt:    add the sources and reviewed rules of a spec file to registry.json (a dry run unless --write).
docs:     check, or with --write refresh, the numbers and tables in README.md and docs/ that come from registry.json.
audit:    check, from a run and the agent's own transcript, that the skill's procedure was followed and reported straight.
discover, propose and audit only print (discover --mark-seen also records the listed pages as triaged). adopt is the one command that
edits registry.json, and only with --write: which rules a page holds and what they mean is judged by whoever writes the
spec (docs/maintenance.md); adopt checks the spec against the live page and records who reviewed. Standard library only.
"""
import argparse
import json
import os
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


def registry_problems(reg):
    """What the repository requires of a registry beyond validate_registry: no source without a route or a rule, every rule
    reviewed and complete, API advice kept to the API surface, and a model's guide grounding only that model's rules."""
    sources = {s["source_id"]: s for s in reg["sources"]}
    routed = {i for t in reg["targets"] for i in t["required_sources"] + [x for c in t["conditional_sources"] for x in c["source_ids"]]}
    problems = [f"source {i} is not routed by any target" for i in sorted(set(sources) - routed)]
    problems += [f"source {i} has no rule" for i in sorted(set(sources) - {r["source_id"] for r in reg["rules"]})]
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


def adopt(spec_path, write=False):
    reg, _ = ev.load_registry()
    original = ev.REGISTRY_PATH.read_bytes()
    if dump_registry(reg).encode("utf-8") != original:
        raise ev.ContractError("registry.json is not in the layout dump_registry writes (one compact record per line): put it back first")
    spec = ev.load_json(spec_path)
    ev.shape(spec, SPEC_F, "spec")
    if spec["schema_version"] != 1 or not spec["reviewer_id"].strip():
        raise ev.ContractError('spec: schema_version must be 1 and reviewer_id must say who reviewed ("human:<name>" for a person)')
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
    ev.validate_registry(reg)
    problems = registry_problems(reg)
    if problems:
        raise ev.ContractError("; ".join(problems))
    text = dump_registry(reg).encode("utf-8")
    if write:
        tmp = ev.REGISTRY_PATH.with_name(ev.REGISTRY_PATH.name + ".tmp")
        tmp.write_bytes(text)
        os.replace(tmp, ev.REGISTRY_PATH)
    return {"schema_version": 1, "written": write, "reviewer_id": spec["reviewer_id"], "sources": rows,
            "registry": {"sources": len(reg["sources"]), "rules": len(reg["rules"]), "bytes": len(text)}}


# ---------------------------------------------------------------- docs
ROOT = HERE.parent
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
    """Per model: its guide, and the rules that name the model (the guide's own, and those of a source for every model)."""
    rows = ["| `model_id` | 取得される、モデル別ガイド | 使える規則 |", "|---|---|---|"]
    for s in reg["sources"]:
        if s["models"] == ["all"]:
            continue
        for m in s["models"]:
            others = [r["rule_id"] for r in reg["rules"] if r["source_id"] != s["source_id"] and m in r["models"]]
            n = sum(1 for r in reg["rules"] if r["source_id"] == s["source_id"] and m in r["models"]) + len(others)
            rows.append(f"| `{m}` | `{s['source_id']}` | {n} 件" + (f"({_ids(others)} を含む)" if others else "") + " |")
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
NEGATED = re.compile(r"ない|ません|不|未")
LITERAL = re.compile(r"`[^`\n]+`|[A-Za-z_][A-Za-z0-9_./:\-]*[A-Za-z0-9_]|\d+(?:\.\d+)*")


def transcript_texts(path):
    """(the first user text, the last assistant text) of a Claude Code transcript's own thread."""
    rows, own = ev.read_transcript(path)
    prompt = answer = ""
    for _, d in rows:
        msg = d.get("message") if isinstance(d, dict) else None
        if not isinstance(msg, dict) or (d.get("isSidechain") and not own):
            continue
        blocks = [{"type": "text", "text": msg["content"]}] if isinstance(msg.get("content"), str) else (msg.get("content") or [])
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if text.strip() and d.get("type") == "user" and not prompt:
            prompt = text
        elif text.strip() and d.get("type") == "assistant":
            answer = text
    return prompt, answer


def squash(text):
    return "\n".join(line.rstrip() for line in ev.norm(text).strip().split("\n") if line.strip())


def audit(run, transcript):
    """Did the agent that made this run follow the skill's procedure and report it honestly? Rule-based, from the run's files and the
    agent's own transcript; what the optimised prompt means is not judged. -> {"verdict": PASS|FAIL, "checks": [{id, status, detail}]}"""
    run = Path(run)
    seen = ev.sources_report(run, transcript)  # consistency of the run, and the reads the transcript shows
    events, (prompt, answer) = ev.parse_transcript(transcript), transcript_texts(transcript)
    checks = []

    def check(cid, ok, detail, level="FAIL"):
        checks.append({"id": cid, "status": "PASS" if ok else level, "detail": detail})

    bash = [(e["seq"], str(e["input"].get("command", ""))) for e in events if e["kind"] == "use" and e["name"] == "Bash"]
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
    check("selected_bodies_were_read", bool(seen["selected"]) and not unread,
          f"not shown by a Read before the first write: {unread}" if unread else "every selected source was shown by a Read before the first write")
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
    shown = squash(ev.render_sources(ev.sources_report(run)))
    check("sources_report_pasted_as_printed", shown in squash(answer), "the answer holds the sources --markdown report as the script prints it")
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
