#!/usr/bin/env python3
"""official-prompt-tailor evidence tool: fetch / verify / assess / compare / drift / rules / sources.

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
import shlex
import socket
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SKILL_DIR / "references" / "registry.json"
RUBRIC_PATH = SKILL_DIR / "references" / "review-rubric.md"

HOSTS = ("claude-code", "codex", "chatgpt-work", "antigravity", "claude-cowork")  # products that can run this skill
ANTIGRAVITY_CLI_VERSIONS = ("1.3.5", "1.4.0", "1.4.3")
LOG_ADAPTERS = {"antigravity": "antigravity-transcript-jsonl-v1", "claude-code": "claude-code-transcript-jsonl-v1", "codex": "codex-rollout-jsonl-v1"}  # host -> pinned log format
LOG_ADAPTER_HOSTS = tuple(LOG_ADAPTERS)  # hosts whose session logs the read-observation adapter understands
CODEX_CLI_VERSION = re.compile(r"(?:0\.155\.1|0\.163\.0|0\.162\.[0-9]+(?:-alpha(?:\.[0-9]+)+)?)")
TARGET_IDS = ("chatgpt", "chatgpt-work", "codex", "claude", "claude-cowork", "claude-code", "gemini",
              "antigravity", "gemini-agent-app", "gemini-cli", "google-adk-agent")
PURPOSES = ("one-shot", "persistent-instructions", "skill-instructions", "agent-instruction")
EXTRACTIONS = ("html-main-v1", "html-article-v1", "html-body-v1", "markdown-v1", "markdown-v2")
MARKDOWN = ("markdown-v1", "markdown-v2")
FRESH_DAYS = 7  # local policy, not a vendor rule
MAX_HOPS, TIMEOUT, MAX_BYTES = 5, 15, 5 * 1024 * 1024
RETRY_WAIT = 2  # seconds drift waits before it looks again at a source that looks wrong
MAX_EXCERPT = 400
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
JUDGMENTS = ("supported", "contradicted", "insufficient")
CATEGORIES = ("source-support", "scope", "intent-preservation", "unrequested-additions")
HUMAN_REVIEWER = "human:"  # a rule's reviewer_id is "human:<name or role>" only when a person reviewed it; nothing else counts
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


def human_reviewed(rule):
    return rule["reviewer_id"] is not None and rule["reviewer_id"].startswith(HUMAN_REVIEWER)


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
            return "dns_delegated_to_proxy"  # residual risk: documented in docs/trust-boundary.md
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


def decode_content(headers, raw):
    """Undo the gzip a server may apply on its own: antigravity.google does for 1 answer in 10 or so, whatever the request
    asks for, and urllib does not unpack it. The result is capped at MAX_BYTES + 1 like any body, so an oversized or
    bomb-like answer is refused later as response_too_large."""
    coding = headers.get("content-encoding", "").strip().lower()
    if coding in ("", "identity"):
        return raw
    if coding not in ("gzip", "x-gzip"):
        raise Blocked("unsupported_content_encoding")
    unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        body = unpacker.decompress(raw, MAX_BYTES + 1)
    except zlib.error:
        raise Blocked("bad_content_encoding")
    if len(body) <= MAX_BYTES and not unpacker.eof:
        raise Blocked("bad_content_encoding")  # the stream stopped before its end
    return body


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
        headers, raw = {k.lower(): v for k, v in r.headers.items()}, r.read(MAX_BYTES + 1)
    # only an answer that will be read as a page is unpacked; an error page keeps its status code whatever its bytes are
    return status, headers, decode_content(headers, raw) if status == 200 else raw


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
# "Updated 3 days ago" changes every day without the content changing, so it must not move the body hash.
# Absolute dates ("Last updated: Jun 18, 2026") stay: they only change when the page does.
UPDATED_LABEL = re.compile(r"^Updated (today|yesterday|(about |over |almost )?(a|an|\d+) (minute|hour|day|week|month|year)s? ago)$", re.I)


# markdown-v2 is markdown-v1 without the query of a signed image address. Intercom (support.claude.com) signs the images of an article
# (`?expires=…&signature=…&req=…`) and re-signs them every hour, so the same article has a new body hash each hour (seen 2026-10-16, claude-cowork-projects).
# Only an image whose query holds `signature=` is touched; the bounds keep the match linear on hostile input.
SIGNED_IMAGE = re.compile(r"(!\[[^\]\n]{0,300}\]\([^)\s?]{1,2000})\?[^)\s]{0,2000}\bsignature=[^)\s]{0,2000}\)")


def process_body(src, raw, content_type):
    """Deterministic raw bytes -> normalised body text. Raises Blocked(code)."""
    ex = src["extraction"]
    if ex not in EXTRACTIONS:
        raise Blocked("unsupported_extraction_version")
    ctype = (content_type or "").split(";")[0].strip().lower()
    m = re.search(r"charset=([^;\s]+)", content_type or "", re.I)
    if m and m.group(1).strip("\"'").lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise Blocked("unsupported_charset")
    want = ("text/markdown", "text/plain", "text/x-markdown") if ex in MARKDOWN else \
        ("text/html", "application/xhtml+xml")
    if ctype not in want:
        raise Blocked(f"unexpected_content_type:{ctype}")
    try:
        text = norm(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise Blocked("not_utf8")
    if ex in MARKDOWN:
        body = "\n".join(l.rstrip() for l in text.split("\n")).strip("\n") + "\n"
        if ex == "markdown-v2":
            body = SIGNED_IMAGE.sub(r"\1)", body)
    else:
        p = _Extract({"html-main-v1": "main", "html-article-v1": "article", "html-body-v1": "body"}[ex])
        p.feed(text)
        p.close()
        lines = [ln for ln in p.result() if not UPDATED_LABEL.match(ln)]
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
            if human_reviewed(r) and not r["reviewer_id"][len(HUMAN_REVIEWER):].strip():
                raise ContractError(f"rule {r['rule_id']}: a person's reviewer_id needs a name after '{HUMAN_REVIEWER}'")
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


def route_sources(reg, inp):
    """What the target routes for this purpose and surface (the model is not looked at yet).
    -> (routed, via, missed): routed = source ids in fetch order, required ones first; via = the route that chose each;
    missed = for each source the target lists but did not route, the conditional routes that did not match, and which half failed."""
    t = next(t for t in reg["targets"] if t["target_id"] == inp["target_id"])
    routed, via, missed = list(t["required_sources"]), {i: {"route": "required"} for i in t["required_sources"]}, {}
    for c in t["conditional_sources"]:
        purpose_ok = "all" in c["purposes"] or inp["purpose"] in c["purposes"]
        surface_ok = in_list(c["surfaces"], inp["target_surface"])
        for i in c["source_ids"]:
            if purpose_ok and surface_ok:
                if i not in routed:
                    routed.append(i)
                    via[i] = {"route": "conditional", "purposes": c["purposes"], "surfaces": c["surfaces"]}
            else:
                missed.setdefault(i, []).append({"purposes": c["purposes"], "surfaces": c["surfaces"],
                                                 "purpose_ok": purpose_ok, "surface_ok": surface_ok})
    return routed, via, {i: m for i, m in missed.items() if i not in via}


def select_sources(reg, inp):
    """Sources required for this target + those whose purpose AND surface conditions both match.
    A source limited to certain models is kept only for those models: its rules cannot apply to any other."""
    if inp["target_id"] == "unresolved":
        return []
    models = {s["source_id"]: s["models"] for s in reg["sources"]}
    return [i for i in route_sources(reg, inp)[0] if in_list(models[i], inp["model_id"])]


def explain_sources(reg, inp):
    """select_sources, and why each other source of the target was left out (same routing; a test pins the two together).
    -> {"selected": [{source_id, models, route, purposes?, surfaces?}], "skipped": [{source_id, models, reason, ...}]};
    reason: route_not_matched (purpose and/or surface), model_mismatch, model_unknown (the model is not given)."""
    if inp["target_id"] == "unresolved":
        return {"selected": [], "skipped": []}
    routed, via, missed = route_sources(reg, inp)
    models = {s["source_id"]: s["models"] for s in reg["sources"]}
    selected, skipped = [], []
    for i in routed:
        if in_list(models[i], inp["model_id"]):
            selected.append({"source_id": i, "models": models[i], **via[i]})
        else:
            skipped.append({"source_id": i, "models": models[i],
                            "reason": "model_unknown" if inp["model_id"] == "unknown" else "model_mismatch"})
    for i, routes in missed.items():
        near = min(routes, key=lambda r: (not r["purpose_ok"]) + (not r["surface_ok"]))
        skipped.append({"source_id": i, "models": models[i], "reason": "route_not_matched", **near})
    return {"selected": selected, "skipped": skipped}


def model_hint(reg, model_id):
    """Registered model ids that differ from model_id only in spelling (case, '.', '_' or spaces, a 'claude-' prefix, a date suffix).
    A hint for the reader: the selection itself needs the exact id."""
    def key(m):
        m = re.sub(r"[\s._]+", "-", m.strip().lower())
        return re.sub(r"^claude-", "", re.sub(r"-(\d{8}|\d{4}-\d{2}-\d{2})$", "", m))
    registered = sorted({m for s in reg["sources"] for m in s["models"] if m != "all"})
    return [] if model_id in registered else [m for m in registered if key(m) == key(model_id)]


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


def fetch_live(src, run, process=None):
    """Fetch the first allowed URL and save what came back. `process` (default process_body) turns the bytes into the saved text:
    tools/maintain.py passes its own for a sitemap; everything else about the fetch is the same."""
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
        body = (process or process_body)(src, raw, e["content_type"])
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
    # Python 3.9 on Windows returns a relative path that does not exist yet unresolved: make it absolute first.
    if not (Path.cwd() / out.parent).resolve().is_relative_to(Path.cwd().resolve()):
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
    (out / "original.md").write_bytes(original.encode("utf-8"))
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


def _rule_stale(rule, body, body_sha):
    """True when the source body changed since the rule was reviewed (hash differs or the excerpt is gone)."""
    return rule["source_body_sha256"] != body_sha or rule["source_excerpt"] not in body


def _rule_state(rule, body, body_sha):
    if body is None:
        return "unverifiable"
    if rule["review_status"] != "reviewed":
        return "unreviewed"
    if not _rule_stale(rule, body, body_sha):
        return "current"
    return "excerpt_missing" if rule["source_excerpt"] not in body else "changed"


Observed = namedtuple("Observed", "status error body sha256")  # one source when checked; body is None if it could not be read


def _observe_run(run, only):
    acq = load_json(_read_run(run, "acquisition.json"))
    validate_acquisition(acq)
    observed = {}
    for e in acq["sources"]:
        if only and e["source_id"] not in only:
            continue
        body = None
        if e["status"] in ("live", "cache"):  # stale / blocked / missing cannot speak for the source today
            raw = safe_join(run, e["body_path"]).read_bytes()
            if sha_bytes(raw) != e["body_sha256"]:
                raise ContractError(f"{e['source_id']}: body.txt differs from the recorded SHA-256")
            body = norm(raw.decode("utf-8"))
        observed[e["source_id"]] = Observed(e["status"], e["error"], body, e["body_sha256"])
    return observed


def _observe_live(sources, only):
    observed = {}
    with tempfile.TemporaryDirectory() as tmp:
        for s in sources:
            if only and s["source_id"] not in only:
                continue
            e = fetch_live(s, Path(tmp))
            body = read_text(Path(tmp) / e["body_path"]) if e["status"] == "live" else None
            observed[s["source_id"]] = Observed(e["status"], e["error"], body, e["body_sha256"])
    return observed


def _rule_states(reg, observed):
    return [{"rule_id": r["rule_id"], "source_id": r["source_id"],
             "state": _rule_state(r, observed[r["source_id"]].body, observed[r["source_id"]].sha256)}
            for r in reg["rules"] if r["source_id"] in observed]


def drift(run=None, only=()):
    """Have source bodies changed since each rule was reviewed? Hash and excerpt only; no semantic judgement."""
    reg, reg_sha = load_registry()
    unknown = sorted(set(only) - {s["source_id"] for s in reg["sources"]})
    if unknown:
        raise ContractError(f"unknown source ids {unknown}")
    observed = _observe_run(Path(run), only) if run else _observe_live(reg["sources"], only)
    rules = _rule_states(reg, observed)
    doubtful = {r["source_id"] for r in rules if r["state"] in ("changed", "excerpt_missing")} | {k for k, o in observed.items() if o.body is None}
    if doubtful and not run:  # one answer can be a CDN node mid-deploy or a dropped connection: look once more before reporting
        time.sleep(RETRY_WAIT)
        observed.update(_observe_live([s for s in reg["sources"] if s["source_id"] in doubtful], ()))
        rules = _rule_states(reg, observed)
    if any(r["state"] in ("changed", "excerpt_missing") for r in rules):
        verdict = "DRIFT"
    else:
        verdict = "INCOMPLETE" if any(o.body is None for o in observed.values()) else "CURRENT"
    return {"schema_version": 1, "mode": "run" if run else "live", "checked_at": iso(utcnow()),
            "registry_sha256": reg_sha, "verdict": verdict,
            "sources": [{"source_id": k, "status": o.status, "error": o.error, "body_sha256": o.sha256} for k, o in observed.items()],
            "rules": rules}


def _applicable(reg, inp, acq):
    """Reviewed rules in scope for the input (the checks verify makes) and grounded in a source the run fetched."""
    srcs = {s["source_id"]: s for s in reg["sources"] if s["source_id"] in acq["selected_sources"]}
    tid, surface, model, purpose = inp["target_id"], inp["target_surface"], inp["model_id"], inp["purpose"]
    return [r for r in reg["rules"]
            if r["review_status"] == "reviewed" and r["source_id"] in srcs and purpose in r["purposes"]
            and tid in r["target_ids"] and tid in srcs[r["source_id"]]["target_ids"]
            and in_list(r["surfaces"], surface) and in_list(srcs[r["source_id"]]["surfaces"], surface) and in_list(r["models"], model)]


def _review_summary(rules):
    """Who reviewed these rules: how many, how many by a person (reviewer_id 'human:...'), and the reviewer_ids with counts."""
    rules, reviewers = list(rules), {}
    for r in rules:
        reviewers[r["reviewer_id"]] = reviewers.get(r["reviewer_id"], 0) + 1
    return {"rules": len(rules), "human_reviewed": sum(1 for r in rules if human_reviewed(r)), "reviewers": dict(sorted(reviewers.items()))}


def applicable_rules(run):
    """The reviewed rules a run may choose from. The registry is too large to read whole; preconditions and exclusions
    are left to the one who picks. Each rule says who reviewed it, so a result built on them can say so."""
    run = Path(run)
    reg, reg_sha = load_registry()
    inp, acq = load_json(_read_run(run, "input.json")), load_json(_read_run(run, "acquisition.json"))
    validate_input(inp)
    validate_acquisition(acq)
    if acq["registry_sha256"] != reg_sha:
        raise ContractError("registry.json changed since this run was fetched")
    fields = ("rule_id", "source_id", "kind", "purposes", "surfaces", "models", "source_excerpt", "meaning", "allowed_change",
              "preconditions", "exclusions", "reviewer_id")
    chosen = _applicable(reg, inp, acq)
    rules = [{**{k: r[k] for k in fields}, "human_reviewed": human_reviewed(r)} for r in chosen]
    return {"schema_version": 1, "run_id": acq["run_id"], "target_id": inp["target_id"], "target_surface": inp["target_surface"],
            "model_id": inp["model_id"], "purpose": inp["purpose"], "selected_sources": acq["selected_sources"], "rules": rules,
            "review": _review_summary(chosen)}


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
    if _rule_stale(rule, body, body_sha):
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


def read_transcript(path):
    """-> (rows, own): a Claude Code transcript as (line index, record) and whether it is one subagent's own record.
    A subagent's transcript has every message marked sidechain, all under one agentId: it is that agent's whole record."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError) as e:
        raise Unreadable(f"host log unreadable: {e}")
    rows = []
    for seq, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append((seq, json.loads(line)))
        except ValueError:
            raise ContractError(f"host log line {seq + 1}: invalid JSON")
    carriers = [d for _, d in rows if isinstance(d, dict) and isinstance(d.get("message"), dict)]
    agents = {d.get("agentId") for d in carriers}
    return rows, bool(carriers) and all(d.get("isSidechain") for d in carriers) and len(agents) == 1 and None not in agents


def parse_transcript(path):
    """claude-code-transcript-jsonl-v1: tool_use / tool_result blocks of the main thread (or, for a subagent's own file, of that agent)."""
    events = []
    rows, own = read_transcript(path)
    for seq, d in rows:
        msg = d.get("message") if isinstance(d, dict) else None
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), list) or (d.get("isSidechain") and not own):
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


_JS_CMD = re.compile(r'exec_command\(\{\s*cmd\s*:\s*("(?:[^"\\]|\\.)*")')
_TRUNCATED = re.compile(r"…\d+ tokens truncated…")


def _log_string(record, field, seq):
    if not isinstance(record, dict) or not isinstance(record.get(field), str) or (field in ("call_id", "id", "tool_use_id") and not record[field]):
        raise ContractError(f"host log line {seq + 1}: {field} must be a string (nonempty for IDs)")
    return record[field]


def parse_host_log(host, path):
    parsers = {"codex": parse_codex_rollout, "claude-code": parse_transcript, "antigravity": parse_antigravity_transcript}
    if host not in LOG_ADAPTERS or host not in parsers:
        raise ContractError(f"no log adapter for host: {host}")
    return parsers[host](path)


def parse_antigravity_transcript(path):
    """Observed pinned agy transcript; version sidecar is an operator assertion, not authentication."""
    try:
        manifest = json.loads(Path(str(path) + ".version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ContractError("Antigravity version sidecar missing or invalid") from ex
    if not isinstance(manifest, dict):
        raise ContractError("Antigravity version sidecar must be an object")
    expected = {"format": LOG_ADAPTERS["antigravity"], "cli_version": manifest.get("cli_version"),
                "sha256": sha_bytes(Path(path).read_bytes())}
    if manifest != expected or manifest.get("cli_version") not in ANTIGRAVITY_CLI_VERSIONS:
        raise ContractError("Antigravity format/version/hash does not match pinned transcript")
    rows, _ = read_transcript(path)
    if not rows:
        raise ContractError("empty Antigravity transcript")
    events, previous = [], -1
    for _, record in rows:
        if not isinstance(record, dict) or type(record.get("step_index")) is not int or record["step_index"] <= previous:
            raise ContractError("Antigravity step indices must be strictly increasing integers")
        if manifest["cli_version"] == "1.4.3" and record["step_index"] != previous + 1:
            raise ContractError("Antigravity 1.4.3 steps must be contiguous from zero; missing steps may conceal generation")
        previous = record["step_index"]
        allowed = ("USER_INPUT", "PLANNER_RESPONSE", "GENERIC") + (("SYSTEM_MESSAGE",) if manifest["cli_version"] == "1.4.0" else ())
        if record.get("type") not in allowed or record.get("status") not in ("DONE", "ERROR"):
            raise ContractError("unsupported Antigravity record type/status")
        if record["type"] == "SYSTEM_MESSAGE" and (record.get("source") != "SYSTEM" or record.get("tool_calls") or record["status"] != "DONE"):
            raise ContractError("invalid Antigravity system message")
    for offset, (_, record) in enumerate(rows):
        if record["type"] == "PLANNER_RESPONSE":
            events.extend(_antigravity_tool_events(record, rows[offset + 1:offset + 3], manifest["cli_version"]))
            if manifest["cli_version"] == "1.4.0":
                events.extend(_antigravity_batch_reads(record, rows[offset + 1:offset + 1 + len(record.get("tool_calls", []))]))
    return events


def _antigravity_tool_events(record, following, version):
    calls = record.get("tool_calls", [])
    if not isinstance(calls, list) or any(not isinstance(call, dict) or not isinstance(call.get("args"), dict) or not isinstance(call.get("name"), str) for call in calls):
        raise ContractError("invalid Antigravity tool calls")
    events = []
    read_name, path_key = ("read_file", "Path") if version == "1.4.3" else ("view_file", "AbsolutePath")
    for call in calls:
        seq, args, name = record["step_index"], call["args"], call["name"]
        if name == read_name:
            if not isinstance(args.get(path_key), str) or not os.path.isabs(args[path_key]):
                raise ContractError("invalid Antigravity read path")
            if version == "1.4.3":
                if args.keys() - {"Path", "StartLine", "EndLine"}:
                    raise ContractError("unsupported Antigravity read arguments")
                for key in ("StartLine", "EndLine"):
                    if key in args and (type(args[key]) is not int or args[key] < 1):
                        raise ContractError("invalid Antigravity read range")
                if args.get("EndLine", args.get("StartLine", 1)) < args.get("StartLine", 1):
                    raise ContractError("invalid Antigravity read range")
        # Preserve actual commands for maintain.audit; opaque tools remain conservative write barriers.
        if name == "run_command":
            command = args.get("CommandLine")
            if not isinstance(command, str):
                raise ContractError("invalid Antigravity command")
            events.append({"kind": "use", "seq": seq, "id": str(seq), "name": "Bash", "input": {"command": command}})
        elif name in ("write_to_file", "replace_file_content", "multi_replace_file_content"):
            if not isinstance(args.get("TargetFile"), str):
                raise ContractError("invalid Antigravity write path")
            events.append({"kind": "use", "seq": seq, "id": str(seq), "name": "Write", "input": {"file_path": args["TargetFile"]}})
        elif name == "write_file" and version == "1.4.3":
            if not isinstance(args.get("Path"), str):
                raise ContractError("invalid Antigravity write path")
            events.append({"kind": "use", "seq": seq, "id": str(seq), "name": "Write", "input": {"file_path": args["Path"]}})
        elif name != read_name:
            events.append({"kind": "use", "seq": seq, "id": str(seq), "name": "CodexExec",
                           "input": {"script": json.dumps(args), "cmds": [], "cwd": ""}})
        if name != read_name or len(calls) != 1 or record.get("truncated_fields") or record["status"] != "DONE":
            continue
        events.extend(_antigravity_read_events(record, following, version))
    return events


def _antigravity_batch_reads(record, following):
    """Only the observed pure-read batch: one contiguous, path-bound result per distinct file."""
    calls = record.get("tool_calls", [])
    if len(calls) < 2 or len(following) != len(calls) or record["status"] != "DONE" or record.get("truncated_fields"):
        return []
    if any(call["name"] != "view_file" for call in calls):
        return []
    paths = [call["args"]["AbsolutePath"] for call in calls]
    if len(set(paths)) != len(paths):
        return []
    events = []
    for index, (path, (_, response)) in enumerate(zip(paths, following)):
        text = response.get("content")
        if (response["type"] != "GENERIC" or response["step_index"] != record["step_index"] + index + 1
                or response["status"] != "DONE" or response.get("error") or response.get("truncated_fields") or not isinstance(text, str)):
            return []
        numbered = _antigravity_numbered_output(text, path)
        if not numbered:
            return []
        identity = f"read-{record['step_index']}-{index}"
        events.extend([{"kind": "use", "seq": record["step_index"], "id": identity, "name": "Read", "input": {"file_path": path}},
                       {"kind": "result", "seq": response["step_index"], "id": identity, "error": False, "text": numbered}])
    return events


def _antigravity_read_events(record, following, version):
    if not following:
        return []
    response = following[0][1]
    seq, args = record["step_index"], record["tool_calls"][0]["args"]
    text, path = response.get("content"), args.get("Path" if version == "1.4.3" else "AbsolutePath")
    if (response["type"] != "GENERIC" or response["step_index"] != seq + 1 or response["status"] != "DONE"
            or response.get("error") or response.get("truncated_fields") or not isinstance(text, str)
            or not isinstance(path, str) or not os.path.isabs(path)):
        return []
    if version == "1.4.3":
        if len(following) > 1 and following[1][1]["type"] == "GENERIC":
            return []
        window = re.search(r"^Showing lines (\d+)-(\d+) of (\d+)$", text, re.M)
        if not window or int(window[1]) != args.get("StartLine", 1) or int(window[2]) > args.get("EndLine", int(window[3])):
            return []
    numbered = _antigravity_numbered_output(text, path, version)
    return [{"kind": "use", "seq": seq, "id": "read-" + str(seq), "name": "Read", "input": {"file_path": path}},
            {"kind": "result", "seq": response["step_index"], "id": "read-" + str(seq), "error": False, "text": numbered}]


def _antigravity_numbered_output(text, path, version="1.4.0"):
    # 1.4.3 lacks a response path: only a single call with its immediately adjacent result is credited (#34).
    if version == "1.4.3":
        match = re.search(r"^Showing lines (\d+)-(\d+) of (\d+)$", text, re.M)
        if not match or int(match[2]) > int(match[3]):
            return ""
    else:
        match = re.search(r"^Showing lines (\d+) to (\d+)$", text, re.M)
        if not match or f"File Path: `file://{path}`" not in text[:match.start()].splitlines():
            return ""
    start, end = map(int, match.groups()[:2])
    if start < 1 or end < start:
        return ""
    lines = text[match.end():].splitlines()
    numbered = []
    for line in lines:
        if line.startswith(f"{start}: "):
            break
    else:
        return ""
    first = lines.index(line)
    for number, line in enumerate(lines[first:first + end - start + 1], start):
        prefix = f"{number}: "
        if not line.startswith(prefix):
            break
        numbered.append(f"{number}\t{line[len(prefix):]}")
    return "\n".join(numbered)


def antigravity_transcript_texts(path):
    """Audit hook: first user input and last non-tool planner text; never return thinking or tool output."""
    parse_antigravity_transcript(path)
    rows, _ = read_transcript(path)
    prompt, answer = "", ""
    for _, record in rows:
        text = record.get("content")
        if not isinstance(text, str) or record["status"] != "DONE" or record.get("truncated_fields"):
            continue
        if record["type"] == "USER_INPUT" and not prompt:
            prompt = text
        elif record["type"] == "PLANNER_RESPONSE" and not record.get("tool_calls"):
            answer = text
    return prompt, answer


def parse_codex_rollout(path):
    """codex-rollout-jsonl-v1 (session_meta.cli_version 0.155.1, 0.162.* or 0.163.0): one "CodexExec" use per `exec` custom_tool_call (a JS script), and its output.
    Any other cli_version is refused, not guessed at."""
    rows, _ = read_transcript(path)
    meta = rows[0][1] if rows else None
    info = meta.get("payload") if isinstance(meta, dict) and meta.get("type") == "session_meta" else None
    if not isinstance(info, dict) or not isinstance(info.get("cli_version"), str) or not CODEX_CLI_VERSION.fullmatch(info["cli_version"]):
        raise ContractError(f"not a {LOG_ADAPTERS['codex']} log: the first record must be a session_meta with cli_version 0.155.1, 0.163.0 or numeric 0.162.patch[-alpha.N[.N...]]")
    events = []
    for seq, d in rows[1:]:
        q = d.get("payload") if isinstance(d, dict) else None
        if not isinstance(q, dict):
            continue
        if q.get("type") == "custom_tool_call" and q.get("name") == "exec":
            _log_string(q, "call_id", seq)
            _log_string(q, "input", seq)
            cmds = []
            for m in _JS_CMD.findall(q["input"]):
                try:
                    cmds.append(json.loads(m))
                except ValueError:
                    cmds.append(None)
            # `script` is kept whole so that a generation write is found even in a command that could not be decoded
            events.append({"kind": "use", "seq": seq, "id": q.get("call_id"), "name": "CodexExec",
                           "input": {"script": q["input"], "cmds": cmds, "cwd": info.get("cwd") if isinstance(info.get("cwd"), str) else ""}})
        elif q.get("type") == "custom_tool_call_output":
            _log_string(q, "call_id", seq)
            if not isinstance(q.get("output"), list):
                raise ContractError(f"host log line {seq + 1}: output must be a list")
            items = [_log_string(x, "text", seq) for x in q["output"]]
            ok = bool(items) and items[0].startswith("Script completed")
            body, err = "\n".join(items[1:]), not ok
            try:
                obj = json.loads(body)
            except ValueError:
                obj = None
            if isinstance(obj, dict):  # text(await tools.exec_command(..)): the result object; any other shape shows nothing
                body, err = _log_string(obj, "output", seq), err or obj.get("exit_code") not in (0, None)
            events.append({"kind": "result", "seq": seq, "id": q.get("call_id"), "error": err, "text": body})
    return events


def _codex_reads(inp, target):
    """True when the script is one plain read command (cat/nl/head/tail/sed -n/bat/rtk read) naming target: only then is its output that file's."""
    if len(inp["cmds"]) != 1 or not isinstance(inp["cmds"][0], str) or inp["script"].count("exec_command") != 1 or re.search(r"[;&|<>`$]", inp["cmds"][0]):
        return False
    try:
        toks = shlex.split(inp["cmds"][0])
    except ValueError:
        return False
    # RTK proxy preserves the command output; unwrap exactly one explicit prefix.
    if toks[:2] == ["rtk", "proxy"]:
        toks = toks[2:]
    # A concatenation cannot attribute matching output to one of several files.
    if toks[:1] == ["cat"] and len([t for t in toks[1:] if not t.startswith("-") or t == "-"]) != 1:
        return False
    if toks[:2] == ["rtk", "read"] or toks[:1] in (["cat"], ["nl"], ["head"], ["tail"], ["bat"]) or (toks[:2] == ["sed", "-n"]):
        return any(not t.startswith("-") and (os.path.isabs(t) or (inp["cwd"] and "workdir" not in inp["script"])) and _resolves_to(t if os.path.isabs(t) else os.path.join(inp["cwd"], t), [target]) for t in toks[1:])
    return False


def _aligned_lines(text, body_lines):
    """Body line numbers shown verbatim as a run of lines in text. A line next to a truncation mark is cut, so it is not counted;
    a run that fits two places in the body is ambiguous, so it is not counted either."""
    seen, segs = set(), _TRUNCATED.split(text)
    for si, seg in enumerate(segs):
        lines = seg.split("\n")
        lines = lines[1:] if si else lines
        lines = lines[:-1] if si < len(segs) - 1 or (lines and lines[-1] == "") else lines
        i = 0
        while i < len(lines):
            runs = []
            for k in range(len(body_lines)):
                n = 0
                while i + n < len(lines) and k + n < len(body_lines) and lines[i + n] == body_lines[k + n]:
                    n += 1
                if n:
                    runs.append((n, k))
            top = max(runs)[0] if runs else 0
            best = [k for n, k in runs if n == top]
            if len(best) == 1:
                seen.update(range(best[0] + 1, best[0] + top + 1))
            i += max(top, 1)
    return seen


_NUMBERED = re.compile(r"^\s*(\d+)[\t→](.*)$")


def observed_read_lines(events, body_path, gen_paths):
    """-> (set of body line numbers shown by successful Reads before the first generation write, gen_seq or None)."""
    target = Path(body_path).resolve()
    gens = [ev["seq"] for ev in events if ev["kind"] == "use" and (
        (ev["name"] in ("Write", "Edit") and _resolves_to(ev["input"].get("file_path"), gen_paths)) or
        (ev["name"] == "Bash" and any(g.name in str(ev["input"].get("command", "")) for g in gen_paths)) or
        (ev["name"] == "CodexExec" and any(g.name in ev["input"]["script"] for g in gen_paths)))]
    gen_seq = min(gens) if gens else None
    body_lines = Path(body_path).read_text(encoding="utf-8").split("\n")[:-1]
    results = {ev["id"]: ev for ev in events if ev["kind"] == "result"}
    seen = set()
    for ev in events:
        if ev["kind"] != "use" or not (
                (ev["name"] == "Read" and _resolves_to(ev["input"].get("file_path"), [target])) or
                (ev["name"] == "CodexExec" and _codex_reads(ev["input"], target))):
            continue
        res = results.get(ev["id"])
        if res is None or res["error"] or res["seq"] < ev["seq"] or (gen_seq is not None and res["seq"] > gen_seq):
            continue
        if ev["name"] == "CodexExec":
            seen |= _aligned_lines(res["text"], body_lines)
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
    for target in targets:
        target = Path(target).resolve()
        if rp == target:
            return True
        try:
            if rp.samefile(target):
                return True
        except OSError:
            continue
    return False


def _check_reads(R, run, rec, acq, bodies, sources_out, host_log, ctx, host):
    ctx["host_log_sha256"], ctx["host"] = None, host
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
        events = parse_host_log(host, hl)
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
                           f"Host-log adapter {LOG_ADAPTERS.get(ctx.get('host'), '(none)')} trusts the path the operator supplied via --host-log."],
           "change_counts": ctx.get("counts"), "host_log_sha256": ctx.get("host_log_sha256")}
    write_json(run / "verification.json", rep)
    return rep


# ---------------------------------------------------------------- sources
def sources_plan(reg, reg_sha, inp, all_models=False):
    """Which sources an input is read against, and why each other source of the target is left out. Needs no run and no network."""
    exp = explain_sources(reg, inp)
    state = {x["source_id"]: x for x in exp["selected"] + exp["skipped"]}
    limited = [s for s in reg["sources"] if s["models"] != ["all"] and inp["target_id"] in s["target_ids"]]
    guides = []
    for s in limited:
        x = state.get(s["source_id"])
        if x is None:
            g = {"status": "not_routed"}
        elif "reason" in x:
            g = {"status": "skipped", **{k: v for k, v in x.items() if k not in ("source_id", "models")}}
        else:
            g = {"status": "selected"}
        guides.append({"source_id": s["source_id"], "models": s["models"], **g})
    out = {"schema_version": 1, "mode": "plan", "registry_sha256": reg_sha,
           "input": {k: inp[k] for k in ("target_id", "target_surface", "model_id", "purpose")},
           "selected": exp["selected"], "skipped": exp["skipped"],
           "model": {"model_id": inp["model_id"], "model_specific_sources": len(limited), "guides": guides,
                     "spelling_hint": model_hint(reg, inp["model_id"]) if limited else []}}
    if all_models:
        base = select_sources(reg, {**inp, "model_id": "unknown"})
        out["models"] = {"base": base, "added": {m: [i for i in select_sources(reg, {**inp, "model_id": m}) if i not in base]
                                                  for m in sorted({m for s in limited for m in s["models"]})}}
    return out


def _body_integrity(run, e):
    if e["status"] not in ("live", "cache"):
        return "unavailable"
    try:
        data = safe_join(run, e["body_path"]).read_bytes()
    except (ContractError, FileNotFoundError, TypeError):
        return "missing"
    return "ok" if sha_bytes(data) == e["body_sha256"] else "mismatch"


def _ranges(nums):
    out = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def _source_reads(run, host, host_log, rows, rec, problem):
    """Fill row["read"] from the supplied host log, with the adapter and the rule verify uses.
    -> SHA-256 of the log, or None when no log was read."""
    for row in rows:
        row["read"] = {"status": "unavailable", "reason": None}
    if host not in LOG_ADAPTER_HOSTS:  # a log from another product is never parsed or guessed at
        for row in rows:
            row["read"]["reason"] = "no_log_adapter_for_host"
        return None
    if host_log is None:
        for row in rows:
            row["read"]["reason"] = "no_host_log"
        return None
    hl = Path(host_log)
    if not hl.is_file():
        raise Unreadable(f"host log not found: {hl}")
    log_sha = sha_file(hl)
    if hl.resolve().is_relative_to(run.resolve()):
        problem("fail", "host_log_inside_run", "host log inside the run directory is self-authored, not observed")
        for row in rows:
            row["read"] = {"status": "self_reported", "reason": "host_log_inside_run"}
        return log_sha
    try:
        events = parse_host_log(host, hl)
    except ContractError as e:
        problem("fail", "host_log_invalid", str(e))
        for row in rows:
            row["read"]["reason"] = "host_log_invalid"
        return log_sha
    gen_paths = [run / "optimized.md", run / "recommendations.json"]
    for row in rows:
        sid = row["source_id"]
        if row["integrity"] != "ok":
            row["read"]["reason"] = "body_not_verified"
            continue
        body = run / "snapshots" / sid / "body.txt"
        seen, gen_seq = observed_read_lines(events, body, gen_paths)
        need = set()
        for c in (rec["changes"] if rec else []):
            if c["source_id"] == sid and c["source_start_line"] is not None:
                need |= set(range(c["source_start_line"], c["source_end_line"] + 1))
        covered = (not (need - seen)) if need else None
        row["read"] = {"status": "unavailable", "reason": "generation_write_not_found", "lines_seen": len(seen),
                       "lines_total": len(body.read_text(encoding="utf-8").split("\n")) - 1, "ranges": _ranges(seen),
                       "cited_lines_covered": covered}
        if gen_seq is None:
            problem("incomplete", "generation_write_not_found", "no write of optimized.md/recommendations.json in the log", source_id=sid)
            continue
        ok = covered if need else bool(seen)
        row["read"].update(status="observed" if ok else "not_observed", reason=None)
        if not ok:
            problem("incomplete", "read_not_observed",
                    "no successful pre-generation Read showing the cited lines" if need else "no successful pre-generation Read of this body",
                    source_id=sid)
    return log_sha


def sources_report(run, host_log=None):
    """What a run was read against: the sources chosen for its model, purpose and surface, what was fetched and whether the
    bodies still match, which rules the changes cite and who reviewed them, and (with a log) which lines were actually read.
    Everything is recomputed from the run's files and the registry; nothing the agent wrote is taken on trust."""
    run = Path(run)
    if not run.is_dir():
        raise Unreadable(f"run directory not found: {run}")
    reg, reg_sha = load_registry()
    inp_bytes = _read_run(run, "input.json").read_bytes()
    inp = load_json(run / "input.json")
    validate_input(inp)
    acq = load_json(_read_run(run, "acquisition.json"))
    validate_acquisition(acq)
    rec = None
    if (run / "recommendations.json").is_file():
        rec = load_json(run / "recommendations.json")
        validate_recommendations(rec)
    problems = []

    def problem(severity, code, message, **ids):
        problems.append({"severity": severity, "code": code, "message": message, **ids})

    if sha_bytes(inp_bytes) != acq["input_sha256"]:
        problem("fail", "input_hash_mismatch", "input.json differs from the input this run was fetched with")
    plan, same = sources_plan(reg, reg_sha, inp), acq["registry_sha256"] == reg_sha
    expected = [x["source_id"] for x in plan["selected"]]
    if not same:
        problem("incomplete", "registry_changed", "registry.json changed since this run was fetched: the routing shown is today's, and rules cannot be matched")
    elif expected != acq["selected_sources"]:
        problem("fail", "selected_sources_mismatch", f"registry routing expects {expected}, acquisition has {acq['selected_sources']}")
    via = {x["source_id"]: x for x in plan["selected"]}
    srcmap, entries = {s["source_id"]: s for s in reg["sources"]}, {e["source_id"]: e for e in acq["sources"]}
    applicable = _applicable(reg, inp, acq) if same else None
    changes = [c for c in (rec["changes"] if rec else []) if c["rule_id"] is not None]
    rows = []
    for sid in acq["selected_sources"]:
        e, src, route = entries[sid], srcmap.get(sid), via.get(sid, {})
        if src is None:
            problem("fail", "unknown_source", "source is not in the registry", source_id=sid)
        row = {"source_id": sid, "models": src["models"] if src else None, "route": route.get("route"),
               "purposes": route.get("purposes"), "surfaces": route.get("surfaces"),
               "url": e["final_url"] or e["requested_url"], "status": e["status"], "http_status": e["http_status"],
               "fetched_at": e["fetched_at"], "error": e["error"], "body_sha256": e["body_sha256"],
               "integrity": _body_integrity(run, e),
               "rules": {"applicable": None if applicable is None else sum(1 for r in applicable if r["source_id"] == sid),
                         "cited": sorted({c["rule_id"] for c in changes if c["source_id"] == sid})}}
        rows.append(row)
        if e["status"] not in ("live", "cache"):
            problem("incomplete", f"source_{e['status']}", e["error"] or e["status"], source_id=sid)
        elif row["integrity"] != "ok":
            problem("fail", f"body_{row['integrity']}", "body.txt is missing" if row["integrity"] == "missing"
                    else "body.txt differs from the recorded SHA-256", source_id=sid)
    rules_by_id, in_scope = {r["rule_id"]: r for r in reg["rules"]}, None if applicable is None else {r["rule_id"] for r in applicable}
    citations = []
    for c in changes:
        r = rules_by_id.get(c["rule_id"])
        item = {"change_id": c["change_id"], "kind": c["kind"], "rule_id": c["rule_id"], "source_id": c["source_id"],
                "lines": [c["source_start_line"], c["source_end_line"]], "reviewer_id": r["reviewer_id"] if r else None,
                "human_reviewed": bool(r and human_reviewed(r)), "in_scope": None if in_scope is None else c["rule_id"] in in_scope}
        citations.append(item)
        ids = {"change_id": c["change_id"], "source_id": c["source_id"]}
        if c["source_id"] not in acq["selected_sources"]:
            problem("fail", "cited_source_not_selected", "the cited source is not one this run fetched", **ids)
        if r is None:
            problem("fail", "unknown_rule", f"rule {c['rule_id']} is not in the registry", **ids)
        elif item["in_scope"] is False:
            problem("fail", "cited_rule_out_of_scope", f"rule {c['rule_id']} is unreviewed or does not apply to this target, purpose, surface and model", **ids)
    log_sha = _source_reads(run, inp["execution_host"], host_log, rows, rec, problem)
    levels = {p["severity"] for p in problems}
    return {"schema_version": 1, "mode": "run", "run_id": acq["run_id"],
            "verdict": "INCONSISTENT" if "fail" in levels else ("INCOMPLETE" if "incomplete" in levels else "CONSISTENT"),
            "input": {k: inp[k] for k in ("execution_host", "target_id", "target_surface", "model_id", "purpose")},
            "registry": {"sha256": reg_sha, "run_sha256": acq["registry_sha256"], "matches": same},
            "fetch": {"mode": acq["mode"], "started_at": acq["started_at"]},
            "model": plan["model"], "selected": rows, "skipped": plan["skipped"], "citations": citations,
            "review": _review_summary(rules_by_id[i] for i in sorted({c["rule_id"] for c in changes}) if i in rules_by_id),
            "problems": problems, "host_log_sha256": log_sha}


def do_sources(run=None, input_path=None, target=None, surface="unknown", model="unknown", purpose="one-shot",
               all_models=False, host_log=None):
    if run is not None:
        if all_models or input_path or target:
            raise ContractError("--run cannot be combined with --input, --target or --all-models")
        return sources_report(run, host_log)
    if host_log:
        raise ContractError("--host-log needs --run")
    reg, reg_sha = load_registry()
    if input_path:
        if target:
            raise ContractError("give --input or --target, not both")
        inp = load_json(Path(input_path))
        validate_input(inp)
    elif target:
        inp = {"target_id": target, "target_surface": surface, "model_id": model, "purpose": purpose}
        if target not in TARGET_IDS + ("unresolved",) or purpose not in PURPOSES or not surface or not model:
            raise ContractError(f"--target must be one of {TARGET_IDS}, --purpose one of {PURPOSES}, and --surface/--model not empty")
    else:
        raise ContractError("give --run, --input or --target")
    return sources_plan(reg, reg_sha, inp, all_models)


def _ids(xs):
    return "、".join(f"`{x}`" for x in xs) or "なし"


def _cond(xs):
    return "・".join("すべて" if x == "all" else f"`{x}`" for x in xs)


def _why_left_out(x, inp):
    if x["reason"] == "model_mismatch":
        return f"モデルが違います(入力: `{inp['model_id']}`)"
    if x["reason"] == "model_unknown":
        return "`model_id` が `unknown` です(指定すると選ばれます)"
    need, given = [], []
    if not x["purpose_ok"]:
        need.append("purpose が " + _cond(x["purposes"]))
        given.append(f"purpose=`{inp['purpose']}`")
    if not x["surface_ok"]:
        need.append("target_surface が " + _cond(x["surfaces"]))
        given.append(f"target_surface=`{inp['target_surface']}`")
    return " かつ ".join(need) + " のときだけ選ばれます(入力: " + "、".join(given) + ")"


def _model_lines(rep):
    m, inp = rep["model"], rep["input"]
    if m["model_specific_sources"] == 0:
        return [f"- この利用先(`{inp['target_id']}`)には、モデル別の資料がありません。`model_id` を変えても、選ばれる資料は変わりません。"]
    chosen = [g["source_id"] for g in m["guides"] if g["status"] == "selected"]
    out = [f"- この利用先には、モデル別の資料が {m['model_specific_sources']} 件あります。`model_id`=`{inp['model_id']}` で、"
           + (f"選ばれたのは {_ids(chosen)} です。" if chosen else "選ばれたものはありません。")]
    if m["spelling_hint"]:
        out.append(f"- `model_id` の綴りが違うかもしれません。登録されている ID: {_ids(m['spelling_hint'])}。選択には完全に一致する ID が要ります(この表示は選択に使っていません)。")
    rest = [g for g in m["guides"] if g["status"] != "selected"]
    if rest:
        out += ["", "| 選ばれなかったモデル別の資料 | 対象モデル | 理由 |", "|---|---|---|"]
        out += [f"| `{g['source_id']}` | {_ids(g['models'])} | "
                + ("この利用先の経路に入っていません" if g["status"] == "not_routed" else _why_left_out(g, inp)) + " |" for g in rest]
    return out


def _read_text(r):
    if r["status"] == "observed":
        return f"観測(読んだ行 {r['ranges']} / 全 {r['lines_total']} 行)"
    if r["status"] == "not_observed":
        return f"観測できず(読んだ行: {r['ranges'] or 'なし'})"
    if r["status"] == "self_reported":
        return "無効(ログが run の中にある)"
    return {"no_host_log": "未観測(ホストのログなし)", "no_log_adapter_for_host": "未観測(このホストは対象外)",
            "host_log_invalid": "未観測(ログを読めない)", "generation_write_not_found": "未観測(生成の書込みがログにない)",
            "body_not_verified": "未観測(本文を確認できない)"}.get(r["reason"], "未観測")


SOURCES_FORMAT = 4  # raise it whenever render_sources changes its wording or layout: a pasted report is compared only with the same format
FORMAT_MARK = re.compile(r"<!-- sources-report format (\d+) -->")


def _no_source_line(inp, plan):
    """Why no source is chosen: the reader of an empty list should not take it for a failure."""
    if inp["target_id"] == "unresolved":
        return "利用先が `unresolved` のため、資料を選んでいません。"
    return (f"この入力では、使える公式規則がなく、{'取得される' if plan else '取得する'}資料がありません(故障ではありません)。"
            "`purpose`・`target_surface`・`model_id` が変わると、資料が選ばれることがあります。"
            + ("" if plan else "`verify` は、この理由で `INCOMPLETE`(`no_applicable_official_source`)にします。"))


def render_sources(rep):
    """The report as Markdown, from the report alone: a script writes it, not a model. The first line is an invisible mark of the format."""
    inp, plan = rep["input"], rep["mode"] == "plan"
    order = ("target_id", "target_surface", "model_id", "purpose") + (() if plan else ("execution_host",))
    out = [f"<!-- sources-report format {SOURCES_FORMAT} -->", "# 見る資料の計画" if plan else f"# 見た資料: {rep['run_id']}", "",
           "- 入力: " + " / ".join(f"{k}=`{inp[k]}`" for k in order)]
    if plan:
        out.append(f"- registry: SHA-256 `{rep['registry_sha256'][:12]}…`。通信せず、registry.json だけから求めました。")
    else:
        r = rep["registry"]
        out += [f"- registry: SHA-256 `{r['sha256'][:12]}…`(" + ("取得時と一致" if r["matches"] else "**取得のあとで変わっています**") + ")",
                f"- 取得: {rep['fetch']['mode']}(開始 {rep['fetch']['started_at']})", f"- 判定: **{rep['verdict']}**"]
    out += ["", "## モデルによる選択", ""] + _model_lines(rep) + ["", f"## {'選ばれる' if plan else '選んだ'}資料({len(rep['selected'])} 件)", ""]
    why = lambda x: "常に取得" if x["route"] == "required" else (f"条件付き(purpose: {_cond(x['purposes'])}、target_surface: {_cond(x['surfaces'])})" if x["route"] else "—")  # noqa: E731
    if not rep["selected"]:  # an empty table would look like a failure
        out.append(_no_source_line(inp, plan))
    elif plan:
        out += ["| 資料 | 選ばれる理由 | 対象モデル |", "|---|---|---|"]
        out += [f"| `{x['source_id']}` | {why(x)} | {_cond(x['models'])} |" for x in rep["selected"]]
    else:
        out += ["| 資料 | 選んだ理由 | 取得 | 本文 SHA-256 | 規則(適用 / 引用) | 読取り |", "|---|---|---|---|---|---|"]
        mark = {"ok": "一致", "mismatch": "**不一致**", "missing": "**ファイルなし**", "unavailable": "—"}
        for x in rep["selected"]:
            sha = f"`{x['body_sha256'][:12]}…` {mark[x['integrity']]}" if x["body_sha256"] else mark[x["integrity"]]
            n = "?" if x["rules"]["applicable"] is None else x["rules"]["applicable"]
            out.append(f"| [`{x['source_id']}`]({x['url']}) | {why(x)} | {x['status']} {x['fetched_at'] or ''} | {sha} | {n} / {len(x['rules']['cited'])} | {_read_text(x['read'])} |")
    rest = [x for x in rep["skipped"] if x["reason"] == "route_not_matched" and x["models"] == ["all"]]  # the model guides were listed above
    if rest:
        out += ["", f"## 選ばなかった資料(モデル別の資料を除く。経路に合わないもの、{len(rest)} 件)", ""]
        out += [f"- `{x['source_id']}`: {_why_left_out(x, inp)}" for x in rest]
    if plan and "models" in rep:
        out += ["", "## モデルごとの違い", "", f"`model_id` が `unknown` のときの資料: {_ids(rep['models']['base'])}", ""]
        if rep["models"]["added"]:
            out += ["| `model_id` | 追加で選ばれる資料 |", "|---|---|"]
            out += [f"| `{m}` | {_ids(a) if a else 'なし(この `target_surface` ではモデル別の資料が選ばれません)'} |" for m, a in rep["models"]["added"].items()]
    if not plan:
        out += ["", "## 引用した規則と審査者", ""]
        if rep["citations"]:
            out += ["| 変更 | 規則 | 資料 | 行 | 審査者 | 人の審査 | 範囲内 |", "|---|---|---|---|---|---|---|"]
            out += [f"| {c['change_id']} | `{c['rule_id']}` | `{c['source_id']}` | {c['lines'][0]}-{c['lines'][1]} | {c['reviewer_id']} | "
                    f"{'あり' if c['human_reviewed'] else 'なし'} | {'?' if c['in_scope'] is None else ('はい' if c['in_scope'] else '**いいえ**')} |" for c in rep["citations"]]
            rv = rep["review"]
            out += ["", f"引用した規則 {rv['rules']} 件のうち、人が審査したものは {rv['human_reviewed']} 件です。"]
        else:
            out.append("規則を引用した変更はありません。")
        out += ["", "## 確認結果", ""]
        out += [f"- {p['severity']} `{p['code']}`: {p['message']}" + (f"(`{p['source_id']}`)" if p.get("source_id") else "") for p in rep["problems"]] \
            or ["- 資料の選択は registry の規則から再現でき、本文のハッシュも取得時の記録と一致しました。"]
        if any(x["read"]["reason"] == "no_host_log" for x in rep["selected"]):
            out.append(f"- 読取りは観測していません。`{inp['execution_host']}` に対応する版・形式のログを、操作者が `--host-log` に指定してください。対応外の版・形式では観測できません。")
        elif any(x["read"]["reason"] == "no_log_adapter_for_host" for x in rep["selected"]):
            out.append(f"- `{inp['execution_host']}` は読取り観測に未対応です。ログを渡しても、読取りは観測できません。")
    out += ["", "これは、registry の規則から求めた計画です。実際に取得して読んだかは、run の報告(`--run`)で確かめます。" if plan else
            "これは、取得した資料と記録の整合を見る報告です。規則の解釈が正しいことや、資料の内容を理解したことは確かめません。"]
    return "\n".join(out) + "\n"


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


def validate_plan(plan):
    """Validate a plan without creating evaluation outputs or loading cases."""
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
        validate_plan(plan)
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
        if s["method"] not in ("code", "human") and (not s["calibration_sha256"] or not s["rubric_version"]):
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
    d = sub.add_parser("drift")
    d.add_argument("--run")
    d.add_argument("--source", action="append", default=[])
    r = sub.add_parser("rules")
    r.add_argument("--run", required=True)
    s = sub.add_parser("sources")
    s.add_argument("--run")
    s.add_argument("--host-log")
    s.add_argument("--input")
    s.add_argument("--target")
    s.add_argument("--surface", default="unknown")
    s.add_argument("--model", default="unknown")
    s.add_argument("--purpose", default="one-shot")
    s.add_argument("--all-models", action="store_true")
    s.add_argument("--markdown", action="store_true")
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
        if args.cmd == "drift":
            rep = drift(args.run, args.source)
            stale = [r["rule_id"] for r in rep["rules"] if r["state"] in ("changed", "excerpt_missing")]
            return _emit("drift", 0 if rep["verdict"] == "CURRENT" else 2, rep, f"{rep['verdict']} {stale}")
        if args.cmd == "rules":
            out = applicable_rules(args.run)
            return _emit("rules", 0, out, f"{len(out['rules'])} rules, {out['review']['human_reviewed']} reviewed by a person")
        if args.cmd == "sources":
            rep = do_sources(args.run, args.input, args.target, args.surface, args.model, args.purpose, args.all_models, args.host_log)
            code = {"CONSISTENT": 0, "INCONSISTENT": 1}.get(rep.get("verdict"), 0 if rep["mode"] == "plan" else 2)
            note = (f"{len(rep['selected'])} selected, {len(rep['skipped'])} left out" if rep["mode"] == "plan"
                    else f"{rep['verdict']} {sorted({p['code'] for p in rep['problems']})}")
            if not args.markdown:
                return _emit("sources", code, rep, note)
            print(render_sources(rep), end="")
            print(f"sources: {note} (exit {code})", file=sys.stderr)
            return code
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
