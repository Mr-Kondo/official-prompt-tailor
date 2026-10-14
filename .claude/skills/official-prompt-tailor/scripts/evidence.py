#!/usr/bin/env python3
"""official-prompt-tailor evidence tool: fetch / verify / assess / compare.

Standard library only. It verifies recorded evidence (acquisition, hashes, citations,
scope, change binding, observed reads, review records, evaluation records); it does not
prove comprehension, semantic correctness or performance. See references/evidence-contract.md.
"""
import argparse
import difflib
import hashlib
import html.parser
import http.client
import ipaddress
import json
import os
import re
import socket
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SKILL_DIR / "references" / "registry.json"
RUBRIC_PATH = SKILL_DIR / "references" / "review-rubric.md"

HOSTS = ("claude-code", "codex", "chatgpt-work", "antigravity", "claude-cowork")  # products that can run this skill
LOG_ADAPTER_HOSTS = ("claude-code",)  # hosts whose session logs the read-observation adapter understands
TARGET_IDS = ("chatgpt", "chatgpt-work", "codex", "claude", "claude-cowork", "claude-code", "gemini",
              "antigravity", "gemini-agent-app", "gemini-cli", "google-adk-agent")
PURPOSES = ("one-shot", "persistent-instructions", "skill-instructions", "agent-instruction")
EXTRACTIONS = ("html-main-v1", "html-article-v1", "html-body-v1", "markdown-v1")
FRESH_DAYS = 7  # local policy, not a vendor rule
MAX_HOPS, TIMEOUT, MAX_BYTES = 5, 15, 5 * 1024 * 1024
MAX_EXCERPT = 400
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
JUDGMENTS = ("supported", "contradicted", "insufficient")
CATEGORIES = ("source-support", "scope", "intent-preservation", "unrequested-additions")
LOG_ADAPTER = "claude-code-transcript-jsonl-v1"
CHECKS = ("structure", "official_acquisition", "body_integrity", "citation", "scope", "rule_review",
          "change_binding", "read_observation")


class ContractError(Exception):
    """Contract violation (exit 1)."""


class Unreadable(Exception):
    """Input unreadable / not runnable (exit 2)."""


class Blocked(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- basics
def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime(TS_FMT)


def parse_ts(s, where="timestamp"):
    try:
        return datetime.strptime(s, TS_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise ContractError(f"{where}: bad UTC timestamp {s!r}")


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha_text(text):
    return sha_bytes(norm(text).encode("utf-8"))


def read_text(p):
    """UTF-8 strict + LF normalisation."""
    try:
        return norm(Path(p).read_bytes().decode("utf-8"))
    except UnicodeDecodeError:
        raise ContractError(f"{p}: not strict UTF-8")


def load_json(p):
    try:
        return json.loads(Path(p).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise Unreadable(f"missing file: {p}")
    except (UnicodeDecodeError, ValueError) as e:
        raise ContractError(f"{p}: invalid JSON ({e})")


def write_json(p, obj):
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def shape(obj, fields, where):
    """fields: {key: (types, required)}. Rejects unknown keys, missing keys, wrong types."""
    if not isinstance(obj, dict):
        raise ContractError(f"{where}: object expected")
    extra = sorted(set(obj) - set(fields))
    if extra:
        raise ContractError(f"{where}: unknown keys {extra}")
    for k, (t, req) in fields.items():
        if k not in obj:
            if req:
                raise ContractError(f"{where}: missing key {k}")
            continue
        types = t if isinstance(t, tuple) else (t,)
        v = obj[k]
        if (isinstance(v, bool) and bool not in types) or not isinstance(v, types):
            raise ContractError(f"{where}.{k}: wrong type {type(v).__name__}")


def strs(v, where, nonempty=False):
    if not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v) or (nonempty and not v):
        raise ContractError(f"{where}: non-empty string array expected")
    return v


def uniq(ids, what):
    if len(ids) != len(set(ids)):
        raise ContractError(f"duplicate {what}")


def safe_id(v, where):
    if not isinstance(v, str) or not SAFE_ID.match(v):
        raise ContractError(f"{where}: unsafe id {v!r}")


def safe_join(base, rel):
    """Join rel under base; reject absolute, '..', symlink components and escapes."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or ".." in Path(rel).parts:
        raise ContractError(f"unsafe relative path {rel!r}")
    cur = Path(base)
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            raise ContractError(f"symlink in path {rel!r}")
    if not cur.resolve().is_relative_to(Path(base).resolve()):
        raise ContractError(f"path escapes base: {rel!r}")
    return cur


def in_list(listed, value):
    """'all' matches anything; 'unknown' only ever matches 'all'."""
    return "all" in listed or (value != "unknown" and value in listed)


S, SN, I, L, D, B, N = str, (str, type(None)), int, list, dict, bool, (int, float)
NN = (int, float, type(None))


# ---------------------------------------------------------------- URL / network
def url_key(u):
    try:
        p = urllib.parse.urlsplit(u)
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443) or not p.hostname:
            return None
        return (p.hostname.lower(), p.path, p.query)
    except ValueError:
        return None


def url_allowed(url, src):
    """Exact host/path/query match against the source's registered URLs (fragment ignored)."""
    k = url_key(urllib.parse.urldefrag(url)[0])
    if k is None:
        return False, "url_not_https_or_has_userinfo_or_port"
    if not any(url_key(a) == k for a in src["allowed_urls"]):
        return False, "url_not_registered"
    return True, ""


def check_host_public(host):
    try:
        ipaddress.ip_address(host)
        raise Blocked("ip_literal_rejected")
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".localhost"):
        raise Blocked("localhost_rejected")
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        if any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy")):
            return "dns_delegated_to_proxy"  # residual risk: documented in README
        raise Blocked("dns_failure")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise Blocked("non_public_address")
    return "checked"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_get(url):
    """One request, redirects not followed. Returns (status, lower-cased headers, body<=MAX_BYTES+1)."""
    req = urllib.request.Request(url, headers={"User-Agent": "official-prompt-tailor/1",
                                               "Accept": "text/html,text/markdown,text/plain;q=0.9"})
    try:
        r = urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        status = getattr(r, "status", None) or r.code
        return status, {k.lower(): v for k, v in r.headers.items()}, r.read(MAX_BYTES + 1)


# ---------------------------------------------------------------- body extraction
_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "aside", "footer", "iframe", "button",
         "select", "dialog"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
         "track", "wbr"}
_BLOCK = {"p", "div", "li", "ul", "ol", "pre", "table", "tr", "td", "th", "section", "article", "main",
          "blockquote", "dt", "dd", "dl", "details", "summary", "figure", "figcaption", "header", "caption",
          "thead", "tbody", "h1", "h2", "h3", "h4", "h5", "h6"}
_HEAD = {f"h{i}": i for i in range(1, 7)}


class _Extract(html.parser.HTMLParser):
    """Collect text of the first <container> element, minus navigation/script-like elements."""

    def __init__(self, container):
        super().__init__(convert_charrefs=True)
        self.container, self.stack, self.flags = container, [], []
        self.region = None
        self.skip = self.pre = 0
        self.done = False
        self.lines, self.buf = [], []

    def _flush(self):
        text = "".join(self.buf).rstrip() if self.pre else " ".join("".join(self.buf).split())
        self.buf = []
        if text.strip():
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if tag == "br" and self.region and not self.skip:
                self._flush()
            return
        self.stack.append(tag)
        if self.region is None:
            if tag == self.container and not self.done:
                self.region = len(self.stack)
            self.flags.append(False)
            return
        self.flags.append(tag in _SKIP)
        if tag in _SKIP:
            self.skip += 1
        if self.skip or self.done:
            return
        if tag in _BLOCK:
            self._flush()
        if tag == "pre":
            self.pre += 1

    def handle_endtag(self, tag):
        if tag in _VOID or tag not in self.stack:
            return
        while self.stack:
            depth, popped, flag = len(self.stack), self.stack.pop(), self.flags.pop()
            if self.region is not None and not self.done:
                if flag:
                    self.skip -= 1
                elif not self.skip:
                    if popped in _HEAD:
                        text = " ".join("".join(self.buf).split())
                        self.buf = []
                        if text:
                            self.lines.append("#" * _HEAD[popped] + " " + text)
                    elif popped in _BLOCK:
                        self._flush()
                    if popped == "pre":
                        self.pre -= 1
                if depth == self.region:
                    self.done, self.region = True, None
            if popped == tag:
                break

    def handle_data(self, data):
        if self.region is None or self.skip or self.done:
            return
        if self.pre:
            parts = data.split("\n")
            for part in parts[:-1]:
                self.buf.append(part)
                self._flush()
            self.buf.append(parts[-1])
        else:
            self.buf.append(data)

    def result(self):
        self._flush()
        return self.lines


CHALLENGE = ("just a moment", "verify you are human", "access denied", "enable javascript", "captcha",
             "sign in to continue", "log in to continue")


def process_body(src, raw, content_type):
    """Deterministic raw bytes -> normalised body text. Raises Blocked(code)."""
    ex = src["extraction"]
    if ex not in EXTRACTIONS:
        raise Blocked("unsupported_extraction_version")
    ctype = (content_type or "").split(";")[0].strip().lower()
    m = re.search(r"charset=([^;\s]+)", content_type or "", re.I)
    if m and m.group(1).strip("\"'").lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise Blocked("unsupported_charset")
    want = ("text/markdown", "text/plain", "text/x-markdown") if ex == "markdown-v1" else \
        ("text/html", "application/xhtml+xml")
    if ctype not in want:
        raise Blocked(f"unexpected_content_type:{ctype}")
    try:
        text = norm(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise Blocked("not_utf8")
    if ex == "markdown-v1":
        body = "\n".join(l.rstrip() for l in text.split("\n")).strip("\n") + "\n"
    else:
        p = _Extract({"html-main-v1": "main", "html-article-v1": "article", "html-body-v1": "body"}[ex])
        p.feed(text)
        p.close()
        lines = p.result()
        if not lines:
            raise Blocked("container_missing_or_empty" if not p.done else "body_empty")
        body = "\n".join(lines) + "\n"
    low = body[:1000].lower()
    if any(c in low for c in CHALLENGE):
        raise Blocked("challenge_or_login_page")
    if len(body.strip()) < 200:
        raise Blocked("body_too_short_or_js_shell")
    if not any(l.startswith("#") for l in body.split("\n")):
        raise Blocked("no_heading_in_body")
    missing = [x for x in src["body_locators"] if x not in body]
    if missing:
        raise Blocked(f"locators_missing:{missing[0]}")
    return body


# ---------------------------------------------------------------- registry
SRC_F = {"source_id": (S, 1), "allowed_urls": (L, 1), "target_ids": (L, 1), "surfaces": (L, 1),
         "models": (L, 1), "category": (S, 1), "extraction": (S, 1), "body_locators": (L, 1)}
TGT_F = {"target_id": (S, 1), "required_sources": (L, 1), "conditional_sources": (L, 1)}
COND_F = {"purposes": (L, 1), "surfaces": (L, 1), "source_ids": (L, 1)}
RULE_F = {"rule_id": (S, 1), "source_id": (S, 1), "target_ids": (L, 1), "surfaces": (L, 1), "models": (L, 1),
          "kind": (S, 1), "purposes": (L, 1), "preconditions": (L, 1), "exclusions": (L, 1),
          "source_body_sha256": (SN, 1), "source_excerpt": (S, 1), "reviewed_at": (SN, 1),
          "reviewer_id": (SN, 1), "meaning": (S, 1), "source_locator": (S, 1), "allowed_change": (S, 1),
          "review_status": (S, 1)}


def validate_registry(reg):
    shape(reg, {"schema_version": (I, 1), "sources": (L, 1), "targets": (L, 1), "rules": (L, 1)}, "registry")
    if reg["schema_version"] != 1:
        raise ContractError("registry: schema_version must be 1")
    sources = {}
    for s in reg["sources"]:
        shape(s, SRC_F, "source")
        safe_id(s["source_id"], "source_id")
        for k in ("allowed_urls", "target_ids", "surfaces", "models", "body_locators"):
            strs(s[k], f"source.{k}", True)
        if any(url_key(u) is None or "#" in u for u in s["allowed_urls"]):
            raise ContractError(f"source {s['source_id']}: bad allowed_urls")
        if s["extraction"] not in EXTRACTIONS or len(s["body_locators"]) < 2:
            raise ContractError(f"source {s['source_id']}: bad extraction or <2 body_locators")
        if not set(s["target_ids"]) <= set(TARGET_IDS):
            raise ContractError(f"source {s['source_id']}: unknown target_ids")
        sources[s["source_id"]] = s
    uniq([s["source_id"] for s in reg["sources"]], "source_id")
    for t in reg["targets"]:
        shape(t, TGT_F, "target")
        tid = t["target_id"]
        if tid not in TARGET_IDS:
            raise ContractError(f"unknown target_id {tid}")
        used = list(strs(t["required_sources"], "required_sources"))
        for c in t["conditional_sources"]:
            shape(c, COND_F, f"target {tid} conditional")
            strs(c["purposes"], "purposes", True)
            strs(c["surfaces"], "surfaces", True)
            used += strs(c["source_ids"], "source_ids", True)
            if not set(c["purposes"]) <= set(PURPOSES) | {"all"}:
                raise ContractError(f"target {tid}: unknown purposes")
        for sid in used:
            if sid not in sources or tid not in sources[sid]["target_ids"]:
                raise ContractError(f"target {tid}: source {sid} unknown or not scoped to target")
    uniq([t["target_id"] for t in reg["targets"]], "target_id")
    if {t["target_id"] for t in reg["targets"]} != set(TARGET_IDS):
        raise ContractError("registry must define all 11 targets")
    for r in reg["rules"]:
        shape(r, RULE_F, "rule")
        safe_id(r["rule_id"], "rule_id")
        for k in ("target_ids", "surfaces", "models", "purposes"):
            strs(r[k], f"rule.{k}", True)
        strs(r["preconditions"], "preconditions")
        strs(r["exclusions"], "exclusions")
        src = sources.get(r["source_id"])
        if src is None or not set(r["target_ids"]) <= set(src["target_ids"]):
            raise ContractError(f"rule {r['rule_id']}: source unknown or target not in source scope")
        if r["kind"] not in ("official", "inference") or r["review_status"] not in ("unreviewed", "reviewed"):
            raise ContractError(f"rule {r['rule_id']}: bad kind/review_status")
        if not set(r["purposes"]) <= set(PURPOSES):
            raise ContractError(f"rule {r['rule_id']}: unknown purposes")
        if r["review_status"] == "reviewed":
            if not (r["source_body_sha256"] and SHA_RE.match(r["source_body_sha256"]) and r["reviewer_id"]):
                raise ContractError(f"rule {r['rule_id']}: reviewed rule needs hash and reviewer_id")
            parse_ts(r["reviewed_at"], f"rule {r['rule_id']}.reviewed_at")
    uniq([r["rule_id"] for r in reg["rules"]], "rule_id")


def load_registry(path=None):
    p = Path(path or REGISTRY_PATH)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise Unreadable(f"missing registry {p}")
    try:
        reg = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ContractError(f"registry: invalid JSON ({e})")
    validate_registry(reg)
    return reg, sha_bytes(raw)


def select_sources(reg, inp):
    """Sources required for this target + those whose purpose AND surface conditions both match."""
    if inp["target_id"] == "unresolved":
        return []
    t = next(t for t in reg["targets"] if t["target_id"] == inp["target_id"])
    ids = list(t["required_sources"])
    for c in t["conditional_sources"]:
        if ("all" in c["purposes"] or inp["purpose"] in c["purposes"]) and in_list(c["surfaces"], inp["target_surface"]):
            ids += [i for i in c["source_ids"] if i not in ids]
    return ids


# ---------------------------------------------------------------- input / entries
INPUT_F = {"schema_version": (I, 1), "execution_host": (S, 1), "target_id": (S, 1), "target_surface": (S, 1),
           "model_id": (S, 1), "purpose": (S, 1), "original_path": (S, 1), "constraints": (L, 1)}
ENTRY_F = {"source_id": (S, 1), "status": (S, 1), "method": (S, 1), "requested_url": (S, 1),
           "final_url": (SN, 1), "redirect_chain": (L, 1), "fetched_at": (SN, 1), "http_status": ((int, type(None)), 1),
           "content_type": (SN, 1), "raw_path": (SN, 1), "body_path": (SN, 1), "raw_sha256": (SN, 1),
           "body_sha256": (SN, 1), "extraction_version": (SN, 1), "origin_status": (S, 1), "error": (SN, 1),
           "rechecked_at": (SN, 1)}
ACQ_F = {"schema_version": (I, 1), "run_id": (S, 1), "started_at": (S, 1), "mode": (S, 1),
         "registry_sha256": (S, 1), "input_sha256": (S, 1), "original_sha256": (S, 1),
         "selected_sources": (L, 1), "sources": (L, 1)}
STATUSES = ("live", "cache", "blocked", "missing", "stale")


def validate_input(inp):
    shape(inp, INPUT_F, "input")
    if inp["schema_version"] != 1 or inp["execution_host"] not in HOSTS:
        raise ContractError(f"input: schema_version must be 1 and execution_host one of {HOSTS}")
    if inp["target_id"] not in TARGET_IDS + ("unresolved",):
        raise ContractError(f"input: unknown target_id {inp['target_id']!r}")
    if inp["purpose"] not in PURPOSES:
        raise ContractError(f"input: unknown purpose {inp['purpose']!r}")
    if not inp["target_surface"] or not inp["model_id"]:
        raise ContractError("input: target_surface/model_id must be non-empty ('unknown' if unknown)")
    strs(inp["constraints"], "input.constraints")


def validate_acquisition(acq):
    shape(acq, ACQ_F, "acquisition")
    if acq["schema_version"] != 1 or acq["mode"] not in ("live", "cache"):
        raise ContractError("acquisition: bad schema_version/mode")
    safe_id(acq["run_id"], "run_id")
    parse_ts(acq["started_at"], "started_at")
    for k in ("registry_sha256", "input_sha256", "original_sha256"):
        if not SHA_RE.match(acq[k]):
            raise ContractError(f"acquisition.{k}: bad sha256")
    strs(acq["selected_sources"], "selected_sources")
    uniq(acq["selected_sources"], "selected source")
    for e in acq["sources"]:
        shape(e, ENTRY_F, "acquisition.source")
        safe_id(e["source_id"], "source_id")
        if e["status"] not in STATUSES or e["method"] not in ("urllib", "external-tool") \
                or e["origin_status"] not in ("observed", "unobserved"):
            raise ContractError(f"source {e['source_id']}: bad status/method/origin_status")
        for h in ("raw_sha256", "body_sha256"):
            if e[h] is not None and not SHA_RE.match(e[h]):
                raise ContractError(f"source {e['source_id']}: bad {h}")
        for hop in e["redirect_chain"]:
            shape(hop, {"from": (S, 1), "to": (S, 1), "status": (I, 1)}, "redirect hop")
        for k in ("fetched_at", "rechecked_at"):
            if e[k] is not None:
                parse_ts(e[k], k)
    uniq([e["source_id"] for e in acq["sources"]], "acquisition source")
    if sorted(e["source_id"] for e in acq["sources"]) != sorted(acq["selected_sources"]):
        raise ContractError("acquisition: sources do not match selected_sources")


def new_entry(sid, url, method="urllib"):
    return {"source_id": sid, "status": "blocked", "method": method, "requested_url": url, "final_url": None,
            "redirect_chain": [], "fetched_at": None, "http_status": None, "content_type": None, "raw_path": None,
            "body_path": None, "raw_sha256": None, "body_sha256": None, "extraction_version": None,
            "origin_status": "unobserved", "error": None, "rechecked_at": None}


# ---------------------------------------------------------------- fetch
def _save_snapshot(run, sid, raw, body, entry):
    d = run / "snapshots" / sid
    d.mkdir(parents=True, exist_ok=False)
    with open(d / "raw.bin", "xb") as f:
        f.write(raw)
    entry["raw_path"], entry["raw_sha256"] = f"snapshots/{sid}/raw.bin", sha_bytes(raw)
    if body is not None:
        with open(d / "body.txt", "x", encoding="utf-8", newline="\n") as f:
            f.write(body)
        entry["body_path"], entry["body_sha256"] = f"snapshots/{sid}/body.txt", sha_bytes(body.encode("utf-8"))


def fetch_live(src, run):
    url = src["allowed_urls"][0]
    e = new_entry(src["source_id"], url)
    raw = body = None
    try:
        for hop in range(MAX_HOPS + 1):
            ok, why = url_allowed(url, src)
            if not ok:
                raise Blocked("redirect_not_registered:" + url if e["redirect_chain"] else why)
            check_host_public(urllib.parse.urlsplit(url).hostname)
            try:
                status, headers, raw = http_get(url)
            except (urllib.error.URLError, OSError, http.client.HTTPException) as ex:
                raise Blocked(f"network_error:{type(ex).__name__}")
            e["origin_status"], e["http_status"], e["final_url"] = "observed", status, url
            e["fetched_at"] = iso(utcnow())
            if status in (301, 302, 303, 307, 308):
                loc = headers.get("location")
                if not loc:
                    raise Blocked("redirect_without_location")
                nxt = urllib.parse.urljoin(url, loc)
                e["redirect_chain"].append({"from": url, "to": nxt, "status": status})
                if hop == MAX_HOPS:
                    raise Blocked("too_many_redirects")
                url = nxt
                continue
            break
        if status != 200:
            raise Blocked(f"http_{status}")
        if len(raw) > MAX_BYTES:
            raise Blocked("response_too_large")
        e["content_type"] = headers.get("content-type")
        e["extraction_version"] = src["extraction"]
        body = process_body(src, raw, e["content_type"])
        e["status"] = "live"
    except Blocked as b:
        e["error"] = b.code
    if raw is not None and len(raw) <= MAX_BYTES:
        _save_snapshot(run, src["source_id"], raw, body, e)  # kept for diagnosis even when blocked
    return e


def fetch_cache(src, cache_dir, run, now):
    sid = src["source_id"]
    acq = load_json(cache_dir / "acquisition.json")
    validate_acquisition(acq)
    old = next((x for x in acq["sources"] if x["source_id"] == sid), None)
    if old is None:
        e = new_entry(sid, src["allowed_urls"][0])
        e["status"], e["error"] = "missing", "not_in_cache"
        return e
    e = dict(old)
    e["rechecked_at"] = iso(now)  # fetched_at stays the original acquisition time
    if old["status"] not in ("live", "cache"):
        e["status"], e["error"] = "blocked", f"cached_source_not_usable:{old['status']}"
        return e
    raw_p, body_p = safe_join(cache_dir, old["raw_path"]), safe_join(cache_dir, old["body_path"])
    raw, body = raw_p.read_bytes(), body_p.read_bytes()
    if sha_bytes(raw) != old["raw_sha256"] or sha_bytes(body) != old["body_sha256"]:
        raise ContractError(f"cache {sid}: stored hash mismatch (tampered snapshot)")
    try:
        again = process_body(src, raw, old["content_type"])
    except Blocked as b:
        raise ContractError(f"cache {sid}: re-extraction failed ({b.code})")
    if again.encode("utf-8") != body:
        raise ContractError(f"cache {sid}: body does not match raw re-extraction")
    fetched = parse_ts(old["fetched_at"], "cache fetched_at")
    if fetched > now:
        raise ContractError(f"cache {sid}: fetched_at is in the future")
    _save_snapshot(run, sid, raw, again, {})
    e["status"] = "stale" if now - fetched > timedelta(days=FRESH_DAYS) else "cache"
    e["error"] = "older_than_7_days" if e["status"] == "stale" else None
    return e


def resolve_workspace_file(p):
    ws = Path.cwd().resolve()
    try:
        rp = (Path(p) if os.path.isabs(p) else ws / p).resolve(strict=True)
    except FileNotFoundError:
        raise ContractError(f"original_path not found: {p}")
    if not rp.is_relative_to(ws) or not rp.is_file():
        raise ContractError(f"original_path must be a file inside the workspace: {p}")
    return rp


def do_fetch(input_path, out, mode, cache=None):
    reg, reg_sha = load_registry()
    try:
        inp_raw = Path(input_path).read_bytes()
    except FileNotFoundError:
        raise Unreadable(f"missing input: {input_path}")
    try:
        inp = json.loads(inp_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ContractError(f"input: invalid JSON ({e})")
    validate_input(inp)
    original = read_text(resolve_workspace_file(inp["original_path"]))
    out = Path(out)
    safe_id(out.name, "run id")
    if ".." in out.parts:
        raise ContractError("--out must not contain '..'")
    if not out.parent.resolve().is_relative_to(Path.cwd().resolve()):
        raise ContractError("--out must be inside the workspace")
    if mode == "cache":
        if not cache:
            raise ContractError("--mode cache requires --cache <earlier run dir>")
        cache = Path(cache)
        if not (cache / "acquisition.json").is_file():
            raise Unreadable(f"no acquisition.json in cache run {cache}")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.mkdir()
    except FileExistsError:
        raise ContractError(f"run directory already exists: {out}")
    srcmap = {s["source_id"]: s for s in reg["sources"]}
    selected = select_sources(reg, inp)
    started = utcnow()
    (out / "input.json").write_bytes(inp_raw)
    (out / "original.md").write_text(original, encoding="utf-8", newline="\n")
    entries = []
    for sid in selected:
        entries.append(fetch_live(srcmap[sid], out) if mode == "live" else fetch_cache(srcmap[sid], cache, out, started))
    acq = {"schema_version": 1, "run_id": out.name, "started_at": iso(started), "mode": mode,
           "registry_sha256": reg_sha, "input_sha256": sha_bytes(inp_raw), "original_sha256": sha_text(original),
           "selected_sources": selected, "sources": entries}
    write_json(out / "acquisition.json", acq)
    write_json(out / "semantic-review.json", stub_review(acq["original_sha256"], reg_sha))
    write_json(out / "evaluation.json", {"schema_version": 1, "status": "NOT_EVALUATED",
                                         "reason": "no comparison executed", "target_id": inp["target_id"]})
    want = "live" if mode == "live" else "cache"
    ok = inp["target_id"] != "unresolved" and all(e["status"] == want for e in entries)
    return (0 if ok else 2), acq


def stub_review(orig_sha, reg_sha):
    return {"schema_version": 1, "original_sha256": orig_sha, "optimized_sha256": None, "registry_sha256": reg_sha,
            "source_hashes": {}, "rubric_version": rubric_version(), "reviewer_id": None, "review_method": "unavailable",
            "model_id": None, "observed_execution_ref": None, "calibration_ref": None, "calibration_sha256": None,
            "checked_at": None, "checks": [], "status": "NOT_REVIEWED"}


def rubric_version():
    m = re.search(r"^rubric_version:\s*(\S+)", RUBRIC_PATH.read_text(encoding="utf-8"), re.M)
    if not m:
        raise Unreadable("review-rubric.md has no rubric_version line")
    return m.group(1)


# ---------------------------------------------------------------- verify
class Report:
    def __init__(self, names):
        self.checks = {n: {"status": "PASS"} for n in names}
        self.errors = []

    def add(self, check, severity, code, msg, **ids):
        self.errors.append({"check": check, "severity": severity, "code": code, "message": msg, **ids})
        cur = self.checks[check]["status"]
        if severity == "fail":
            self.checks[check]["status"] = "FAIL"
        elif cur == "PASS":
            self.checks[check]["status"] = "UNAVAILABLE"

    def fail(self, check, code, msg, **ids):
        self.add(check, "fail", code, msg, **ids)

    def inc(self, check, code, msg, **ids):
        self.add(check, "incomplete", code, msg, **ids)

    def skip_rest(self, why):
        for c in self.checks.values():
            if c["status"] == "PASS":
                c["status"] = "UNAVAILABLE"
        self.errors.append({"check": "-", "severity": "incomplete", "code": "skipped", "message": why})

    def verdict(self, pass_name):
        st = {c["status"] for c in self.checks.values()}
        return "FAIL" if "FAIL" in st else ("INCOMPLETE" if "UNAVAILABLE" in st else pass_name)


CHG_F = {"change_id": (S, 1), "kind": (S, 1), "rule_id": (SN, 1), "source_id": (SN, 1), "body_sha256": (SN, 1),
         "source_start_line": ((int, type(None)), 1), "source_end_line": ((int, type(None)), 1),
         "exact_excerpt": (SN, 1), "before_start": (I, 1), "before_end": (I, 1), "after_start": (I, 1),
         "after_end": (I, 1), "after_excerpt": (S, 1), "rationale": (S, 1)}
REC_F = {"schema_version": (I, 1), "target_id": (S, 1), "target_surface": (S, 1), "model_id": (S, 1),
         "generation_status": (S, 1), "original_sha256": (S, 1), "optimized_sha256": (S, 1), "changes": (L, 1)}


def validate_recommendations(rec):
    shape(rec, REC_F, "recommendations")
    if rec["schema_version"] != 1 or rec["generation_status"] not in ("complete", "provisional"):
        raise ContractError("recommendations: bad schema_version/generation_status")
    for k in ("original_sha256", "optimized_sha256"):
        if not SHA_RE.match(rec[k]):
            raise ContractError(f"recommendations.{k}: bad sha256")
    for c in rec["changes"]:
        shape(c, CHG_F, "change")
        safe_id(c["change_id"], "change_id")
        if c["kind"] not in ("official", "inference", "editorial"):
            raise ContractError(f"change {c['change_id']}: bad kind")
        cited = ("rule_id", "source_id", "body_sha256", "source_start_line", "source_end_line", "exact_excerpt")
        if c["kind"] == "editorial" and any(c[k] is not None for k in cited):
            raise ContractError(f"change {c['change_id']}: editorial change must not cite a source")
        have = sum(c[k] is not None for k in cited)
        if (c["kind"] == "official" and have < len(cited)) or (c["kind"] == "inference" and 0 < have < len(cited)):
            raise ContractError(f"change {c['change_id']}: needs rule_id/source/lines/excerpt/hash (inference: all or none)")
        if c["body_sha256"] is not None and not SHA_RE.match(c["body_sha256"]):
            raise ContractError(f"change {c['change_id']}: bad body_sha256")
    uniq([c["change_id"] for c in rec["changes"]], "change_id")


def _ranges_overlap(a, b):
    return max(a[0], b[0]) < min(a[1], b[1])


def _read_run(run, name):
    p = run / name
    if not p.is_file():
        raise Unreadable(f"missing file in run: {name}")
    return p


def verify(run, mode, host_log=None):
    run = Path(run)
    if not run.is_dir():
        raise Unreadable(f"run directory not found: {run}")
    now = utcnow()
    R = Report(CHECKS)
    ctx = {"orig_sha": None, "opt_sha": None, "reg_sha": None, "inp_sha": None}
    sources_out = []
    try:
        reg, ctx["reg_sha"] = load_registry()
        inp_b = _read_run(run, "input.json").read_bytes()
        ctx["inp_sha"] = sha_bytes(inp_b)
        inp = load_json(run / "input.json")
        validate_input(inp)
        acq = load_json(_read_run(run, "acquisition.json"))
        validate_acquisition(acq)
        rec = load_json(_read_run(run, "recommendations.json"))
        validate_recommendations(rec)
        orig = read_text(_read_run(run, "original.md"))
        opt = read_text(_read_run(run, "optimized.md"))
        ctx["orig_sha"], ctx["opt_sha"] = sha_text(orig), sha_text(opt)
    except ContractError as e:
        R.fail("structure", "invalid_structure", str(e))
        R.skip_rest("structure failed")
        return _finish(run, R, mode, ctx, sources_out, now)
    srcmap = {s["source_id"]: s for s in reg["sources"]}
    rules = {r["rule_id"]: r for r in reg["rules"]}
    entries = {e["source_id"]: e for e in acq["sources"]}

    # structure: cross-file bindings
    if acq["run_id"] != run.name:
        R.fail("structure", "run_id_mismatch", "acquisition.run_id differs from directory name")
    for ok, code in ((acq["registry_sha256"] == ctx["reg_sha"], "registry_changed"),
                     (acq["input_sha256"] == ctx["inp_sha"], "input_hash_mismatch"),
                     (acq["original_sha256"] == ctx["orig_sha"], "original_hash_mismatch"),
                     (rec["original_sha256"] == ctx["orig_sha"], "rec_original_hash_mismatch"),
                     (rec["optimized_sha256"] == ctx["opt_sha"], "rec_optimized_hash_mismatch"),
                     ((rec["target_id"], rec["target_surface"], rec["model_id"]) ==
                      (inp["target_id"], inp["target_surface"], inp["model_id"]), "rec_target_mismatch")):
        if not ok:
            R.fail("structure", code, code.replace("_", " "))
    expected = select_sources(reg, inp)
    if expected != acq["selected_sources"]:
        R.fail("scope", "selected_sources_mismatch",
               f"registry routing expects {expected}, acquisition has {acq['selected_sources']}")
    if inp["target_id"] == "unresolved":
        R.inc("scope", "target_unresolved", "target is unresolved; no verified optimisation possible")
    elif not expected:
        R.inc("scope", "no_applicable_official_source", "no registered official source applies to this target/purpose")
    if rec["generation_status"] == "provisional":
        R.inc("scope", "provisional_generation", "generation_status is provisional")

    # official acquisition + body integrity
    started = parse_ts(acq["started_at"])
    if acq["mode"] != mode:
        R.inc("official_acquisition", "mode_mismatch", f"run was acquired in {acq['mode']} mode, verifying as {mode}")
    bodies = {}
    for sid in acq["selected_sources"]:
        e, src = entries[sid], srcmap.get(sid)
        row = {"source_id": sid, "acquisition_status": e["status"], "origin_status": e["origin_status"],
               "read_status": "unavailable", "integrity_status": "UNAVAILABLE"}
        sources_out.append(row)
        if src is None:
            R.fail("official_acquisition", "unknown_source", "source not in registry", source_id=sid)
            continue
        ok_chain = _check_chain(R, e, src, sid)
        if e["method"] == "external-tool":
            if e["status"] == "live" or e["origin_status"] != "unobserved":
                R.fail("official_acquisition", "external_origin_claimed_observed",
                       "external-tool snapshots are cache with unobserved origin", source_id=sid)
            else:
                R.inc("official_acquisition", "external_origin_unverified", "external snapshot origin not independently verified", source_id=sid)
        elif e["status"] in ("live", "cache") and e["origin_status"] != "observed":
            R.fail("official_acquisition", "origin_not_observed", "urllib snapshot must have observed origin", source_id=sid)
        if e["status"] not in ("live", "cache"):
            R.inc("official_acquisition", f"source_{e['status']}", e["error"] or e["status"], source_id=sid)
            continue
        want = "live" if mode == "live" else "cache"
        if e["status"] != want:
            R.inc("official_acquisition", "status_not_for_mode", f"status {e['status']} cannot satisfy {mode} mode", source_id=sid)
        if e["fetched_at"] is None:
            R.fail("official_acquisition", "no_fetched_at", "missing fetched_at", source_id=sid)
            continue
        fetched = parse_ts(e["fetched_at"])
        if fetched > now:
            R.fail("official_acquisition", "future_timestamp", "fetched_at is in the future", source_id=sid)
        elif now - fetched > timedelta(days=FRESH_DAYS):
            R.inc("official_acquisition", "stale", f"snapshot older than {FRESH_DAYS} days", source_id=sid)
        if e["status"] == "live" and fetched < started:
            R.fail("official_acquisition", "live_before_run", "live snapshot predates this run (cache posing as live)", source_id=sid)
        if e["status"] == "cache" and (fetched > started or e["rechecked_at"] is None):
            R.fail("official_acquisition", "cache_timestamp_refreshed",
                   "cache fetched_at must predate the run and rechecked_at must be set", source_id=sid)
        body = _check_body(R, run, e, src, sid, row)
        if body is not None and ok_chain:
            bodies[sid] = body

    # changes: citation, scope, rule review
    counts = {"official": 0, "inference": 0, "editorial": 0}
    for c in rec["changes"]:
        counts[c["kind"]] += 1
        if c["rule_id"] is not None:
            _check_change_evidence(R, c, bodies, entries, srcmap, rules, inp, acq)
    _check_binding(R, rec, orig, opt)

    # read observation
    _check_reads(R, run, rec, acq, bodies, sources_out, host_log, ctx, inp["execution_host"])
    ctx["counts"] = counts
    return _finish(run, R, mode, ctx, sources_out, now)


def _check_chain(R, e, src, sid):
    ok = True
    chain, cur = e["redirect_chain"], e["requested_url"]
    if len(chain) > MAX_HOPS:
        R.fail("official_acquisition", "too_many_redirects", "redirect chain too long", source_id=sid)
        ok = False
    for u in [cur] + [h["to"] for h in chain]:
        good, why = url_allowed(u, src)
        if not good:
            R.fail("official_acquisition", "url_not_registered", f"{why}: {u}", source_id=sid)
            ok = False
    for h in chain:
        if h["from"] != cur or h["status"] not in (301, 302, 303, 307, 308):
            R.fail("official_acquisition", "broken_redirect_chain", "redirect hops are not contiguous", source_id=sid)
            ok = False
        cur = h["to"]
    if e["final_url"] is not None and e["final_url"] != cur:
        R.fail("official_acquisition", "final_url_mismatch", "final_url differs from redirect chain end", source_id=sid)
        ok = False
    return ok


def _check_body(R, run, e, src, sid, row):
    try:
        raw = safe_join(run, e["raw_path"]).read_bytes()
        body_b = safe_join(run, e["body_path"]).read_bytes()
    except (ContractError, FileNotFoundError, TypeError) as ex:
        R.fail("body_integrity", "snapshot_missing_or_unsafe", str(ex), source_id=sid)
        row["integrity_status"] = "FAIL"
        return None
    if sha_bytes(raw) != e["raw_sha256"] or sha_bytes(body_b) != e["body_sha256"]:
        R.fail("body_integrity", "hash_mismatch", "raw/body bytes differ from recorded SHA-256", source_id=sid)
        row["integrity_status"] = "FAIL"
        return None
    if e["extraction_version"] not in EXTRACTIONS or e["extraction_version"] != src["extraction"]:
        R.inc("body_integrity", "unsupported_extraction_version", f"extraction {e['extraction_version']!r} not supported", source_id=sid)
        return None
    try:
        again = process_body(src, raw, e["content_type"])
    except Blocked as b:
        R.fail("body_integrity", "body_check_failed", b.code, source_id=sid)
        row["integrity_status"] = "FAIL"
        return None
    if again.encode("utf-8") != body_b:
        R.fail("body_integrity", "body_not_from_raw", "body.txt is not the deterministic extraction of raw.bin", source_id=sid)
        row["integrity_status"] = "FAIL"
        return None
    row["integrity_status"] = "PASS"
    return again


def _check_change_evidence(R, c, bodies, entries, srcmap, rules, inp, acq):
    cid, sid = c["change_id"], c["source_id"]
    kw = {"change_id": cid, "source_id": sid}
    rule = rules.get(c["rule_id"])
    if rule is None:
        R.fail("scope", "unknown_rule", f"rule {c['rule_id']} not in registry", **kw)
        return
    if rule["source_id"] != sid or rule["kind"] != c["kind"]:
        R.fail("scope", "rule_source_or_kind_mismatch", "rule belongs to another source or kind", **kw)
    if sid not in acq["selected_sources"] or sid not in srcmap:
        R.fail("scope", "source_not_selected", "cited source is not a selected source for this run", **kw)
        return
    if inp["target_id"] not in rule["target_ids"] or inp["target_id"] not in srcmap[sid]["target_ids"]:
        R.fail("scope", "target_mismatch", "rule/source does not apply to this target", **kw)
    if not in_list(rule["surfaces"], inp["target_surface"]) or not in_list(srcmap[sid]["surfaces"], inp["target_surface"]):
        R.fail("scope", "surface_mismatch", "rule/source does not apply to this surface", **kw)
    if not in_list(rule["models"], inp["model_id"]):
        R.fail("scope", "model_mismatch", "model-limited rule cannot apply to this/unknown model", **kw)
    if inp["purpose"] not in rule["purposes"]:
        R.fail("scope", "purpose_mismatch", "rule does not apply to this purpose", **kw)
    if rule["review_status"] != "reviewed":
        R.fail("rule_review", "rule_unreviewed", "unreviewed rule used as evidence", **kw)
    body = bodies.get(sid)
    if body is None:
        R.inc("citation", "no_verified_body", "cited source body not verified", **kw)
        return
    body_sha = entries[sid]["body_sha256"]
    if c["body_sha256"] != body_sha:
        R.fail("citation", "body_hash_mismatch", "change cites a different body hash than acquired", **kw)
    lines = body.split("\n")[:-1]
    s, e = c["source_start_line"], c["source_end_line"]
    if not (1 <= s <= e <= len(lines)):
        R.fail("citation", "bad_line_range", f"lines {s}-{e} outside body (1-{len(lines)})", **kw)
        return
    region = "\n".join(lines[s - 1:e])
    if len(c["exact_excerpt"]) > MAX_EXCERPT:
        R.fail("citation", "excerpt_too_long", "exact_excerpt must stay short", **kw)
    if not c["exact_excerpt"] or c["exact_excerpt"] not in region:
        R.fail("citation", "excerpt_not_in_lines", "exact_excerpt not found in cited lines", **kw)
    if rule["source_excerpt"] not in region:
        R.fail("citation", "lines_not_rule_evidence", "cited lines do not contain the rule's source_excerpt", **kw)
    if rule["source_body_sha256"] != body_sha or rule["source_excerpt"] not in body:
        R.inc("rule_review", "rereview_pending", "source body changed since the rule was reviewed", **kw)


def _check_binding(R, rec, orig, opt):
    spans = []
    for c in rec["changes"]:
        cid = c["change_id"]
        bs, be, as_, ae = c["before_start"], c["before_end"], c["after_start"], c["after_end"]
        if not (0 <= bs <= be <= len(orig) and 0 <= as_ <= ae <= len(opt)):
            R.fail("change_binding", "span_out_of_range", "before/after span outside text", change_id=cid)
            continue
        if opt[as_:ae] != c["after_excerpt"]:
            R.fail("change_binding", "after_excerpt_mismatch", "after_excerpt differs from optimized span", change_id=cid)
        for other in spans:
            if _ranges_overlap((bs, be), other[0]) or _ranges_overlap((as_, ae), other[1]):
                R.fail("change_binding", "overlapping_changes", "change spans overlap", change_id=cid)
        spans.append(((bs, be), (as_, ae)))
    # A character diff aligns by content, so a region may straddle two adjacent changes: test coverage
    # against the union of the before spans and of the after spans, not one change.
    before, after = _union([b for b, _ in spans]), _union([a for _, a in spans])
    sm = difflib.SequenceMatcher(None, orig, opt, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal" and not (any(s <= i1 and i2 <= e for s, e in before) and any(s <= j1 and j2 <= e for s, e in after)):
            R.fail("change_binding", "unmapped_change", f"diff region original[{i1}:{i2}] -> optimized[{j1}:{j2}] has no change record")


def _union(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def parse_transcript(path):
    """claude-code-transcript-jsonl-v1: tool_use / tool_result blocks of non-sidechain entries."""
    events = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError) as e:
        raise Unreadable(f"host log unreadable: {e}")
    for seq, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            raise ContractError(f"host log line {seq + 1}: invalid JSON")
        msg = d.get("message") if isinstance(d, dict) else None
        if d.get("isSidechain") or not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
            continue
        for b in msg["content"]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and d.get("type") == "assistant":
                events.append({"kind": "use", "seq": seq, "id": b.get("id"), "name": b.get("name"), "input": b.get("input") or {}})
            elif b.get("type") == "tool_result" and d.get("type") == "user":
                c = b.get("content")
                text = c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""
                events.append({"kind": "result", "seq": seq, "id": b.get("tool_use_id"), "error": bool(b.get("is_error")), "text": text})
    return events


_NUMBERED = re.compile(r"^\s*(\d+)[\t→](.*)$")


def observed_read_lines(events, body_path, gen_paths):
    """-> (set of body line numbers shown by successful Reads before the first generation write, gen_seq or None)."""
    target = Path(body_path).resolve()
    gens = [ev["seq"] for ev in events if ev["kind"] == "use" and (
        (ev["name"] in ("Write", "Edit") and _resolves_to(ev["input"].get("file_path"), gen_paths)) or
        (ev["name"] == "Bash" and any(g.name in str(ev["input"].get("command", "")) for g in gen_paths)))]
    gen_seq = min(gens) if gens else None
    body_lines = Path(body_path).read_text(encoding="utf-8").split("\n")[:-1]
    results = {ev["id"]: ev for ev in events if ev["kind"] == "result"}
    seen = set()
    for ev in events:
        if ev["kind"] != "use" or ev["name"] != "Read" or not _resolves_to(ev["input"].get("file_path"), [target]):
            continue
        res = results.get(ev["id"])
        if res is None or res["error"] or res["seq"] < ev["seq"] or (gen_seq is not None and res["seq"] > gen_seq):
            continue
        for ln in res["text"].split("\n"):
            m = _NUMBERED.match(ln)
            if m and 1 <= int(m.group(1)) <= len(body_lines) and body_lines[int(m.group(1)) - 1] == m.group(2):
                seen.add(int(m.group(1)))
    return seen, gen_seq


def _resolves_to(fp, targets):
    if not isinstance(fp, str):
        return False
    try:
        rp = Path(fp).resolve()
    except OSError:
        return False
    return any(rp == Path(t).resolve() for t in targets)


def _check_reads(R, run, rec, acq, bodies, sources_out, host_log, ctx, host):
    ctx["host_log_sha256"] = None
    if host not in LOG_ADAPTER_HOSTS:  # a log from another product is never parsed or guessed at
        R.inc("read_observation", "no_log_adapter_for_host", f"no read-observation adapter for execution_host {host}")
        return
    if host_log is None:
        R.inc("read_observation", "no_host_log", "no independent host log supplied; reads cannot be observed")
        return
    hl = Path(host_log)
    if not hl.is_file():
        raise Unreadable(f"host log not found: {hl}")
    ctx["host_log_sha256"] = sha_file(hl)
    if hl.resolve().is_relative_to(run.resolve()):
        R.fail("read_observation", "host_log_inside_run", "host log inside the run directory is self-authored, not observed")
        for r in sources_out:
            r["read_status"] = "self_reported"
        return
    try:
        events = parse_transcript(hl)
    except ContractError as e:
        R.fail("read_observation", "host_log_invalid", str(e))
        return
    gen_paths = [run / "optimized.md", run / "recommendations.json"]
    for row in sources_out:
        sid = row["source_id"]
        if sid not in bodies:
            continue
        seen, gen_seq = observed_read_lines(events, run / "snapshots" / sid / "body.txt", gen_paths)
        if gen_seq is None:
            R.inc("read_observation", "generation_write_not_found", "no write of optimized.md/recommendations.json in log", source_id=sid)
            continue
        need = set()
        for c in rec["changes"]:
            if c["source_id"] == sid:
                need |= set(range(c["source_start_line"], c["source_end_line"] + 1))
        if (need - seen) if need else not seen:
            row["read_status"] = "not_observed"
            R.inc("read_observation", "read_not_observed",
                  "no successful pre-generation Read showing the cited lines" if need else "no successful pre-generation Read of this body",
                  source_id=sid)
        else:
            row["read_status"] = "observed"


def _finish(run, R, mode, ctx, sources_out, now):
    verdict = R.verdict("PASS_LIVE" if mode == "live" else "PASS_CACHE")
    rep = {"schema_version": 1, "run_id": run.name, "checked_at": iso(now), "mode": mode, "verdict": verdict,
           "registry_sha256": ctx["reg_sha"], "input_sha256": ctx["inp_sha"], "original_sha256": ctx["orig_sha"],
           "optimized_sha256": ctx["opt_sha"], "sources": sources_out, "checks": R.checks, "errors": R.errors,
           "limitations": ["Does not establish cognitive comprehension of the sources.",
                           "Does not establish that a cited excerpt semantically supports a change (see semantic review).",
                           "Does not establish that the optimised prompt performs better (see evaluation).",
                           "Snapshots are re-checkable, not tamper-proof against a user who controls the filesystem.",
                           f"Host-log adapter {LOG_ADAPTER} trusts the path the operator supplied via --host-log."],
           "change_counts": ctx.get("counts"), "host_log_sha256": ctx.get("host_log_sha256")}
    write_json(run / "verification.json", rep)
    return rep


# ---------------------------------------------------------------- assess
REV_F = {"schema_version": (I, 1), "original_sha256": (SN, 1), "optimized_sha256": (SN, 1), "registry_sha256": (SN, 1),
         "source_hashes": (D, 1), "rubric_version": (S, 1), "reviewer_id": (SN, 1), "review_method": (S, 1),
         "model_id": (SN, 1), "observed_execution_ref": (SN, 1), "calibration_ref": (SN, 1),
         "calibration_sha256": (SN, 1), "checked_at": (SN, 1), "checks": (L, 1), "status": (S, 1)}
CHK_F = {"check_id": (S, 1), "change_id": (SN, 1), "category": (S, 1), "judgment": (S, 1),
         "original_span": ((list, type(None)), 1), "optimized_span": ((list, type(None)), 1),
         "source_id": (SN, 1), "source_lines": ((list, type(None)), 1), "excerpt": (SN, 1), "reason": (S, 1)}
CAL_F = {"schema_version": (I, 1), "reviewer_id": (S, 1), "model_id": (SN, 1), "rubric_version": (S, 1),
         "case_set_sha256": (S, 1), "labels": (L, 1), "counts": (D, 1), "tolerance": (D, 1),
         "disagreement_handling": (S, 1), "created_at": (S, 1)}
LABEL_F = {"case_id": (S, 1), "human_label": (S, 1), "human_by": (S, 1), "scorer_label": (SN, 1)}


def _span(v, n, where):
    if v is None:
        return None
    if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in v)
            and 0 <= v[0] <= v[1] <= n):
        raise ContractError(f"{where}: span must be [start,end] within text")
    return v


def case_set_hash(labels):
    return sha_bytes(json.dumps(sorted(l["case_id"] for l in labels)).encode("utf-8"))


def check_calibration(R, review, cal_path, now):
    cal_p = Path(cal_path)
    if not cal_p.is_file():
        R.inc("calibration", "calibration_missing", "calibration_ref file not found")
        return None
    if sha_file(cal_p) != review["calibration_sha256"]:
        R.fail("calibration", "calibration_hash_mismatch", "calibration file hash differs from calibration_sha256")
        return None
    cal = load_json(cal_p)
    shape(cal, CAL_F, "calibration")
    for l in cal["labels"]:
        shape(l, LABEL_F, "calibration.label")
        if l["human_label"] not in JUDGMENTS or not l["human_by"] or (l["scorer_label"] not in JUDGMENTS + (None,)):
            raise ContractError("calibration: bad label")
    if (cal["reviewer_id"], cal["model_id"], cal["rubric_version"]) != \
            (review["reviewer_id"], review["model_id"], review["rubric_version"]):
        R.fail("calibration", "calibration_other_scorer", "calibration belongs to another scorer/model/rubric")
        return None
    if cal["case_set_sha256"] != case_set_hash(cal["labels"]) or not cal["disagreement_handling"].strip():
        R.fail("calibration", "calibration_case_set_mismatch", "case_set_sha256 or disagreement_handling invalid")
        return None
    tol, cnt = cal["tolerance"], cal["counts"]
    shape(tol, {"min_agreement": (NN, 1), "max_undetermined": (I, 1), "min_cases": (I, 1)}, "calibration.tolerance")
    shape(cnt, {"agree": (I, 1), "disagree": (I, 1), "undetermined": (I, 1)}, "calibration.counts")
    n = len(cal["labels"])
    if review["review_method"] == "human":
        if any(l["scorer_label"] is not None for l in cal["labels"]) or cnt != {"agree": 0, "disagree": 0, "undetermined": n} \
                or tol["min_agreement"] is not None:
            R.fail("calibration", "human_calibration_fabricated_agreement", "human-only calibration must not report model agreement")
            return None
    else:
        agree = sum(1 for l in cal["labels"] if l["scorer_label"] == l["human_label"] and l["scorer_label"] != "insufficient")
        unde = sum(1 for l in cal["labels"] if l["scorer_label"] == "insufficient" or l["human_label"] == "insufficient"
                   or l["scorer_label"] is None)
        dis = n - agree - unde
        if cnt != {"agree": agree, "disagree": dis, "undetermined": unde}:
            R.fail("calibration", "calibration_counts_mismatch", "declared counts differ from labels")
            return None
        critical = sum(1 for l in cal["labels"] if l["human_label"] == "contradicted" and l["scorer_label"] == "supported")
        if tol["min_agreement"] is None or n < tol["min_cases"] or unde > tol["max_undetermined"] \
                or (agree / max(1, agree + dis)) < tol["min_agreement"] or critical:
            R.inc("calibration", "scorer_not_calibrated", "agreement below pre-set tolerance, too few cases, or critical disagreement")
            return None
    if n < tol["min_cases"]:
        R.inc("calibration", "too_few_calibration_cases", "fewer calibration cases than tolerance.min_cases")
        return None
    return {"cases": n, **cnt}


def assess(run, review_path, allow_mock=False):
    run = Path(run)
    now = utcnow()
    R = Report(("structure", "provenance", "coverage", "judgments", "calibration"))
    rec = load_json(_read_run(run, "recommendations.json"))
    validate_recommendations(rec)
    inp = load_json(_read_run(run, "input.json"))
    acq = load_json(_read_run(run, "acquisition.json"))
    review = None
    cal_summary = None
    try:
        review = load_json(review_path)
        shape(review, REV_F, "semantic-review")
        if review["schema_version"] != 1 or review["review_method"] not in ("human", "separate-context", "mock", "unavailable") \
                or review["status"] not in ("PASS", "FAIL", "INCOMPLETE", "NOT_REVIEWED"):
            raise ContractError("semantic-review: bad schema_version/review_method/status")
        if review["review_method"] == "unavailable" or review["status"] == "NOT_REVIEWED":
            R.inc("provenance", "not_reviewed", "no semantic review has been performed")
        else:
            cal_summary = _assess_review(R, run, review, rec, inp, acq, allow_mock, now)
    except ContractError as e:
        R.fail("structure", "invalid_review", str(e))
    except Unreadable as e:
        R.inc("structure", "review_unreadable", str(e))
    verdict = R.verdict("PASS")
    out = {"schema_version": 1, "run_id": run.name, "checked_at": iso(now), "verdict": verdict,
           "review_sha256": sha_file(review_path) if Path(review_path).is_file() else None,
           "review_method": review and review.get("review_method"), "reviewer_id": review and review.get("reviewer_id"),
           "model_id": review and review.get("model_id"), "rubric_version": review and review.get("rubric_version"),
           "mock": bool(review and review.get("review_method") == "mock"), "calibration": cal_summary,
           "checks": R.checks, "errors": R.errors,
           "limitations": ["Validates the review record (structure, hashes, positions, quotes, provenance, calibration).",
                           "A supported judgment is not proven semantically correct; it is the reviewer's judgment.",
                           "Does not change the rule-based verification verdict."]}
    write_json(run / "semantic-verification.json", out)
    return out


def _assess_review(R, run, review, rec, inp, acq, allow_mock, now):
    orig, opt = read_text(run / "original.md"), read_text(run / "optimized.md")
    if review["review_method"] == "mock" and not allow_mock:
        R.inc("provenance", "mock_not_production", "mock reviews are offline-test only")
    for k, want in (("original_sha256", sha_text(orig)), ("optimized_sha256", sha_text(opt)),
                    ("registry_sha256", acq["registry_sha256"])):
        if review[k] != want:
            R.fail("structure", f"{k}_mismatch", f"review was made for a different artifact ({k})")
    if review["rubric_version"] != rubric_version():
        R.fail("provenance", "rubric_version_mismatch", "review used another rubric version")
    if not review["reviewer_id"]:
        R.fail("provenance", "reviewer_missing", "reviewer_id required")
    if not review["checked_at"]:
        R.fail("provenance", "checked_at_missing", "checked_at required")
    else:
        parse_ts(review["checked_at"], "checked_at")
    ver = run / "verification.json"
    used = {c["source_id"] for c in rec["changes"] if c["source_id"]}
    bodies = {}
    for e in acq["sources"]:
        if e["source_id"] in used and e["body_path"]:
            bodies[e["source_id"]] = read_text(safe_join(run, e["body_path"]))
            if review["source_hashes"].get(e["source_id"]) != e["body_sha256"]:
                R.fail("structure", "source_hash_mismatch", "source_hashes differ from acquired bodies", source_id=e["source_id"])
    if used - set(bodies):
        R.inc("structure", "cited_body_unavailable", "a cited source body is not available for quote checks")
    if review["review_method"] == "separate-context":
        ref = review["observed_execution_ref"]
        if not review["model_id"] or not ref or not Path(ref).is_file():
            R.inc("provenance", "execution_not_observed", "separate-context review needs model_id and an existing observed_execution_ref")
        else:
            events = parse_transcript(ref)
            if not events:
                R.inc("provenance", "execution_log_empty", "observed_execution_ref has no tool events in the supported format")
            host_sha = load_json(ver).get("host_log_sha256") if ver.is_file() else None
            if host_sha and sha_file(ref) == host_sha:
                R.inc("provenance", "not_separate_context", "review execution log is the generation session's log")
            if Path(ref).resolve().is_relative_to(run.resolve()):
                R.fail("provenance", "execution_ref_inside_run", "execution log inside run directory is self-authored")
    elif review["review_method"] == "human" and review["observed_execution_ref"]:
        R.fail("provenance", "human_review_with_model_log", "human review must not claim a model execution log")
    changes = {c["change_id"]: c for c in rec["changes"]}
    seen_ids, per = set(), {}
    judgments = []
    for ck in review["checks"]:
        shape(ck, CHK_F, "check")
        if ck["check_id"] in seen_ids or ck["category"] not in CATEGORIES or ck["judgment"] not in JUDGMENTS:
            raise ContractError(f"check {ck['check_id']}: duplicate id, bad category or judgment")
        seen_ids.add(ck["check_id"])
        cid = ck["change_id"]
        ch = changes.get(cid) if cid is not None else None
        if cid is not None and ch is None:
            R.fail("coverage", "unknown_change", "check refers to a change that does not exist", change_id=cid)
            continue
        osp, nsp = _span(ck["original_span"], len(orig), "original_span"), _span(ck["optimized_span"], len(opt), "optimized_span")
        if ch is not None and (osp != [ch["before_start"], ch["before_end"]] or nsp != [ch["after_start"], ch["after_end"]]):
            R.fail("coverage", "span_not_change_span", "check spans differ from the change's before/after spans", change_id=cid)
        if ck["category"] == "source-support":
            if ch is None or ch["source_id"] is None:
                R.fail("coverage", "source_support_without_source", "source-support needs a change that cites a source", change_id=cid)
            else:
                _check_quote(R, ck, ch, bodies)
        per.setdefault((cid, ck["category"]), []).append(ck["judgment"])
        judgments.append(ck["judgment"])
    for c in rec["changes"]:
        need = ["scope"] + (["source-support"] if c["source_id"] is not None else [])
        for cat in need:
            if (c["change_id"], cat) not in per:
                R.inc("coverage", "missing_check", f"no {cat} check for change", change_id=c["change_id"])
    for cat, minimum in (("intent-preservation", max(1, len(inp["constraints"]))), ("unrequested-additions", 1)):
        if len(per.get((None, cat), [])) < minimum:
            R.inc("coverage", "missing_check", f"need >= {minimum} global {cat} check(s)")
    if "contradicted" in judgments:
        R.fail("judgments", "contradicted", "a check judged the change/prompt contradicted")
    if "insufficient" in judgments:
        R.inc("judgments", "insufficient", "a check could not be decided")
    undecided = not review["checks"] or "insufficient" in judgments or any(e["code"] == "missing_check" for e in R.errors)
    if "contradicted" not in judgments and not undecided and review["status"] != "PASS":
        R.fail("judgments", "claimed_status_mismatch", f"claimed {review['status']} but every check is supported")
    elif "contradicted" not in judgments and undecided and review["status"] == "PASS":
        R.inc("judgments", "claimed_status_overstated", "claimed PASS over missing or undecided checks")
    if not review["calibration_ref"] or not review["calibration_sha256"]:
        R.inc("calibration", "uncalibrated", "no calibration record referenced; scorer is uncalibrated")
        return None
    return check_calibration(R, review, review["calibration_ref"], now)


def _check_quote(R, ck, ch, bodies):
    cid = ck["change_id"]
    sid, lines, ex = ck["source_id"], ck["source_lines"], ck["excerpt"]
    if sid != ch["source_id"] or lines is None or ex is None or len(ex) > MAX_EXCERPT:
        R.fail("coverage", "source_support_citation_missing", "source-support check lacks source/lines/short excerpt", change_id=cid)
        return
    body = bodies.get(sid)
    if body is None:
        return
    bl = body.split("\n")[:-1]
    if not (len(lines) == 2 and all(isinstance(x, int) for x in lines) and 1 <= lines[0] <= lines[1] <= len(bl)) \
            or ex not in "\n".join(bl[lines[0] - 1:lines[1]]):
        R.fail("coverage", "fabricated_quote", "excerpt not found at the stated source lines", change_id=cid)


# ---------------------------------------------------------------- compare
PLAN_F = {"schema_version": (I, 1), "plan_id": (S, 1), "target_id": (S, 1), "target_surface": (S, 1),
          "model_id": (S, 1), "registry_sha256": (S, 1), "rubric_version": (S, 1), "created_at": (S, 1),
          "cases": (L, 1), "repeats": (D, 1), "budget": (D, 1), "stop_conditions": (L, 1),
          "execution_conditions": (L, 1), "metrics": (L, 1), "scorers": (L, 1), "adoption": (D, 1)}
ADOPT_F = {"primary_metric": (S, 1), "min_primary_improvement": (N, 1), "max_critical_violations": (I, 1),
           "min_cases": (I, 1), "max_loss_ratio": (N, 1), "max_uncertain_ratio": (N, 1),
           "max_time_increase_ratio": (NN, 1), "tie_margin": (N, 1)}
ROW_F = {"case_id": (S, 1), "variant": (S, 1), "repeat_id": (S, 1), "target_id": (S, 1), "target_surface": (S, 1),
         "model_id": (S, 1), "plan_sha256": (S, 1), "prompt_sha256": (S, 1), "input_sha256": (S, 1),
         "started_at": (S, 1), "finished_at": (S, 1), "outcome": (S, 1), "execution_ref": (SN, 1),
         "execution_sha256": (SN, 1), "artifact_ref": (SN, 1), "artifact_sha256": (SN, 1), "metrics": (D, 1),
         "critical_violations": (I, 1), "scoring": (D, 1), "order": (I, 1), "mock": (B, 1)}
CASE_F = {"case_id": (S, 1), "split": (S, 1), "target_id": (S, 1), "target_surface": (S, 1), "model_id": (S, 1),
          "input_ref": (S, 1), "input_sha256": (S, 1), "original_ref": (S, 1), "original_sha256": (S, 1),
          "optimized_ref": (S, 1), "optimized_sha256": (S, 1), "requirements": (L, 1), "rubric": (L, 1),
          "status": (S, 1)}
OUTCOMES = ("completed", "failed", "timeout", "refused", "not_run")


def load_jsonl(p):
    out = []
    try:
        lines = Path(p).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise Unreadable(f"missing file: {p}")
    for i, line in enumerate(lines, 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                raise ContractError(f"{p} line {i}: invalid JSON")
    return out


def _case_stats(plan, rows):
    """Aggregate one case by case (repeats are not independent cases)."""
    pm, dirn = plan["adoption"]["primary_metric"], 1
    for m in plan["metrics"]:
        if m["name"] == pm:
            dirn = 1 if m["direction"] == "higher" else -1
    per = {}
    for v in ("original", "optimized"):
        rs = [r for r in rows if r["variant"] == v]
        done = [r for r in rs if r["outcome"] == "completed"]
        vals = [r["metrics"][pm] for r in done]
        per[v] = {"n": len(rs), "fail_rate": (len(rs) - len(done)) / max(1, len(rs)), "mean": statistics.fmean(vals) if vals else None,
                  "sd": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
                  "seconds": statistics.fmean(_secs(r) for r in rs) if rs else 0.0,
                  "metrics": {m["name"]: statistics.fmean(r["metrics"][m["name"]] for r in done) if done else None
                              for m in plan["metrics"]},
                  "crit": sum(r["critical_violations"] for r in rs),
                  "inconsistent": any(r["scoring"].get("swapped_consistent") is False for r in done)}
    o, p = per["original"], per["optimized"]
    delta = None if o["mean"] is None or p["mean"] is None else (p["mean"] - o["mean"]) * dirn
    if p["fail_rate"] > o["fail_rate"]:
        res = "loss"
    elif p["fail_rate"] < o["fail_rate"]:
        res = "win"
    elif delta is None:
        res = "tie"
    elif o["inconsistent"] or p["inconsistent"]:
        res = "uncertain"
    else:
        tm = plan["adoption"]["tie_margin"]
        res = "win" if delta > tm else ("loss" if delta < -tm else "tie")
    return {"result": res, "delta": delta, "original": o, "optimized": p}


def _secs(r):
    return (parse_ts(r["finished_at"]) - parse_ts(r["started_at"])).total_seconds()


def compare(run, plan_path, results_path, cases_path="evals/prompt-tailor-cases.jsonl", allow_mock=False):
    run = Path(run)
    now = utcnow()
    inp = load_json(_read_run(run, "input.json"))
    _, reg_sha = load_registry()
    ev = {"schema_version": 1, "run_id": run.name, "checked_at": iso(now), "plan_id": None, "plan_sha256": None,
          "target_id": inp["target_id"], "target_surface": inp["target_surface"], "model_id": inp["model_id"],
          "registry_sha256": reg_sha, "rubric_version": rubric_version(), "status": "NOT_EVALUATED",
          "contract_violations": [], "incomplete_reasons": [], "execution_artifacts": [], "cases_evaluated": 0,
          "cases_missing": 0, "per_variant_metrics": None, "differences": None, "repeat_variability": None,
          "wins": 0, "ties": 0, "losses": 0, "uncertain": 0, "critical_violations": {"original": 0, "optimized": 0},
          "cost": None,
          "scope": "Only the listed cases, target, surface, model and conditions; not a general performance claim.",
          "limitations": ["Plan freezing before execution is checked by timestamps and plan hash only.",
                          "Free-form scorer quality is only as good as its recorded calibration.",
                          "Results do not transfer to other surfaces, models, products or later product versions."]}
    cv, inc = ev["contract_violations"], ev["incomplete_reasons"]

    def done(status):
        ev["status"] = status
        write_json(run / "evaluation.json", ev)
        code = {"IMPROVED_ON_TEST_SET": 0, "REGRESSED": 1, "NO_CLEAR_IMPROVEMENT": 1}.get(status, 2)
        return (1 if cv else code), ev

    if not Path(plan_path).is_file() or not Path(results_path).is_file():
        inc.append("plan or results file missing: nothing was executed/recorded")
        return done("NOT_EVALUATED")
    plan_raw = Path(plan_path).read_bytes()
    try:
        plan = json.loads(plan_raw.decode("utf-8"))
        shape(plan, PLAN_F, "plan")
        shape(plan["adoption"], ADOPT_F, "plan.adoption")
        shape(plan["budget"], {"max_runs": (I, 1), "max_total_seconds": (NN, 1)}, "plan.budget")
        shape(plan["repeats"], {"original": (I, 1), "optimized": (I, 1)}, "plan.repeats")
        strs(plan["stop_conditions"], "stop_conditions")
        strs(plan["execution_conditions"], "execution_conditions")
        for m in plan["metrics"]:
            shape(m, {"name": (S, 1), "direction": (S, 1), "method": (S, 1)}, "plan.metric")
            if m["direction"] not in ("higher", "lower") or m["method"] not in ("code", "rubric"):
                raise ContractError("plan.metric: bad direction/method")
        for sc in plan["scorers"]:
            shape(sc, {"scorer_id": (S, 1), "method": (S, 1), "rubric_version": (SN, 1), "calibration_ref": (SN, 1),
                       "calibration_sha256": (SN, 1)}, "plan.scorer")
        for c in plan["cases"]:
            shape(c, {"case_id": (S, 1), "split": (S, 1)}, "plan.case")
            if c["split"] not in ("development", "held-out"):
                raise ContractError("plan.case: bad split")
        uniq([c["case_id"] for c in plan["cases"]], "plan case_id")
        if plan["schema_version"] != 1 or plan["adoption"]["primary_metric"] not in {m["name"] for m in plan["metrics"]}:
            raise ContractError("plan: bad schema_version or primary_metric not declared")
        parse_ts(plan["created_at"], "plan.created_at")
        raw_rows = load_jsonl(results_path)
        for r in raw_rows:
            shape(r, ROW_F, "result row")
            shape(r["scoring"], {"method": (S, 1), "scorer_id": (S, 1), "rubric_version": (SN, 1),
                                 "calibration_sha256": (SN, 1), "blind": (B, 1), "swapped_consistent": ((bool, type(None)), 1)},
                  "row.scoring")
            if r["variant"] not in ("original", "optimized") or r["outcome"] not in OUTCOMES:
                raise ContractError("result row: bad variant/outcome")
    except ContractError as e:
        cv.append(str(e))
        return done("INCOMPLETE")
    plan_sha = sha_bytes(plan_raw)
    ev["plan_id"], ev["plan_sha256"] = plan["plan_id"], plan_sha
    if not raw_rows:
        inc.append("no result rows recorded")
        return done("NOT_EVALUATED")
    if (plan["target_id"], plan["target_surface"], plan["model_id"]) != (inp["target_id"], inp["target_surface"], inp["model_id"]):
        cv.append("plan target/surface/model differ from the run (results do not transfer across surfaces/models)")
    if plan["registry_sha256"] != reg_sha or plan["rubric_version"] != rubric_version():
        inc.append("plan was frozen against another registry/rubric version")
    base = Path(results_path).resolve().parent
    # cases file
    cases = {}
    try:
        for c in load_jsonl(cases_path):
            shape(c, CASE_F, "case")
            cases.setdefault(c["case_id"], c)
    except (Unreadable, ContractError) as e:
        cv.append(f"cases file: {e}")
    cdir = Path(cases_path).resolve().parent
    for pc in plan["cases"]:
        c = cases.get(pc["case_id"])
        if c is None or c["split"] != pc["split"]:
            cv.append(f"case {pc['case_id']}: missing from cases file or split differs (held-out mixing)")
            continue
        if (c["target_id"], c["target_surface"], c["model_id"]) != (plan["target_id"], plan["target_surface"], plan["model_id"]):
            cv.append(f"case {pc['case_id']}: target/surface/model differ from plan")
        if c["status"] != "ready":
            inc.append(f"case {pc['case_id']}: requirements not reviewed yet")
        for ref, h in (("input_ref", "input_sha256"), ("original_ref", "original_sha256"), ("optimized_ref", "optimized_sha256")):
            try:
                if sha_file(safe_join(cdir, c[ref])) != c[h]:
                    cv.append(f"case {pc['case_id']}: {ref} hash mismatch")
            except (ContractError, FileNotFoundError) as ex:
                cv.append(f"case {pc['case_id']}: {ref} unreadable ({ex})")
    expected_runs = len(plan["cases"]) * (plan["repeats"]["original"] + plan["repeats"]["optimized"])
    if expected_runs > plan["budget"]["max_runs"]:
        inc.append("planned runs exceed budget.max_runs: execution stopped before completion")
    if len(raw_rows) > plan["budget"]["max_runs"]:
        cv.append("recorded runs exceed the planned budget")
    scorers = {(s["scorer_id"], s["rubric_version"], s["calibration_sha256"]): s for s in plan["scorers"]}
    for s in plan["scorers"]:
        if s["method"] != "code" and (not s["calibration_sha256"] or not s["rubric_version"]):
            inc.append(f"scorer {s['scorer_id']} is uncalibrated")
        if s["calibration_ref"]:
            try:
                if sha_file(Path(s["calibration_ref"])) != s["calibration_sha256"]:
                    cv.append(f"scorer {s['scorer_id']}: calibration hash mismatch")
            except FileNotFoundError:
                inc.append(f"scorer {s['scorer_id']}: calibration_ref not found")
    seen, by_case, total_secs = set(), {}, 0.0
    plan_ts = parse_ts(plan["created_at"])
    for r in raw_rows:
        key = (r["case_id"], r["variant"], r["repeat_id"])
        pcase = next((c for c in plan["cases"] if c["case_id"] == r["case_id"]), None)
        if key in seen or pcase is None:
            cv.append(f"row {key}: duplicate repeat or case not in plan")
            continue
        seen.add(key)
        if r["plan_sha256"] != plan_sha:
            cv.append(f"row {key}: plan_sha256 differs (plan changed after the fact)")
        if (r["target_id"], r["target_surface"], r["model_id"]) != (plan["target_id"], plan["target_surface"], plan["model_id"]):
            cv.append(f"row {key}: target/surface/model differ from plan (condition mismatch)")
        c = cases.get(r["case_id"])
        if c and (r["prompt_sha256"] != c[f"{r['variant']}_sha256"] or r["input_sha256"] != c["input_sha256"]):
            cv.append(f"row {key}: prompt/input hash differs from the case")
        if r["mock"] and not allow_mock:
            inc.append(f"row {key}: mock result (offline tests only)")
        if parse_ts(r["started_at"]) < plan_ts:
            inc.append(f"row {key}: started before the plan was frozen")
        if parse_ts(r["finished_at"]) < parse_ts(r["started_at"]):
            cv.append(f"row {key}: finished before start")
        total_secs += _secs(r)
        if r["outcome"] != "not_run":
            for ref, h in (("execution_ref", "execution_sha256"), ("artifact_ref", "artifact_sha256")):
                if r[ref] is None and (ref == "execution_ref" or r["outcome"] == "completed"):
                    inc.append(f"row {key}: no {ref} evidence")
                elif r[ref] is not None:
                    try:
                        if sha_file(safe_join(base, r[ref])) != r[h]:
                            cv.append(f"row {key}: {ref} hash mismatch")
                        else:
                            ev["execution_artifacts"].append({"case_id": r["case_id"], "variant": r["variant"], "repeat_id": r["repeat_id"], ref: r[ref], h: r[h]})
                    except (ContractError, FileNotFoundError):
                        inc.append(f"row {key}: {ref} missing or unsafe")
        sc = r["scoring"]
        if (sc["scorer_id"], sc["rubric_version"], sc["calibration_sha256"]) not in scorers:
            inc.append(f"row {key}: scorer/rubric/calibration not in the frozen plan")
        elif sc["method"] != "code" and not (sc["blind"] and sc["swapped_consistent"] is not None):
            inc.append(f"row {key}: free-form scoring must be label-blind with a reversed-order check")
        if r["outcome"] == "completed" and any(r["metrics"].get(m["name"]) is None for m in plan["metrics"]):
            inc.append(f"row {key}: completed row lacks planned metrics")
        by_case.setdefault(r["case_id"], []).append(r)
    orders = [r["order"] for rs in by_case.values() for r in rs
              if r["variant"] == "optimized" and r["scoring"]["method"] != "code" and r["outcome"] == "completed"]
    if len(orders) > 1 and len(set(orders)) == 1:
        inc.append("free-form scoring order was not varied: the optimized output was always presented in the same position")
    if plan["budget"]["max_total_seconds"] is not None and total_secs > plan["budget"]["max_total_seconds"]:
        cv.append("recorded time exceeds budget.max_total_seconds")
    # aggregation (held-out cases decide adoption; development cases are reported only)
    stats, missing = {}, 0
    for pc in plan["cases"]:
        rows = by_case.get(pc["case_id"], [])
        exp = plan["repeats"]
        if any(sum(1 for r in rows if r["variant"] == v and r["outcome"] != "not_run") < exp[v] for v in exp):
            missing += pc["split"] == "held-out"
            ev["cases_missing"] += 1
            continue
        stats[pc["case_id"]] = (pc["split"], _case_stats(plan, rows))
    held = [s for sp, s in stats.values() if sp == "held-out"]
    ev["cases_evaluated"] = len(stats)
    crit_o = sum(s["optimized"]["crit"] for _, s in stats.values()) + sum(
        r["critical_violations"] for rs in by_case.values() for r in rs if r["variant"] == "optimized" and r["case_id"] not in stats)
    ev["critical_violations"] = {"original": sum(s["original"]["crit"] for _, s in stats.values()), "optimized": crit_o}
    for s in held:
        k = {"win": "wins", "tie": "ties", "loss": "losses", "uncertain": "uncertain"}[s["result"]]
        ev[k] += 1
    if held:
        names = [m["name"] for m in plan["metrics"]]
        ev["per_variant_metrics"] = {v: {n: _mean([s[v]["metrics"][n] for s in held]) for n in names} for v in ("original", "optimized")}
        ev["differences"] = {n: _mean([_sub(s["optimized"]["metrics"][n], s["original"]["metrics"][n]) for s in held]) for n in names}
        ev["repeat_variability"] = {v: statistics.fmean(s[v]["sd"] for s in held) for v in ("original", "optimized")}
        ev["cost"] = {"original_seconds_per_run": statistics.fmean(s["original"]["seconds"] for s in held),
                      "optimized_seconds_per_run": statistics.fmean(s["optimized"]["seconds"] for s in held)}
    if cv:
        return done("INCOMPLETE")
    if inc:
        # a recorded critical violation is not offset by missing evidence elsewhere, but untrusted rows prove nothing
        return done("INCOMPLETE")
    ad = plan["adoption"]
    if crit_o > ad["max_critical_violations"]:
        return done("REGRESSED")
    if not held:
        inc.append("no complete held-out case evaluated")
        return done("INCOMPLETE")
    if missing or len(held) < ad["min_cases"]:
        inc.append("held-out cases missing or fewer than adoption.min_cases")
        return done("INCOMPLETE")
    n = len(held)
    delta = _mean([s["delta"] for s in held if s["delta"] is not None]) or 0.0
    t_ok = True
    if ad["max_time_increase_ratio"] is not None and ev["cost"]["original_seconds_per_run"] > 0:
        t_ok = ev["cost"]["optimized_seconds_per_run"] <= ev["cost"]["original_seconds_per_run"] * (1 + ad["max_time_increase_ratio"])
    if delta >= ad["min_primary_improvement"] and ev["losses"] / n <= ad["max_loss_ratio"] \
            and ev["uncertain"] / n <= ad["max_uncertain_ratio"] and t_ok:
        return done("IMPROVED_ON_TEST_SET")
    if delta < -ad["tie_margin"] or ev["losses"] > ev["wins"]:
        return done("REGRESSED")
    return done("NO_CLEAR_IMPROVEMENT")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _sub(a, b):
    return None if a is None or b is None else a - b


# ---------------------------------------------------------------- CLI
def _emit(name, code, obj, summary):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    print(f"{name}: {summary} (exit {code})", file=sys.stderr)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(prog="evidence.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--input", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--mode", choices=("live", "cache"), required=True)
    f.add_argument("--cache")
    v = sub.add_parser("verify")
    v.add_argument("--run", required=True)
    v.add_argument("--mode", choices=("live", "cache"), required=True)
    v.add_argument("--host-log")
    a = sub.add_parser("assess")
    a.add_argument("--run", required=True)
    a.add_argument("--review", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--run", required=True)
    c.add_argument("--plan", required=True)
    c.add_argument("--results", required=True)
    c.add_argument("--cases", default="evals/prompt-tailor-cases.jsonl")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page; the JSON holds Japanese text
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "fetch":
            code, acq = do_fetch(args.input, args.out, args.mode, args.cache)
            bad = [f"{e['source_id']}={e['status']}({e['error']})" for e in acq["sources"] if e["status"] != ("live" if args.mode == "live" else "cache")]
            return _emit("fetch", code, acq, "ok" if code == 0 else "not satisfied: " + (", ".join(bad) or "target unresolved"))
        if args.cmd == "verify":
            rep = verify(args.run, args.mode, args.host_log)
            code = {"PASS_LIVE": 0, "PASS_CACHE": 0, "FAIL": 1}.get(rep["verdict"], 2)
            codes = sorted({e["code"] for e in rep["errors"]})
            return _emit("verify", code, rep, f"{rep['verdict']} {codes}")
        if args.cmd == "assess":
            out = assess(args.run, args.review)
            code = {"PASS": 0, "FAIL": 1}.get(out["verdict"], 2)
            return _emit("assess", code, out, f"{out['verdict']} {sorted({e['code'] for e in out['errors']})}")
        code, ev = compare(args.run, args.plan, args.results, args.cases)
        return _emit("compare", code, ev, f"{ev['status']} {ev['contract_violations'][:3] or ev['incomplete_reasons'][:3]}")
    except ContractError as e:
        print(f"{args.cmd}: contract violation: {e}", file=sys.stderr)
        return 1
    except Unreadable as e:
        print(f"{args.cmd}: not runnable: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
