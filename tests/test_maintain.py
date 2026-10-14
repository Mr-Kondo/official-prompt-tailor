"""Unit tests for tools/maintain.py (stdlib unittest, network mocked). Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import maintain  # noqa: E402
from test_official_prompt_tailor import A_URL, API_URL, HTML, Base, ev  # noqa: E402

INDEX_URL, NEW_URL, NEW_MD = "https://docs.example.com/llms.txt", "https://docs.example.com/new-guide", "https://docs.example.com/new-notes.md"
TEXT = {"content-type": "text/plain; charset=utf-8"}
LLMS = """# Example Docs

> Official documentation of the example product. Each line below is one page of the documentation.

## Docs
- [Prompting guide](https://docs.example.com/docs/prompting.md): how to write a prompt
- [Memory](https://docs.example.com/docs/memory/index.md): how memory works
- [Billing](https://docs.example.com/docs/billing.md): invoices and plans
- [Best practices](https://docs.example.com/guide): already registered
- [Skills](https://docs.example.com/skills/index.md): skills
- [Legacy prompting](http://www.docs.example.com/docs/legacy-prompting.md): old spelling of the host
- [External prompting](https://other.example.org/docs/prompting.md): another host
- [Blog prompt tips](https://docs.example.com/blog/prompt-tips.md): outside the scope
"""


class MaintainBase(Base):
    def setUp(self):
        super().setUp()
        self.cfg, self.seen = self.tmp / "discovery.json", self.tmp / "seen.json"
        for p in (mock.patch.object(maintain, "CONFIG", self.cfg), mock.patch.object(maintain, "SEEN", self.seen),
                  mock.patch.object(ev, "RETRY_WAIT", 0)):
            p.start()
            self.addCleanup(p.stop)
        self.write_config()
        self.responses[INDEX_URL] = (200, TEXT, LLMS.encode("utf-8"))

    def write_config(self, **over):
        cfg = {"schema_version": 1, "keywords": ["prompt", "memory", "practices", "skills"],
               "indexes": [{"index_id": "example-docs", "url": INDEX_URL, "scope": ["/docs/", "/guide", "/skills/"]}]}
        cfg.update(over)
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")

    def tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = maintain.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestDiscover(MaintainBase):
    def keys(self, rep):
        return [c["key"] for r in rep["indexes"] for c in r["candidates"]]

    def test_lists_new_relevant_pages_and_nothing_else(self):
        code, out, err = self.tool("discover")
        rep = json.loads(out)
        self.assertEqual((code, rep["verdict"]), (2, "CANDIDATES"))
        self.assertIn("4 candidates", err)
        # Billing has no keyword, "Best practices" is registered, the other host and the blog are out of scope.
        self.assertEqual(self.keys(rep), ["docs.example.com/docs/legacy-prompting", "docs.example.com/docs/memory",
                                          "docs.example.com/docs/prompting", "docs.example.com/skills"])
        urls = {c["key"]: c["url"] for c in rep["indexes"][0]["candidates"]}
        self.assertEqual(urls["docs.example.com/docs/legacy-prompting"], "https://www.docs.example.com/docs/legacy-prompting.md")
        self.assertEqual(rep["indexes"][0]["pages"], 7)  # the link to the other host is not counted
        self.assertEqual(self.calls, [INDEX_URL])  # an index that answers is asked once

    def test_mark_seen_makes_the_next_run_quiet(self):
        code, out, _ = self.tool("discover", "--mark-seen")
        self.assertEqual((code, json.loads(out)["marked_seen"]), (2, 4))
        self.assertEqual(len(json.loads(self.seen.read_text(encoding="utf-8"))), 4)
        code, out, _ = self.tool("discover")
        self.assertEqual((code, json.loads(out)["verdict"]), (0, "NONE"))

    def test_a_blocked_index_is_incomplete_not_empty(self):
        self.responses[INDEX_URL] = (503, HTML, b"")
        code, out, _ = self.tool("discover")
        row = json.loads(out)["indexes"][0]
        self.assertEqual((code, json.loads(out)["verdict"], row["status"], row["error"]), (2, "INCOMPLETE", "blocked", "http_503"))
        self.assertEqual(self.calls, [INDEX_URL, INDEX_URL])  # looked at once more, then reported

    def test_an_index_that_answers_badly_once_is_listed_from_the_second_look(self):
        # 2026-10-15: antigravity-docs answered once with bytes that were not UTF-8; its pages vanished from that week's report.
        replies = iter([(200, TEXT, b"\xff\xfe\x00"), (200, TEXT, LLMS.encode("utf-8"))])
        self.responses[INDEX_URL] = lambda: next(replies)
        code, out, _ = self.tool("discover")
        rep = json.loads(out)
        self.assertEqual((code, rep["verdict"], rep["indexes"][0]["status"], len(self.keys(rep))), (2, "CANDIDATES", "live", 4))
        self.assertEqual(self.calls, [INDEX_URL, INDEX_URL])

    def test_per_index_keywords_and_exclude(self):
        only_memory = {"index_id": "example-docs", "url": INDEX_URL, "scope": ["/docs/"], "keywords": ["memory"]}
        self.write_config(indexes=[only_memory])
        self.assertEqual(self.keys(json.loads(self.tool("discover")[1])), ["docs.example.com/docs/memory"])
        self.write_config(indexes=[{**only_memory, "exclude": ["memory"]}])
        self.assertEqual(self.keys(json.loads(self.tool("discover")[1])), [])

    def test_page_key_treats_one_page_spelled_differently_as_one(self):
        same = [("https://x.example/", "https://x.example/index.md"),
                ("https://www.antigravity.google/docs/rules/", "https://antigravity.google/docs/rules"),
                ("https://adk.dev/agents/llm-agents/index.md", "https://adk.dev/agents/llm-agents/"),
                ("http://x.example/a.md?hl=en#top", "https://x.example/a")]
        for a, b in same:
            with self.subTest(a=a, b=b):
                self.assertEqual(maintain.page_key(a), maintain.page_key(b))
        self.assertNotEqual(maintain.page_key("https://x.example/a"), maintain.page_key("https://x.example/b"))

    def test_bad_or_missing_config_is_reported(self):
        self.write_config(indexes=[{"index_id": "example-docs", "url": INDEX_URL, "scope": ["docs"]}])
        self.assertEqual(self.tool("discover")[0], 1)
        self.write_config(indexes=[{"index_id": "example-docs", "url": "http://docs.example.com/llms.txt", "scope": []}])
        self.assertEqual(self.tool("discover")[0], 1)
        self.cfg.unlink()
        self.assertEqual(self.tool("discover")[0], 2)


class TestPropose(MaintainBase):
    def test_html_page_becomes_a_registry_entry_that_validates(self):
        self.responses[NEW_URL] = (200, HTML, self.page)
        code, out, err = self.tool("propose", "--url", NEW_URL, "--target", "claude-code")
        rep = json.loads(out)
        self.assertEqual((code, rep["ok"], rep["stable"]), (0, True, True))
        src = rep["source"]
        self.assertEqual((src["extraction"], src["body_locators"], src["source_id"]),
                         ("html-main-v1", ["Best practices", "Provide context"], "new-docs-example-com-new-guide"))
        self.assertEqual(rep["tried"], {})  # the first extraction that fits is taken
        reg = json.loads(ev.REGISTRY_PATH.read_text(encoding="utf-8"))
        reg["sources"].append(src)
        ev.validate_registry(reg)  # the entry is accepted as it stands

    def test_markdown_page_uses_markdown_extraction(self):
        self.responses[NEW_MD] = (200, {"content-type": "text/markdown"}, self.md)
        rep = json.loads(self.tool("propose", "--url", NEW_MD, "--target", "claude-code", "--source-id", "notes")[1])
        self.assertEqual((rep["ok"], rep["source"]["extraction"], rep["source"]["source_id"]), (True, "markdown-v1", "notes"))
        self.assertEqual(sorted(rep["tried"]), ["html-article-v1", "html-body-v1", "html-main-v1"])  # rejected on the content type

    def test_a_body_that_differs_between_two_fetches_is_flagged(self):
        pages = iter([self.page, self.page.replace(b"Give a check it can run", b"Give a check it can run now")] * 2)
        self.responses[NEW_URL] = lambda: (200, HTML, next(pages))
        code, out, err = self.tool("propose", "--url", NEW_URL, "--target", "claude-code")
        self.assertEqual((code, json.loads(out)["stable"]), (2, False))
        self.assertIn("unstable", err)

    def test_a_blocked_page_reports_the_reason(self):
        self.responses[NEW_URL] = (503, HTML, b"")
        code, out, _ = self.tool("propose", "--url", NEW_URL, "--target", "claude-code")
        self.assertEqual((code, json.loads(out)["ok"], json.loads(out)["error"]), (2, False, "http_503"))

    def test_a_page_with_a_single_heading_has_no_usable_locators(self):
        prose = b"A line of prose that is long enough to pass the length check of the body. " * 6
        self.responses[NEW_MD] = (200, {"content-type": "text/markdown"}, b"# Only heading\n" + prose)
        code, out, _ = self.tool("propose", "--url", NEW_MD, "--target", "claude-code")
        rep = json.loads(out)
        self.assertEqual((code, rep["ok"], rep["error"], rep["tried"]["markdown-v1"]), (2, False, "no_extraction_fits", "too_few_headings"))

    def test_refuses_what_it_should_not_fetch_or_propose(self):
        base = ["propose", "--target", "claude-code", "--url"]
        for bad in ("http://docs.example.com/new-guide", NEW_URL + "#top", "https://evil.example.net/page", A_URL, API_URL):
            with self.subTest(url=bad):
                self.assertEqual(self.tool(*base, bad)[0], 1)
        self.assertEqual(self.tool("propose", "--url", NEW_URL, "--target", "not-a-target")[0], 1)
        self.assertEqual(self.tool("propose", "--url", NEW_URL, "--target", "claude-code", "--source-id", "doc-a")[0], 1)
        self.assertEqual(self.calls, [])  # nothing was fetched for a refused request


if __name__ == "__main__":
    unittest.main()
