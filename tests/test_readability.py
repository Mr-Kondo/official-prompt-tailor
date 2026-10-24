"""Execution receipts use synthetic editor scripts, not a live model or quality judge."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".claude/skills/official-prompt-tailor/scripts"))
import evidence as ev


class ReadabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "日本語 run"
        self.run.mkdir()
        (self.run / "readability-before.md").write_text("上限は100文字程度を予定している。\n", encoding="utf-8")
        (self.run / "optimized.md").write_text("上限は100文字程度を予定。\n", encoding="utf-8")
        self.editor = self.root / "日本語 editor"
        scripts = self.editor / "scripts"
        scripts.mkdir(parents=True)
        (self.editor / "SKILL.md").write_text("Artificial editor fixture\n", encoding="utf-8")
        self.compare = scripts / "compare_rewrite.py"
        self.compare.write_text('import json; print(json.dumps({"numbers": {}, "urls": {}, "unmatched": {"after": ["candidate"]}}))', encoding="utf-8")
        (scripts / "make_change_report.py").write_text('''import json,sys
from pathlib import Path
p=Path(sys.argv[sys.argv.index('--out-dir')+1]); p.mkdir()
data={"kind":"jre-change-report","checks":{"numbers":{"status":"candidate"}}}
(p/'saved.json').write_text(json.dumps(data),encoding='utf-8')
print(json.dumps(data))
''', encoding="utf-8")

    def read(self, name):
        return json.loads((self.run / name).read_text(encoding="utf-8"))

    def execute(self):
        return ev.record_readability(self.run, self.editor)

    def test_executes_tools_and_checks_saved_report_with_candidates(self):
        record = self.execute()
        self.assertEqual(record["status"], "EXECUTION_RECORDED")
        self.assertEqual(ev.check_readability(self.run)["status"], "CONSISTENT_EXECUTION_RECORD")
        self.assertEqual(len(record["files"]), 7)
        self.assertEqual(record["tools"]["compare_rewrite.py"]["returncode"], 0)
        self.assertEqual(self.read("readability-verification.json")["semantic_review"], "NOT_REVIEWED")

    def test_rerun_retains_prior_attempt_and_before_snapshot(self):
        first = self.execute()
        second = self.execute()
        self.assertNotEqual(first["attempt"], second["attempt"])
        self.assertTrue((self.run / first["attempt"] / "before.md").is_file())
        self.assertEqual(first["before_sha256_bytes"], second["before_sha256_bytes"])

    def test_skip_and_legacy_missing_receipt_are_distinct(self):
        self.assertEqual(ev.check_readability(self.run)["status"], "NOT_RECORDED")
        for reason in ("unavailable", "non_japanese"):
            ev.record_readability(self.run, skip=reason)
            self.assertEqual(ev.check_readability(self.run)["status"], "SKIPPED_REPORTED")
            self.assertEqual(self.read("readability.json")["reason"], reason)

    def test_failure_and_missing_installation_are_automatically_recorded(self):
        self.compare.write_text('raise SystemExit(7)', encoding="utf-8")
        record = self.execute()
        self.assertEqual(record["status"], "EXECUTION_FAILED")
        self.assertEqual(record["tools"]["compare_rewrite.py"]["returncode"], 7)
        self.assertEqual(ev.check_readability(self.run)["status"], "EXECUTION_FAILED")
        self.assertEqual(ev.record_readability(self.run, self.root / "absent")["status"], "EXECUTION_FAILED")

    def test_timeout_is_saved_without_success_claim(self):
        with mock.patch.object(ev.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 30)):
            record = self.execute()
        self.assertEqual(record["reason"], "timeout")
        self.assertEqual(ev.check_readability(self.run)["status"], "EXECUTION_FAILED")

    def test_invalid_json_or_missing_saved_report_is_failure(self):
        for text in ('print("not JSON")', 'print("{}")'):
            self.compare.write_text(text, encoding="utf-8")
            self.assertEqual(self.execute()["status"], "EXECUTION_FAILED")
        self.compare.write_text('print(\'{"numbers":{},"urls":{},"unmatched":{}}\')', encoding="utf-8")
        (self.editor / "scripts/make_change_report.py").write_text('print(\'{"kind":"jre-change-report"}\')', encoding="utf-8")
        self.assertEqual(self.execute()["status"], "EXECUTION_FAILED")

    def test_changed_final_or_missing_or_modified_artifact_is_detected(self):
        record = self.execute()
        after = self.run / "optimized.md"
        content = after.read_bytes()
        after.write_bytes(content.replace(b"\n", b"\r\n"))
        self.assertEqual(ev.check_readability(self.run)["status"], "STALE")
        after.write_bytes(content)
        artifact = self.run / record["attempt"] / "compare.json"
        artifact.write_text("{}", encoding="utf-8")
        self.assertEqual(ev.check_readability(self.run)["status"], "INVALID_RECORD")
        artifact.unlink()
        self.assertEqual(ev.check_readability(self.run)["status"], "INVALID_RECORD")

    def test_malformed_record_and_path_escape_are_not_legacy_missing(self):
        (self.run / "readability.json").write_text("{", encoding="utf-8")
        self.assertEqual(ev.check_readability(self.run)["status"], "INVALID_RECORD")
        record = self.execute()
        record["files"]["../../outside"] = "0" * 64
        ev.write_json(self.run / "readability.json", record)
        self.assertEqual(ev.check_readability(self.run)["status"], "INVALID_RECORD")

    def test_verification_does_not_reexecute_editor(self):
        self.execute()
        with mock.patch.object(ev.subprocess, "run", side_effect=AssertionError("must not run")):
            self.assertEqual(ev.check_readability(self.run)["status"], "CONSISTENT_EXECUTION_RECORD")

    def test_artifact_read_failure_replaces_previous_success(self):
        self.execute()
        real_sha = ev.sha_file
        def fail_stderr(path):
            if Path(path).name == "compare.stderr":
                raise PermissionError("synthetic unreadable artifact")
            return real_sha(path)
        with mock.patch.object(ev, "sha_file", side_effect=fail_stderr):
            record = self.execute()
        self.assertEqual(record["status"], "EXECUTION_FAILED")
        self.assertEqual(self.read("readability.json")["reason"], "artifact_collection_failed")
        self.assertEqual(self.read("readability-verification.json")["status"], "EXECUTION_FAILED")

    def test_cli_exits_nonzero_if_final_changes_during_execution(self):
        self.compare.write_text('''import json,sys
from pathlib import Path
(Path(sys.argv[2]).parent.parent/'optimized.md').write_text('changed',encoding='utf-8')
print(json.dumps({"numbers":{},"urls":{},"unmatched":{}}))
''', encoding="utf-8")
        result = subprocess.run([sys.executable, str(Path(ev.__file__)), "readability", "--run", str(self.run), "--editor-dir", str(self.editor)], capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["execution"]["status"], "EXECUTION_RECORDED")
        self.assertEqual(output["verification"]["status"], "STALE")

    def test_same_file_inputs_fail_and_cli_skip_saves_receipt(self):
        before = self.run / "readability-before.md"
        before.unlink()
        try:
            before.hardlink_to(self.run / "optimized.md")
        except AttributeError:
            import os
            os.link(self.run / "optimized.md", before)
        self.assertEqual(self.execute()["status"], "EXECUTION_FAILED")
        result = subprocess.run([sys.executable, str(Path(ev.__file__)), "readability", "--run", str(self.run), "--skip", "unavailable"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read("readability.json")["status"], "SKIPPED_REPORTED")


if __name__ == "__main__":
    unittest.main()
