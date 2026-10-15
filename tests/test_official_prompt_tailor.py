"""Unit tests for official-prompt-tailor (stdlib unittest, network mocked, fixtures synthetic).

A pass here shows the checking code behaves; it does not show that a live official fetch,
an independent read observation, a semantic review or a performance gain happened.
Run: python3 -m unittest discover -s tests -p 'test_official_prompt_tailor.py'
"""
import contextlib
import gzip
import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "official-prompt-tailor"
sys.path.insert(0, str(SKILL / "scripts"))
import evidence as ev  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "official_prompt_tailor"
REAL_CHECK_HOST = ev.check_host_public
REAL_HTTP_GET = ev.http_get
A_URL, MD_URL, API_URL = "https://docs.example.com/guide", "https://docs.example.com/guide.md", "https://api.example.com/prompting"
HTML = {"content-type": "text/html; charset=utf-8"}
ORIGINAL = "バグを直して。公開APIは変更しない。テストはpytestで。\n"
OPTIMIZED = "バグを修正してください。\n制約: 公開APIは変更しない。\n確認: pytestを実行する。\n"
DEFAULT_CHANGES = [
    ("c1", "editorial", None, "バグを直して。", "バグを修正してください。\n", None, None),
    ("c2", "editorial", None, "公開APIは変更しない。", "制約: 公開APIは変更しない。\n", None, None),
    ("c3", "official", "rule-verify", "テストはpytestで。", "確認: pytestを実行する。\n", (2, 2), "Give a check it can run"),
]
NO_SOURCE_OPT = "バグを修正してください。\n制約: 公開APIは変更しない。テストはpytestで。\n"
NO_SOURCE_CHANGES = [DEFAULT_CHANGES[0], ("c2", "editorial", None, "公開APIは変更しない。", "制約: 公開APIは変更しない。", None, None)]
sha = lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def codes(rep):
    return {e["code"] for e in rep["errors"]}


def symlink(link, target):
    try:
        Path(link).symlink_to(target)
    except (OSError, NotImplementedError):
        raise unittest.SkipTest("symlinks are not available here")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        self.clock, self.calls = Clock(), []
        self.page, self.md = (FIX / "guide.html").read_bytes(), (FIX / "guide.md").read_bytes()
        self.responses = {A_URL: (200, HTML, self.page), API_URL: (200, HTML, self.page),
                          MD_URL: (200, {"content-type": "text/markdown"}, self.md)}
        for p in (mock.patch.object(ev, "utcnow", self.clock), mock.patch.object(ev, "http_get", self.fake_get),
                  mock.patch.object(ev, "check_host_public", lambda h: "mock"),
                  mock.patch.object(ev, "REGISTRY_PATH", self.tmp / "registry.json"),
                  mock.patch.object(ev, "RUBRIC_PATH", SKILL / "references" / "review-rubric.md")):
            p.start()
            self.addCleanup(p.stop)
        self.set_registry()

    # ---- fakes / builders
    def fake_get(self, url):
        self.calls.append(url)
        r = self.responses.get(url)
        if r is None:
            raise urllib.error.URLError("no route")
        return r() if callable(r) else r

    def set_registry(self, mutate=None):
        def src(sid, url, targets, surfaces, ex, loc):
            return {"source_id": sid, "allowed_urls": [url], "target_ids": targets, "surfaces": surfaces,
                    "models": ["all"], "category": "product-prompting", "extraction": ex, "body_locators": loc}
        a = src("doc-a", A_URL, ["claude-code", "codex"], ["all"], "html-main-v1", ["Best practices", "Provide context"])
        md = src("doc-md", MD_URL, ["claude-code"], ["all"], "markdown-v1", ["# Notes", "## Details"])
        api = src("doc-api", API_URL, ["claude-code"], ["api"], "html-main-v1", ["Best practices", "Provide context"])
        body_sha = sha(ev.process_body(a, self.page, HTML["content-type"]))

        def rule(rid, source="doc-a", targets=("claude-code", "codex"), purposes=("one-shot", "skill-instructions"),
                 surfaces=("all",), models=("all",), excerpt="Give a check it can run", kind="official", reviewed=True):
            return {"rule_id": rid, "source_id": source, "target_ids": list(targets), "surfaces": list(surfaces),
                    "models": list(models), "kind": kind, "purposes": list(purposes), "preconditions": ["when applicable"],
                    "exclusions": ["adding unrequested work"], "source_body_sha256": body_sha if reviewed else None,
                    "source_excerpt": excerpt, "reviewed_at": "2026-10-14T00:00:00Z" if reviewed else None,
                    "reviewer_id": "unit-test" if reviewed else None, "meaning": "m", "source_locator": excerpt,
                    "allowed_change": "c", "review_status": "reviewed" if reviewed else "unreviewed"}
        targets = [{"target_id": t, "required_sources": [], "conditional_sources": []} for t in ev.TARGET_IDS]
        for t in targets:
            if t["target_id"] in ("claude-code", "codex"):
                t["required_sources"] = ["doc-a"]
            if t["target_id"] == "claude-code":
                t["conditional_sources"] = [
                    {"purposes": ["skill-instructions"], "surfaces": ["all"], "source_ids": ["doc-md"]},
                    {"purposes": ["all"], "surfaces": ["api"], "source_ids": ["doc-api"]}]
        reg = {"schema_version": 1, "sources": [a, md, api], "targets": targets, "rules": [
            rule("rule-verify"),
            rule("rule-model", models=("model-x",), excerpt="Reference specific files"),
            rule("rule-api", source="doc-api", targets=("claude-code",), purposes=("one-shot",), surfaces=("api",)),
            rule("rule-unreviewed", excerpt="Reference specific files", reviewed=False),
            rule("rule-skill-only", purposes=("skill-instructions",), excerpt="Reference specific files"),
            rule("rule-codex-only", targets=("codex",), excerpt="Reference specific files"),
            rule("rule-inference", kind="inference", excerpt="Reference specific files"),
            rule("rule-surface-api", surfaces=("api",), excerpt="Reference specific files")]}
        if mutate:
            mutate(reg)
        (self.tmp / "registry.json").write_text(json.dumps(reg), encoding="utf-8")

    def make_input(self, run_id="run-1", original=ORIGINAL, **over):
        inp = {"schema_version": 1, "execution_host": "claude-code", "target_id": "claude-code",
               "target_surface": "unknown", "model_id": "unknown", "purpose": "one-shot", "original_path": "original.md",
               "constraints": ["公開APIは変更しない", "テストはpytest"]}
        inp.update(over)
        (self.tmp / "original.md").write_text(original, encoding="utf-8")
        p = self.tmp / f"input-{run_id}.json"
        p.write_text(json.dumps(inp), encoding="utf-8")
        return p

    def fetch(self, run_id="run-1", mode="live", cache=None, **over):
        return ev.do_fetch(self.make_input(run_id, **over), self.tmp / "outputs" / run_id, mode, cache)

    def run_dir(self, run_id="run-1"):
        return self.tmp / "outputs" / run_id

    def entry(self, run, sid="doc-a"):
        return next(e for e in json.loads((run / "acquisition.json").read_text(encoding="utf-8"))["sources"] if e["source_id"] == sid)

    def generate(self, run, changes=None, optimized=OPTIMIZED, status="complete"):
        changes = DEFAULT_CHANGES if changes is None else changes
        inp = json.loads((run / "input.json").read_text(encoding="utf-8"))
        orig = (run / "original.md").read_text(encoding="utf-8")
        (run / "optimized.md").write_text(optimized, encoding="utf-8")
        recs = []
        for cid, kind, rule, before, after, lines, excerpt in changes:
            bs, as_ = orig.find(before), optimized.find(after)
            assert bs >= 0 and as_ >= 0, (cid, bs, as_)
            cited = rule is not None
            recs.append({"change_id": cid, "kind": kind, "rule_id": rule, "source_id": "doc-a" if cited else None,
                         "body_sha256": self.entry(run)["body_sha256"] if cited else None,
                         "source_start_line": lines[0] if cited else None, "source_end_line": lines[1] if cited else None,
                         "exact_excerpt": excerpt, "before_start": bs, "before_end": bs + len(before), "after_start": as_,
                         "after_end": as_ + len(after), "after_excerpt": after, "rationale": "r"})
        rec = {"schema_version": 1, "target_id": inp["target_id"], "target_surface": inp["target_surface"],
               "model_id": inp["model_id"], "generation_status": status, "original_sha256": sha(orig),
               "optimized_sha256": sha(optimized), "changes": recs}
        (run / "recommendations.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return rec

    def rewrite_rec(self, run, fn):
        p = run / "recommendations.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        fn(rec)
        p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    def rewrite_acq(self, run, fn):
        p = run / "acquisition.json"
        acq = json.loads(p.read_text(encoding="utf-8"))
        fn(acq)
        p.write_text(json.dumps(acq), encoding="utf-8")

    def write_log(self, run, lines=None, error=False, gen_first=False, read=True, write=True, text=None,
                  path=None, sidechain=False, source="doc-a", read_path=None):
        body = run / "snapshots" / source / "body.txt"
        shown = text
        if shown is None and read:
            bl = body.read_text(encoding="utf-8").split("\n")[:-1]
            shown = "\n".join(f"{n:6d}\t{bl[n - 1]}" for n in (range(1, len(bl) + 1) if lines is None else lines))

        def use(i, name, inp):
            return {"type": "assistant", "isSidechain": sidechain,
                    "message": {"content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]}}

        def res(i, content, err=False):
            return {"type": "user", "isSidechain": sidechain,
                    "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": content, "is_error": err}]}}
        r = [use("r1", "Read", {"file_path": str(read_path or body)}), res("r1", shown, error)] if read else []
        w = [use("w1", "Write", {"file_path": str(run / "optimized.md")}), res("w1", "ok")] if write else []
        seq = w + r if gen_first else r + w
        p = Path(path) if path else self.tmp / "logs" / "session.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in seq) + "\n", encoding="utf-8")
        return p

    def good_run(self, run_id="run-1", mode="live", cache=None, **over):
        code, _ = self.fetch(run_id, mode, cache, **over)
        self.assertEqual(code, 0)
        run = self.run_dir(run_id)
        self.generate(run)
        return run, self.write_log(run, path=self.tmp / "logs" / f"{run_id}.jsonl")

    def verify(self, run, mode="live", log=None):
        return ev.verify(run, mode, str(log) if log else None)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ev.main(list(argv))
        return code, out.getvalue(), err.getvalue()


# =========================================================== positive paths
class TestPositive(Base):
    def test_live_fetch_records_observed_acquisition(self):
        code, acq = self.fetch()
        self.assertEqual(code, 0)
        e = acq["sources"][0]
        run = self.run_dir()
        self.assertEqual((e["status"], e["method"], e["origin_status"]), ("live", "urllib", "observed"))
        self.assertEqual(e["raw_sha256"], ev.sha_file(run / e["raw_path"]))
        self.assertEqual(e["body_sha256"], ev.sha_file(run / e["body_path"]))
        self.assertEqual(acq["original_sha256"], ev.sha_text(ORIGINAL))
        self.assertEqual(acq["selected_sources"], ["doc-a"])
        # nav/script/footer text is not evidence; <pre> indentation is kept
        body = (run / e["body_path"]).read_text(encoding="utf-8")
        for absent in ("NavOnlyLocator", "ScriptOnlyLocator", "FooterOnlyLocator"):
            self.assertNotIn(absent, body)
        self.assertIn("  tests/test_date.py", body)
        self.assertEqual(json.loads((run / "evaluation.json").read_text(encoding="utf-8"))["status"], "NOT_EVALUATED")
        self.assertEqual(json.loads((run / "semantic-review.json").read_text(encoding="utf-8"))["status"], "NOT_REVIEWED")

    def test_pass_live_requires_everything_and_exits_0(self):
        run, log = self.good_run()
        rep = self.verify(run, "live", log)
        self.assertEqual(rep["verdict"], "PASS_LIVE", rep["errors"])
        self.assertEqual(rep["sources"][0]["read_status"], "observed")
        self.assertEqual(rep["change_counts"], {"official": 1, "inference": 0, "editorial": 2})
        code, out, err = self.cli("verify", "--run", str(run), "--mode", "live", "--host-log", str(log))
        self.assertEqual(code, 0)
        self.assertIn("PASS_LIVE", err)
        self.assertEqual(json.loads(out)["verdict"], "PASS_LIVE")

    def test_pass_cache_is_labelled_and_keeps_original_fetch_time(self):
        run1, _ = self.good_run("run-1")
        first = self.entry(run1)
        self.clock.advance(hours=2)
        code, _ = self.fetch("run-2", "cache", run1)
        self.assertEqual(code, 0)
        run2 = self.run_dir("run-2")
        self.generate(run2)
        log2 = self.write_log(run2, path=self.tmp / "logs" / "r2.jsonl")
        e = self.entry(run2)
        self.assertEqual((e["status"], e["fetched_at"], e["raw_sha256"]), ("cache", first["fetched_at"], first["raw_sha256"]))
        self.assertNotEqual(e["rechecked_at"], e["fetched_at"])
        self.assertEqual(self.verify(run2, "cache", log2)["verdict"], "PASS_CACHE")
        # a cache run is never a live pass
        self.assertEqual(self.verify(run2, "live", log2)["verdict"], "INCOMPLETE")
        self.assertEqual(self.cli("verify", "--run", str(run2), "--mode", "live", "--host-log", str(log2))[0], 2)

    def test_inference_without_source_is_allowed_and_not_official(self):
        code, _ = self.fetch()
        run = self.run_dir()
        chg = [("c1", "inference", None, "バグを直して。", "バグを修正してください。\n", None, None)] + DEFAULT_CHANGES[1:]
        self.generate(run, chg)
        rep = self.verify(run, "live", self.write_log(run))
        self.assertEqual(rep["verdict"], "PASS_LIVE", rep["errors"])
        self.assertEqual(rep["change_counts"]["inference"], 1)


# =========================================================== url / connection boundary
class TestUrlAndConnection(Base):
    def source(self, sid="doc-a"):
        return next(s for s in ev.load_registry()[0]["sources"] if s["source_id"] == sid)

    def test_url_allowed_exact_match_only(self):
        src = self.source()
        bad = ["https://docs.example.com.evil.test/guide", "https://user:pw@docs.example.com/guide",
               "https://docs.example.com/community/guide", "https://docs.example.com/guide?x=1",
               "https://docs.example.com:8443/guide", "http://docs.example.com/guide", "https://evil.test/guide",
               "https://docs.example.com/guide/", "https://docs.example.com/guide/../guide2"]
        for u in bad:
            with self.subTest(url=u):
                self.assertFalse(ev.url_allowed(u, src)[0])
        for u in (A_URL, "https://DOCS.example.com/guide", "https://docs.example.com:443/guide#frag"):
            with self.subTest(url=u):
                self.assertTrue(ev.url_allowed(u, src)[0])

    def test_unregistered_intermediate_redirect_is_never_contacted(self):
        self.responses[A_URL] = (302, {"location": "https://evil.test/landing"}, b"")
        self.responses["https://evil.test/landing"] = (302, {"location": A_URL}, b"")
        code, acq = self.fetch()
        e = acq["sources"][0]
        self.assertEqual(code, 2)
        self.assertEqual(e["status"], "blocked")
        self.assertTrue(e["error"].startswith("redirect_not_registered"))
        self.assertNotIn("https://evil.test/landing", self.calls)

    def test_registered_redirect_hops_are_recorded_and_verified(self):
        new = "https://docs.example.com/guide-new"
        self.set_registry(lambda r: r["sources"][0]["allowed_urls"].append(new))
        self.responses[A_URL] = (301, {"location": "/guide-new"}, b"")
        self.responses[new] = (200, HTML, self.page)
        run, log = self.good_run()
        e = self.entry(run)
        self.assertEqual(e["redirect_chain"], [{"from": A_URL, "to": new, "status": 301}])
        self.assertEqual(e["final_url"], new)
        self.assertEqual(self.verify(run, "live", log)["verdict"], "PASS_LIVE")
        self.rewrite_acq(run, lambda a: a["sources"][0]["redirect_chain"][0].update({"to": "https://evil.test/x"}))
        self.assertEqual(self.verify(run, "live", log)["verdict"], "FAIL")

    def test_too_many_redirects(self):
        self.responses[A_URL] = (302, {"location": A_URL}, b"")
        code, acq = self.fetch()
        self.assertEqual((code, acq["sources"][0]["error"]), (2, "too_many_redirects"))
        self.assertEqual(len(self.calls), ev.MAX_HOPS + 1)

    def test_non_public_addresses_are_rejected(self):
        for ip in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fd00::1", "fe80::1", "::ffff:10.0.0.1"):
            with self.subTest(ip=ip), mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443))]):
                with self.assertRaises(ev.Blocked):
                    REAL_CHECK_HOST("docs.example.com")
        with mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("93.184.216.34", 443)),
                                                            (0, 0, 0, "", ("10.0.0.1", 443))]):
            with self.assertRaises(ev.Blocked):  # one private answer is enough to refuse
                REAL_CHECK_HOST("docs.example.com")
        for host in ("localhost", "app.localhost", "127.0.0.1", "::1", "93.184.216.34"):
            with self.subTest(host=host), self.assertRaises(ev.Blocked):
                REAL_CHECK_HOST(host)
        for ip in ("93.184.216.34", "2606:4700:4700::1111"):
            with self.subTest(public=ip), mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443))]):
                self.assertEqual(REAL_CHECK_HOST("docs.example.com"), "checked")

    def test_dns_failure_blocks_unless_proxy_resolves(self):
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror), mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ev.Blocked):
                REAL_CHECK_HOST("docs.example.com")
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror), \
                mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:1"}, clear=True):
            self.assertEqual(REAL_CHECK_HOST("docs.example.com"), "dns_delegated_to_proxy")

    def test_network_failures_become_blocked_not_crashes(self):
        def boom(exc):
            def f():
                raise exc
            return f
        for name, exc in (("timeout", TimeoutError()), ("urlerror", urllib.error.URLError("x")),
                          ("incomplete", http.client.IncompleteRead(b"abc"))):
            with self.subTest(name), mock.patch.dict(self.responses, {A_URL: boom(exc)}):
                code, acq = self.fetch(f"run-{name}")
                self.assertEqual((code, acq["sources"][0]["status"]), (2, "blocked"))
                self.assertTrue(acq["sources"][0]["error"].startswith("network_error"))

    def real_get(self, status, headers, body):
        """The real http_get, with the connection replaced by one canned answer."""
        class Answer:
            def __init__(self):
                self.status, self.headers = status, headers

            def read(self, n=-1):
                return body[:n]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        opener = mock.Mock(open=lambda req, timeout=None: Answer())
        with mock.patch("urllib.request.build_opener", return_value=opener):
            return REAL_HTTP_GET(A_URL)

    def test_gzip_answer_is_unpacked_to_the_same_page(self):
        # 2026-10-15: antigravity.google answered 1 request in 10 with gzip bytes and "Content-Encoding: gzip", unasked;
        # the unpacked page must be what the plain answer is, or the body hash and the extraction both go wrong
        status, _, body = self.real_get(200, {"Content-Type": "text/html", "Content-Encoding": "gzip"}, gzip.compress(self.page))
        self.assertEqual((status, body), (200, self.page))
        src = self.source()
        self.assertEqual(ev.process_body(src, body, HTML["content-type"]), ev.process_body(src, self.page, HTML["content-type"]))

    def test_content_encodings_that_cannot_be_read_are_blocked_with_their_own_code(self):
        packed = gzip.compress(self.page)
        for name, coding, data, code in (("brotli", "br", b"\x8b\x02", "unsupported_content_encoding"),
                                         ("not gzip", "gzip", b"plain text", "bad_content_encoding"),
                                         ("cut short", "gzip", packed[:len(packed) // 2], "bad_content_encoding")):
            with self.subTest(name), self.assertRaises(ev.Blocked) as cm:
                self.real_get(200, {"Content-Encoding": coding}, data)
            self.assertEqual(cm.exception.code, code)
        for coding in (None, "", "identity"):
            with self.subTest(coding=coding):
                headers = {} if coding is None else {"Content-Encoding": coding}
                self.assertEqual(self.real_get(200, headers, self.page)[2], self.page)

    def test_an_error_page_keeps_its_status_whatever_its_encoding(self):
        status, _, body = self.real_get(503, {"Content-Encoding": "gzip"}, b"half a gzip stream")
        self.assertEqual((status, body), (503, b"half a gzip stream"))
        self.responses[A_URL] = (status, HTML, body)
        code, acq = self.fetch()
        self.assertEqual((code, acq["sources"][0]["error"]), (2, "http_503"))

    def test_a_gzip_bomb_is_refused_as_too_large(self):
        bomb = gzip.compress(b"a" * 4000)
        with mock.patch.object(ev, "MAX_BYTES", 1000):
            unpacked = ev.decode_content({"content-encoding": "gzip"}, bomb)
            self.responses[A_URL] = (200, HTML, unpacked)
            code, acq = self.fetch()
        self.assertEqual(len(unpacked), 1001)  # stopped at the limit, not unpacked in full
        self.assertEqual((code, acq["sources"][0]["error"]), (2, "response_too_large"))

    def test_oversize_response_is_blocked_and_not_saved(self):
        self.responses[A_URL] = (200, HTML, b"x" * 2000)
        with mock.patch.object(ev, "MAX_BYTES", 1000):
            code, acq = self.fetch()
        self.assertEqual((code, acq["sources"][0]["error"]), (2, "response_too_large"))
        self.assertFalse(any((self.run_dir() / "snapshots").rglob("*.bin")))

    def test_http_status_and_url_only_record(self):
        self.responses[A_URL] = (404, HTML, b"nope")
        code, acq = self.fetch()
        self.assertEqual((code, acq["sources"][0]["error"]), (2, "http_404"))
        # a hand-written URL-only record with no snapshot never verifies
        run = self.run_dir()
        self.generate(run)
        self.rewrite_acq(run, lambda a: a["sources"][0].update({"status": "live", "http_status": 200, "origin_status": "observed",
                                                                  "fetched_at": a["started_at"], "final_url": A_URL, "error": None}))
        rep = self.verify(run, "live", self.write_log(run, read=False))
        self.assertEqual(rep["verdict"], "FAIL")


# =========================================================== body
class TestBody(Base):
    def blocked(self, page, ctype="text/html; charset=utf-8", mutate=None):
        if mutate:
            self.set_registry(mutate)
        self.responses[A_URL] = (200, {"content-type": ctype}, page)
        code, acq = self.fetch(f"run-{len(list(self.tmp.glob('outputs/*')))}")
        self.assertEqual(code, 2)
        self.assertEqual(acq["sources"][0]["status"], "blocked")
        return acq["sources"][0]["error"]

    def test_http_200_that_is_not_the_document(self):
        self.assertIn("challenge", self.blocked((FIX / "challenge.html").read_bytes()))
        login = b"<html><body><main><h1>Sign in to continue</h1><p>" + b"Please enter your account details. " * 8 + b"</p></main></body></html>"
        self.assertIn("challenge", self.blocked(login))
        self.assertEqual(self.blocked(b"<html><body><main></main></body></html>"), "body_empty")
        self.assertEqual(self.blocked(b"<html><body><div id=root></div><script>render()</script></body></html>"),
                         "container_missing_or_empty")
        self.assertIn("too_short", self.blocked(b"<html><body><main><h1>Best practices</h1><p>Provide context</p></main></body></html>"))

    def test_locator_only_in_navigation_script_or_footer_is_not_evidence(self):
        for loc in ("NavOnlyLocator", "ScriptOnlyLocator", "FooterOnlyLocator"):
            with self.subTest(loc):
                err = self.blocked(self.page, mutate=lambda r, loc=loc: r["sources"][0].update({"body_locators": [loc, "Best practices"]}))
                self.assertEqual(err, f"locators_missing:{loc}")

    def test_content_type_charset_and_encoding(self):
        self.assertTrue(self.blocked(self.page, "text/plain").startswith("unexpected_content_type"))
        self.assertEqual(self.blocked(self.page, "text/html; charset=iso-8859-1"), "unsupported_charset")
        self.assertEqual(self.blocked(b"<html><main>\xff\xfe</main></html>"), "not_utf8")

    def test_markdown_source_uses_markdown_adapter(self):
        code, acq = self.fetch(purpose="skill-instructions")
        self.assertEqual((code, acq["selected_sources"]), (0, ["doc-a", "doc-md"]))
        self.assertIn("## Details", (self.run_dir() / "snapshots/doc-md/body.txt").read_text(encoding="utf-8"))

    def tamper_cases(self):
        run, log = self.good_run()
        return run, log, self.entry(run)

    def test_raw_or_body_modification_fails(self):
        for target in ("raw.bin", "body.txt"):
            with self.subTest(target):
                run, log, _ = self.tamper_cases() if target == "raw.bin" else (None, None, None)
                if run is None:
                    run, log = self.run_dir(), self.write_log(self.run_dir())
                p = run / "snapshots" / "doc-a" / target
                p.write_bytes(p.read_bytes() + b" tampered")
                rep = self.verify(run, "live", log)
                self.assertEqual(rep["verdict"], "FAIL")
                self.assertIn("hash_mismatch", codes(rep))

    def test_consistent_body_edit_is_caught_by_reextraction(self):
        run, log, e = self.tamper_cases()
        body = run / e["body_path"]
        body.write_text(body.read_text(encoding="utf-8").replace("Give a check", "Skip every check"), encoding="utf-8")
        self.rewrite_acq(run, lambda a: a["sources"][0].update({"body_sha256": ev.sha_file(body)}))
        self.assertIn("body_not_from_raw", codes(self.verify(run, "live", log)))

    def test_unsupported_extraction_version_is_incomplete(self):
        run, log, _ = self.tamper_cases()
        self.rewrite_acq(run, lambda a: a["sources"][0].update({"extraction_version": "html-main-v0"}))
        rep = self.verify(run, "live", log)
        self.assertEqual(rep["verdict"], "INCOMPLETE")
        self.assertIn("unsupported_extraction_version", codes(rep))


# =========================================================== evidence / output binding
class TestEvidenceBinding(Base):
    def setUp(self):
        super().setUp()
        self.run, self.log = self.good_run()

    def check(self, expect, mode="live"):
        rep = self.verify(self.run, mode, self.log)
        self.assertEqual(rep["verdict"], "FAIL", rep["errors"])
        self.assertIn(expect, codes(rep))
        return rep

    def c3(self, **kv):
        def f(rec):
            next(c for c in rec["changes"] if c["change_id"] == "c3").update(kv)
        self.rewrite_rec(self.run, f)

    def test_invented_rule_or_source(self):
        self.c3(rule_id="ghost-rule")
        self.check("unknown_rule")
        self.generate(self.run)
        self.c3(source_id="ghost-source")
        rep = self.check("rule_source_or_kind_mismatch")
        self.assertIn("source_not_selected", codes(rep))

    def test_missing_or_wrong_citation(self):
        for field, value, code in (("exact_excerpt", "text that is nowhere in the page", "excerpt_not_in_lines"),
                                   ("source_start_line", 40, "bad_line_range"), ("source_end_line", 99, "bad_line_range"),
                                   ("body_sha256", "0" * 64, "body_hash_mismatch")):
            with self.subTest(field):
                self.generate(self.run)
                self.c3(**{field: value})
                self.check(code)
        self.generate(self.run)
        self.c3(source_start_line=1, source_end_line=1, exact_excerpt="Best practices")  # real text, wrong evidence for the rule
        self.check("lines_not_rule_evidence")

    def test_span_and_hash_tampering(self):
        self.c3(after_excerpt="別の文")
        self.check("after_excerpt_mismatch")
        self.generate(self.run)
        self.c3(after_end=10_000)
        self.check("span_out_of_range")
        self.generate(self.run)
        (self.run / "optimized.md").write_text(OPTIMIZED + "追加\n", encoding="utf-8")
        self.check("rec_optimized_hash_mismatch")
        self.generate(self.run)
        self.rewrite_rec(self.run, lambda r: r.update({"original_sha256": "1" * 64}))
        self.check("rec_original_hash_mismatch")

    def test_unrecorded_edit_is_detected(self):
        edited = OPTIMIZED.replace("確認: pytestを実行する。", "確認: pytestを実行する。急いで。")
        (self.run / "optimized.md").write_text(edited, encoding="utf-8")
        self.rewrite_rec(self.run, lambda r: r.update({"optimized_sha256": sha(edited)}))
        self.check("unmapped_change")

    def test_overlapping_changes_fail(self):
        self.rewrite_rec(self.run, lambda r: r["changes"][1].update({"before_start": 0, "after_start": 0}))
        self.check("overlapping_changes")

    def test_a_pure_insertion_binds_with_an_anchor_or_an_empty_before_span_but_not_when_left_uncovered(self):
        # references/recommendations-example.md tells the one who writes recommendations.json how to record text that is only added
        inserted = "バグを直して。\n公開APIは変更しない。テストはpytestで。\n"  # a line break added after the first sentence
        anchored = [("c1", "editorial", None, "バグを直して。", "バグを直して。\n", None, None)]
        self.generate(self.run, changes=anchored, optimized=inserted)  # anchored: the sentence it follows is rewritten with the break
        self.assertEqual(self.verify(self.run, "live", self.log)["checks"]["change_binding"]["status"], "PASS")
        self.rewrite_rec(self.run, lambda r: r["changes"][0].update(before_start=7, before_end=7, after_start=7, after_end=8, after_excerpt="\n"))
        self.assertEqual(self.verify(self.run, "live", self.log)["checks"]["change_binding"]["status"], "PASS")  # empty before span
        self.rewrite_rec(self.run, lambda r: r["changes"].clear())
        self.check("unmapped_change")  # the added line break belongs to no change

    def test_scope_mismatches(self):
        for name, setup, code in (
                ("other target", lambda: self.c3(rule_id="rule-codex-only", exact_excerpt="Reference specific files",
                                                 source_start_line=3, source_end_line=3), "target_mismatch"),
                ("api doc is not routed for a ui surface", lambda: self.c3(rule_id="rule-api", source_id="doc-api"), "source_not_selected"),
                ("api rule applied to a ui surface", lambda: self.c3(rule_id="rule-surface-api", exact_excerpt="Reference specific files",
                                                                    source_start_line=3, source_end_line=3), "surface_mismatch"),
                ("model limited, unknown model", lambda: self.c3(rule_id="rule-model", exact_excerpt="Reference specific files",
                                                                source_start_line=3, source_end_line=3), "model_mismatch"),
                ("purpose", lambda: self.c3(rule_id="rule-skill-only", exact_excerpt="Reference specific files",
                                            source_start_line=3, source_end_line=3), "purpose_mismatch"),
                ("unreviewed", lambda: self.c3(rule_id="rule-unreviewed", exact_excerpt="Reference specific files",
                                               source_start_line=3, source_end_line=3), "rule_unreviewed"),
                ("kind", lambda: self.c3(rule_id="rule-inference", exact_excerpt="Reference specific files",
                                         source_start_line=3, source_end_line=3), "rule_source_or_kind_mismatch")):
            with self.subTest(name):
                self.generate(self.run)
                setup()
                self.check(code)

    def test_model_limited_rule_applies_to_its_model(self):
        run, log = self.good_run("run-m", model_id="model-x")
        chg = DEFAULT_CHANGES[:2] + [("c3", "official", "rule-model", "テストはpytestで。", "確認: pytestを実行する。\n", (3, 3), "Reference specific files")]
        self.generate(run, chg)
        self.assertEqual(self.verify(run, "live", self.write_log(run))["verdict"], "PASS_LIVE")

    def test_rule_review_goes_stale_when_body_changes(self):
        self.set_registry(lambda r: [x.update({"source_body_sha256": "2" * 64}) for x in r["rules"] if x["rule_id"] == "rule-verify"])
        # registry changed after fetch -> acquisition no longer matches the registry it was made with
        rep = self.verify(self.run, "live", self.log)
        self.assertIn("registry_changed", codes(rep))
        run, log = self.good_run("run-s")
        rep = self.verify(run, "live", log)
        self.assertEqual(rep["verdict"], "INCOMPLETE")
        self.assertIn("rereview_pending", codes(rep))

    def test_selected_sources_must_follow_registry_routing(self):
        self.rewrite_acq(self.run, lambda a: a.update({"selected_sources": [], "sources": []}))
        self.check("selected_sources_mismatch")


# =========================================================== time and reads
class TestTimeAndReads(Base):
    def setUp(self):
        super().setUp()
        self.run, self.log = self.good_run()

    def test_stale_snapshot_is_incomplete(self):
        self.clock.advance(days=8)
        rep = self.verify(self.run, "live", self.log)
        self.assertEqual(rep["verdict"], "INCOMPLETE")
        self.assertIn("stale", codes(rep))
        self.clock.t -= timedelta(days=2)  # 6 days old is still fresh
        self.assertEqual(self.verify(self.run, "live", self.log)["verdict"], "PASS_LIVE")

    def test_future_timestamp_fails(self):
        self.rewrite_acq(self.run, lambda a: a["sources"][0].update({"fetched_at": "2026-10-15T12:00:00Z"}))
        rep = self.verify(self.run, "live", self.log)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertIn("future_timestamp", codes(rep))

    def test_cache_cannot_pose_as_live_or_refresh_its_date(self):
        self.clock.advance(hours=1)
        self.fetch("run-2", "cache", self.run)
        run2 = self.run_dir("run-2")
        self.generate(run2)
        log2 = self.write_log(run2, path=self.tmp / "logs" / "r2.jsonl")
        self.rewrite_acq(run2, lambda a: a["sources"][0].update({"status": "live"}))
        self.assertIn("live_before_run", codes(self.verify(run2, "live", log2)))
        self.rewrite_acq(run2, lambda a: a["sources"][0].update({"status": "cache", "fetched_at": "2026-10-14T13:00:01Z"}))
        rep = self.verify(run2, "cache", log2)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertIn("cache_timestamp_refreshed", codes(rep))

    def test_cache_fetch_rejects_tampered_snapshot_and_future_date(self):
        p = self.run / "snapshots/doc-a/body.txt"
        p.write_text(p.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        with self.assertRaises(ev.ContractError):
            self.fetch("run-t", "cache", self.run)
        p.write_text(p.read_text(encoding="utf-8")[:-2], encoding="utf-8")
        self.rewrite_acq(self.run, lambda a: a["sources"][0].update({"fetched_at": "2026-12-01T00:00:00Z"}))
        with self.assertRaises(ev.ContractError):
            self.fetch("run-f", "cache", self.run)

    def test_cache_fetch_rejects_a_snapshot_swapped_for_another_valid_page(self):
        other = self.page.replace(b"Give a check it can run", b"Skip every check you can run")
        src = next(s for s in ev.load_registry()[0]["sources"] if s["source_id"] == "doc-a")
        (self.run / "snapshots/doc-a/raw.bin").write_bytes(other)
        (self.run / "snapshots/doc-a/body.txt").write_text(ev.process_body(src, other, HTML["content-type"]), encoding="utf-8")
        with self.assertRaises(ev.ContractError) as cm:
            self.fetch("run-sw", "cache", self.run)
        self.assertIn("hash mismatch", str(cm.exception))

    def test_cache_without_the_needed_source_is_missing(self):
        code, acq = self.fetch("run-3", "cache", self.run, purpose="skill-instructions")  # cached run only holds doc-a
        self.assertEqual(code, 2)
        self.assertEqual({e["source_id"]: e["status"] for e in acq["sources"]}, {"doc-a": "cache", "doc-md": "missing"})

    def test_malformed_host_log_is_a_failure_not_a_crash(self):
        bad = self.tmp / "bad.jsonl"
        bad.write_text("{not json\n", encoding="utf-8")
        rep = self.verify(self.run, "live", bad)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertIn("host_log_invalid", codes(rep))

    def test_cache_older_than_a_week_is_stale_and_fetch_exits_2(self):
        self.clock.advance(days=8)
        code, acq = self.fetch("run-old", "cache", self.run)
        self.assertEqual((code, acq["sources"][0]["status"]), (2, "stale"))

    def test_reads_that_must_not_pass(self):
        r = self.run
        cases = {
            "no log": (None, "no_host_log"),
            "failed read": (self.write_log(r, error=True, path=self.tmp / "l1.jsonl"), "read_not_observed"),
            "unrelated lines only": (self.write_log(r, lines=[1, 3, 4], path=self.tmp / "l2.jsonl"), "read_not_observed"),
            "read after generation": (self.write_log(r, gen_first=True, path=self.tmp / "l3.jsonl"), "read_not_observed"),
            "no read at all": (self.write_log(r, read=False, path=self.tmp / "l4.jsonl"), "read_not_observed"),
            "altered text": (self.write_log(r, text="     2\tGive a check it can skip", path=self.tmp / "l5.jsonl"), "read_not_observed"),
            "other file": (self.write_log(r, read_path=r / "original.md", path=self.tmp / "l6.jsonl"), "read_not_observed"),
            "sidechain": (self.write_log(r, sidechain=True, path=self.tmp / "l7.jsonl"), "generation_write_not_found"),
            "no generation write": (self.write_log(r, write=False, path=self.tmp / "l8.jsonl"), "generation_write_not_found"),
        }
        for name, (log, code) in cases.items():
            with self.subTest(name):
                rep = self.verify(r, "live", log)
                self.assertEqual(rep["verdict"], "INCOMPLETE", rep["errors"])
                self.assertIn(code, codes(rep))
                self.assertNotEqual(rep["sources"][0]["read_status"], "observed")

    def test_model_authored_receipt_inside_run_is_fraud(self):
        forged = self.write_log(self.run, path=self.run / "read-receipt.json")
        rep = self.verify(self.run, "live", forged)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(rep["sources"][0]["read_status"], "self_reported")

    def test_partial_read_covering_cited_lines_is_enough(self):
        log = self.write_log(self.run, lines=[2], path=self.tmp / "partial.jsonl")
        self.assertEqual(self.verify(self.run, "live", log)["verdict"], "PASS_LIVE")

    def test_external_tool_snapshot_never_passes(self):
        self.rewrite_acq(self.run, lambda a: a["sources"][0].update({"method": "external-tool", "status": "cache",
                                                                      "origin_status": "unobserved", "rechecked_at": "2026-10-14T12:30:00Z",
                                                                      "fetched_at": "2026-10-14T11:00:00Z"}))
        rep = self.verify(self.run, "cache", self.log)
        self.assertEqual(rep["verdict"], "INCOMPLETE")
        self.assertIn("external_origin_unverified", codes(rep))
        self.rewrite_acq(self.run, lambda a: a["sources"][0].update({"origin_status": "observed"}))
        self.assertEqual(self.verify(self.run, "cache", self.log)["verdict"], "FAIL")


# =========================================================== routing and retention
class TestRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg, _ = ev.load_registry(SKILL / "references" / "registry.json")

    def route(self, target, purpose="one-shot", surface="unknown", model="unknown"):
        return ev.select_sources(self.reg, {"target_id": target, "purpose": purpose, "target_surface": surface, "model_id": model})

    def test_all_eleven_targets_have_expected_minimum_sources(self):
        expect = {"chatgpt": ["openai-prompting"], "chatgpt-work": ["openai-prompting", "openai-work"],
                  "codex": ["openai-prompting"], "claude": ["claude-chat-help"], "claude-cowork": ["claude-cowork-help"],
                  "claude-code": ["claude-code-best-practices"], "gemini": [], "antigravity": [],
                  "gemini-agent-app": ["gemini-tasks"], "gemini-cli": ["gemini-cli-memory"],
                  "google-adk-agent": ["google-adk-instructions"]}
        self.assertEqual(set(expect), set(ev.TARGET_IDS))
        for target, want in expect.items():
            with self.subTest(target):
                # the required sources come first; one-shot also pulls the conditional sources that hold for every surface
                self.assertEqual(self.route(target)[:len(want)], want)
        self.assertEqual(self.route("unresolved"), [])

    def test_conditional_sources_need_purpose_and_surface(self):
        cases = [("codex", "persistent-instructions", "unknown", "openai-agents-md"),
                 ("codex", "skill-instructions", "cli", "openai-skills"),
                 ("claude-code", "skill-instructions", "unknown", "claude-code-skills"),
                 ("gemini", "persistent-instructions", "gems", "gemini-gems"),
                 ("gemini", "skill-instructions", "unknown", "gemini-app-skills"),
                 ("antigravity", "persistent-instructions", "unknown", "antigravity-rules"),
                 ("antigravity", "skill-instructions", "unknown", "antigravity-skills"),
                 ("gemini-cli", "skill-instructions", "unknown", "gemini-cli-skills"),
                 ("claude-code", "one-shot", "api", "claude-model-prompting"),
                 ("chatgpt", "one-shot", "api", "openai-api-prompting")]
        for target, purpose, surface, sid in cases:
            with self.subTest(target=target, purpose=purpose):
                self.assertIn(sid, self.route(target, purpose, surface))
        # no leakage: a plain chat request never pulls Gems / Rule / Skill / API documents
        for target, purpose, surface, sid in (("gemini", "one-shot", "unknown", "gemini-gems"),
                                              ("gemini", "persistent-instructions", "unknown", "gemini-gems"),
                                              ("antigravity", "one-shot", "unknown", "antigravity-rules"),
                                              ("antigravity", "one-shot", "unknown", "antigravity-skills"),
                                              ("claude-code", "one-shot", "unknown", "claude-model-prompting"),
                                              ("chatgpt", "one-shot", "unknown", "openai-api-prompting")):
            with self.subTest(no=sid, purpose=purpose):
                self.assertNotIn(sid, self.route(target, purpose, surface))

    def test_a_source_limited_to_a_model_is_fetched_only_for_that_model(self):
        # the Opus 5.5 guide is routed for every Claude API run, but its rules apply to claude-opus-5-5 alone
        for model, has_opus, has_sonnet in (("claude-opus-5-5", True, False), ("claude-sonnet-5-5", False, True),
                                            ("claude-opus-4-0", False, False), ("unknown", False, False)):
            with self.subTest(model=model):
                got = self.route("claude", "one-shot", "api", model)
                self.assertEqual(("claude-prompting-opus-5-5" in got, "claude-prompting-sonnet-5-5" in got), (has_opus, has_sonnet))
                self.assertIn("claude-model-prompting", got)  # a source for all models is kept
        self.assertNotIn("claude-prompting-opus-5-5", self.route("claude", "one-shot", "unknown", "claude-opus-5-5"))  # surface still decides

    def all_inputs(self):
        models = ["unknown", "other-model"] + sorted({m for s in self.reg["sources"] for m in s["models"] if m != "all"})
        for target in ev.TARGET_IDS + ("unresolved",):
            for purpose in ev.PURPOSES:
                for surface in ("unknown", "api", "gems", "cli"):
                    for model in models:
                        yield {"target_id": target, "purpose": purpose, "target_surface": surface, "model_id": model}

    def test_the_explanation_selects_exactly_what_the_routing_selects(self):
        # explain_sources is what the sources report shows the reader: it must never disagree with what fetch fetches
        for inp in self.all_inputs():
            exp = ev.explain_sources(self.reg, inp)
            chosen, left = [x["source_id"] for x in exp["selected"]], [x["source_id"] for x in exp["skipped"]]
            self.assertEqual(chosen, ev.select_sources(self.reg, inp), inp)
            self.assertEqual((len(left), set(chosen) & set(left)), (len(set(left)), set()), inp)

    def test_every_model_guide_is_reachable_and_never_chosen_for_another_model(self):
        limited = {s["source_id"]: s for s in self.reg["sources"] if s["models"] != ["all"]}
        self.assertTrue(limited)
        reached = {(sid, m): False for sid, s in limited.items() for m in s["models"]}
        for inp in self.all_inputs():
            for sid in ev.select_sources(self.reg, inp):
                if sid in limited:
                    self.assertIn(inp["model_id"], limited[sid]["models"], inp)  # a guide never reaches another model, nor 'unknown'
                    reached[(sid, inp["model_id"])] = True
        self.assertEqual([k for k, v in reached.items() if not v], [])  # and each guide is chosen for each of its own models

    def test_a_model_given_with_another_spelling_gets_a_hint_but_no_guide(self):
        for given, hint in (("claude-opus-5-5-20261001", ["claude-opus-5-5"]), ("Claude Opus 5.5", ["claude-opus-5-5"]),
                            ("opus_5_5", ["claude-opus-5-5"]), ("claude-opus-5-1", []), ("claude-opus-5-5", []),
                            ("unknown", []), ("gpt-5", [])):
            with self.subTest(given):
                self.assertEqual(ev.model_hint(self.reg, given), hint)
                if given != "claude-opus-5-5":
                    self.assertNotIn("claude-prompting-opus-5-5", self.route("claude", "one-shot", "api", given))  # the hint is never used to select

    def test_distinct_gemini_products_are_not_merged(self):
        ids = {t: self.route(t) for t in ("gemini-agent-app", "gemini-cli", "google-adk-agent", "gemini", "antigravity")}
        flat = [tuple(v) for v in ids.values() if v]
        self.assertEqual(len(flat), len(set(flat)))

    def test_registry_is_internally_consistent_and_rules_are_reviewed(self):
        sources = {s["source_id"] for s in self.reg["sources"]}
        self.assertEqual(len(sources), 50)
        routed = {i for t in self.reg["targets"] for i in t["required_sources"] + [x for c in t["conditional_sources"] for x in c["source_ids"]]}
        self.assertEqual((sources - routed, sources - {r["source_id"] for r in self.reg["rules"]}), (set(), set()))  # no dead source
        for r in self.reg["rules"]:
            with self.subTest(r["rule_id"]):
                self.assertEqual(r["review_status"], "reviewed")
                self.assertNotIn("human", r["reviewer_id"].replace("not human", ""))  # never claims a human review
                self.assertTrue(r["preconditions"] and r["exclusions"])
        api = {s["source_id"] for s in self.reg["sources"] if s["surfaces"] == ["api"]}
        for r in self.reg["rules"]:
            if r["source_id"] in api:
                self.assertEqual(r["surfaces"], ["api"])  # API advice is never applied to UI surfaces
        limited = {s["source_id"]: s["models"] for s in self.reg["sources"] if s["models"] != ["all"]}
        for r in self.reg["rules"]:
            if r["source_id"] in limited:
                self.assertTrue(set(r["models"]) <= set(limited[r["source_id"]]), r["rule_id"])  # a model's guide never grounds another model's rule


class TestExecutionHosts(Base):
    def test_every_documented_host_can_run_but_only_claude_code_can_observe_reads(self):
        for host in ev.HOSTS:
            with self.subTest(host):
                run, log = self.good_run(f"run-{host}", execution_host=host)
                rep = self.verify(run, "live", log)  # a Claude Code-format log is offered to every host
                if host == "claude-code":
                    self.assertEqual(rep["verdict"], "PASS_LIVE", rep["errors"])
                else:
                    self.assertEqual(rep["verdict"], "INCOMPLETE")
                    self.assertEqual(codes(rep), {"no_log_adapter_for_host"})  # nothing else is wrong; the read is just unobservable
                    self.assertEqual(rep["sources"][0]["read_status"], "unavailable")
                    self.assertEqual(rep["checks"]["read_observation"]["status"], "UNAVAILABLE")
                    self.assertEqual(self.cli("verify", "--run", str(run), "--mode", "live", "--host-log", str(log))[0], 2)

    def test_unknown_host_is_rejected(self):
        with self.assertRaises(ev.ContractError):
            self.fetch(execution_host="some-agent")


class TestLifecycleRouting(Base):
    def test_unresolved_target_does_work_but_never_verifies(self):
        code, acq = self.fetch(target_id="unresolved")
        self.assertEqual((code, acq["selected_sources"]), (2, []))
        run = self.run_dir()
        self.generate(run, NO_SOURCE_CHANGES, optimized=NO_SOURCE_OPT, status="provisional")
        rep = self.verify(run, "live", self.write_log(run, read=False))
        self.assertEqual(rep["verdict"], "INCOMPLETE")
        self.assertIn("target_unresolved", codes(rep))

    def test_target_without_applicable_source_is_provisional_at_best(self):
        code, acq = self.fetch(target_id="gemini")
        self.assertEqual((code, acq["selected_sources"]), (0, []))
        run = self.run_dir()
        self.generate(run, NO_SOURCE_CHANGES, optimized=NO_SOURCE_OPT, status="provisional")
        rep = self.verify(run, "live", self.write_log(run, read=False))
        self.assertEqual(rep["verdict"], "INCOMPLETE")
        self.assertIn("no_applicable_official_source", codes(rep))

    def test_provisional_generation_is_never_a_pass(self):
        run, log = self.good_run()
        self.rewrite_rec(run, lambda r: r.update({"generation_status": "provisional"}))
        self.assertEqual(self.verify(run, "live", log)["verdict"], "INCOMPLETE")

    def test_unknown_surface_and_model_stay_unknown(self):
        self.fetch()
        inp = json.loads((self.run_dir() / "input.json").read_text(encoding="utf-8"))
        self.assertEqual((inp["target_surface"], inp["model_id"]), ("unknown", "unknown"))
        with self.assertRaises(ev.ContractError):
            self.fetch("run-b", target_surface="")


# =========================================================== files and structure
class TestFilesAndStructure(Base):
    def test_existing_run_is_never_overwritten(self):
        self.fetch()
        marker = self.run_dir() / "marker.txt"
        marker.write_text("keep", encoding="utf-8")
        before = (self.run_dir() / "acquisition.json").read_bytes()
        with self.assertRaises(ev.ContractError):
            self.fetch()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual((self.run_dir() / "acquisition.json").read_bytes(), before)

    def test_unsafe_run_ids_and_paths(self):
        for name in ("..", ".hidden", "a b", "x/../y", "a;b"):
            with self.subTest(name):
                with self.assertRaises((ev.ContractError, ValueError, FileNotFoundError, OSError)):
                    ev.do_fetch(self.make_input("run-x"), self.tmp / "outputs" / name, "live")
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "o.md").write_text("x", encoding="utf-8")
        for path in (str(outside / "o.md"), "../../etc/hosts", "missing.md"):
            with self.subTest(original=path), self.assertRaises(ev.ContractError):
                self.fetch("run-y", original_path=path)
        link = self.tmp / "link.md"
        symlink(link, outside / "o.md")
        with self.assertRaises(ev.ContractError):
            self.fetch("run-z", original_path="link.md")

    def test_snapshot_paths_cannot_escape_the_run(self):
        run, log = self.good_run()
        (self.tmp / "outside.bin").write_bytes(b"x")
        for rel in ("../../outside.bin", str(self.tmp / "outside.bin")):
            with self.subTest(rel):
                self.rewrite_acq(run, lambda a, rel=rel: a["sources"][0].update({"raw_path": rel}))
                rep = self.verify(run, "live", log)
                self.assertEqual(rep["verdict"], "FAIL")
                self.assertIn("snapshot_missing_or_unsafe", codes(rep))
        run2, log2 = self.good_run("run-2")
        raw = run2 / "snapshots/doc-a/raw.bin"
        data = raw.read_bytes()
        raw.unlink()
        (self.tmp / "real.bin").write_bytes(data)
        symlink(raw, self.tmp / "real.bin")
        self.assertIn("snapshot_missing_or_unsafe", codes(self.verify(run2, "live", log2)))
        run3, log3 = self.good_run("run-3")  # a symlink that stays inside the run is refused as well
        raw3 = run3 / "snapshots/doc-a/raw.bin"
        (run3 / "snapshots/doc-a/copy.bin").write_bytes(raw3.read_bytes())
        raw3.unlink()
        symlink(raw3, run3 / "snapshots/doc-a/copy.bin")
        self.assertIn("snapshot_missing_or_unsafe", codes(self.verify(run3, "live", log3)))

    def test_unknown_keys_types_enums_and_duplicates(self):
        run, log = self.good_run()
        mutations = {
            "unknown acquisition key": lambda a: a.update({"extra": 1}),
            "unknown entry key": lambda a: a["sources"][0].update({"extra": 1}),
            "wrong type": lambda a: a["sources"][0].update({"http_status": "200"}),
            "bad status": lambda a: a["sources"][0].update({"status": "weird"}),
            "bad method": lambda a: a["sources"][0].update({"method": "curl"}),
            "bad timestamp": lambda a: a.update({"started_at": "yesterday"}),
            "duplicate source": lambda a: (a["sources"].append(dict(a["sources"][0])), a["selected_sources"].append("doc-a")),
        }
        good = (run / "acquisition.json").read_text(encoding="utf-8")
        for name, fn in mutations.items():
            with self.subTest(name):
                (run / "acquisition.json").write_text(good, encoding="utf-8")
                self.rewrite_acq(run, fn)
                rep = self.verify(run, "live", log)
                self.assertEqual(rep["verdict"], "FAIL")
                self.assertIn("invalid_structure", codes(rep))
        (run / "acquisition.json").write_text(good, encoding="utf-8")
        rec_mut = {"duplicate change id": lambda r: r["changes"][1].update({"change_id": "c1"}),
                   "bad kind": lambda r: r["changes"][0].update({"kind": "guess"}),
                   "editorial with source": lambda r: r["changes"][0].update({"rule_id": "rule-verify"}),
                   "official without source": lambda r: r["changes"][2].update({"source_id": None}),
                   "unknown key": lambda r: r.update({"verified": True}),
                   "bad status": lambda r: r.update({"generation_status": "verified"})}
        good_rec = (run / "recommendations.json").read_text(encoding="utf-8")
        for name, fn in rec_mut.items():
            with self.subTest(name):
                (run / "recommendations.json").write_text(good_rec, encoding="utf-8")
                self.rewrite_rec(run, fn)
                self.assertIn("invalid_structure", codes(self.verify(run, "live", log)))

    def test_registry_validation_rejects_bad_registries(self):
        good = json.loads((self.tmp / "registry.json").read_text(encoding="utf-8"))
        def mutate(fn):
            r = json.loads(json.dumps(good))
            fn(r)
            return r
        bad = {
            "unknown source key": lambda r: r["sources"][0].update({"x": 1}),
            "http url": lambda r: r["sources"][0].update({"allowed_urls": ["http://docs.example.com/guide"]}),
            "userinfo url": lambda r: r["sources"][0].update({"allowed_urls": ["https://u:p@docs.example.com/guide"]}),
            "one locator": lambda r: r["sources"][0].update({"body_locators": ["only"]}),
            "bad extraction": lambda r: r["sources"][0].update({"extraction": "regex-all"}),
            "path-like source id": lambda r: r["sources"][0].update({"source_id": "../x"}),
            "duplicate source id": lambda r: r["sources"].append(dict(r["sources"][0])),
            "rule unknown source": lambda r: r["rules"][0].update({"source_id": "ghost"}),
            "duplicate rule": lambda r: r["rules"].append(dict(r["rules"][0])),
            "reviewed without hash": lambda r: r["rules"][0].update({"source_body_sha256": None}),
            "bad purpose": lambda r: r["rules"][0].update({"purposes": ["everything"]}),
            "missing target": lambda r: r["targets"].pop(),
            "target source not scoped": lambda r: r["targets"][0].update({"required_sources": ["doc-a"]}),
            "bad schema_version": lambda r: r.update({"schema_version": 2}),
        }
        for name, fn in bad.items():
            with self.subTest(name), self.assertRaises(ev.ContractError):
                ev.validate_registry(mutate(fn))
        ev.validate_registry(mutate(lambda r: None))

    def test_input_validation(self):
        for name, over in (("unknown key", {"extra": 1}), ("bad target", {"target_id": "gemini-agent"}),
                           ("bad purpose", {"purpose": "chat"}), ("bad host", {"execution_host": "my-own-agent"}),
                           ("bad constraints", {"constraints": "text"}), ("bad version", {"schema_version": "1"})):
            with self.subTest(name), self.assertRaises(ev.ContractError):
                self.fetch(f"run-{name[:3]}{len(name)}", **over)

    def test_cli_exit_codes(self):
        self.make_input("run-c")
        code, out, err = self.cli("fetch", "--input", "input-run-c.json", "--out", "outputs/run-c", "--mode", "live")
        self.assertEqual(code, 0)
        code, _, err = self.cli("fetch", "--input", "input-run-c.json", "--out", "outputs/run-c", "--mode", "live")
        self.assertEqual(code, 1)
        code, _, _ = self.cli("fetch", "--input", "nope.json", "--out", "outputs/run-d", "--mode", "live")
        self.assertEqual(code, 2)
        code, _, _ = self.cli("fetch", "--input", "input-run-c.json", "--out", "outputs/run-e", "--mode", "cache")
        self.assertEqual(code, 1)
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            ev.main(["verify", "--run", "x"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.cli("verify", "--run", "outputs/none", "--mode", "live")[0], 2)

    def test_out_directory_outside_the_workspace_is_refused(self):
        outside = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        self.make_input("run-o")
        code, out, err = self.cli("fetch", "--input", "input-run-o.json", "--out", str(outside / "run-o"), "--mode", "live")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("inside the workspace", err)
        self.assertFalse((outside / "run-o").exists())


# =========================================================== semantic review (assess)
class AssessBase(Base):
    def setUp(self):
        super().setUp()
        self.run, self.log = self.good_run()
        self.rubric = ev.rubric_version()

    def calibration(self, method="human", **over):
        labels = [{"case_id": f"k{i}", "human_label": lab, "human_by": "reviewer-a",
                   "scorer_label": None if method == "human" else lab}
                  for i, lab in enumerate(("supported", "contradicted", "supported"))]
        cal = {"schema_version": 1, "reviewer_id": "reviewer-a" if method == "human" else "model-judge", "model_id": None if method == "human" else "model-m",
               "rubric_version": self.rubric, "case_set_sha256": ev.case_set_hash(labels), "labels": labels,
               "counts": {"agree": 0, "disagree": 0, "undetermined": 3} if method == "human" else {"agree": 3, "disagree": 0, "undetermined": 0},
               "tolerance": {"min_agreement": None if method == "human" else 0.9, "max_undetermined": 0, "min_cases": 3},
               "disagreement_handling": "severe disagreements are re-judged by a person", "created_at": "2026-10-14T00:00:00Z"}
        cal.update(over)
        p = self.tmp / f"cal-{method}.json"
        p.write_text(json.dumps(cal), encoding="utf-8")
        return p

    def review(self, method="human", cal=None, mutate=None, **over):
        rec = json.loads((self.run / "recommendations.json").read_text(encoding="utf-8"))
        inp = json.loads((self.run / "input.json").read_text(encoding="utf-8"))
        acq = json.loads((self.run / "acquisition.json").read_text(encoding="utf-8"))
        checks, n = [], 0

        def add(cat, cid, judgment="supported", osp=None, nsp=None, src=None, lines=None, ex=None):
            nonlocal n
            n += 1
            checks.append({"check_id": f"k{n}", "change_id": cid, "category": cat, "judgment": judgment, "original_span": osp,
                           "optimized_span": nsp, "source_id": src, "source_lines": lines, "excerpt": ex, "reason": "reviewed"})
        for c in rec["changes"]:
            sp = dict(osp=[c["before_start"], c["before_end"]], nsp=[c["after_start"], c["after_end"]])
            add("scope", c["change_id"], **sp)
            if c["source_id"]:
                add("source-support", c["change_id"], src=c["source_id"], lines=[c["source_start_line"], c["source_end_line"]],
                    ex=c["exact_excerpt"], **sp)
        for _ in inp["constraints"]:
            add("intent-preservation", None)
        add("unrequested-additions", None)
        method_cal = cal if cal is not None else self.calibration("human" if method == "human" else "model")
        rv = {"schema_version": 1, "original_sha256": rec["original_sha256"], "optimized_sha256": rec["optimized_sha256"],
              "registry_sha256": acq["registry_sha256"], "source_hashes": {"doc-a": self.entry(self.run)["body_sha256"]},
              "rubric_version": self.rubric, "reviewer_id": "reviewer-a" if method == "human" else "model-judge",
              "review_method": method, "model_id": None if method == "human" else "model-m",
              "observed_execution_ref": None if method == "human" else str(self.write_log(self.run, text="     1\tjudge session", path=self.tmp / "judge.jsonl")),
              "calibration_ref": str(method_cal), "calibration_sha256": ev.sha_file(method_cal),
              "checked_at": "2026-10-14T12:30:00Z", "checks": checks, "status": "PASS"}
        rv.update(over)
        if mutate:
            mutate(rv)
        p = self.run / "semantic-review.json"
        p.write_text(json.dumps(rv, ensure_ascii=False), encoding="utf-8")
        return p

    def assess(self, p, **kw):
        return ev.assess(self.run, p, **kw)

    def set_check(self, category, judgment, nth=0, status=None):
        def f(rv):
            hits = [c for c in rv["checks"] if c["category"] == category]
            hits[nth]["judgment"] = judgment
            if status:
                rv["status"] = status
        return f


class TestAssess(AssessBase):
    def test_human_review_with_human_calibration_passes(self):
        out = self.assess(self.review())
        self.assertEqual(out["verdict"], "PASS", out["errors"])
        self.assertEqual(out["review_method"], "human")
        self.assertEqual(json.loads((self.run / "semantic-verification.json").read_text(encoding="utf-8"))["verdict"], "PASS")
        self.assertEqual(self.cli("assess", "--run", str(self.run), "--review", str(self.run / "semantic-review.json"))[0], 0)

    def test_nothing_reviewed_is_incomplete_not_pass(self):
        stub = self.run / "semantic-review.json"
        self.assertEqual(json.loads(stub.read_text(encoding="utf-8"))["status"], "NOT_REVIEWED")
        out = self.assess(stub)
        self.assertEqual(out["verdict"], "INCOMPLETE")
        self.assertEqual(self.cli("assess", "--run", str(self.run), "--review", str(stub))[0], 2)

    def test_changed_numbers_negation_or_exception_fail(self):
        for name, cat in (("number or exception changed", "intent-preservation"), ("added tool for a light chat", "unrequested-additions")):
            with self.subTest(name):
                out = self.assess(self.review(mutate=self.set_check(cat, "contradicted", status="FAIL")))
                self.assertEqual(out["verdict"], "FAIL")
                self.assertIn("contradicted", codes(out))
                self.assertEqual(self.cli("assess", "--run", str(self.run), "--review", str(self.run / "semantic-review.json"))[0], 1)

    def test_a_single_contradiction_is_not_averaged_away(self):
        out = self.assess(self.review(mutate=self.set_check("source-support", "contradicted", status="FAIL")))
        self.assertEqual(out["verdict"], "FAIL")
        self.assertGreater(sum(c["judgment"] == "supported" for c in json.loads((self.run / "semantic-review.json").read_text(encoding="utf-8"))["checks"]), 5)

    def test_insufficient_is_never_a_pass(self):
        out = self.assess(self.review(mutate=self.set_check("scope", "insufficient", status="INCOMPLETE")))
        self.assertEqual(out["verdict"], "INCOMPLETE")
        out = self.assess(self.review(mutate=self.set_check("scope", "insufficient", status="PASS")))
        self.assertEqual(out["verdict"], "INCOMPLETE")  # a PASS claim over an undecided check does not make it a pass
        self.assertIn("claimed_status_overstated", codes(out))
        out = self.assess(self.review(mutate=lambda rv: rv.update({"status": "FAIL"})))
        self.assertEqual(out["verdict"], "FAIL")  # claiming a failure no check shows is a contract violation
        self.assertIn("claimed_status_mismatch", codes(out))

    def test_missing_checks_are_incomplete(self):
        def drop(rv):
            rv["checks"] = [c for c in rv["checks"] if c["category"] != "scope"]
        out = self.assess(self.review(mutate=drop))
        self.assertEqual(out["verdict"], "INCOMPLETE")
        self.assertIn("missing_check", codes(out))
        out = self.assess(self.review(mutate=lambda rv: rv.update({"checks": [c for c in rv["checks"] if c["category"] != "unrequested-additions"]})))
        self.assertEqual(out["verdict"], "INCOMPLETE")
        out = self.assess(self.review(mutate=lambda rv: rv.update({"checks": [c for c in rv["checks"] if c["category"] != "intent-preservation"][:]})))
        self.assertEqual(out["verdict"], "INCOMPLETE")

    def test_review_of_another_artifact_fails(self):
        for key in ("original_sha256", "optimized_sha256", "registry_sha256"):
            with self.subTest(key):
                out = self.assess(self.review(**{key: "3" * 64}))
                self.assertEqual(out["verdict"], "FAIL")
        out = self.assess(self.review(source_hashes={"doc-a": "4" * 64}))
        self.assertIn("source_hash_mismatch", codes(out))
        out = self.assess(self.review(rubric_version="0.1"))
        self.assertIn("rubric_version_mismatch", codes(out))

    def test_invented_quotes_and_spans_fail(self):
        def quote(rv):
            next(c for c in rv["checks"] if c["category"] == "source-support").update({"excerpt": "a sentence the page never says"})
        self.assertIn("fabricated_quote", codes(self.assess(self.review(mutate=quote))))
        def lines(rv):
            next(c for c in rv["checks"] if c["category"] == "source-support").update({"source_lines": [1, 1]})
        self.assertIn("fabricated_quote", codes(self.assess(self.review(mutate=lines))))
        def span(rv):
            next(c for c in rv["checks"] if c["category"] == "scope").update({"original_span": [0, 1]})
        self.assertEqual(self.assess(self.review(mutate=span))["verdict"], "FAIL")
        def unknown(rv):
            rv["checks"][0]["change_id"] = "ghost"
        self.assertIn("unknown_change", codes(self.assess(self.review(mutate=unknown))))

    def test_mock_and_self_scoring_are_not_production_passes(self):
        mock_review = self.review("mock", cal=self.calibration("model"))
        self.assertEqual(self.assess(mock_review)["verdict"], "INCOMPLETE")
        self.assertIn("mock_not_production", codes(self.assess(mock_review)))
        self.assertEqual(self.assess(mock_review, allow_mock=True)["verdict"], "PASS")  # offline tests only
        self.assertEqual(self.cli("assess", "--run", str(self.run), "--review", str(mock_review))[0], 2)
        # a "separate" review whose execution log is the generation session's own log
        self.verify(self.run, "live", self.log)
        same = self.review("separate-context", cal=self.calibration("model"),
                           observed_execution_ref=str(self.log))
        out = self.assess(same)
        self.assertEqual(out["verdict"], "INCOMPLETE")
        self.assertIn("not_separate_context", codes(out))
        none = self.review("separate-context", cal=self.calibration("model"), observed_execution_ref=None)
        self.assertIn("execution_not_observed", codes(self.assess(none)))
        inside = self.write_log(self.run, path=self.run / "judge-claimed.jsonl")
        self.assertEqual(self.assess(self.review("separate-context", cal=self.calibration("model"),
                                                 observed_execution_ref=str(inside)))["verdict"], "FAIL")

    def test_separate_context_review_with_calibrated_model_passes(self):
        self.verify(self.run, "live", self.log)
        out = self.assess(self.review("separate-context", cal=self.calibration("model")))
        self.assertEqual(out["verdict"], "PASS", out["errors"])
        self.assertEqual(out["calibration"], {"cases": 3, "agree": 3, "disagree": 0, "undetermined": 0})

    def test_calibration_is_required_matching_and_honest(self):
        none = self.review(calibration_ref=None, calibration_sha256=None)
        out = self.assess(none)
        self.assertEqual(out["verdict"], "INCOMPLETE")
        self.assertIn("uncalibrated", codes(out))
        bad_hash = self.review(calibration_sha256="5" * 64)
        self.assertIn("calibration_hash_mismatch", codes(self.assess(bad_hash)))
        other = self.calibration("human", reviewer_id="someone-else")
        self.assertIn("calibration_other_scorer", codes(self.assess(self.review(cal=other))))
        other_rubric = self.calibration("human", rubric_version="9.9")
        self.assertIn("calibration_other_scorer", codes(self.assess(self.review(cal=other_rubric))))
        labels = [{"case_id": f"k{i}", "human_label": "supported", "human_by": "reviewer-a", "scorer_label": "supported"} for i in range(3)]
        faked = self.calibration("human", labels=labels, case_set_sha256=ev.case_set_hash(labels))
        self.assertIn("human_calibration_fabricated_agreement", codes(self.assess(self.review(cal=faked))))
        wrong_set = self.calibration("human", case_set_sha256="6" * 64)
        self.assertIn("calibration_case_set_mismatch", codes(self.assess(self.review(cal=wrong_set))))

    def test_model_calibration_disagreement_blocks_pass(self):
        self.verify(self.run, "live", self.log)
        labels = [{"case_id": f"k{i}", "human_label": h, "human_by": "reviewer-a", "scorer_label": s}
                  for i, (h, s) in enumerate((("contradicted", "supported"), ("supported", "supported"), ("supported", "supported")))]
        cal = self.calibration("model", labels=labels, case_set_sha256=ev.case_set_hash(labels),
                               counts={"agree": 2, "disagree": 1, "undetermined": 0})
        out = self.assess(self.review("separate-context", cal=cal))
        self.assertEqual(out["verdict"], "INCOMPLETE")
        self.assertIn("scorer_not_calibrated", codes(out))
        liar = self.calibration("model", counts={"agree": 3, "disagree": 0, "undetermined": 1})
        self.assertIn("calibration_counts_mismatch", codes(self.assess(self.review("separate-context", cal=liar))))


# =========================================================== effect comparison (compare)
HELD = ("h1", "h2", "h3", "h4")


class CompareBase(Base):
    def setUp(self):
        super().setUp()
        self.run, _ = self.good_run()
        self.rubric, self.reg_sha = ev.rubric_version(), ev.load_registry()[1]
        self.cases = self.tmp / "evals" / "prompt-tailor-cases.jsonl"
        self.res = self.tmp / "res"
        self.res.mkdir()
        self.make_cases()

    def make_cases(self, held=HELD, dev=("d1",), **over):
        rows = []
        for cid, split in [(c, "held-out") for c in held] + [(c, "development") for c in dev]:
            d = self.tmp / "evals" / "cases" / cid
            d.mkdir(parents=True, exist_ok=True)
            row = {"case_id": cid, "split": split, "target_id": "claude-code", "target_surface": "unknown", "model_id": "unknown",
                   "requirements": ["keep the public API"], "rubric": ["tests pass"], "status": "ready"}
            for k, text in (("input", f"state of {cid}"), ("original", f"original prompt {cid}"), ("optimized", f"optimized prompt {cid}")):
                (d / f"{k}.md").write_text(text, encoding="utf-8")
                row.update({f"{k}_ref": f"cases/{cid}/{k}.md", f"{k}_sha256": ev.sha_file(d / f"{k}.md")})
            row.update(over)
            rows.append(row)
        self.cases.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        self.case_rows = {r["case_id"]: r for r in rows}

    def plan(self, **over):
        p = {"schema_version": 1, "plan_id": "plan-1", "target_id": "claude-code", "target_surface": "unknown", "model_id": "unknown",
             "registry_sha256": self.reg_sha, "rubric_version": self.rubric, "created_at": "2026-10-14T13:00:00Z",
             "cases": [{"case_id": c, "split": "held-out"} for c in HELD] + [{"case_id": "d1", "split": "development"}],
             "repeats": {"original": 2, "optimized": 2}, "budget": {"max_runs": 100, "max_total_seconds": 100000},
             "stop_conditions": ["stop when the run budget is reached"], "execution_conditions": ["same files, tools and model"],
             "metrics": [{"name": "success", "direction": "higher", "method": "code"}],
             "scorers": [{"scorer_id": "unit-check", "method": "code", "rubric_version": None, "calibration_ref": None, "calibration_sha256": None}],
             "adoption": {"primary_metric": "success", "min_primary_improvement": 0.2, "max_critical_violations": 0, "min_cases": 3,
                          "max_loss_ratio": 0.25, "max_uncertain_ratio": 0.25, "max_time_increase_ratio": 0.5, "tie_margin": 0.05}}
        p.update(over)
        self.plan_path = self.tmp / "eval-plan.json"
        self.plan_path.write_text(json.dumps(p), encoding="utf-8")
        self.plan_data = p
        return p

    def rows(self, fn=None, drop=lambda r: False, cases=None):
        """fn(case, variant, repeat) -> field overrides; default: original succeeds 1 of 2, optimized 2 of 2."""
        plan_sha = ev.sha_file(self.plan_path)
        out = []
        for c in cases or [*HELD, "d1"]:
            for variant in ("original", "optimized"):
                for rep in range(2):
                    base = {"success": (1.0 if rep == 0 else 0.0) if variant == "original" else 1.0}
                    over = (fn(c, variant, rep) if fn else {}) or {}
                    ex, art = self.res / f"{c}-{variant}-{rep}.log", self.res / f"{c}-{variant}-{rep}.out"
                    ex.write_text(f"execution {c} {variant} {rep}", encoding="utf-8")
                    art.write_text(f"artifact {c} {variant} {rep}", encoding="utf-8")
                    t0 = datetime(2026, 10, 14, 13, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=rep)
                    secs = 10 if variant == "original" else 11
                    row = {"case_id": c, "variant": variant, "repeat_id": f"r{rep}", "target_id": "claude-code", "target_surface": "unknown",
                           "model_id": "unknown", "plan_sha256": plan_sha, "prompt_sha256": self.case_rows[c][f"{variant}_sha256"],
                           "input_sha256": self.case_rows[c]["input_sha256"], "started_at": t0.strftime(ev.TS_FMT),
                           "finished_at": (t0 + timedelta(seconds=secs)).strftime(ev.TS_FMT), "outcome": "completed",
                           "execution_ref": ex.name, "execution_sha256": ev.sha_file(ex), "artifact_ref": art.name,
                           "artifact_sha256": ev.sha_file(art), "metrics": base, "critical_violations": 0,
                           "scoring": {"method": "code", "scorer_id": "unit-check", "rubric_version": None, "calibration_sha256": None,
                                       "blind": False, "swapped_consistent": None}, "order": rep, "mock": False}
                    row.update(over)
                    if not drop(row):
                        out.append(row)
        self.results = self.res / "eval-results.jsonl"
        self.results.write_text("\n".join(json.dumps(r) for r in out) + "\n", encoding="utf-8")
        return out

    def compare(self, **kw):
        return ev.compare(self.run, self.plan_path, self.results, self.cases, **kw)


class TestCompare(CompareBase):
    def test_normal_pairs_improve_on_the_test_set_and_exit_0(self):
        self.plan()
        self.rows()
        code, e = self.compare()
        self.assertEqual((code, e["status"]), (0, "IMPROVED_ON_TEST_SET"), e)
        self.assertEqual((e["wins"], e["losses"], e["cases_evaluated"], e["cases_missing"]), (4, 0, 5, 0))
        self.assertAlmostEqual(e["differences"]["success"], 0.5)
        self.assertGreater(len(e["execution_artifacts"]), 0)
        self.assertIn("not a general", e["scope"])
        self.assertEqual(json.loads((self.run / "evaluation.json").read_text(encoding="utf-8"))["status"], "IMPROVED_ON_TEST_SET")
        code, out, _ = self.cli("compare", "--run", str(self.run), "--plan", str(self.plan_path), "--results", str(self.results), "--cases", str(self.cases))
        self.assertEqual(code, 0)

    def test_regression_tie_timeout_and_missing(self):
        self.plan()
        scenarios = {
            "regressed": (lambda c, v, r: {"metrics": {"success": 0.0}} if v == "optimized" else {"metrics": {"success": 1.0}}, "REGRESSED", 1),
            "no clear improvement": (lambda c, v, r: {"metrics": {"success": 0.5}}, "NO_CLEAR_IMPROVEMENT", 1),
            "timeouts are failures, not deleted": (lambda c, v, r: {"outcome": "timeout", "metrics": {"success": None}} if v == "optimized" else {}, "REGRESSED", 1),
        }
        for name, (fn, status, exit_code) in scenarios.items():
            with self.subTest(name):
                self.rows(fn)
                code, e = self.compare()
                self.assertEqual((code, e["status"]), (exit_code, status), e)
        with self.subTest("missing pair"):
            self.rows(drop=lambda r: r["case_id"] == "h2" and r["variant"] == "optimized")
            code, e = self.compare()
            self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))
            self.assertEqual(e["cases_missing"], 1)
        with self.subTest("not_run is not a failure"):
            self.rows(lambda c, v, r: {"outcome": "not_run", "metrics": {"success": None}} if (c, v) == ("h1", "optimized") else {})
            code, e = self.compare()
            self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))

    def test_mixed_results_keep_the_original_when_improvement_is_unclear(self):
        self.plan()
        self.rows(lambda c, v, r: {"metrics": {"success": 1.0 if v == "optimized" else 0.7}} if c != "h4" else
                  {"metrics": {"success": 0.0 if v == "optimized" else 1.0}})
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertNotEqual(e["status"], "IMPROVED_ON_TEST_SET")
        self.assertEqual(e["losses"], 1)

    def test_critical_violation_is_not_offset_by_a_better_average(self):
        self.plan()
        self.rows(lambda c, v, r: {"critical_violations": 1} if (c, v, r) == ("h1", "optimized", 0) else {})
        code, e = self.compare()
        self.assertEqual((code, e["status"]), (1, "REGRESSED"))
        self.assertEqual(e["critical_violations"]["optimized"], 1)
        self.assertGreater(e["differences"]["success"], 0.4)

    def test_conditions_must_match_the_plan(self):
        self.plan()
        for name, fn in (("api result for a ui target", lambda c, v, r: {"target_surface": "api"}),
                         ("other model", lambda c, v, r: {"model_id": "model-y"}),
                         ("prompt hash differs from the case", lambda c, v, r: {"prompt_sha256": "7" * 64}),
                         ("plan changed afterwards", lambda c, v, r: {"plan_sha256": "8" * 64})):
            with self.subTest(name):
                self.rows(fn)
                code, e = self.compare()
                self.assertEqual(code, 1, e)
                self.assertTrue(e["contract_violations"])
                self.assertNotEqual(e["status"], "IMPROVED_ON_TEST_SET")
        self.plan(target_surface="api")  # plan for another surface than the run
        self.rows(lambda c, v, r: {"target_surface": "api"})
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertIn("surfaces/models", " ".join(e["contract_violations"]))

    def test_plan_edited_after_results_are_recorded(self):
        self.plan()
        self.rows()
        self.plan(adoption={**self.plan_data["adoption"], "min_primary_improvement": 0.01})
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertTrue(any("plan_sha256" in v for v in e["contract_violations"]))

    def test_plan_must_be_frozen_before_runs_start(self):
        self.plan(created_at="2026-10-14T14:00:00Z")
        self.rows()
        code, e = self.compare()
        self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))
        self.assertTrue(any("frozen" in r for r in e["incomplete_reasons"]))

    def test_held_out_cases_cannot_be_mixed_with_development(self):
        self.plan()
        self.rows()
        self.make_cases(held=("h1", "h2", "h3"), dev=("d1", "h4"))  # h4 was moved to development after tuning
        self.rows()
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertTrue(any("held-out mixing" in v for v in e["contract_violations"]))
        self.make_cases()
        self.plan(cases=[{"case_id": c, "split": "development"} for c in HELD] + [{"case_id": "d1", "split": "development"}])
        self.rows()
        self.assertEqual(self.compare()[0], 1)
        # development-only evidence can never decide adoption
        self.make_cases(held=(), dev=("d1", "d2"))
        self.plan(cases=[{"case_id": "d1", "split": "development"}, {"case_id": "d2", "split": "development"}])
        self.rows(cases=["d1", "d2"])
        code, e = self.compare()
        self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))

    def test_repeats_are_not_independent_cases(self):
        self.plan(adoption={**self.plan()["adoption"], "min_cases": 5})
        self.rows()
        code, e = self.compare()  # 4 held-out cases x 2 repeats = 8 runs per arm, still only 4 cases
        self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))
        self.plan()
        rows = self.rows()
        rows.append(dict(rows[0]))  # same (case, variant, repeat) counted twice
        self.results.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertTrue(any("duplicate" in v for v in e["contract_violations"]))

    def test_budget_overrun_and_early_stop(self):
        self.plan(budget={"max_runs": 19, "max_total_seconds": 100000})
        self.rows()
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertTrue(any("budget" in v for v in e["contract_violations"]))
        self.assertTrue(any("budget" in r for r in e["incomplete_reasons"]))
        self.plan(budget={"max_runs": 1000, "max_total_seconds": 5})
        self.rows()
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertTrue(any("max_total_seconds" in v for v in e["contract_violations"]))

    def test_time_cost_is_part_of_the_adoption_criteria(self):
        self.plan(adoption={**self.plan()["adoption"], "max_time_increase_ratio": 0.01})
        self.rows()
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertEqual(e["status"], "NO_CLEAR_IMPROVEMENT")

    def test_missing_evidence_files_or_plan_are_not_evaluated(self):
        self.plan()
        self.rows()
        (self.res / "h1-optimized-0.log").unlink()
        code, e = self.compare()
        self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))
        self.rows()
        (self.res / "h1-original-0.out").write_text("tampered", encoding="utf-8")
        code, e = self.compare()
        self.assertEqual(code, 1)
        self.assertTrue(any("hash mismatch" in v for v in e["contract_violations"]))
        code, e = ev.compare(self.run, self.tmp / "none.json", self.tmp / "none.jsonl", self.cases)
        self.assertEqual((code, e["status"]), (2, "NOT_EVALUATED"))
        self.assertEqual(self.cli("compare", "--run", str(self.run), "--plan", "none.json", "--results", "none.jsonl")[0], 2)

    def test_mock_results_are_offline_only(self):
        self.plan()
        self.rows(lambda c, v, r: {"mock": True})
        code, e = self.compare()
        self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))
        code, e = self.compare(allow_mock=True)
        self.assertEqual((code, e["status"]), (0, "IMPROVED_ON_TEST_SET"))

    def test_free_form_scoring_needs_calibration_blinding_and_a_reversed_check(self):
        cal = self.tmp / "cal.json"
        cal.write_text("{}", encoding="utf-8")
        scorer = {"scorer_id": "judge", "method": "rubric", "rubric_version": self.rubric, "calibration_ref": str(cal),
                  "calibration_sha256": ev.sha_file(cal)}
        metrics = [{"name": "success", "direction": "higher", "method": "rubric"}]
        self.plan(scorers=[scorer], metrics=metrics)
        free = {"method": "rubric", "scorer_id": "judge", "rubric_version": self.rubric, "calibration_sha256": scorer["calibration_sha256"]}
        self.rows(lambda c, v, r: {"scoring": {**free, "blind": True, "swapped_consistent": True}})
        self.assertEqual(self.compare()[1]["status"], "IMPROVED_ON_TEST_SET")
        self.rows(lambda c, v, r: {"scoring": {**free, "blind": False, "swapped_consistent": True}})
        self.assertEqual(self.compare()[1]["status"], "INCOMPLETE")
        self.rows(lambda c, v, r: {"scoring": {**free, "blind": True, "swapped_consistent": None}})
        self.assertEqual(self.compare()[1]["status"], "INCOMPLETE")
        self.rows(lambda c, v, r: {"scoring": {**free, "calibration_sha256": "9" * 64, "blind": True, "swapped_consistent": True}})
        self.assertEqual(self.compare()[1]["status"], "INCOMPLETE")  # another artifact's calibration is not reused
        unc = {**scorer, "calibration_ref": None, "calibration_sha256": None}
        self.rows(lambda c, v, r: {"order": 0, "scoring": {**free, "blind": True, "swapped_consistent": True}})
        code, e = self.compare()  # optimized output always shown first: position bias
        self.assertEqual(e["status"], "INCOMPLETE")
        self.assertTrue(any("order" in r for r in e["incomplete_reasons"]))
        self.plan(scorers=[unc], metrics=metrics)
        self.rows(lambda c, v, r: {"scoring": {**free, "calibration_sha256": None, "blind": True, "swapped_consistent": True}})
        self.assertEqual(self.compare()[1]["status"], "INCOMPLETE")
        self.plan(scorers=[scorer], metrics=metrics)
        self.rows(lambda c, v, r: {"scoring": {**free, "blind": True, "swapped_consistent": (c != "h1")}})
        code, e = self.compare()
        self.assertEqual(e["uncertain"], 1)  # order-dependent judgements stay uncertain, not wins

    def test_plan_for_another_registry_or_rubric_is_incomplete(self):
        self.plan(registry_sha256="a" * 64)
        self.rows()
        self.assertEqual(self.compare()[1]["status"], "INCOMPLETE")
        self.plan(rubric_version="0.0")
        self.rows()
        self.assertEqual(self.compare()[1]["status"], "INCOMPLETE")

    def test_incomplete_case_requirements_block_evaluation(self):
        self.plan()
        self.make_cases(status="pending-review")
        self.rows()
        code, e = self.compare()
        self.assertEqual((code, e["status"]), (2, "INCOMPLETE"))


# =========================================================== drift
class TestDrift(Base):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(ev, "RETRY_WAIT", 0)
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def answers(*replies):
        """A source that gives these replies in turn and then keeps giving the last one."""
        queue = list(replies)
        return lambda: queue.pop(0) if len(queue) > 1 else queue[0]

    def drift(self, *argv):
        code, out, err = self.cli("drift", *argv)
        return code, json.loads(out), err

    @staticmethod
    def states(rep):
        return {r["rule_id"]: r["state"] for r in rep["rules"]}

    def test_live_is_current_when_nothing_changed(self):
        code, rep, err = self.drift()
        self.assertEqual((code, rep["verdict"], rep["mode"]), (0, "CURRENT", "live"))
        self.assertIn("CURRENT", err)
        st = self.states(rep)
        self.assertEqual((st["rule-verify"], st["rule-unreviewed"]), ("current", "unreviewed"))
        self.assertEqual([s["source_id"] for s in rep["sources"]], ["doc-a", "doc-md", "doc-api"])

    def test_changed_body_and_lost_excerpt_are_told_apart(self):
        self.responses[A_URL] = (200, HTML, self.page.replace(b"Give a check it can run", b"Give a check it can run now"))
        code, rep, err = self.drift()
        st = self.states(rep)
        self.assertEqual((code, rep["verdict"]), (2, "DRIFT"))
        self.assertEqual((st["rule-verify"], st["rule-model"], st["rule-api"]), ("changed", "changed", "current"))
        self.assertIn("rule-verify", err)
        self.responses[A_URL] = (200, HTML, self.page.replace(b"Give a check it can run", b"Skip every check you can run"))
        st = self.states(self.drift()[1])
        self.assertEqual((st["rule-verify"], st["rule-model"]), ("excerpt_missing", "changed"))

    def test_blocked_source_is_incomplete_but_drift_still_wins(self):
        self.responses[A_URL] = (503, HTML, b"")
        code, rep, _ = self.drift()
        a = next(s for s in rep["sources"] if s["source_id"] == "doc-a")
        self.assertEqual((code, rep["verdict"], a["status"], a["error"]), (2, "INCOMPLETE", "blocked", "http_503"))
        self.assertEqual(self.states(rep)["rule-verify"], "unverifiable")
        self.responses[API_URL] = (200, HTML, self.page.replace(b"Give a check it can run", b"Give a check it can run now"))
        self.assertEqual(self.drift()[1]["verdict"], "DRIFT")

    def test_source_filter_limits_the_report_and_an_unknown_source_is_refused(self):
        code, rep, _ = self.drift("--source", "doc-md")
        self.assertEqual(([s["source_id"] for s in rep["sources"]], rep["rules"]), (["doc-md"], []))
        code, out, err = self.cli("drift", "--source", "nope")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("unknown source", err)

    def test_run_mode_reads_the_snapshot_without_network(self):
        self.fetch()
        self.calls.clear()
        code, rep, _ = self.drift("--run", str(self.run_dir()))
        self.assertEqual((code, rep["verdict"], rep["mode"], self.calls), (0, "CURRENT", "run", []))
        self.set_registry(lambda r: [x.update({"source_body_sha256": "2" * 64}) for x in r["rules"] if x["rule_id"] == "rule-verify"])
        code, rep, _ = self.drift("--run", str(self.run_dir()))
        self.assertEqual((code, self.states(rep)["rule-verify"]), (2, "changed"))

    def test_run_mode_trusts_a_cache_run_but_not_a_stale_one(self):
        self.fetch("run-1")
        self.clock.advance(hours=2)
        self.fetch("run-2", "cache", self.run_dir("run-1"))
        code, rep, _ = self.drift("--run", str(self.run_dir("run-2")))
        self.assertEqual((code, rep["sources"][0]["status"]), (0, "cache"))
        self.clock.advance(days=8)
        self.fetch("run-3", "cache", self.run_dir("run-1"))
        code, rep, _ = self.drift("--run", str(self.run_dir("run-3")))
        self.assertEqual((code, rep["verdict"], rep["sources"][0]["status"]), (2, "INCOMPLETE", "stale"))

    def test_run_mode_rejects_a_tampered_snapshot(self):
        self.fetch()
        p = self.run_dir() / "snapshots" / "doc-a" / "body.txt"
        p.write_text(p.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        code, out, err = self.cli("drift", "--run", str(self.run_dir()))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("SHA-256", err)

    def test_run_mode_treats_a_blocked_entry_as_unverifiable(self):
        self.responses[A_URL] = (503, HTML, b"")
        self.assertEqual(self.fetch()[0], 2)
        code, rep, _ = self.drift("--run", str(self.run_dir()))
        self.assertEqual((code, rep["verdict"], self.states(rep)["rule-verify"]), (2, "INCOMPLETE", "unverifiable"))

    def test_a_difference_that_is_gone_on_the_second_look_is_not_reported(self):
        changed = (200, HTML, self.page.replace(b"Give a check it can run", b"Give a check it can run now"))
        self.responses[A_URL] = self.answers(changed, (200, HTML, self.page))
        code, rep, _ = self.drift()
        self.assertEqual((code, rep["verdict"], self.calls.count(A_URL)), (0, "CURRENT", 2))

    def test_a_block_that_is_gone_on_the_second_look_is_not_reported(self):
        self.responses[A_URL] = self.answers((503, HTML, b""), (200, HTML, self.page))
        self.assertEqual(self.drift()[1]["verdict"], "CURRENT")

    def test_only_the_source_that_looks_wrong_is_fetched_again(self):
        self.responses[A_URL] = (200, HTML, self.page.replace(b"Give a check it can run", b"Give a check it can run now"))
        self.assertEqual(self.drift()[1]["verdict"], "DRIFT")
        self.assertEqual([self.calls.count(u) for u in (A_URL, MD_URL, API_URL)], [2, 1, 1])


class TestRules(Base):
    """`rules` lists what a run may choose from, so that nobody reads the whole registry (about 140 KB once it held 104 rules)."""
    def listed(self, run_id="run-1", **over):
        code, _ = self.fetch(run_id, **over)
        self.assertEqual(code, 0)
        return [r["rule_id"] for r in ev.applicable_rules(self.run_dir(run_id))["rules"]]

    def test_only_reviewed_rules_in_scope_for_the_run_are_listed(self):
        # claude-code, one-shot, surface and model unknown. Out: rule-model (model-x), rule-surface-api, rule-skill-only (purpose),
        # rule-codex-only (target), rule-unreviewed, and rule-api (its source was not fetched in this run)
        self.assertEqual(self.listed(), ["rule-verify", "rule-inference"])

    def test_surface_model_purpose_and_target_decide(self):
        for name, over, want in (("model", {"model_id": "model-x"}, ["rule-verify", "rule-model", "rule-inference"]),
                                 ("api", {"target_surface": "api", "model_id": "model-x"},
                                  ["rule-verify", "rule-model", "rule-api", "rule-inference", "rule-surface-api"]),
                                 ("skill", {"purpose": "skill-instructions"}, ["rule-verify", "rule-skill-only", "rule-inference"]),
                                 ("codex", {"target_id": "codex"}, ["rule-verify", "rule-codex-only", "rule-inference"])):
            with self.subTest(name):
                self.assertEqual(self.listed(f"run-{name}", **over), want)

    def test_a_registry_changed_since_the_fetch_is_refused(self):
        self.fetch()
        self.set_registry(lambda r: r["rules"].pop())
        with self.assertRaises(ev.ContractError):
            ev.applicable_rules(self.run_dir())

    def test_command_prints_what_is_needed_to_choose_and_cite(self):
        self.fetch()
        code, out, err = self.cli("rules", "--run", str(self.run_dir()))
        rep = json.loads(out)
        self.assertEqual((code, rep["selected_sources"], rep["purpose"]), (0, ["doc-a"], "one-shot"))
        self.assertEqual(set(rep["rules"][0]), {"rule_id", "source_id", "kind", "purposes", "surfaces", "models", "source_excerpt",
                                                "meaning", "allowed_change", "preconditions", "exclusions", "reviewer_id", "human_reviewed"})
        self.assertIn("2 rules, 0 reviewed by a person", err)
        self.assertEqual(self.cli("rules", "--run", str(self.tmp / "no-such-run"))[0], 2)  # not a run directory: not runnable

    def test_each_rule_says_who_reviewed_it_and_only_a_named_person_counts(self):
        self.fetch()
        rep = ev.applicable_rules(self.run_dir())
        self.assertEqual([(r["reviewer_id"], r["human_reviewed"]) for r in rep["rules"]], [("unit-test", False)] * 2)
        self.assertEqual(rep["review"], {"rules": 2, "human_reviewed": 0, "reviewers": {"unit-test": 2}})

        def by_person(reg):
            next(r for r in reg["rules"] if r["rule_id"] == "rule-verify")["reviewer_id"] = "human:Taro"
        self.set_registry(by_person)
        self.fetch("run-h")
        rep = ev.applicable_rules(self.run_dir("run-h"))
        self.assertEqual(rep["review"], {"rules": 2, "human_reviewed": 1, "reviewers": {"human:Taro": 1, "unit-test": 1}})
        self.assertEqual({r["rule_id"]: r["human_reviewed"] for r in rep["rules"]}, {"rule-verify": True, "rule-inference": False})
        self.assertIn("1 reviewed by a person", self.cli("rules", "--run", str(self.run_dir("run-h")))[2])

    def test_a_reviewer_id_counts_as_a_person_only_with_the_prefix_and_a_name(self):
        for reviewer, valid, person in (("human:", False, None), ("human:  ", False, None), ("human:Taro", True, True),
                                        ("humanoid-review", True, False), ("claude-code/x (agent review; not human)", True, False)):
            with self.subTest(reviewer):
                self.set_registry(lambda reg, rid=reviewer: next(r for r in reg["rules"] if r["rule_id"] == "rule-verify").update(reviewer_id=rid))
                if valid:
                    reg, _ = ev.load_registry()
                    self.assertEqual(ev.human_reviewed(next(r for r in reg["rules"] if r["rule_id"] == "rule-verify")), person)
                else:
                    with self.assertRaises(ev.ContractError):
                        ev.load_registry()


class TestSourcesPlan(unittest.TestCase):
    """The plan answers, before any run: which sources would this model, purpose and surface be read against, and why not the others."""
    @classmethod
    def setUpClass(cls):
        cls.reg, cls.sha = ev.load_registry(SKILL / "references" / "registry.json")

    def plan(self, target="claude", surface="api", model="claude-opus-5-5", purpose="agent-instruction", **kw):
        return ev.sources_plan(self.reg, self.sha, {"target_id": target, "target_surface": surface, "model_id": model, "purpose": purpose}, **kw)

    def test_the_guide_of_the_given_model_is_chosen_and_the_others_are_left_out_for_the_model(self):
        rep = self.plan()
        status = {g["source_id"]: g["status"] for g in rep["model"]["guides"]}
        self.assertEqual(status.pop("claude-prompting-opus-5-5"), "selected")
        self.assertEqual((set(status.values()), len(status)), ({"skipped"}, 7))
        self.assertEqual({g["reason"] for g in rep["model"]["guides"] if g["status"] == "skipped"}, {"model_mismatch"})
        self.assertIn("claude-prompting-opus-5-5", [x["source_id"] for x in rep["selected"]])
        self.assertIn("claude-model-prompting", [x["source_id"] for x in rep["selected"]])  # a source for every model stays
        self.assertEqual(rep["model"]["model_specific_sources"], 8)

    def test_without_a_model_or_the_api_surface_no_guide_is_chosen_and_the_reason_says_which(self):
        no_model = self.plan(model="unknown")
        self.assertEqual({g["reason"] for g in no_model["model"]["guides"]}, {"model_unknown"})
        no_surface = self.plan(surface="unknown")
        self.assertEqual({(g["reason"], g["surface_ok"], g["purpose_ok"]) for g in no_surface["model"]["guides"]},
                         {("route_not_matched", False, True)})  # the surface, not the model, keeps them out
        self.assertEqual(self.plan(target="unresolved")["selected"], [])

    def test_a_target_without_model_guides_does_not_depend_on_the_model(self):
        for target in ("chatgpt", "chatgpt-work", "codex", "gemini", "antigravity", "gemini-cli", "google-adk-agent"):
            with self.subTest(target):
                a, b = self.plan(target, "api", "gpt-5"), self.plan(target, "api", "unknown")
                self.assertEqual((a["model"]["model_specific_sources"], a["model"]["guides"]), (0, []))
                self.assertEqual(a["selected"], b["selected"])

    def test_all_models_lists_what_each_model_adds_to_the_common_sources(self):
        limited = {s["source_id"]: s["models"] for s in self.reg["sources"] if s["models"] != ["all"]}
        rep = self.plan(model="unknown", all_models=True)
        self.assertEqual(rep["models"]["added"], {m: [sid] for sid, models in limited.items() for m in models})
        self.assertFalse(set(limited) & set(rep["models"]["base"]))
        by_ui = self.plan(surface="unknown", all_models=True)["models"]["added"]
        self.assertEqual({m: a for m, a in by_ui.items() if a}, {})  # off the API surface, no model changes the sources

    def test_markdown_names_the_guide_the_reason_and_the_model(self):
        text = ev.render_sources(self.plan(model="claude-opus-5-5-20261001", all_models=True))
        self.assertIn("# 見る資料の計画", text)
        self.assertIn("綴りが違うかもしれません。登録されている ID: `claude-opus-5-5`", text)
        self.assertIn("| `claude-prompting-opus-5-5` | `claude-opus-5-5` | モデルが違います(入力: `claude-opus-5-5-20261001`) |", text)
        self.assertIn("| `claude-opus-5-5` | `claude-prompting-opus-5-5` |", text)
        text = ev.render_sources(self.plan("codex", "api", "gpt-5"))
        self.assertIn("モデル別の資料がありません。`model_id` を変えても、選ばれる資料は変わりません", text)

    def test_markdown_says_so_when_no_guide_is_chosen_and_names_each_guide_once(self):
        text = ev.render_sources(self.plan(surface="unknown", model="unknown"))
        self.assertIn("`model_id`=`unknown` で、選ばれたものはありません。", text)
        self.assertEqual(text.count("`claude-prompting-opus-5-5`"), 1)  # in the model section; not again among the sources the route left out
        self.assertIn("モデル別の資料を除く。経路に合わないもの", text)
        self.assertIn("で、選ばれたのは `claude-prompting-opus-5-5` です。", ev.render_sources(self.plan()))


class TestSourcesRun(Base):
    """The report on a run is recomputed from the run's files and the registry; what the agent wrote is only checked against them."""
    def setUp(self):
        super().setUp()
        self.run, self.log = self.good_run()

    def report(self, log="default", **kw):
        return ev.sources_report(self.run, self.log if log == "default" else log, **kw)

    def problem_codes(self, rep):
        return {p["code"] for p in rep["problems"]}

    def test_a_good_run_is_consistent_and_shows_what_was_read_cited_and_who_reviewed_it(self):
        rep = self.report()
        self.assertEqual((rep["verdict"], rep["problems"], rep["registry"]["matches"]), ("CONSISTENT", [], True))
        (row,) = rep["selected"]
        self.assertEqual((row["source_id"], row["route"], row["status"], row["integrity"]), ("doc-a", "required", "live", "ok"))
        self.assertEqual(row["body_sha256"], self.entry(self.run)["body_sha256"])
        self.assertEqual(row["rules"], {"applicable": 2, "cited": ["rule-verify"]})
        self.assertEqual((row["read"]["status"], row["read"]["cited_lines_covered"]), ("observed", True))
        (cite,) = rep["citations"]
        self.assertEqual((cite["rule_id"], cite["reviewer_id"], cite["human_reviewed"], cite["in_scope"]),
                         ("rule-verify", "unit-test", False, True))
        self.assertEqual(rep["review"], {"rules": 1, "human_reviewed": 0, "reviewers": {"unit-test": 1}})

    def test_without_a_log_the_reads_are_unavailable_but_everything_else_is_still_checked(self):
        rep = self.report(None)
        self.assertEqual(rep["verdict"], "CONSISTENT")
        self.assertEqual((rep["selected"][0]["read"]["status"], rep["selected"][0]["read"]["reason"]), ("unavailable", "no_host_log"))
        run, log = self.good_run("run-codex", execution_host="codex")
        rep = ev.sources_report(run, log)  # a Claude Code log is offered to a host whose logs are not understood: not parsed
        self.assertEqual((rep["verdict"], rep["selected"][0]["read"]["reason"]), ("CONSISTENT", "no_log_adapter_for_host"))

    def test_reads_that_do_not_show_the_cited_lines_are_incomplete_and_a_forged_log_is_inconsistent(self):
        partial = self.write_log(self.run, lines=[1, 3], path=self.tmp / "partial.jsonl")
        rep = self.report(partial)
        self.assertEqual(rep["verdict"], "INCOMPLETE")
        self.assertEqual((rep["selected"][0]["read"]["status"], rep["selected"][0]["read"]["ranges"], rep["selected"][0]["read"]["cited_lines_covered"]),
                         ("not_observed", "1, 3", False))
        self.assertEqual(self.problem_codes(rep), {"read_not_observed"})
        no_write = self.write_log(self.run, write=False, path=self.tmp / "nowrite.jsonl")
        self.assertEqual(self.problem_codes(self.report(no_write)), {"generation_write_not_found"})
        forged = self.write_log(self.run, path=self.run / "read-receipt.json")
        rep = self.report(forged)
        self.assertEqual((rep["verdict"], rep["selected"][0]["read"]["status"]), ("INCONSISTENT", "self_reported"))
        bad = self.tmp / "bad.jsonl"
        bad.write_text("{not json\n", encoding="utf-8")
        self.assertEqual(self.problem_codes(self.report(bad)), {"host_log_invalid"})
        with self.assertRaises(ev.Unreadable):
            self.report(self.tmp / "no-such-log.jsonl")

    def test_a_body_that_no_longer_matches_its_record_is_inconsistent(self):
        p = self.run / "snapshots/doc-a/body.txt"
        p.write_text(p.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        rep = self.report()
        self.assertEqual((rep["verdict"], rep["selected"][0]["integrity"], self.problem_codes(rep)), ("INCONSISTENT", "mismatch", {"body_mismatch"}))
        self.assertEqual(rep["selected"][0]["read"]["reason"], "body_not_verified")
        p.unlink()
        rep = self.report()
        self.assertEqual((rep["verdict"], rep["selected"][0]["integrity"]), ("INCONSISTENT", "missing"))

    def test_citations_outside_the_run_are_inconsistent(self):
        self.rewrite_rec(self.run, lambda r: r["changes"][2].update(rule_id="rule-skill-only"))  # a skill-only rule in a one-shot run
        rep = self.report()
        self.assertEqual((rep["verdict"], self.problem_codes(rep), rep["citations"][0]["in_scope"]), ("INCONSISTENT", {"cited_rule_out_of_scope"}, False))
        self.rewrite_rec(self.run, lambda r: r["changes"][2].update(rule_id="rule-nope", source_id="doc-md"))
        self.assertEqual(self.problem_codes(self.report()), {"unknown_rule", "cited_source_not_selected"})

    def test_a_registry_changed_after_the_fetch_is_incomplete_and_rules_are_not_matched(self):
        self.set_registry(lambda r: r["rules"].pop())
        rep = self.report()
        self.assertEqual((rep["verdict"], self.problem_codes(rep), rep["registry"]["matches"]), ("INCOMPLETE", {"registry_changed"}, False))
        self.assertEqual((rep["selected"][0]["rules"]["applicable"], rep["citations"][0]["in_scope"]), (None, None))

    def test_an_input_or_selection_that_is_not_what_was_fetched_is_inconsistent(self):
        p = self.run / "input.json"
        inp = json.loads(p.read_text(encoding="utf-8"))
        inp["purpose"] = "skill-instructions"  # the registry would now also route doc-md
        p.write_text(json.dumps(inp), encoding="utf-8")
        self.assertEqual(self.problem_codes(self.report()), {"input_hash_mismatch", "selected_sources_mismatch"})

    def test_a_source_that_was_not_fetched_is_incomplete(self):
        self.rewrite_acq(self.run, lambda a: a["sources"][0].update(status="blocked", error="http_503"))
        rep = self.report()
        self.assertEqual((rep["verdict"], rep["selected"][0]["integrity"], self.problem_codes(rep)), ("INCOMPLETE", "unavailable", {"source_blocked"}))

    def test_command_prints_json_or_markdown_and_exits_by_verdict(self):
        code, out, err = self.cli("sources", "--run", str(self.run))
        self.assertEqual((code, json.loads(out)["verdict"], json.loads(out)["mode"]), (0, "CONSISTENT", "run"))
        self.assertIn("CONSISTENT []", err)
        code, out, err = self.cli("sources", "--run", str(self.run), "--host-log", str(self.log), "--markdown")
        self.assertEqual(code, 0)
        for text in ("# 見た資料: run-1", "判定: **CONSISTENT**", "[`doc-a`](https://docs.example.com/guide)", "| 2 / 1 |",
                     "unit-test", "人が審査したものは 0 件", "観測(読んだ行", "規則の解釈が正しいことや"):
            self.assertIn(text, out)
        self.assertIn("(exit 0)", err)
        p = self.run / "snapshots/doc-a/body.txt"
        p.write_text(p.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        code, out, _ = self.cli("sources", "--run", str(self.run), "--markdown")
        self.assertEqual(code, 1)
        self.assertIn("**不一致**", out)
        self.assertEqual(self.cli("sources", "--run", str(self.tmp / "no-such-run"))[0], 2)

    def test_arguments_must_name_a_run_or_a_plan(self):
        for argv in (("sources",), ("sources", "--target", "nope"), ("sources", "--target", "claude-code", "--purpose", "nope"),
                     ("sources", "--target", "claude-code", "--host-log", "x"), ("sources", "--run", str(self.run), "--all-models"),
                     ("sources", "--run", str(self.run), "--target", "claude-code")):
            with self.subTest(argv):
                self.assertEqual(self.cli(*argv)[0], 1)

    def test_a_plan_can_come_from_an_input_file(self):
        p = self.make_input("plan", target_id="claude-code", target_surface="api", model_id="unknown")
        code, out, _ = self.cli("sources", "--input", str(p))
        self.assertEqual((code, [x["source_id"] for x in json.loads(out)["selected"]]), (0, ["doc-a", "doc-api"]))


class TestSourcesByModel(Base):
    """A run of a model-limited guide: only the model it is for gets it, and the report says why the others did not."""
    def setUp(self):
        super().setUp()

        def limit(reg):
            for s in reg["sources"]:
                if s["source_id"] == "doc-api":
                    s["models"] = ["model-x"]
            for r in reg["rules"]:
                if r["source_id"] == "doc-api":
                    r["models"] = ["model-x"]
        self.set_registry(limit)

    def test_the_run_reads_the_guide_of_its_model_only_and_the_report_agrees(self):
        for model, got, reason in (("model-x", ["doc-a", "doc-api"], None), ("model-y", ["doc-a"], "model_mismatch"),
                                   ("unknown", ["doc-a"], "model_unknown")):
            with self.subTest(model):
                code, acq = self.fetch(f"run-{model}", target_surface="api", model_id=model)
                self.assertEqual(acq["selected_sources"], got)
                rep = ev.sources_report(self.run_dir(f"run-{model}"))
                self.assertEqual(([r["source_id"] for r in rep["selected"]], rep["verdict"]), (got, "CONSISTENT"))
                guide = next(g for g in rep["model"]["guides"] if g["source_id"] == "doc-api")
                self.assertEqual((guide["status"], guide.get("reason")), ("selected" if reason is None else "skipped", reason))
                self.assertEqual(rep["model"]["model_specific_sources"], 1)

    def test_a_misspelled_model_gets_no_guide_and_a_hint(self):
        self.fetch("run-sp", target_surface="api", model_id="Model_X")
        rep = ev.sources_report(self.run_dir("run-sp"))
        self.assertEqual(([r["source_id"] for r in rep["selected"]], rep["model"]["spelling_hint"]), (["doc-a"], ["model-x"]))
        self.assertIn("綴りが違うかもしれません", ev.render_sources(rep))


class TestShippedEvals(unittest.TestCase):
    def test_shipped_cases_are_well_formed_split_and_not_claimed_reviewed(self):
        rows = ev.load_jsonl(ROOT / "evals" / "prompt-tailor-cases.jsonl")
        ids = [r["case_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        for r in rows:
            with self.subTest(r["case_id"]):
                ev.shape(r, ev.CASE_F, "case")
                self.assertIn(r["split"], ("development", "held-out"))
                for ref, h in (("input_ref", "input_sha256"), ("original_ref", "original_sha256"), ("optimized_ref", "optimized_sha256")):
                    self.assertEqual(ev.sha_file(ev.safe_join(ROOT / "evals", r[ref])), r[h])
                # requirements were extracted by the implementer; nobody has reviewed them yet
                self.assertEqual(r["status"], "pending-review")
        self.assertTrue({r["split"] for r in rows} == {"development", "held-out"})
        kinds = " ".join(r["case_id"] for r in rows)
        for needed in ("number-altered", "ui-misapplied", "ambiguous"):  # negative / boundary examples exist
            self.assertIn(needed, kinds)


class TestSkillFiles(unittest.TestCase):
    def test_skill_md_frontmatter_and_links(self):
        import re
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))  # Claude Code reads frontmatter only from the first line
        front = text.split("---\n")[1]
        self.assertIn("name: official-prompt-tailor", front)
        self.assertNotIn("allowed-tools", front)  # no pre-approved tools
        keys = {l.split(":")[0] for l in front.splitlines() if re.match(r"^[a-z_-]+:", l)}
        self.assertEqual(keys, {"name", "description"})  # claude.ai rejects keys outside its six; Codex/Antigravity need only these two
        desc = next(l for l in front.splitlines() if l.startswith("description:"))[len("description: "):]
        self.assertLessEqual(len(desc), 200)  # the stricter of the documented limits
        self.assertNotIn("${", text)  # no Claude Code-only substitutions
        for rel in re.findall(r"\]\((?!http)([^)]+)\)", text):
            self.assertTrue((SKILL / rel).exists(), rel)
        self.assertLessEqual(len(text.splitlines()), 120)  # entry point stays short; detail lives in references/

    def test_rubric_declares_a_version(self):
        self.assertRegex((SKILL / "references" / "review-rubric.md").read_text(encoding="utf-8"), r"(?m)^rubric_version: \S+")


class TestInstall(unittest.TestCase):
    def setUp(self):
        import subprocess
        self.sp = subprocess
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj, self.home = self.tmp / "proj", self.tmp / "home"
        self.proj.mkdir()
        self.home.mkdir()

    def install(self, *args):
        # HOME for POSIX, USERPROFILE for Windows: Path.home() reads whichever the platform uses
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        return self.sp.run([sys.executable, "-B", str(ROOT / "install.py"), *args], capture_output=True, text=True, encoding="utf-8", env=env)

    def test_requires_an_explicit_target(self):
        r = self.install()
        self.assertEqual(r.returncode, 2)  # argparse usage error
        self.assertIn("--project", r.stderr)
        self.assertEqual(self.install("--bogus").returncode, 2)
        self.assertEqual(self.install("--user", "--project", str(self.proj)).returncode, 2)
        self.assertEqual(self.install("--project", str(self.tmp / "missing")).returncode, 1)
        self.assertEqual(self.install("--user", "--gitignore").returncode, 1)

    def test_dry_run_changes_nothing(self):
        r = self.install("--project", str(self.proj), "--dry-run", "--gitignore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.proj.iterdir()), [])
        self.assertIn("nothing was changed", r.stdout)

    def test_project_install_is_self_contained_and_touches_no_settings(self):
        r = self.install("--project", str(self.proj), "--gitignore")
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = self.proj / ".claude" / "skills" / "official-prompt-tailor"
        for rel in ("SKILL.md", "scripts/evidence.py", "references/registry.json", "references/review-rubric.md"):
            self.assertTrue((dest / rel).is_file(), rel)
        self.assertFalse(list(dest.rglob("__pycache__")))
        self.assertEqual(sorted(p.name for p in (self.proj / ".claude").iterdir()), ["skills"])  # no settings / hooks
        self.assertEqual((self.proj / ".gitignore").read_text(encoding="utf-8"), "outputs/\n")
        self.assertEqual(self.install("--project", str(self.proj), "--force", "--gitignore").returncode, 0)
        self.assertEqual((self.proj / ".gitignore").read_text(encoding="utf-8"), "outputs/\n")  # not appended twice
        # the copy runs on its own (SKILL_DIR is derived from the script location)
        out = self.sp.run([sys.executable, "-I", "-B", str(dest / "scripts" / "evidence.py"), "verify", "--run", "none", "--mode", "live"],
                          capture_output=True, text=True, encoding="utf-8", cwd=self.proj)
        self.assertEqual(out.returncode, 2)
        self.assertIn("run directory not found", out.stderr)

    def test_gitignore_keeps_existing_lines_and_adds_a_missing_newline(self):
        (self.proj / ".gitignore").write_text("node_modules/", encoding="utf-8")
        self.assertEqual(self.install("--project", str(self.proj), "--gitignore").returncode, 0)
        self.assertEqual((self.proj / ".gitignore").read_text(encoding="utf-8"), "node_modules/\noutputs/\n")

    def test_existing_copy_is_refused_then_kept_as_a_backup_outside_skills(self):
        self.assertEqual(self.install("--project", str(self.proj)).returncode, 0)
        dest = self.proj / ".claude" / "skills" / "official-prompt-tailor"
        (dest / "local-note.txt").write_text("mine", encoding="utf-8")
        self.assertEqual(self.install("--project", str(self.proj)).returncode, 1)
        self.assertEqual((dest / "local-note.txt").read_text(encoding="utf-8"), "mine")
        self.assertEqual(self.install("--project", str(self.proj), "--force").returncode, 0)
        backups = list((self.proj / ".claude").glob("official-prompt-tailor.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "local-note.txt").read_text(encoding="utf-8"), "mine")
        self.assertEqual([p.name for p in (self.proj / ".claude" / "skills").iterdir()], ["official-prompt-tailor"])
        self.assertFalse((dest / "local-note.txt").exists())

    def test_user_install_goes_under_home(self):
        r = self.install("--user")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.home / ".claude" / "skills" / "official-prompt-tailor" / "SKILL.md").is_file())

    def test_failed_self_check_rolls_back_and_restores_the_previous_copy(self):
        sys.path.insert(0, str(ROOT))
        import install
        self.addCleanup(sys.path.remove, str(ROOT))
        self.assertEqual(self.install("--project", str(self.proj)).returncode, 0)
        dest = self.proj / ".claude" / "skills" / "official-prompt-tailor"
        (dest / "local-note.txt").write_text("mine", encoding="utf-8")
        broken = self.tmp / "broken" / "official-prompt-tailor"
        (broken / "scripts").mkdir(parents=True)
        (broken / "SKILL.md").write_text("x", encoding="utf-8")
        (broken / "scripts" / "evidence.py").write_text("raise SystemExit('boom')\n", encoding="utf-8")
        with mock.patch.object(install, "SRC", broken), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                install.main(["--project", str(self.proj), "--force"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual((dest / "local-note.txt").read_text(encoding="utf-8"), "mine")  # previous copy is back
        self.assertEqual(list((self.proj / ".claude").glob("*.bak-*")), [])

    def test_each_product_gets_the_folder_its_documentation_names(self):
        p2 = self.tmp / "proj2"  # codex and antigravity share .agents/skills in a project, so each gets its own project
        p2.mkdir()
        cases = [("codex", "--project", self.proj / ".agents/skills"), ("codex", "--user", self.home / ".agents/skills"),
                 ("antigravity", "--project", p2 / ".agents/skills"), ("antigravity", "--user", self.home / ".gemini/config/skills"),
                 ("antigravity-cli", "--user", self.home / ".gemini/antigravity-cli/skills"),
                 ("claude-code", "--user", self.home / ".claude/skills")]
        for target, scope, base in cases:
            with self.subTest(target=target, scope=scope):
                args = ["--target", target, scope] + ([str(base.parents[1])] if scope == "--project" else [])
                r = self.install(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue((base / "official-prompt-tailor" / "SKILL.md").is_file())
                self.assertEqual(self.install(*args).returncode, 1)  # second run refuses
        self.assertEqual(self.install("--target", "gemini", "--user").returncode, 2)

    def test_zip_for_claude_ai_and_cowork_upload(self):
        import zipfile
        out = self.tmp / "dist"
        out.mkdir()
        r = self.install("--zip", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        z = zipfile.ZipFile(out / "official-prompt-tailor.zip")
        names = z.namelist()
        self.assertTrue(all(n.startswith("official-prompt-tailor/") for n in names))  # skill folder is the ZIP root
        for needed in ("SKILL.md", "scripts/evidence.py", "references/registry.json"):
            self.assertIn("official-prompt-tailor/" + needed, names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])
        z.extractall(self.tmp / "unz")  # the extracted skill runs on its own
        z.close()  # Windows cannot rename an open file, and the --force run below renames this ZIP
        out2 = self.sp.run([sys.executable, "-I", "-B", str(self.tmp / "unz/official-prompt-tailor/scripts/evidence.py"), "verify", "--run", "none", "--mode", "live"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertIn("run directory not found", out2.stderr)
        self.assertEqual(self.install("--zip", str(out)).returncode, 1)  # refuses to overwrite
        self.assertEqual(self.install("--zip", str(out), "--force").returncode, 0)
        self.assertEqual(len(list(out.glob("*.bak-*"))), 1)
        self.assertEqual(self.install("--zip", str(out), "--target", "codex").returncode, 1)
        self.assertEqual(self.install("--zip", str(self.tmp / "none")).returncode, 1)
        dry = self.tmp / "dry"
        dry.mkdir()
        self.assertEqual(self.install("--zip", str(dry), "--dry-run").returncode, 0)
        self.assertEqual(list(dry.iterdir()), [])

    def test_zip_refuses_a_skill_that_claude_ai_would_reject(self):
        sys.path.insert(0, str(ROOT))
        import install
        self.addCleanup(sys.path.remove, str(ROOT))
        self.assertEqual(install.upload_problems(), [])  # the shipped SKILL.md is uploadable
        bad = self.tmp / "bad" / "official-prompt-tailor"
        (bad / "scripts").mkdir(parents=True)
        (bad / "scripts" / "evidence.py").write_text("", encoding="utf-8")
        (bad / "SKILL.md").write_text("---\nname: official-prompt-tailor\ndescription: " + "x" * 201 + "\nargument-hint: [a]\n---\nbody\n", encoding="utf-8")
        with mock.patch.object(install, "SRC", bad):
            problems = install.upload_problems()
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("argument-hint" in p for p in problems) and any("201" in p for p in problems))

    def test_cannot_install_onto_itself(self):
        self.assertEqual(self.install("--project", str(ROOT)).returncode, 1)

    def test_plugin_has_the_manifest_shape_openai_validates(self):
        out = self.tmp / "plug"
        out.mkdir()
        r = self.install("--plugin", str(out), "--author", "Someone")
        self.assertEqual(r.returncode, 0, r.stderr)
        root = out / "official-prompt-tailor"
        m = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        # the key sets below mirror OpenAI's plugin validator (plugin-creator/scripts/validate_plugin.py)
        self.assertLessEqual(set(m), {"id", "name", "version", "description", "skills", "apps", "mcpServers", "interface", "author",
                                      "homepage", "repository", "license", "keywords"})
        self.assertEqual(m["name"], root.name)  # folder name and manifest name are the same
        self.assertRegex(m["version"], r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
        self.assertTrue(m["description"].strip())
        self.assertEqual(m["author"], {"name": "Someone"})
        self.assertEqual(m["skills"], "./skills/")
        ui = m["interface"]
        for key in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
            self.assertTrue(ui[key].strip(), key)
        self.assertEqual(ui["developerName"], "Someone")
        self.assertTrue(ui["capabilities"] and all(c.strip() for c in ui["capabilities"]))
        self.assertTrue(1 <= len(ui["defaultPrompt"]) <= 3 and all(0 < len(s) <= 128 for s in ui["defaultPrompt"]))
        skill = root / "skills" / "official-prompt-tailor"
        for rel in ("SKILL.md", "scripts/evidence.py", "references/registry.json", "references/recommendations-example.md"):
            self.assertTrue((skill / rel).is_file(), rel)
        self.assertFalse(list(root.rglob("__pycache__")))
        out2 = self.sp.run([sys.executable, "-I", "-B", str(skill / "scripts" / "evidence.py"), "verify", "--run", "none", "--mode", "live"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertIn("run directory not found", out2.stderr)  # the bundled skill runs on its own

    def test_plugin_refuses_overwrite_keeps_a_backup_and_checks_its_arguments(self):
        out = self.tmp / "plug"
        out.mkdir()
        self.assertEqual(self.install("--plugin", str(out)).returncode, 0)
        (out / "official-prompt-tailor" / "local-note.txt").write_text("mine", encoding="utf-8")
        self.assertEqual(self.install("--plugin", str(out)).returncode, 1)
        self.assertEqual(self.install("--plugin", str(out), "--force").returncode, 0)
        backups = list(out.glob("official-prompt-tailor.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "local-note.txt").read_text(encoding="utf-8"), "mine")
        self.assertFalse((out / "official-prompt-tailor" / "local-note.txt").exists())
        for bad in (["--target", "codex"], ["--gitignore"]):
            self.assertEqual(self.install("--plugin", str(out), *bad).returncode, 1, bad)
        self.assertEqual(self.install("--user", "--author", "x").returncode, 1)  # --author belongs to --plugin
        self.assertEqual(self.install("--plugin", str(self.tmp / "none")).returncode, 1)
        self.assertEqual(self.install("--plugin", str(ROOT / ".claude" / "skills" / "official-prompt-tailor")).returncode, 1)  # inside the source
        dry = self.tmp / "dry"
        dry.mkdir()
        self.assertEqual(self.install("--plugin", str(dry), "--dry-run").returncode, 0)
        self.assertEqual(list(dry.iterdir()), [])

    def test_uninstall_removes_only_the_copy_and_reports_missing_ones(self):
        self.assertEqual(self.install("--project", str(self.proj)).returncode, 0)
        skills = self.proj / ".claude" / "skills"
        dest = skills / "official-prompt-tailor"
        (skills / "other-skill").mkdir()
        (skills / "other-skill" / "SKILL.md").write_text("x", encoding="utf-8")
        r = self.install("--uninstall", "--project", str(self.proj), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(dest.is_dir())
        self.assertIn("nothing was changed", r.stdout)
        self.assertEqual(self.install("--uninstall", "--project", str(self.proj)).returncode, 0)
        self.assertFalse(dest.exists())
        self.assertEqual([p.name for p in skills.iterdir()], ["other-skill"])  # siblings and the folder stay
        r = self.install("--uninstall", "--project", str(self.proj))
        self.assertEqual(r.returncode, 1)  # a typo in --target/--project must not look like success
        self.assertIn("not installed", r.stderr)

    def test_uninstall_leaves_backups_and_things_it_does_not_recognise(self):
        self.assertEqual(self.install("--project", str(self.proj)).returncode, 0)
        self.assertEqual(self.install("--project", str(self.proj), "--force").returncode, 0)
        dest = self.proj / ".claude" / "skills" / "official-prompt-tailor"
        (dest / "local-note.txt").write_text("mine", encoding="utf-8")
        r = self.install("--uninstall", "--project", str(self.proj))
        self.assertEqual(r.returncode, 1)  # an unknown file is inside: refuse unless --force
        self.assertIn("local-note.txt", r.stderr)
        self.assertTrue(dest.is_dir())
        self.assertEqual(self.install("--uninstall", "--project", str(self.proj), "--force").returncode, 0)
        self.assertFalse(dest.exists())
        self.assertEqual(len(list((self.proj / ".claude").glob("official-prompt-tailor.bak-*"))), 1)  # backups are never deleted
        stranger = self.proj / ".agents" / "skills" / "official-prompt-tailor"
        stranger.mkdir(parents=True)
        (stranger / "notes.txt").write_text("not this skill", encoding="utf-8")
        r = self.install("--uninstall", "--target", "codex", "--project", str(self.proj), "--force")
        self.assertEqual(r.returncode, 1)  # --force does not override recognition
        self.assertEqual((stranger / "notes.txt").read_text(encoding="utf-8"), "not this skill")

    def test_uninstall_never_touches_the_source_or_what_a_symlink_points_to(self):
        self.assertEqual(self.install("--uninstall", "--project", str(ROOT), "--force").returncode, 1)
        self.assertTrue((SKILL / "SKILL.md").is_file())
        real = self.tmp / "real"
        real.mkdir()
        (real / "keep.txt").write_text("keep", encoding="utf-8")
        link = self.proj / ".claude" / "skills" / "official-prompt-tailor"
        link.parent.mkdir(parents=True)
        symlink(link, real)
        self.assertEqual(self.install("--uninstall", "--project", str(self.proj)).returncode, 0)
        self.assertFalse(link.exists() or link.is_symlink())
        self.assertEqual((real / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_uninstall_a_plugin_and_its_argument_rules(self):
        out = self.tmp / "plug"
        out.mkdir()
        self.assertEqual(self.install("--plugin", str(out)).returncode, 0)
        self.assertEqual(self.install("--plugin", str(out), "--uninstall").returncode, 0)
        self.assertEqual(list(out.iterdir()), [])
        other = out / "official-prompt-tailor" / ".codex-plugin"
        other.mkdir(parents=True)
        (other / "plugin.json").write_text('{"name": "someone-elses"}', encoding="utf-8")
        self.assertEqual(self.install("--plugin", str(out), "--uninstall", "--force").returncode, 1)  # a different plugin is left alone
        self.assertTrue((other / "plugin.json").is_file())
        for bad in (["--zip", str(out)], ["--user", "--gitignore"], ["--user", "--author", "x"]):
            self.assertEqual(self.install("--uninstall", *bad).returncode, 1, bad)

    def test_skill_commands_do_not_depend_on_where_the_skill_is_installed(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("<skill-dir>/scripts/evidence.py", text)
        self.assertNotIn("CLAUDE_SKILL_DIR", text)
        self.assertNotIn("python3 .claude/skills/", text)


class TestExtraction(unittest.TestCase):
    def test_extractor_keeps_content_and_drops_chrome(self):
        html = ("<html><body><nav>MENU</nav><main><h2>Title</h2><p>One <code>x</code> two</p><ul><li>a</li><li>b</li></ul>"
                "<button>Click</button><script>no()</script></main><footer>F</footer></body></html>")
        p = ev._Extract("main")
        p.feed(html)
        p.close()
        self.assertEqual(p.result(), ["## Title", "One x two", "a", "b"])

    def test_unknown_matches_only_all(self):
        self.assertTrue(ev.in_list(["all"], "unknown"))
        for listed in (["unknown"], ["api"], ["gems", "unknown"]):
            self.assertFalse(ev.in_list(listed, "unknown"), listed)
        self.assertTrue(ev.in_list(["api", "gems"], "gems"))

    def test_relative_update_labels_are_dropped_but_absolute_dates_stay(self):
        src = {"extraction": "html-main-v1", "body_locators": ["Best practices", "Provide context"]}
        page = (FIX / "guide.html").read_bytes()
        plain = ev.process_body(src, page, "text/html")
        for label in (b"Updated 3 days ago", b"Updated today", b"Updated over a week ago", b"updated yesterday"):
            self.assertEqual(plain, ev.process_body(src, page.replace(b"<main>", b"<main><p>" + label + b"</p>"), "text/html"), label)
        for kept in (b"Last updated: Jun 18, 2026", b"Updated user model to handle edge cases", b"Updated 3 days ago, see below"):
            body = ev.process_body(src, page.replace(b"<main>", b"<main><p>" + kept + b"</p>"), "text/html")
            self.assertIn(kept.decode(), body)

    def test_extraction_is_deterministic(self):
        src = {"extraction": "html-main-v1", "body_locators": ["Best practices", "Provide context"]}
        page = (FIX / "guide.html").read_bytes()
        a = ev.process_body(src, page, "text/html")
        self.assertEqual(a, ev.process_body(src, page.replace(b"\n", b"\r\n"), "text/html"))
        self.assertEqual(ev.sha_text("a\r\nb\rc"), ev.sha_text("a\nb\nc"))


if __name__ == "__main__":
    unittest.main()
