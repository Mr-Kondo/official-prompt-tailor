"""Unit tests for tools/maintain.py (stdlib unittest, network mocked). Run: python3 -m unittest discover -s tests"""
import contextlib
import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import maintain  # noqa: E402
from test_official_prompt_tailor import A_URL, API_URL, HTML, ROOT, SKILL, Base, codes, ev, sha  # noqa: E402

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


class AdoptBase(MaintainBase):
    """A registry in the writer's layout that already meets the repository's rules, and a page to adopt."""
    def setUp(self):
        super().setUp()

        def tidy(reg):
            reg["rules"] = [r for r in reg["rules"] if r["rule_id"] != "rule-unreviewed"]
            reg["rules"].append({**next(r for r in reg["rules"] if r["rule_id"] == "rule-verify"), "rule_id": "rule-md",
                                 "source_id": "doc-md", "target_ids": ["claude-code"]})
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
        self.assertEqual((code, rep["written"], rep["registry"]["sources"], rep["registry"]["rules"]), (0, False, 4, 9))
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
        for name, spec in {"unknown key": self.spec(reviewer="x"), "no reviewer": self.spec(reviewer_id=" "),
                           "wrong version": self.spec(schema_version=2), "no sources": self.spec(sources=[])}.items():
            with self.subTest(name):
                self.assertEqual(self.tool("adopt", "--spec", spec)[0], 1)
        self.assertEqual(self.tool("adopt", "--spec", str(self.tmp / "missing.json"))[0], 2)

    def test_a_registry_not_in_the_writers_layout_is_refused_before_anything_is_fetched(self):
        ev.REGISTRY_PATH.write_text(json.dumps(self.registry(), indent=1), encoding="utf-8")
        self.assertEqual(self.tool("adopt", "--spec", self.spec())[0], 1)
        self.assertEqual(self.calls, [])


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

    def answer(self, lines=("参照検証: INCOMPLETE", "意味審査: NOT_REVIEWED", "効果:     NOT_EVALUATED"), note="この案は候補です。", prompt=None, report=None):
        prompt = (self.run / "optimized.md").read_text(encoding="utf-8") if prompt is None else prompt
        report = ev.render_sources(ev.sources_report(self.run)) if report is None else report
        return "\n".join(lines) + f"\n\n{note}\n\n{prompt}\n\n{report}"

    def record(self, steps=None, answer=None, prompt="Claude Code 向けに最適化して", own=False):
        steps, answer = self.good if steps is None else steps, self.answer() if answer is None else answer
        body_lines = self.body.read_text(encoding="utf-8").split("\n")[:-1]
        meta = {"isSidechain": True, "agentId": "agent-1"} if own else {"isSidechain": False}
        rows = [{"type": "user", **meta, "message": {"content": [{"type": "text", "text": prompt}]}}]
        for i, (name, arg, *more) in enumerate(steps):
            inp = {"command": arg} if name == "Bash" else {"file_path": str(arg), **(more[0] if more else {})}
            shown = "\n".join(f"{n:6d}\t{line}" for n, line in enumerate(body_lines, 1)) if name == "Read" and Path(arg) == self.body else "ok"
            rows.append({"type": "assistant", **meta, "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}})
            rows.append({"type": "user", **meta, "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": shown, "is_error": False}]}})
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
        self.edit("TARGETS_PATH", "| 4 | 1 | 2 | 0 |", "| 9 | 1 | 2 | 0 |")  # a cell of the rule-count table
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
        self.edit("TARGETS_PATH", "| 4 | 1 | 2 | 0 |", "| 9 | 1 | 2 | 0 |")
        code, out, _ = self.tool("docs", "--write")
        rep = json.loads(out)
        self.assertEqual((code, rep["written"], len(rep["problems"])), (1, False, 2))  # the unmatched number, and the block the document lacks the end of
        self.assertIn("| 9 | 1 | 2 | 0 |", self.paths["TARGETS_PATH"].read_text(encoding="utf-8"))  # the stale cell was left alone

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
