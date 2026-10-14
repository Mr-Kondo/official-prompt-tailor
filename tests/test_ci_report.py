"""Unit tests for .github/scripts/ci_report.py (stdlib unittest; `gh` is mocked). Run: python3 -m unittest discover -s tests"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".github" / "scripts"))
import ci_report  # noqa: E402

DRIFT = {"verdict": "DRIFT", "checked_at": "2026-10-15T03:17:00Z", "sources": [
    {"source_id": "a", "status": "live", "error": None, "body_sha256": "x"},
    {"source_id": "b", "status": "blocked", "error": "http_403", "body_sha256": None}], "rules": [
    {"rule_id": "r-ok", "source_id": "a", "state": "current"}, {"rule_id": "r-changed", "source_id": "a", "state": "changed"},
    {"rule_id": "r-lost", "source_id": "a", "state": "excerpt_missing"}, {"rule_id": "r-blocked", "source_id": "b", "state": "unverifiable"}]}
DISCOVER = {"verdict": "CANDIDATES", "checked_at": "2026-10-15T03:17:00Z", "indexes": [
    {"index_id": "docs", "status": "live", "error": None, "pages": 3, "candidates": [
        {"title": "A | B", "url": "https://x.example/a", "key": "x.example/a"}]},
    {"index_id": "down", "status": "blocked", "error": "http_503", "pages": 0, "candidates": []}]}


class TestRender(unittest.TestCase):
    def test_drift_lists_only_what_needs_a_person(self):
        md = ci_report.render("drift", DRIFT)
        self.assertIn("`r-changed`", md)
        self.assertIn("引用が本文から消えた", md)
        self.assertNotIn("r-ok", md)
        self.assertNotIn("r-blocked", md)  # a blocked source is listed as a source, not as a rule
        self.assertIn("取得できなかった資料: `b`(http_403)", md)

    def test_discover_links_the_page_and_escapes_the_table_separator(self):
        md = ci_report.render("discover", DISCOVER)
        self.assertIn("[A \\| B](https://x.example/a)", md)
        self.assertIn("取得できなかった一覧: `down`(http_503)", md)

    def test_a_clean_result_is_a_header_only(self):
        md = ci_report.render("drift", {**DRIFT, "verdict": "CURRENT", "rules": [], "sources": []})
        self.assertEqual(md.splitlines()[0], "## 公式資料の更新確認: CURRENT")
        self.assertNotIn("|", md)


class FakeGitHub:
    """Just enough of `gh issue ...` to look at the issues afterwards instead of at the commands sent."""

    def __init__(self):
        self.issues = {}  # number -> {"label", "title", "body", "open"}

    @staticmethod
    def option(args, name):
        return args[args.index(name) + 1]

    def __call__(self, cmd, input=None, **kw):
        args, out = cmd[1:], ""
        if args[:2] == ["issue", "list"]:
            label = self.option(args, "--label")
            numbers = [n for n, i in self.issues.items() if i["label"] == label and i["open"]]
            out = f"{numbers[0]}\n" if numbers else ""
        elif args[:2] == ["issue", "edit"]:
            self.issues[int(args[2])]["body"] = input
        elif args[:2] == ["issue", "create"]:
            self.issues[len(self.issues) + 1] = {"label": self.option(args, "--label"), "title": self.option(args, "--title"),
                                                 "body": input, "open": True}
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patch in (mock.patch.object(ci_report.subprocess, "run", self.gh), mock.patch("builtins.print")):
            patch.start()
            self.addCleanup(patch.stop)

    def report(self, result, *flags, kind="drift"):
        path = Path(self.tmp.name) / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        ci_report.main(["ci_report.py", kind, str(path), *flags])

    def test_an_actionable_result_opens_one_issue_holding_the_report(self):
        self.report(DRIFT, "--issue")
        (issue,) = self.gh.issues.values()
        self.assertEqual((issue["label"], issue["open"]), ("drift", True))
        self.assertIn("`r-changed`", issue["body"])

    def test_the_next_week_replaces_the_body_of_the_open_issue(self):
        self.report(DRIFT, "--issue")
        self.report({**DRIFT, "rules": [{"rule_id": "r-new", "source_id": "a", "state": "changed"}]}, "--issue")
        (issue,) = self.gh.issues.values()
        self.assertIn("r-new", issue["body"])
        self.assertNotIn("r-changed", issue["body"])

    def test_a_closed_issue_is_not_reused(self):
        self.report(DRIFT, "--issue")
        self.gh.issues[1]["open"] = False
        self.report(DRIFT, "--issue")
        self.assertEqual([i["open"] for i in self.gh.issues.values()], [False, True])

    def test_each_kind_keeps_its_own_issue(self):
        self.report(DRIFT, "--issue")
        self.report(DISCOVER, "--issue", kind="discover")
        self.assertEqual(sorted(i["label"] for i in self.gh.issues.values()), ["discover", "drift"])

    def test_nothing_is_filed_without_the_flag_or_when_nobody_has_to_act(self):
        self.report(DRIFT)
        self.report({**DRIFT, "verdict": "INCOMPLETE"}, "--issue")
        self.report({**DISCOVER, "verdict": "NONE"}, "--issue", kind="discover")
        self.assertEqual(self.gh.issues, {})


if __name__ == "__main__":
    unittest.main()
