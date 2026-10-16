"""Unit tests for tools/maintain.py (stdlib unittest, network mocked). Run: python3 -m unittest discover -s tests"""
import contextlib
import copy
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import maintain  # noqa: E402
from test_official_prompt_tailor import A_URL, API_URL, HTML, NO_SOURCE_CHANGES, NO_SOURCE_OPT, ROOT, SKILL, Base, codes, ev, sha  # noqa: E402

INDEX_URL, NEW_URL, NEW_MD = "https://docs.example.com/llms.txt", "https://docs.example.com/new-guide", "https://docs.example.com/new-notes.md"
SECOND_URL = "https://docs.example.com/second-guide"
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

SITEMAP_URL, CHILD_URL, CHILD_URL_2 = "https://docs.example.com/sitemap.xml", "https://docs.example.com/sitemap-docs.xml", "https://docs.example.com/sitemap-more.xml"
XML = {"content-type": "application/xml; charset=utf-8"}
SITEMAP_INDEX = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><sitemapindex xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
                 f"<sitemap><loc>{CHILD_URL}</loc></sitemap><sitemap><loc>{CHILD_URL_2}</loc></sitemap>"
                 "<sitemap><loc>https://other.example.org/sitemap.xml</loc></sitemap></sitemapindex>")
URLSET_DOCS = ("<?xml version=\"1.0\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
               "<url><loc>https://docs.example.com/docs/prompting-guide</loc></url>"
               "<url><loc>https://docs.example.com/docs/billing</loc></url>"
               "<url><loc>https://docs.example.com/guide</loc></url>"  # registered
               "<url><loc>http://docs.example.com/docs/system-prompts?hl=en</loc></url>"
               "<url><loc>https://other.example.org/docs/prompting</loc></url></urlset>")
URLSET_MORE = ("<urlset><url><loc> https://docs.example.com/docs/memory-tips &amp; </loc></url>"
               "<url><loc>https://docs.example.com/docs/prompting-guide</loc></url></urlset>")  # the first page is listed in the other file too


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


    def sitemap_config(self, **over):
        self.write_config(indexes=[{"index_id": "example-sitemap", "format": "sitemap", "url": SITEMAP_URL, "scope": ["/docs/", "/guide"], **over}])

    def serve_sitemaps(self, index=SITEMAP_INDEX, docs=URLSET_DOCS, more=URLSET_MORE):
        for url, body in ((SITEMAP_URL, index), (CHILD_URL, docs), (CHILD_URL_2, more)):
            self.responses[url] = (200, XML, body.encode("utf-8"))

    def test_a_site_without_llms_txt_is_read_from_its_sitemap_and_the_child_sitemaps_it_lists(self):
        self.sitemap_config()
        self.serve_sitemaps()
        code, out, _ = self.tool("discover")
        rep = json.loads(out)
        row = rep["indexes"][0]
        self.assertEqual((code, rep["verdict"], row["status"], row["error"]), (2, "CANDIDATES", "live", None))
        self.assertEqual(row["skipped"], ["https://other.example.org/sitemap.xml"])  # left alone on purpose, and said so
        # billing has no keyword, /guide is registered, the other host is not ours; http and the query are one page; a page in two files is listed once
        self.assertEqual(self.keys(rep), ["docs.example.com/docs/memory-tips &", "docs.example.com/docs/prompting-guide", "docs.example.com/docs/system-prompts"])
        titles = {c["key"]: c["title"] for c in row["candidates"]}
        self.assertEqual(titles["docs.example.com/docs/prompting-guide"], "prompting guide")  # a sitemap has no titles: the last segment
        self.assertEqual({c["url"] for c in row["candidates"] if c["key"].endswith("system-prompts")}, {"https://docs.example.com/docs/system-prompts?hl=en"})
        self.assertEqual(self.calls, [SITEMAP_URL, CHILD_URL, CHILD_URL_2])  # the child on another host is never asked for

    def test_a_sitemap_that_lists_pages_directly_needs_no_children(self):
        self.sitemap_config()
        self.responses[SITEMAP_URL] = (200, XML, URLSET_DOCS.encode("utf-8"))
        rep = json.loads(self.tool("discover")[1])
        self.assertEqual((self.keys(rep), self.calls), (["docs.example.com/docs/prompting-guide", "docs.example.com/docs/system-prompts"], [SITEMAP_URL]))

    def test_a_child_that_cannot_be_read_is_named_and_the_others_are_still_listed(self):
        self.sitemap_config()
        self.serve_sitemaps()
        self.responses[CHILD_URL] = (404, XML, b"")
        code, out, _ = self.tool("discover")
        rep = json.loads(out)
        row = rep["indexes"][0]
        self.assertEqual((code, rep["verdict"], row["status"]), (2, "CANDIDATES", "partial"))
        self.assertIn(f"{CHILD_URL}: http_404", row["error"])
        self.assertEqual(self.keys(rep), ["docs.example.com/docs/memory-tips &", "docs.example.com/docs/prompting-guide"])
        self.responses[CHILD_URL_2] = (404, XML, b"")
        self.write_config(indexes=[{"index_id": "example-sitemap", "format": "sitemap", "url": SITEMAP_URL, "scope": ["/docs/"]}])
        self.assertEqual(json.loads(self.tool("discover")[1])["verdict"], "INCOMPLETE")  # nothing found and not everything read

    def test_a_refusal_is_reported_as_it_is_and_nothing_gets_past_it(self):
        self.sitemap_config()
        self.responses[SITEMAP_URL] = (403, {"content-type": "text/plain"}, b"Forbidden")
        code, out, _ = self.tool("discover")
        rep = json.loads(out)
        self.assertEqual((code, rep["verdict"], rep["indexes"][0]["status"], rep["indexes"][0]["error"]), (2, "INCOMPLETE", "blocked", "http_403"))
        self.assertEqual(self.calls, [SITEMAP_URL, SITEMAP_URL])  # the same address, asked twice as for any index; no other address, no other way

    def test_what_is_not_a_sitemap_is_refused(self):
        self.sitemap_config()
        for name, reply in {"html": (200, HTML, b"<html><body><urlset><loc>https://docs.example.com/docs/prompting</loc></urlset></body></html>"),
                            "not xml at all": (200, XML, b"hello"), "no addresses": (200, XML, b"<urlset></urlset>"),
                            "gzip": (200, {"content-type": "application/gzip"}, b"\x1f\x8b\x08"), "not utf-8": (200, XML, b"<urlset>\xff\xfe</urlset>"),
                            "other charset": (200, {"content-type": "application/xml; charset=iso-8859-1"}, URLSET_DOCS.encode("utf-8"))}.items():
            with self.subTest(name):
                self.responses[SITEMAP_URL] = reply
                row = json.loads(self.tool("discover")[1])["indexes"][0]
                self.assertEqual((row["status"], row["candidates"]), ("blocked", []))

    def test_an_entity_in_a_sitemap_is_not_expanded(self):
        self.sitemap_config()
        bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "https://docs.example.com/docs/prompting-bomb"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
                "<urlset><url><loc>&lol2;</loc></url><url><loc>https://docs.example.com/docs/prompting-real</loc></url></urlset>")
        self.responses[SITEMAP_URL] = (200, XML, bomb.encode("utf-8"))
        self.assertEqual(self.keys(json.loads(self.tool("discover")[1])), ["docs.example.com/docs/prompting-real"])

    def test_an_index_inside_an_index_and_too_many_children_are_reported_not_followed(self):
        self.sitemap_config()
        nested = SITEMAP_INDEX.replace("https://other.example.org/sitemap.xml", "https://docs.example.com/sitemap-nested.xml")
        self.serve_sitemaps(index=nested)
        self.responses["https://docs.example.com/sitemap-nested.xml"] = (200, XML, SITEMAP_INDEX.encode("utf-8"))
        row = json.loads(self.tool("discover")[1])["indexes"][0]
        self.assertEqual(row["status"], "partial")
        self.assertIn("a sitemap index inside a sitemap index is not followed", row["error"])
        self.assertNotIn("https://docs.example.com/sitemap-nested.xml" + "x", self.calls)
        many = "<sitemapindex>" + "".join(f"<sitemap><loc>https://docs.example.com/s-{i}.xml</loc></sitemap>" for i in range(maintain.MAX_CHILD_SITEMAPS + 4)) + "</sitemapindex>"
        self.responses[SITEMAP_URL] = (200, XML, many.encode("utf-8"))
        self.calls.clear()
        row = json.loads(self.tool("discover")[1])["indexes"][0]
        self.assertIn("4 child sitemaps beyond the first 30 were not read", row["error"])
        self.assertEqual(len(set(self.calls) - {SITEMAP_URL}), maintain.MAX_CHILD_SITEMAPS)

    def test_a_sitemap_over_the_page_limit_is_read_under_its_own_limit_and_the_limit_is_put_back(self):
        self.sitemap_config()
        big = "<urlset><!--" + "x" * (ev.MAX_BYTES + 1000) + "--><url><loc>https://docs.example.com/docs/prompting-big</loc></url></urlset>"
        self.responses[SITEMAP_URL] = (200, XML, big.encode("utf-8"))
        before = ev.MAX_BYTES
        row = json.loads(self.tool("discover")[1])["indexes"][0]
        self.assertEqual((row["status"], [c["key"] for c in row["candidates"]]), ("live", ["docs.example.com/docs/prompting-big"]))
        self.assertEqual(ev.MAX_BYTES, before)  # a page is still held to the page limit
        with mock.patch.object(maintain, "SITEMAP_MAX_BYTES", 1000):
            row = json.loads(self.tool("discover")[1])["indexes"][0]
        self.assertEqual((row["status"], row["error"], ev.MAX_BYTES), ("blocked", "response_too_large", before))

    def test_a_sitemap_built_to_make_the_reader_slow_is_read_in_linear_time(self):
        self.sitemap_config()
        hostile = "<urlset><loc>" + " " * 20000 + "<url><loc>https://docs.example.com/docs/prompting-ok</loc></url></urlset>"  # an unclosed <loc> with 20,000 spaces
        self.responses[SITEMAP_URL] = (200, XML, hostile.encode("utf-8"))
        started = time.monotonic()
        row = json.loads(self.tool("discover")[1])["indexes"][0]
        self.assertLess(time.monotonic() - started, 2)  # cubic time took minutes at this size
        self.assertEqual(row["status"], "live")

    def test_a_body_of_open_brackets_does_not_make_llms_txt_parsing_quadratic(self):
        started = time.monotonic()
        self.assertEqual(maintain.LINK.findall("[" * 200000), [])
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(maintain.LINK.findall("[A title](https://x.example/a)"), [("A title", "https://x.example/a")])

    def test_the_answers_of_child_sitemaps_are_not_kept_while_the_next_ones_are_read(self):
        self.sitemap_config()
        self.serve_sitemaps()
        seen = []
        real = maintain.shutil.rmtree
        with mock.patch.object(maintain.shutil, "rmtree", lambda path, **kw: (seen.append(Path(path).name), real(path, **kw))):
            json.loads(self.tool("discover")[1])
        self.assertEqual([n for n in seen if n.startswith("child-")], ["child-1", "child-2"])  # (the temporary directory itself is removed with the same call)

    def test_a_page_in_many_languages_is_listed_once_at_its_plain_address(self):
        self.sitemap_config()
        langs = ("<urlset>" + "".join(f"<url><loc>https://docs.example.com/docs/prompting-guide{q}</loc></url>" for q in ("?hl=de", "?hl=ko", "", "?hl=ar")) + "</urlset>")
        self.responses[SITEMAP_URL] = (200, XML, langs.encode("utf-8"))
        row = json.loads(self.tool("discover")[1])["indexes"][0]
        self.assertEqual([c["url"] for c in row["candidates"]], ["https://docs.example.com/docs/prompting-guide"])

    def test_the_format_of_an_index_is_checked(self):
        self.write_config(indexes=[{"index_id": "example-sitemap", "format": "rss", "url": SITEMAP_URL, "scope": ["/docs/"]}])
        code, _, err = self.tool("discover")
        self.assertEqual(code, 1)
        self.assertIn("format must be one of", err)

    def test_the_shipped_config_names_a_sitemap_for_each_site_without_llms_txt(self):
        cfg = ev.load_json(ROOT / "tools" / "discovery.json")
        sitemaps = {urllib.parse.urlsplit(i["url"]).hostname for i in cfg["indexes"] if i.get("format") == "sitemap"}
        self.assertEqual(sitemaps, {"developers.openai.com", "ai.google.dev"})
        for i in cfg["indexes"]:
            self.assertIn(i.get("format", "llms"), maintain.FORMATS)


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

    def test_a_markdown_page_with_signed_images_gets_markdown_v2_and_one_without_keeps_v1(self):
        signed = self.md + b"![](https://downloads.intercomcdn.com/i/o/abc/1/shot.png?expires=1792112400&amp;signature=aa11&amp;req=Q)\n"
        self.responses[NEW_MD] = (200, {"content-type": "text/markdown"}, signed)
        rep = json.loads(self.tool("propose", "--url", NEW_MD, "--target", "claude-code", "--source-id", "notes")[1])
        self.assertEqual((rep["ok"], rep["source"]["extraction"]), (True, "markdown-v2"))  # v1 would drift each time the page is re-signed
        self.responses[NEW_MD] = (200, {"content-type": "text/markdown"}, self.md)
        rep = json.loads(self.tool("propose", "--url", NEW_MD, "--target", "claude-code", "--source-id", "notes")[1])
        self.assertEqual(rep["source"]["extraction"], "markdown-v1")

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


class TestRegistryRules(unittest.TestCase):
    """What the repository requires of registry.json, and the one writer that keeps its layout."""
    PATH = SKILL / "references" / "registry.json"

    @classmethod
    def setUpClass(cls):
        cls.reg, _ = ev.load_registry(cls.PATH)

    def problems_after(self, change):
        reg = copy.deepcopy(self.reg)
        change(reg)
        return maintain.registry_problems(reg)

    def test_the_shipped_registry_meets_the_rules_and_the_writer_reproduces_it_byte_for_byte(self):
        self.assertEqual(maintain.registry_problems(self.reg), [])
        self.assertEqual(maintain.dump_registry(self.reg).encode("utf-8"), self.PATH.read_bytes())

    def test_each_rule_of_the_repository_is_enforced(self):
        api_rule = next(r["rule_id"] for r in self.reg["rules"]
                        if next(s for s in self.reg["sources"] if s["source_id"] == r["source_id"])["surfaces"] == ["api"])
        guide = next(s for s in self.reg["sources"] if s["models"] != ["all"])
        guide_rule = next(r["rule_id"] for r in self.reg["rules"] if r["source_id"] == guide["source_id"])

        def rule(reg, rid):
            return next(r for r in reg["rules"] if r["rule_id"] == rid)
        cases = {
            "a source nobody routes and no rule reads": (lambda reg: reg["sources"].append({**reg["sources"][0], "source_id": "orphan"}),
                                                          ["source orphan is not routed by any target", "source orphan has no rule"]),
            # the routing check asks where a rule would apply, not whether it is reviewed: no second message about a source left without a rule
            "an unreviewed rule": (lambda reg: rule(reg, api_rule).update(review_status="unreviewed"), [f"rule {api_rule} is not reviewed"]),
            "a rule without exclusions": (lambda reg: rule(reg, api_rule).update(exclusions=[]), [f"rule {api_rule} lacks preconditions or exclusions"]),
            "API advice offered to every surface": (lambda reg: rule(reg, api_rule).update(surfaces=["all"]),
                                                    [f"rule {api_rule}: advice from an API-only source must be limited to the api surface"]),
            "a model's guide grounding every model": (lambda reg: rule(reg, guide_rule).update(models=["all"]),
                                                      [f"rule {guide_rule}: models outside those of its source"]),
        }
        for name, (change, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.problems_after(change), want)

    def one_purpose_source(self):
        """(target_id, source_id): a source that one route fetches for one purpose, where its rules for that target serve that purpose alone."""
        for t in self.reg["targets"]:
            for route in t["conditional_sources"]:
                for sid in route["source_ids"] if len(route["purposes"]) == 1 and route["purposes"] != ["all"] else []:
                    routes = sum(sid in c["source_ids"] for c in t["conditional_sources"]) + (sid in t["required_sources"])
                    served = {p for r in self.reg["rules"] if r["source_id"] == sid and t["target_id"] in r["target_ids"] for p in r["purposes"]}
                    if routes == 1 and served == set(route["purposes"]):
                        return t["target_id"], sid
        self.fail("the registry has no source fetched for one purpose only")

    def test_a_source_fetched_where_none_of_its_rules_applies_is_named(self):
        tid, sid = self.one_purpose_source()
        guide = next(s["source_id"] for s in self.reg["sources"] if s["models"] != ["all"])

        def fetch_for_every_purpose(reg):
            for route in next(t for t in reg["targets"] if t["target_id"] == tid)["conditional_sources"]:
                if sid in route["source_ids"]:
                    route["purposes"] = ["all"]
        problems = self.problems_after(fetch_for_every_purpose)
        self.assertIn(f"target {tid}: source {sid} is fetched but none of its rules applies", " ".join(problems))
        # a guide for certain models, fetched for every model, is read for the others
        problems = self.problems_after(lambda reg: next(s for s in reg["sources"] if s["source_id"] == guide).update(models=["all"]))
        self.assertTrue(problems and all(f"source {guide} is fetched but none of its rules applies" in p for p in problems), problems)

    def test_a_rule_that_no_route_lets_any_input_use_is_named(self):
        tid, sid = self.one_purpose_source()
        served = next(r["purposes"] for r in self.reg["rules"] if r["source_id"] == sid and tid in r["target_ids"])
        other = next(p for p in ev.PURPOSES if p not in served)

        def add_rule_for_another_purpose(reg):
            rule = copy.deepcopy(next(r for r in reg["rules"] if r["source_id"] == sid and tid in r["target_ids"]))
            reg["rules"].append({**rule, "rule_id": "orphan-rule", "target_ids": [tid], "purposes": [other]})
        problems = self.problems_after(add_rule_for_another_purpose)
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("rule orphan-rule applies to no input"), problems)


class AdoptBase(MaintainBase):
    """A registry in the writer's layout that already meets the repository's rules, and a page to adopt."""
    def setUp(self):
        super().setUp()

        def tidy(reg):
            reg["rules"] = [r for r in reg["rules"] if r["rule_id"] != "rule-unreviewed"]
            by_id = lambda rid: next(r for r in reg["rules"] if r["rule_id"] == rid)  # noqa: E731
            # doc-a is fetched for every purpose and doc-api for every purpose on the api surface: each needs a rule for the purposes it had none for
            reg["rules"] += [{**by_id("rule-verify"), "rule_id": "rule-md", "source_id": "doc-md", "target_ids": ["claude-code"]},
                             {**by_id("rule-verify"), "rule_id": "rule-standing", "purposes": ["persistent-instructions", "agent-instruction"]},
                             {**by_id("rule-api"), "rule_id": "rule-api-more", "purposes": ["persistent-instructions", "skill-instructions", "agent-instruction"]}]
        self.set_registry(tidy)
        ev.REGISTRY_PATH.write_bytes(maintain.dump_registry(json.loads(ev.REGISTRY_PATH.read_text(encoding="utf-8"))).encode("utf-8"))
        self.before = ev.REGISTRY_PATH.read_bytes()
        self.responses[NEW_URL] = (200, HTML, self.page)

    def rule(self, rule_id="new-check", **over):
        return {"rule_id": rule_id, "target_ids": ["claude-code"], "surfaces": ["all"], "models": ["all"],
                "purposes": ["one-shot", "skill-instructions"], "preconditions": ["when applicable"],
                "exclusions": ["adding unrequested work"], "source_excerpt": "Give a check it can run", "meaning": "m",
                "allowed_change": "c", **over}

    def source(self, rules=None, **over):
        return {"source_id": "doc-new", "allowed_urls": [NEW_URL], "target_ids": ["claude-code"], "surfaces": ["all"],
                "models": ["all"], "category": "product-prompting", "extraction": "html-main-v1",
                "body_locators": ["Best practices", "Provide context"], "rules": [self.rule()] if rules is None else rules, **over}

    def spec(self, *sources, **over):
        spec = {"schema_version": 1, "reviewer_id": "unit-test (agent review at adoption; not human)", "sources": list(sources) or [self.source()]}
        spec.update(over)
        path = self.tmp / "spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def registry(self):
        return json.loads(ev.REGISTRY_PATH.read_text(encoding="utf-8"))


class TestAdopt(AdoptBase):
    def test_a_dry_run_checks_everything_and_writes_nothing(self):
        code, out, err = self.tool("adopt", "--spec", self.spec())
        rep = json.loads(out)
        self.assertEqual((code, rep["written"], rep["registry"]["sources"], rep["registry"]["rules"]), (0, False, 4, 11))
        self.assertIn("dry run: nothing written", err)
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_write_adds_the_source_reviewed_rules_and_the_route_in_the_registrys_layout(self):
        code, out, _ = self.tool("adopt", "--spec", self.spec(self.source([self.rule("r1"), self.rule("r2", source_excerpt="Reference specific files")])), "--write")
        rep = json.loads(out)
        self.assertEqual((code, rep["written"], rep["sources"][0]["rules"], rep["sources"][0]["wired"]), (0, True, ["r1", "r2"], ["claude-code"]))
        text = ev.REGISTRY_PATH.read_bytes()
        reg, _ = ev.load_registry()  # still a valid registry
        self.assertEqual(maintain.dump_registry(reg).encode("utf-8"), text)  # and still in the layout
        self.assertFalse(list(self.tmp.glob("registry.json.tmp")))
        body_sha = sha(ev.process_body(reg["sources"][-1], self.page, HTML["content-type"]))
        new = [r for r in reg["rules"] if r["source_id"] == "doc-new"]
        self.assertEqual([r["rule_id"] for r in new], ["r1", "r2"])
        for r in new:
            self.assertEqual((r["review_status"], r["kind"], r["source_body_sha256"], r["reviewer_id"], r["source_locator"]),
                             ("reviewed", "official", body_sha, "unit-test (agent review at adoption; not human)", r["source_excerpt"]))
        code_target = next(t for t in reg["targets"] if t["target_id"] == "claude-code")
        self.assertIn({"purposes": ["one-shot", "skill-instructions"], "surfaces": ["all"], "source_ids": ["doc-new"]}, code_target["conditional_sources"])
        pick = lambda purpose: ev.select_sources(reg, {"target_id": "claude-code", "purpose": purpose, "target_surface": "unknown", "model_id": "unknown"})  # noqa: E731
        self.assertEqual(("doc-new" in pick("one-shot"), "doc-new" in pick("skill-instructions"), "doc-new" in pick("persistent-instructions")),
                         (True, True, False))  # fetched only for the purposes a rule of it serves

    def test_routes_are_shared_when_the_purposes_and_surfaces_are_the_same(self):
        self.tool("adopt", "--spec", self.spec(self.source([self.rule(purposes=["skill-instructions"])])), "--write")
        code_target = next(t for t in self.registry()["targets"] if t["target_id"] == "claude-code")
        self.assertIn({"purposes": ["skill-instructions"], "surfaces": ["all"], "source_ids": ["doc-md", "doc-new"]}, code_target["conditional_sources"])
        self.assertEqual(len(code_target["conditional_sources"]), 2)  # no third route was made

    def test_a_rule_for_every_purpose_routes_under_all(self):
        self.tool("adopt", "--spec", self.spec(self.source([self.rule(purposes=list(ev.PURPOSES))])), "--write")
        code_target = next(t for t in self.registry()["targets"] if t["target_id"] == "claude-code")
        self.assertIn({"purposes": ["all"], "surfaces": ["all"], "source_ids": ["doc-new"]}, code_target["conditional_sources"])

    def test_the_page_is_adopted_from_two_agreeing_looks_not_from_one(self):
        other = self.page.replace(b"Give a check it can run", b"Give a check it can run now")
        pages = iter([(503, HTML, b""), (200, HTML, other), (200, HTML, self.page), (200, HTML, self.page)])  # a failure and a change are skipped
        self.responses[NEW_URL] = lambda: next(pages)
        code, out, _ = self.tool("adopt", "--spec", self.spec(), "--write")
        self.assertEqual((code, self.calls.count(NEW_URL)), (0, 4))
        self.assertEqual(json.loads(out)["sources"][0]["body_sha256"], sha(ev.process_body(self.registry()["sources"][-1], self.page, HTML["content-type"])))
        distinct = iter([self.page.replace(b"pytest", f"pytest{i}".encode()) for i in range(10)])
        self.responses[SECOND_URL] = lambda: (200, HTML, next(distinct))
        before = ev.REGISTRY_PATH.read_bytes()
        self.assertEqual(self.tool("adopt", "--spec", self.spec(self.source(source_id="doc-other", allowed_urls=[SECOND_URL])), "--write")[0], 2)  # no two looks agree: not runnable
        self.assertEqual((self.calls.count(SECOND_URL), ev.REGISTRY_PATH.read_bytes() == before), (6, True))

    def test_nothing_is_written_when_anything_in_the_spec_is_wrong(self):
        long_excerpt = "Give a check it can run" + " and more" * 60
        bad = {
            "excerpt that is not on the page": self.source([self.rule(source_excerpt="Skip every check you can run")]),
            "excerpt that is too long": self.source([self.rule(source_excerpt=long_excerpt)]),
            "rule for a target the source does not serve": self.source([self.rule(target_ids=["codex"])]),
            "rule with an unknown purpose": self.source([self.rule(purposes=["chat"])]),
            "rule with no exclusions": self.source([self.rule(exclusions=[])]),
            "rule id that exists": self.source([self.rule("rule-verify")]),
            "source id that exists": self.source(source_id="doc-a"),
            "page that is registered": self.source(allowed_urls=[A_URL]),
            "host nobody uses": self.source(allowed_urls=["https://evil.example.net/page"]),
            "source with no rule": self.source([]),
            "API-only source whose rule is for every surface": self.source(surfaces=["api"]),
            "model's guide whose rule is for every model": self.source(models=["model-x"]),
        }
        for name, source in bad.items():
            with self.subTest(name):
                self.assertEqual(self.tool("adopt", "--spec", self.spec(source), "--write")[0], 1)
                self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)
        # one good source and one bad one: all or nothing
        self.responses[SECOND_URL] = (200, HTML, self.page)
        two = self.spec(self.source(), self.source([self.rule(source_excerpt="Skip every check you can run")], source_id="doc-two", allowed_urls=[SECOND_URL]))
        self.assertEqual(self.tool("adopt", "--spec", two, "--write")[0], 1)
        self.assertIn(SECOND_URL, self.calls)  # the good source was read and the bad one reached, and still nothing was written
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_the_spec_itself_must_be_well_formed(self):
        # spec() writes one file, so each case is built where it is used: building them all first would test only the last one
        cases = {"unknown key": dict(reviewer="x"), "no reviewer": dict(reviewer_id=" "), "wrong version": dict(schema_version=2), "no sources": dict(sources=[]),
                 "claims a person": dict(reviewer_id="human:Mallory"), "claims a person, other case": dict(reviewer_id=" Human:Mallory")}
        for name, over in cases.items():
            with self.subTest(name):
                self.assertEqual(self.tool("adopt", "--spec", self.spec(**over))[0], 1)
        self.assertEqual(self.tool("adopt", "--spec", str(self.tmp / "missing.json"))[0], 2)

    def test_a_registry_not_in_the_writers_layout_is_refused_before_anything_is_fetched(self):
        ev.REGISTRY_PATH.write_text(json.dumps(self.registry(), indent=1), encoding="utf-8")
        self.assertEqual(self.tool("adopt", "--spec", self.spec())[0], 1)
        self.assertEqual(self.calls, [])


class RereviewBase(AdoptBase):
    """A registry whose rules were reviewed against the fixture page, and a page that can be changed afterwards."""
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(maintain, "STORE", self.tmp / "store"), mock.patch.object(maintain, "ROOT", self.tmp),
                  mock.patch.object(maintain, "LEDGER", self.tmp / "human-reviews.json")):
            p.start()
            self.addCleanup(p.stop)

    SRC = ["doc-a", "doc-api"]  # the sources the fixture page stands for; doc-md's rule cites text its page never had
    SRC_ARGS = ["--source", "doc-a", "--source", "doc-api"]

    def look(self, **kw):
        return maintain.rereview(only=kw.pop("only", self.SRC), context=kw.pop("context", 1), **kw)

    def change_page(self, old, new):
        page = self.page.replace(old, new)
        self.assertNotEqual(page, self.page)
        self.responses[A_URL] = self.responses[API_URL] = (200, HTML, page)
        return page

    def sha_now(self, sid):
        src = self.source_of(sid)
        return ev.sha_bytes(ev.process_body(src, self.responses[src["allowed_urls"][0]][2], "text/html").encode("utf-8"))

    def source_of(self, sid):
        return next(s for s in ev.load_registry()[0]["sources"] if s["source_id"] == sid)

    def rules_of(self, sid="doc-a"):
        return {r["rule_id"]: r for r in ev.load_registry()[0]["rules"] if r["source_id"] == sid}

    def rows(self, rep):
        return {r["rule_id"]: r["class"] for r in rep["rules"]}


class TestRereview(RereviewBase):
    FAR = (b"tests/test_date.py", b"tests/test_dates.py")  # the last line of the page: not next to the excerpts
    NEAR = (b"a screenshot to compare.", b"a screenshot to compare, unless it is trivial.")  # inside the paragraph that holds the excerpt

    def test_a_page_that_is_unchanged_has_nothing_to_renew_and_its_bodies_are_kept(self):
        rep = self.look()
        self.assertEqual((rep["verdict"], rep["rules"], rep["bodies_kept"] > 0), ("CURRENT", [], True))
        self.assertEqual(self.look()["bodies_kept"], 0)  # kept once
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_a_page_that_changed_elsewhere_is_renewed_against_the_body_kept_earlier(self):
        self.look()  # the look before the change keeps the body the rules were reviewed against
        self.change_page(*self.FAR)
        rep = self.look()
        self.assertEqual(rep["verdict"], "READY_TO_STAMP")
        self.assertEqual(set(self.rows(rep).values()), {"context_unchanged"})
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)  # a dry run
        hunk = rep["sources"][0]["changes"][0]["hunks"][0]
        self.assertEqual((hunk["removed"], hunk["added"]), (["  tests/test_date.py"], ["  tests/test_dates.py"]))  # what changed is shown
        code, out, err = self.tool("rereview", *self.SRC_ARGS, "--context", "1", "--write")
        rep = json.loads(out)
        self.assertEqual((code, rep["verdict"], rep["written"], rep["reviewer_id"]), (0, "STAMPED", True, maintain.MACHINE_REVIEWER))
        self.assertIn("written", err)
        after = ev.REGISTRY_PATH.read_bytes()
        self.assertEqual(maintain.dump_registry(json.loads(after.decode("utf-8"))).encode("utf-8"), after)  # the writer's own layout
        for sid in ("doc-a", "doc-api"):
            for r in self.rules_of(sid).values():
                self.assertEqual((r["reviewer_id"], r["source_body_sha256"] == self.sha_now(sid)), (maintain.MACHINE_REVIEWER, True))
        self.assertEqual(self.rules_of("doc-md")["rule-md"]["reviewer_id"], "unit-test")  # a rule of a page that did not change is left as it was
        self.assertEqual(ev.drift(None, self.SRC)["verdict"], "CURRENT")
        self.assertEqual(self.look()["verdict"], "CURRENT")

    def test_a_change_next_to_the_excerpt_is_left_to_a_person_and_shown(self):
        self.look()
        self.change_page(*self.NEAR)
        code, out, _ = self.tool("rereview", *self.SRC_ARGS, "--context", "1", "--write")
        rep = json.loads(out)
        self.assertEqual((code, rep["verdict"], rep["written"]), (2, "NEEDS_REVIEW", False))
        self.assertEqual(set(self.rows(rep).values()), {"context_changed"})
        row = next(r for r in rep["rules"] if r["rule_id"] == "rule-verify")
        self.assertTrue(any("unless it is trivial" in line for line in row["diff"]), row["diff"])
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_a_rule_a_reviewer_accepts_is_renewed_with_their_words_and_the_others_are_not(self):
        self.look()
        self.change_page(*self.NEAR)
        reviewer = "unit-test (read the diff, the rule still holds; not human)"
        code, out, _ = self.tool("rereview", *self.SRC_ARGS, "--context", "1", "--accept", "rule-verify", "--reviewer", reviewer, "--write")
        rep = json.loads(out)
        self.assertEqual((code, rep["verdict"]), (2, "NEEDS_REVIEW"))  # the others still wait
        self.assertEqual({r["rule_id"] for r in rep["rules"] if r["renewed"]}, {"rule-verify"})
        doc_a = self.rules_of("doc-a")
        self.assertEqual((doc_a["rule-verify"]["reviewer_id"], doc_a["rule-verify"]["source_body_sha256"] == self.sha_now("doc-a")), (reviewer, True))
        self.assertEqual(doc_a["rule-standing"]["reviewer_id"], "unit-test")  # same excerpt, not accepted

    def test_what_may_not_be_written_is_refused_before_anything_is_looked_at(self):
        self.look()
        self.change_page(*self.NEAR)
        for argv, why in ((["--accept", "rule-verify", "--write"], "--accept needs --reviewer"),
                          (["--accept", "rule-verify", "--reviewer", "human:Someone", "--write"], "never records a human review"),
                          (["--accept", "rule-verify", "--reviewer", "a person", "--write"], "never records a human review"),
                          (["--accept", "no-such-rule", "--reviewer", "x; not human"], "unknown rules"),
                          (["--source", "no-such-source"], "unknown source ids")):
            with self.subTest(argv):
                code, out, err = self.tool("rereview", *self.SRC_ARGS, "--context", "1", *argv)
                self.assertEqual(code, 1)
                self.assertIn(why, err)
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_without_the_old_body_nothing_is_renewed_by_itself_and_old_runs_can_supply_it(self):
        old_body = ev.process_body(self.source_of("doc-a"), self.page, "text/html")
        self.change_page(*self.FAR)
        rep = maintain.rereview(only=["doc-a"], context=1)
        self.assertEqual((rep["verdict"], set(self.rows(rep).values())), ("NEEDS_REVIEW", {"old_body_unavailable"}))
        self.assertEqual(rep["sources"][0]["changes"][0]["old_body"], "unavailable")
        past = self.tmp / "past" / "run-x" / "snapshots" / "doc-a"
        past.mkdir(parents=True)
        with open(past / "body.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write(old_body)
        (self.tmp / "past" / "run-x" / "snapshots" / "doc-a" / "other.txt").write_text("unrelated", encoding="utf-8")
        rep = maintain.rereview(only=["doc-a"], old_dirs=[self.tmp / "past"], context=1)
        self.assertEqual((rep["verdict"], set(self.rows(rep).values())), ("READY_TO_STAMP", {"context_unchanged"}))
        # a body whose hash is not the recorded one is not the old body, whatever its name says
        with open(past / "body.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write(old_body + "extra line\n")
        self.assertEqual(set(self.rows(maintain.rereview(only=["doc-a"], old_dirs=[self.tmp / "past"], context=1)).values()), {"old_body_unavailable"})

    def test_an_excerpt_that_is_gone_cannot_be_renewed_even_when_accepted(self):
        self.look()
        self.change_page(b"Give a check it can run", b"Give it something to check against")
        reviewer = "unit-test (judged by eye; not human)"
        rep = self.look(accept=["rule-verify"], reviewer=reviewer, write=True)
        self.assertEqual((rep["verdict"], rep["written"], self.rows(rep)["rule-verify"]), ("NEEDS_REVIEW", False, "excerpt_missing"))
        self.assertFalse(next(r for r in rep["rules"] if r["rule_id"] == "rule-verify")["renewable"])
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_a_source_that_does_not_answer_is_incomplete_not_current(self):
        self.responses[A_URL] = (503, HTML, b"")
        code, out, _ = self.tool("rereview", "--source", "doc-a")
        rep = json.loads(out)
        self.assertEqual((code, rep["verdict"], rep["sources"][0]["status"]), (2, "INCOMPLETE", "blocked"))

    def test_a_run_is_read_without_the_network(self):
        self.look()
        self.change_page(*self.FAR)
        code, _ = self.fetch("run-1", "live", target_id="claude-code", purpose="persistent-instructions")
        self.assertEqual(code, 0)
        calls = len(self.calls)
        rep = maintain.rereview(run=self.run_dir("run-1"), context=1)
        self.assertEqual((len(self.calls), rep["mode"], rep["verdict"]), (calls, "run", "READY_TO_STAMP"))
        self.assertEqual({r["source_id"] for r in rep["rules"]}, {"doc-a"})  # the run holds the sources it selected

    def test_the_old_body_is_kept_only_if_it_is_the_body_its_hash_names(self):
        self.assertTrue(maintain.keep_body("doc-a", ev.sha_bytes(b"abc\n"), "abc\n"))
        self.assertFalse(maintain.keep_body("doc-a", ev.sha_bytes(b"abc\n"), "abc\n"))
        self.assertFalse(maintain.keep_body("doc-a", ev.sha_bytes(b"abc\n"), "abd\n"))
        self.assertEqual(maintain.stored_body("doc-a", ev.sha_bytes(b"abc\n")), "abc\n")
        self.assertIsNone(maintain.stored_body("doc-a", ev.sha_bytes(b"other")))


class TestKeep(RereviewBase):
    """`keep` is what the weekly job runs, so that the day a page changes there is an old body to compare with."""
    def stored(self, sid):
        return sorted(p.name for p in (maintain.STORE / sid).glob("*.txt")) if (maintain.STORE / sid).is_dir() else []

    def test_it_keeps_the_body_the_rules_were_reviewed_against_once(self):
        rep = maintain.keep(only=self.SRC)
        self.assertEqual((rep["verdict"], rep["bodies_kept"], rep["without_old_body"]), ("COMPLETE", 2, []))
        self.assertEqual(self.stored("doc-a"), [self.sha_now("doc-a") + ".txt"])
        self.assertEqual(maintain.keep(only=self.SRC)["bodies_kept"], 0)
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_a_page_that_changed_since_the_last_keep_does_not_replace_the_old_body(self):
        maintain.keep(only=self.SRC)
        old = self.stored("doc-a")
        self.change_page(*TestRereview.FAR)
        rep = maintain.keep(only=self.SRC)
        self.assertEqual((rep["verdict"], rep["bodies_kept"], self.stored("doc-a")), ("COMPLETE", 0, old))
        self.assertEqual({r["status"] for r in rep["sources"]}, {"page_changed"})
        self.assertEqual(self.look()["verdict"], "READY_TO_STAMP")  # and rereview finds that old body

    def test_a_page_that_was_never_kept_and_has_changed_leaves_the_old_body_missing(self):
        self.change_page(*TestRereview.FAR)
        rep = maintain.keep(only=self.SRC)
        self.assertEqual((rep["verdict"], len(rep["without_old_body"])), ("PARTIAL", 2))
        code, out, err = self.tool("keep", *self.SRC_ARGS)
        self.assertEqual((code, json.loads(out)["verdict"]), (2, "PARTIAL"))
        self.assertIn("no old body for 2", err)

    def test_a_page_that_cannot_be_fetched_is_reported_and_keeps_what_was_kept(self):
        maintain.keep(only=self.SRC)
        self.responses[A_URL] = (500, HTML, b"")
        rep = maintain.keep(only=["doc-a"])
        self.assertEqual(([r["status"] for r in rep["sources"]], rep["verdict"]), (["not_fetched"], "COMPLETE"))

    def test_bodies_no_rule_refers_to_are_dropped_only_when_no_source_filter_is_given(self):
        maintain.keep(only=self.SRC)
        stray = maintain.STORE / "doc-a" / ("0" * 64 + ".txt")
        stray.write_text("old\n", encoding="utf-8")
        maintain.keep(only=self.SRC)
        self.assertTrue(stray.exists())  # a partial look prunes nothing
        rep = maintain.keep()
        self.assertEqual((rep["bodies_pruned"], stray.exists()), (1, False))
        self.assertEqual(self.stored("doc-a"), [self.sha_now("doc-a") + ".txt"])

    def test_an_unknown_source_is_refused(self):
        with self.assertRaises(ev.ContractError):
            maintain.keep(only=["nope"])


class TestRereviewJudgement(unittest.TestCase):
    RULE = {"source_excerpt": "Use short sentences."}
    BODY = "# Style\n\nIntro line.\n\nUse short sentences.\n\nNext line.\n\n## Other\n\nUnrelated text.\n"

    def judge(self, old, new, context=2):
        return maintain.judge(self.RULE, new, old, context)

    def test_the_neighbourhood_is_the_excerpt_the_lines_around_it_the_heading_above_and_the_number_of_occurrences(self):
        n = maintain.neighbourhood(self.BODY, "Use short sentences.", 1)
        self.assertEqual((n["at"], n["heading"], n["count"], n["lines"]), ([5, 5], "# Style", 1, ["", "Use short sentences.", ""]))
        self.assertEqual(maintain.neighbourhood("a\nb\nc\n", "b\nc", 0)["at"], [2, 3])  # an excerpt may span lines
        self.assertIsNone(maintain.neighbourhood(self.BODY, "not there", 1))

    def test_each_way_the_surroundings_can_change_is_seen(self):
        same = self.judge(self.BODY, self.BODY.replace("Unrelated text.", "Other unrelated text."))
        self.assertEqual((same["class"], same["lines"]), ("context_unchanged", [5, 5]))
        cases = {"a line next to it": self.BODY.replace("Next line.", "Next line, changed."),
                 "the heading above it": self.BODY.replace("# Style", "# Writing style"),
                 "a second occurrence": self.BODY + "\nUse short sentences.\n"}
        for name, new in cases.items():
            with self.subTest(name):
                j = self.judge(self.BODY, new)
                self.assertEqual(j["class"], "context_changed")
                self.assertTrue(j["diff"] or name == "a second occurrence")
        self.assertEqual(self.judge(self.BODY, self.BODY.replace("Use short sentences.", "Prefer short sentences."))["class"], "excerpt_missing")
        self.assertEqual(self.judge(None, self.BODY)["class"], "old_body_unavailable")
        self.assertEqual(self.judge(self.BODY.replace("Use short sentences.", "Something else."), self.BODY)["class"], "context_changed")  # the old body lacks it

    def test_a_wider_window_sees_a_change_a_narrow_one_does_not(self):
        new = self.BODY.replace("Intro line.", "Intro line, changed.")
        self.assertEqual(self.judge(self.BODY, new, context=1)["class"], "context_unchanged")
        self.assertEqual(self.judge(self.BODY, new, context=3)["class"], "context_changed")

    def test_a_risk_word_added_far_from_the_excerpt_in_its_section_is_not_renewed_mechanically(self):
        body = "# Style\n\nUse short sentences.\n\n" + "Filler.\n\n" * 8 + "End.\n\n# Other\n\nUnrelated.\n"
        for added, cls in (("This option is deprecated.", "section_risk"), ("A harmless sentence.", "context_unchanged")):
            with self.subTest(added):
                j = self.judge(body, body.replace("End.", added), context=2)
                self.assertEqual(j["class"], cls)
        self.assertEqual(j.get("added"), None)
        j = self.judge(body, body.replace("End.", "No longer supported."), context=2)
        self.assertEqual(j["added"], ["No longer supported."])
        self.assertEqual(self.judge(body, body.replace("Unrelated.", "Deprecated."), context=2)["class"], "context_unchanged")  # another section


class TestReview(RereviewBase):
    """review is for a person at a terminal: it shows a rule with the page it cites and records a name only when one is typed."""
    ONLY = dict(only=["doc-a"], rule_ids=["rule-verify", "rule-model"])

    def go(self, answers, interactive=True, **kw):
        said, typed = [], iter(answers)

        def ask(prompt):
            said.append(prompt)
            try:
                return next(typed)
            except StopIteration:
                raise EOFError

        with mock.patch.object(maintain, "interactive", lambda: interactive):
            rep = maintain.review(ask=ask, say=said.append, **{**self.ONLY, **kw})
        return rep, "\n".join(said)

    def test_it_will_not_run_where_nobody_can_answer(self):
        with mock.patch.object(maintain, "interactive", lambda: False):
            code, out, err = self.tool("review", "--rule", "rule-verify")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("interactive terminal", err)
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)
        self.assertFalse(maintain.LEDGER.exists())
        with mock.patch.object(maintain, "interactive", lambda: False), self.assertRaises(ev.Unreadable):
            maintain.review(ask=lambda p: "someone", say=lambda s: None)
        for streams in ((True, False), (False, True)):  # both ends must be terminals, and CI must not be set
            with mock.patch.object(sys.stdin, "isatty", lambda: streams[0]), mock.patch.object(sys.stdout, "isatty", lambda: streams[1]):
                self.assertFalse(maintain.interactive())
        with mock.patch.object(sys.stdin, "isatty", lambda: True), mock.patch.object(sys.stdout, "isatty", lambda: True):
            with mock.patch.dict("os.environ", {"CI": "true"}):
                self.assertFalse(maintain.interactive())
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertTrue(maintain.interactive())
            for name in ("GITHUB_ACTIONS", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):  # a terminal opened by an agent or by CI is not a person's
                with self.subTest(name), mock.patch.dict("os.environ", {name: "1"}, clear=True):
                    self.assertFalse(maintain.interactive())
            with mock.patch.dict("os.environ", {"CI": ""}, clear=True):
                self.assertTrue(maintain.interactive())

    def test_a_name_that_is_typed_and_confirmed_is_recorded_against_the_page_that_was_shown(self):
        rep, shown = self.go(["Kondo", "y", ""])
        self.assertEqual((rep["recorded"], rep["skipped"], rep["quit"]), (["rule-verify"], 1, False))
        for needed in ("rule-verify", "Give a check it can run", "when applicable", "adding unrequested work", "審査したときと同じ本文"):
            self.assertIn(needed, shown)
        rules = self.rules_of()
        got = rules["rule-verify"]
        self.assertEqual((got["reviewer_id"], got["source_body_sha256"] == self.sha_now("doc-a"), ev.human_reviewed(got)), ("human:Kondo", True, True))
        self.assertEqual(rules["rule-model"]["reviewer_id"], "unit-test")  # the one whose name was left empty
        self.assertEqual(maintain.human_ledger(), [{"rule_id": "rule-verify", "reviewer_id": "human:Kondo", "reviewed_at": got["reviewed_at"], "source_body_sha256": got["source_body_sha256"]}])
        after = ev.REGISTRY_PATH.read_bytes()
        self.assertEqual(maintain.dump_registry(json.loads(after.decode("utf-8"))).encode("utf-8"), after)
        self.assertEqual(sum(1 for r in ev.load_registry()[0]["rules"] if ev.human_reviewed(r)), 1)

    def test_nothing_is_recorded_without_a_name_and_a_yes(self):
        for answers in (["", ""], ["Kondo", "n", ""], ["Kondo", "", ""], ["x" * 81, "y", ""]):
            with self.subTest(answers):
                rep, _ = self.go(answers)
                self.assertEqual(rep["recorded"], [])
                self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)
        self.assertFalse(maintain.LEDGER.exists())

    def test_q_and_the_end_of_input_stop_the_session_and_keep_what_was_recorded(self):
        rep, _ = self.go(["Kondo", "y", "q"])
        self.assertEqual((rep["recorded"], rep["quit"], rep["remaining"]), (["rule-verify"], True, 1))  # the first is kept; the second was not reviewed
        rep, _ = self.go(["q"], rule_ids=["rule-model", "rule-skill-only"])
        self.assertEqual((rep["recorded"], rep["quit"], rep["remaining"]), ([], True, 2))
        rep, _ = self.go([], rule_ids=["rule-model", "rule-skill-only"])  # input ended
        self.assertEqual((rep["quit"], rep["recorded"]), (True, []))

    def test_a_rule_whose_excerpt_the_page_no_longer_has_is_shown_but_cannot_be_recorded(self):
        self.change_page(b"Give a check it can run", b"Give it something to check against")
        rep, shown = self.go(["Kondo", "y"], rule_ids=["rule-verify"])
        self.assertEqual((rep["recorded"], rep["skipped"]), ([], 1))
        self.assertIn("引用が本文にありません", shown)
        self.assertEqual(ev.REGISTRY_PATH.read_bytes(), self.before)

    def test_a_page_that_changed_since_the_rule_was_reviewed_is_said_so_and_the_new_page_is_what_is_recorded(self):
        self.change_page(*TestRereview.FAR)
        rep, shown = self.go(["Kondo", "y"], rule_ids=["rule-verify"])
        self.assertIn("審査のあとで本文が変わっています", shown)
        self.assertEqual(self.rules_of()["rule-verify"]["source_body_sha256"], self.sha_now("doc-a"))

    def test_a_rule_that_a_person_has_reviewed_is_not_offered_again(self):
        self.go(["Kondo", "y", ""])
        rep, shown = self.go(["", ""])
        self.assertNotIn("rule-verify (", shown)
        rep, _ = self.go([], rule_ids=["rule-verify"])
        self.assertEqual(rep["remaining"], 0)

    def test_rereview_does_not_pass_off_a_machine_renewal_as_the_review_of_a_person(self):
        self.go(["Kondo", "y", ""])
        self.look()  # nothing changed: the person's review stands
        self.assertEqual(self.rules_of()["rule-verify"]["reviewer_id"], "human:Kondo")
        self.change_page(*TestRereview.FAR)
        self.look(write=True)  # the page changed and nobody read it again: what a machine renews is a machine's review
        self.assertEqual(self.rules_of()["rule-verify"]["reviewer_id"], maintain.MACHINE_REVIEWER)

    def test_the_ledger_is_written_before_the_registry_and_taken_back_if_the_registry_cannot_be_written(self):
        real = maintain.replace_registry

        def fail(text, loaded):
            raise OSError("disk full")

        with mock.patch.object(maintain, "replace_registry", fail), self.assertRaises(OSError):
            self.go(["Kondo", "y"])
        self.assertEqual((maintain.human_ledger(), ev.REGISTRY_PATH.read_bytes()), ([], self.before))  # no entry without a rule, no rule without an entry
        order = []
        with mock.patch.object(maintain, "replace_registry", lambda t, l: (order.append(("registry", maintain.human_ledger())), real(t, l))):
            self.go(["Kondo", "y", ""])
        self.assertEqual([len(ledger) for _, ledger in order], [1])  # when the registry is written the ledger already has the entry

    def test_a_registry_that_changed_while_a_person_was_typing_is_not_written_over(self):
        def ask(prompt):
            if "名前" in prompt:  # an edit made while the person is deciding
                ev.REGISTRY_PATH.write_bytes(ev.REGISTRY_PATH.read_bytes() + b"\n")
                return "Kondo"
            return "y"

        with mock.patch.object(maintain, "interactive", lambda: True), self.assertRaisesRegex(ev.ContractError, "changed since it was read"):
            maintain.review(ask=ask, say=lambda s: None, **self.ONLY)
        self.assertEqual(maintain.human_ledger(), [])
        self.assertNotIn(b"human:Kondo", ev.REGISTRY_PATH.read_bytes())

    def test_unknown_rules_and_a_damaged_ledger_are_reported(self):
        with mock.patch.object(maintain, "interactive", lambda: True):
            code, _, err = self.tool("review", "--rule", "nope")
        self.assertEqual(code, 1)
        maintain.LEDGER.write_text('{"not": "a list"}', encoding="utf-8")
        with self.assertRaises(ev.ContractError):
            maintain.human_ledger()
        maintain.LEDGER.write_text('[{"rule_id": "x"}]', encoding="utf-8")
        with self.assertRaises(ev.ContractError):
            maintain.human_ledger()


class TestTranscripts(Base):
    """A subagent keeps its own transcript, every message marked sidechain under one agentId. That file is the agent's whole record."""
    def setUp(self):
        super().setUp()
        self.run, _ = self.good_run()

    def tagged(self, agent_ids):
        log = self.write_log(self.run, sidechain=True, path=self.tmp / "logs" / "sub.jsonl")
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        for i, row in enumerate(rows):
            row["agentId"] = agent_ids[i % len(agent_ids)]
        log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return log

    def test_a_subagents_own_transcript_is_read_whole_and_one_agent_id_is_required(self):
        self.assertEqual(self.verify(self.run, "live", self.tagged(["agent-1"]))["verdict"], "PASS_LIVE")  # observed, as in a main transcript
        for ids in (["agent-1", "agent-2"], [None]):  # two agents in one file, or sidechain messages that name no agent: not one agent's record
            with self.subTest(ids):
                rep = self.verify(self.run, "live", self.tagged(ids))
                self.assertEqual((rep["verdict"], "generation_write_not_found" in codes(rep)), ("INCOMPLETE", True))

    def test_sidechain_messages_inside_a_main_transcript_are_still_ignored(self):
        main = self.write_log(self.run, path=self.tmp / "logs" / "main.jsonl")
        mixed = main.read_text(encoding="utf-8") + json.dumps({"type": "assistant", "isSidechain": True, "agentId": "agent-1", "message": {"content": [
            {"type": "tool_use", "id": "x1", "name": "Write", "input": {"file_path": str(self.run / "optimized.md")}}]}}) + "\n"
        main.write_text(mixed, encoding="utf-8")
        rows, own = ev.read_transcript(main)
        self.assertFalse(own)
        self.assertEqual([e["id"] for e in ev.parse_transcript(main) if e["kind"] == "use"], ["r1", "w1"])  # the sidechain Write was not taken for the agent's

    def test_a_line_that_is_json_but_not_a_record_does_not_break_the_reader(self):
        log = self.write_log(self.run, path=self.tmp / "logs" / "odd.jsonl")
        log.write_text("123\n[1, 2]\nnull\n" + log.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(self.verify(self.run, "live", log)["verdict"], "PASS_LIVE")


class TestAudit(Base):
    """audit judges how an agent behaved, from its transcript and the run it left; whether the rewrite is good is not its question."""
    def setUp(self):
        super().setUp()
        self.run, _ = self.good_run()
        self.verify(self.run, "live")  # leaves verification.json: INCOMPLETE, since no log was given
        self.body, self.n = self.run / "snapshots" / "doc-a" / "body.txt", 0
        self.script = "python3 .claude/skills/official-prompt-tailor/scripts/evidence.py"
        cmd = lambda name, more="": ("Bash", f"{self.script} {name} --run {self.run}{more}")  # noqa: E731
        self.good = [("Bash", f"{self.script} fetch --input i.json --out {self.run} --mode live"), ("Read", self.body), cmd("drift"), cmd("rules"),
                     ("Write", self.run / "optimized.md"), ("Write", self.run / "recommendations.json"), cmd("verify", " --mode live"),
                     cmd("sources", " --markdown")]
        self.cmd = cmd

    def test_audit_compares_the_report_with_the_log_used_by_sources(self):
        path = self.tmp / "logs" / f"audit-{self.n + 1}.jsonl"
        steps = self.good[:-1] + [("Bash", f"{self.script} sources --run {self.run} --markdown --host-log {path}")]
        log = self.record(steps=steps, prompt=f"Use this host log: {path}")
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        rows[-1]["message"]["content"][0]["text"] = self.answer(report=ev.render_sources(ev.sources_report(self.run, log)))
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        self.assertEqual(maintain.audit(self.run, log)["verdict"], "PASS")

    def codex_record(self, steps=None):
        old = ev.load_json(self.run / "input.json")
        old["execution_host"] = "codex"
        ev.write_json(self.run / "input.json", old)
        claude = self.record(steps=steps)
        records = [json.loads(line) for line in claude.read_text(encoding="utf-8").splitlines()]
        rows = [{"type": "session_meta", "payload": {"cli_version": "0.155.1", "cwd": str(self.tmp)}}]
        for d in records:
            for b in d["message"]["content"]:
                if b["type"] == "tool_use":
                    arg = b["input"]
                    command = arg.get("command", "cat " + str(arg.get("file_path", "")))
                    if b["name"] == "Write":
                        command = "python3 gen.py > " + arg["file_path"]
                    rows.append({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": b["id"],
                                "input": "text(await tools.exec_command({cmd:" + json.dumps(command) + "}));"}})
                elif b["type"] == "tool_result":
                    rows.append({"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": b["tool_use_id"],
                                "output": [{"text": "Script completed"}, {"text": b["content"]}]}})
                elif b["type"] == "text":
                    rows.append({"type": "response_item", "payload": {"type": "message", "role": d["type"],
                                "phase": "final_answer" if d["type"] == "assistant" else None,
                                "content": [{"type": "output_text" if d["type"] == "assistant" else "input_text", "text": b["text"]}]}})
        claude.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return claude

    def test_codex_audit_observes_procedure_and_final_answer(self):
        log = self.codex_record()
        rep = maintain.audit(self.run, log)
        self.assertEqual(rep["verdict"], "PASS", rep)

    def test_audit_refuses_unknown_codex_versions_without_crashing(self):
        log = self.codex_record()
        log.write_text(log.read_text(encoding="utf-8").replace("0.155.1", "0.200.0"), encoding="utf-8")
        rep = maintain.audit(self.run, log)
        self.assertEqual((rep["verdict"], rep["checks"][0]["id"]), ("FAIL", "host_log_valid"))

    def test_audit_rejects_malformed_codex_message_content(self):
        for content in (None, [{"type": "input_text", "text": None}]):
            with self.subTest(content=content):
                log = self.codex_record()
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": content}}) + "\n")
                self.assertEqual(maintain.audit(self.run, log)["checks"][0]["id"], "host_log_valid")

    def test_codex_audit_detects_missing_pre_generation_drift(self):
        log = self.codex_record([step for step in self.good if not (step[0] == "Bash" and " drift " in step[1])])
        rep = maintain.audit(self.run, log)
        self.assertIn("drift_before_writing", [c["id"] for c in rep["checks"] if c["status"] == "FAIL"])

    def answer(self, lines=("参照検証: INCOMPLETE", "意味審査: NOT_REVIEWED", "効果:     NOT_EVALUATED"), note="この案は候補です。", prompt=None, report=None):
        prompt = (self.run / "optimized.md").read_text(encoding="utf-8") if prompt is None else prompt
        report = ev.render_sources(ev.sources_report(self.run)) if report is None else report
        return "\n".join(lines) + f"\n\n{note}\n\n{prompt}\n\n{report}"

    def record(self, steps=None, answer=None, prompt="Claude Code 向けに最適化して", own=False, handback=False):
        steps, answer = self.good if steps is None else steps, self.answer() if answer is None else answer
        body_lines = self.body.read_text(encoding="utf-8").split("\n")[:-1]
        meta = {"isSidechain": True, "agentId": "agent-1"} if own else {"isSidechain": False}
        rows = [{"type": "user", **meta, "message": {"content": [{"type": "text", "text": prompt}]}}]
        for i, (name, arg, *more) in enumerate(steps):
            inp = {"command": arg} if name == "Bash" else {"file_path": str(arg), **(more[0] if more else {})}
            shown = "\n".join(f"{n:6d}\t{line}" for n, line in enumerate(body_lines, 1)) if name == "Read" and Path(arg) == self.body else "ok"
            rows.append({"type": "assistant", **meta, "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}})
            rows.append({"type": "user", **meta, "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": shown, "is_error": False}]}})
        if handback:  # a subagent's report goes through the SubagentHandback tool; the text after it is not the answer
            rows.append({"type": "assistant", **meta, "message": {"content": [{"type": "tool_use", "id": "hb", "name": "SubagentHandback", "input": {"message": answer}}]}})
            rows.append({"type": "assistant", **meta, "message": {"content": [{"type": "text", "text": "(done)"}]}})
        else:
            rows.append({"type": "assistant", **meta, "message": {"content": [{"type": "text", "text": answer}]}})
        self.n += 1
        path = self.tmp / "logs" / f"audit-{self.n}.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return path

    def statuses(self, **kw):
        rep = maintain.audit(self.run, self.record(**kw))
        return rep, {c["id"]: c["status"] for c in rep["checks"]}

    def tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = maintain.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_run_that_followed_the_procedure_and_said_so_straight_passes(self):
        for own in (False, True):  # the main thread's record, and a subagent's own
            with self.subTest(own=own):
                rep, status = self.statuses(own=own)
                self.assertEqual((rep["verdict"], rep["sources_verdict"], set(status.values())), ("PASS", "CONSISTENT", {"PASS"}), rep["checks"])

    def test_an_input_that_selects_no_source_has_nothing_to_read_but_the_rest_of_the_procedure_still_counts(self):
        code, _ = self.fetch("run-0", target_id="gemini")
        self.assertEqual(code, 0)
        self.run = self.run_dir("run-0")
        self.generate(self.run, NO_SOURCE_CHANGES, optimized=NO_SOURCE_OPT, status="provisional")
        self.verify(self.run, "live")
        steps = [("Bash", f"{self.script} fetch --input i.json --out {self.run} --mode live"), self.cmd("drift"), self.cmd("rules"),
                 ("Write", self.run / "optimized.md"), ("Write", self.run / "recommendations.json"), self.cmd("verify", " --mode live"),
                 self.cmd("sources", " --markdown")]
        rep, status = self.statuses(steps=steps)
        self.assertEqual((rep["verdict"], rep["sources_verdict"], status["selected_bodies_were_read"]), ("PASS", "CONSISTENT", "NA"), rep["checks"])
        self.assertEqual({s for k, s in status.items() if k != "selected_bodies_were_read"}, {"PASS"})
        rep, status = self.statuses(steps=steps[:1] + steps[3:])  # NA excuses only the read: drift and rules are still owed
        self.assertEqual((rep["verdict"], status["drift_before_writing"], status["rules_before_writing"]), ("FAIL", "FAIL", "FAIL"))

    def test_each_departure_from_the_procedure_is_named(self):
        g, verify, sources = self.good, self.good[6], self.good[7]
        cases = {
            "no fetch": (g[1:], "fetched"),
            "drift never run": (g[:2] + g[3:], "drift_before_writing"),
            "rules run after the writing": (g[:3] + g[4:6] + [g[3]] + g[6:], "rules_before_writing"),
            "verify never run": (g[:6] + [sources], "verified_after_writing"),
            "verify before the writing only": (g[:4] + [verify] + g[4:6] + [sources], "verified_after_writing"),
            "sources never run": (g[:7], "sources_after_verify"),
            "sources before verify": (g[:6] + [sources, verify], "sources_after_verify"),
            "a selected body was not read": (g[:1] + g[2:], "selected_bodies_were_read"),
            "a log the user did not give": (g + [self.cmd("verify", " --mode live --host-log /home/u/session.jsonl")], "no_log_the_user_did_not_give"),
            "registry read whole": (g[:1] + [("Read", ev.REGISTRY_PATH)] + g[1:], "registry_not_read_whole"),
            "registry catted": (g[:1] + [("Bash", f"cat {ev.REGISTRY_PATH}")] + g[1:], "registry_not_read_whole"),
            "nothing written": (g[:4] + g[6:], "wrote_the_result"),
        }
        for name, (steps, failed) in cases.items():
            with self.subTest(name):
                rep, status = self.statuses(steps=steps)
                self.assertEqual((rep["verdict"], status[failed]), ("FAIL", "FAIL"), rep["checks"])

    def test_reading_part_of_the_registry_and_a_log_the_user_gave_are_fine(self):
        steps = self.good[:1] + [("Read", ev.REGISTRY_PATH, {"limit": 20})] + self.good[1:]
        self.assertEqual(self.statuses(steps=steps)[0]["verdict"], "PASS")
        steps = self.good + [self.cmd("verify", " --mode live --host-log /home/u/session.jsonl")]
        self.assertEqual(self.statuses(steps=steps, prompt="ログは /home/u/session.jsonl です")[0]["verdict"], "PASS")

    def test_an_answer_that_misreports_the_records_or_claims_too_much_is_named(self):
        lines = lambda a, b, c: (f"参照検証: {a}", f"意味審査: {b}", f"効果: {c}")  # noqa: E731
        report = ev.render_sources(ev.sources_report(self.run))
        cases = {
            "no lines at all": (self.answer(lines=("結論です",)), "answer_has_three_lines"),
            "PASS_LIVE over an INCOMPLETE record": (self.answer(lines=lines("PASS_LIVE", "NOT_REVIEWED", "NOT_EVALUATED")), "answer_matches_the_records"),
            "a review nobody made": (self.answer(lines=lines("INCOMPLETE", "PASS", "NOT_EVALUATED")), "answer_matches_the_records"),
            "an improvement nobody measured": (self.answer(lines=lines("INCOMPLETE", "NOT_REVIEWED", "IMPROVED_ON_TEST_SET")), "answer_matches_the_records"),
            "called verified": (self.answer(note="この案は検証済みです。候補ではありません。"), "no_overclaim"),
            "called improved": (self.answer(note="候補ですが、改善済みの文面です"), "no_overclaim"),
            "not called a candidate": (self.answer(note="参考にしてください。"), "no_overclaim"),
            "the prompt is missing": (self.answer(prompt="(省略)"), "copy_prompt_in_answer"),
            "the report is missing": (self.answer(report=""), "sources_report_pasted_as_printed"),
            "the report was edited": (self.answer(report=report.replace("CONSISTENT", "PASS")), "sources_report_pasted_as_printed"),
            "the report was summarised": (self.answer(report="資料は 1 件で、整合しています。"), "sources_report_pasted_as_printed"),
        }
        for name, (answer, failed) in cases.items():
            with self.subTest(name):
                rep, status = self.statuses(answer=answer)
                self.assertEqual((rep["verdict"], status[failed]), ("FAIL", "FAIL"), rep["checks"])

    def test_the_answer_of_an_agent_that_hands_its_report_back_is_the_report(self):
        rep, status = self.statuses(own=True, handback=True)
        self.assertEqual((rep["verdict"], set(status.values())), ("PASS", {"PASS"}), rep["checks"])
        self.assertEqual(self.statuses(own=True, handback=True, answer=self.answer(lines=("参照検証: PASS_LIVE", "意味審査: NOT_REVIEWED", "効果: NOT_EVALUATED")))[1]["answer_matches_the_records"], "FAIL")

    def test_a_command_spelled_with_a_variable_it_assigns_is_the_same_command(self):
        script = self.script.split()[-1]
        spelled = lambda name, more="": ("Bash", f"S={script}; python3 $S {name} --run {self.run}{more}; echo \"EXIT=$?\"")  # noqa: E731
        steps = self.good[:2] + [spelled("drift"), spelled("rules")] + self.good[4:6] + [spelled("verify", " --mode live"), spelled("sources", " --markdown")]
        self.assertEqual(self.statuses(steps=steps)[0]["verdict"], "PASS")
        self.assertEqual(maintain.expand_variables('python3 $S drift --run r'), 'python3 $S drift --run r')  # an unassigned variable is left alone
        loop = ("Bash", f"S={script}; for c in drift rules; do python3 $S $c --run {self.run}; done")  # one command line, two commands
        steps = self.good[:2] + [loop] + self.good[4:]
        self.assertEqual(self.statuses(steps=steps)[0]["verdict"], "PASS")
        self.assertEqual(self.statuses(steps=self.good[:2] + [("Bash", f"S={script}; for c in fetch; do python3 $S $c --run {self.run}; done")] + self.good[4:])[1]["drift_before_writing"], "FAIL")

    def test_a_denied_claim_is_not_an_overclaim_however_it_is_denied(self):
        for note in ("この案は検証済みではなく候補です。", "検証済みでないので、候補です。", "改善済みとは言えません。候補です。"):
            with self.subTest(note):
                self.assertEqual(self.statuses(answer=self.answer(note=note))[1]["no_overclaim"], "PASS")

    def test_what_is_not_an_overclaim_is_left_alone(self):
        for name, answer in {"verified, denied": self.answer(note="この案は候補です。「検証済み」「改善済み」とは書けません。"),
                             "blank lines reflowed in the report": self.answer(report=ev.render_sources(ev.sources_report(self.run)).replace("\n", "\n\n"))}.items():
            with self.subTest(name):
                self.assertEqual(self.statuses(answer=answer)[0]["verdict"], "PASS")
        for name, lines in {"one space": ("参照検証: INCOMPLETE", "意味審査: NOT_REVIEWED", "効果: NOT_EVALUATED"),
                            "no space": ("参照検証:INCOMPLETE", "意味審査:NOT_REVIEWED", "効果:NOT_EVALUATED"),
                            "full-width colon and bold": ("**参照検証:INCOMPLETE**".replace(":", "\uff1a"), "意味審査\uff1a `NOT_REVIEWED`", "効果 \uff1a NOT_EVALUATED")}.items():
            with self.subTest(name):
                self.assertEqual(self.statuses(answer=self.answer(lines=lines))[1]["answer_has_three_lines"], "PASS")

    def test_the_report_of_an_older_format_is_a_warning_and_the_invisible_mark_line_is_not_compared(self):
        report = ev.render_sources(ev.sources_report(self.run))
        mark = f"<!-- sources-report format {ev.SOURCES_FORMAT} -->"
        self.assertTrue(report.startswith(mark))
        older = report.replace("判定", "結果")  # the wording of an earlier version
        cases = {"the mark line dropped": (report.replace(mark + "\n", ""), "PASS", "PASS"),
                 "a lower mark": (older.replace(mark, mark.replace(str(ev.SOURCES_FORMAT), str(ev.SOURCES_FORMAT - 1))), "PASS", "WARN"),
                 "no mark, as before the mark existed": (older.replace(mark + "\n", ""), "PASS", "WARN"),
                 "the current mark, the text altered": (older, "FAIL", "FAIL")}
        for name, (pasted, verdict, status) in cases.items():
            with self.subTest(name):
                rep, got = self.statuses(answer=self.answer(report=pasted))
                self.assertEqual((rep["verdict"], got["sources_report_pasted_as_printed"]), (verdict, status), rep["checks"])

    def test_a_missing_record_means_not_done_so_nothing_can_be_claimed_from_it(self):
        # found by the Codex review: with no evaluation.json the effect line used to go unchecked
        (self.run / "evaluation.json").unlink()
        improved = self.answer(lines=("参照検証: INCOMPLETE", "意味審査: NOT_REVIEWED", "効果: IMPROVED_ON_TEST_SET"))
        self.assertEqual(self.statuses(answer=improved)[1]["answer_matches_the_records"], "FAIL")
        self.assertEqual(self.statuses()[1]["answer_matches_the_records"], "PASS")  # NOT_EVALUATED is what a missing evaluation means
        (self.run / "semantic-review.json").write_text(json.dumps({"status": "REVIEWED"}), encoding="utf-8")  # recorded, but assess never ran
        reviewed = self.answer(lines=("参照検証: INCOMPLETE", "意味審査: PASS", "効果: NOT_EVALUATED"))
        self.assertEqual(self.statuses(answer=reviewed)[1]["answer_matches_the_records"], "FAIL")

    def test_an_adoptable_proposal_may_be_called_verified_but_not_improved_without_a_measurement(self):
        (self.run / "verification.json").write_text(json.dumps({"verdict": "PASS_LIVE"}), encoding="utf-8")
        (self.run / "semantic-review.json").write_text(json.dumps({"status": "REVIEWED"}), encoding="utf-8")
        (self.run / "semantic-verification.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
        lines = ("参照検証: PASS_LIVE", "意味審査: PASS", "効果: NOT_EVALUATED")
        for note, passes in (("この案は検証済みです。", True), ("「改善済み」とは書けません。", True), ("この案は検証済みで、改善済みです。", False),
                             ("実測済みの案です。", False)):
            with self.subTest(note):
                status = self.statuses(answer=self.answer(lines=lines, note=note))[1]
                self.assertEqual((status["answer_matches_the_records"], status["no_overclaim"]), ("PASS", "PASS" if passes else "FAIL"))
        (self.run / "evaluation.json").write_text(json.dumps({"status": "IMPROVED_ON_TEST_SET"}), encoding="utf-8")
        status = self.statuses(answer=self.answer(lines=("参照検証: PASS_LIVE", "意味審査: PASS", "効果: IMPROVED_ON_TEST_SET"), note="この案は改善済みです。"))[1]
        self.assertEqual((status["answer_matches_the_records"], status["no_overclaim"]), ("PASS", "PASS"))  # with a measurement behind it

    def test_a_literal_of_the_original_that_is_gone_is_a_warning_not_a_failure(self):
        (self.run / "optimized.md").write_text("バグを修正してください。\n公開APIは変更しない。\n", encoding="utf-8")
        rep, status = self.statuses()
        self.assertEqual((rep["verdict"], status["original_literals_kept"]), ("PASS", "WARN"))
        self.assertIn("pytest", next(c["detail"] for c in rep["checks"] if c["id"] == "original_literals_kept"))

    def test_command_prints_the_checks_and_exits_by_the_verdict(self):
        code, out, err = self.tool("audit", "--run", str(self.run), "--transcript", str(self.record()))
        self.assertEqual((code, json.loads(out)["verdict"]), (0, "PASS"))
        self.assertIn("audit: PASS", err)
        code, out, err = self.tool("audit", "--run", str(self.run), "--transcript", str(self.record(steps=self.good[:6])))
        self.assertEqual(code, 1)
        self.assertIn("verified_after_writing", err)
        self.assertEqual(self.tool("audit", "--run", str(self.run), "--transcript", str(self.tmp / "no-such.jsonl"))[0], 2)


class DocsBase(unittest.TestCase):
    """README.md and docs/ are copied, so that a test can break and refresh them."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "docs").mkdir()
        self.paths = {"README_PATH": self.tmp / "README.md", "TARGETS_PATH": self.tmp / "docs" / "targets.md",
                      "TRUST_PATH": self.tmp / "docs" / "trust-boundary.md"}
        for name, src in (("README_PATH", ROOT / "README.md"), ("TARGETS_PATH", ROOT / "docs" / "targets.md"), ("TRUST_PATH", ROOT / "docs" / "trust-boundary.md")):
            shutil.copyfile(src, self.paths[name])
            p = mock.patch.object(maintain, name, self.paths[name])
            p.start()
            self.addCleanup(p.stop)

    def tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = maintain.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def edit(self, name, old, new):
        p = self.paths[name]
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new, 1), encoding="utf-8")


class TestDocs(DocsBase):
    def test_the_documents_of_the_repository_match_the_registry(self):
        # when this fails after a registry change: python3 tools/maintain.py docs --write
        for name, src in (("README_PATH", ROOT / "README.md"), ("TARGETS_PATH", ROOT / "docs" / "targets.md"), ("TRUST_PATH", ROOT / "docs" / "trust-boundary.md")):
            self.assertEqual(self.paths[name].read_bytes(), src.read_bytes())  # the copies are the real files
        self.assertEqual(maintain.docs(), {"schema_version": 1, "written": False, "stale": [], "problems": []})
        self.assertEqual(self.tool("docs")[0], 0)

    def test_stale_numbers_and_tables_are_found_and_refreshed(self):
        before = {n: p.read_bytes() for n, p in self.paths.items()}
        self.edit("README_PATH", "審査済み規則(", "審査済み規則(1")
        self.edit("TARGETS_PATH", "| 4 | 1 | 2 | ", "| 9 | 1 | 2 | ")  # cells of the rule-count table
        self.edit("TARGETS_PATH", "モデルを限定した規則は ", "モデルを限定した規則は 1")
        self.edit("TRUST_PATH", "人の審査は ", "人の審査は 7")
        code, out, err = self.tool("docs")
        self.assertEqual((code, json.loads(out)["stale"]), (2, ["README.md", "targets.md", "trust-boundary.md"]))
        self.assertIn("run docs --write", err)
        code, out, err = self.tool("docs", "--write")
        self.assertEqual((code, json.loads(out)["written"]), (0, True))
        self.assertEqual({n: p.read_bytes() for n, p in self.paths.items()}, before)  # back to what the registry says
        self.assertEqual(self.tool("docs")[0], 0)

    def test_a_document_that_lacks_a_block_or_a_number_is_a_problem_and_nothing_is_written(self):
        self.edit("TARGETS_PATH", "<!-- generated:rule-counts end -->", "")
        self.edit("README_PATH", "資料(", "資料数(")
        self.edit("TARGETS_PATH", "| 4 | 1 | 2 | ", "| 9 | 1 | 2 | ")
        code, out, _ = self.tool("docs", "--write")
        rep = json.loads(out)
        self.assertEqual((code, rep["written"], len(rep["problems"])), (1, False, 2))  # the unmatched number, and the block the document lacks the end of
        self.assertIn("| 9 | 1 | 2 | ", self.paths["TARGETS_PATH"].read_text(encoding="utf-8"))  # the stale cell was left alone

    def test_a_target_the_docs_do_not_know_is_refused_rather_than_left_out(self):
        for name in ("ROUTING_LABELS", "VENDORS"):
            with self.subTest(name):
                cut = {k: v for k, v in getattr(maintain, name).items() if k not in ("codex", "openai")}
                with mock.patch.object(maintain, name, cut):
                    code, _, err = self.tool("docs")
                self.assertEqual(code, 1)
                self.assertIn("ROUTING_LABELS and VENDORS", err)

    def test_a_block_of_unknown_name_is_a_problem(self):
        self.edit("TARGETS_PATH", "generated:rule-counts begin", "generated:rule-countz begin")
        self.edit("TARGETS_PATH", "generated:rule-counts end", "generated:rule-countz end")
        problems = json.loads(self.tool("docs")[1])["problems"]
        self.assertEqual(sorted(problems), ["generated block rule-counts is missing", "unknown generated block rule-countz"])


if __name__ == "__main__":
    unittest.main()
