"""Artificial choices only: never load business workbooks, text or session logs."""
import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from contextlib import redirect_stderr

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".claude/skills/official-prompt-tailor/scripts/private_feedback.py"
sys.path.insert(0, str(SCRIPT.parent))
import private_feedback as pf

CANARY = "SECRET_CANARY_12345"


def reviewed_draft():
    draft = pf.template()
    draft.update(origin="artificial_test", stage="downstream_workbook", conditions="matched")
    draft["environment"] = {"execution_host": "codex", "os": "windows"}
    return draft


def judged(status, method="human"):
    return {"status": status, "method": method, "reason": {"pass": "none", "fail": "mismatch"}[status]}


class PrivateFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.folder = Path(self.scratch.name)

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
                              capture_output=True, text=True, encoding="utf-8")

    def save_draft(self, draft):
        source = self.folder / (CANARY + ".json")
        source.write_bytes(json.dumps(draft, ensure_ascii=False).encode("utf-8"))
        return source

    def test_unreviewed_template_cannot_be_exported_or_counted_as_pass(self):
        source = self.folder / "draft.json"
        self.assertEqual(self.cli("template", "--out", source).returncode, 0)
        draft = json.loads(source.read_bytes())
        self.assertTrue(all(arm["status"] == "unknown" for pair in draft["checks"].values() for arm in pair.values()))
        output = self.folder / "report.json"
        self.assertEqual(self.cli("export", "--input", source, "--out", output, "--confirm-share").returncode, 2)
        self.assertFalse(output.exists())

    def test_report_uses_fresh_ids_and_preserves_both_regressions_and_fixes(self):
        draft = reviewed_draft()
        draft["checks"]["exact_values"] = {"baseline": judged("fail"), "candidate": judged("pass")}
        draft["checks"]["modality"] = {"baseline": judged("pass"), "candidate": judged("fail")}
        source = self.save_draft(draft)
        reports = []
        for index in range(2):
            output = self.folder / (str(index) + ".json")
            run = self.cli("export", "--input", source, "--out", output, "--confirm-share")
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn(CANARY, run.stdout + run.stderr + output.read_text())
            reports.append(json.loads(output.read_bytes()))
        self.assertNotEqual(reports[0]["case_id"], reports[1]["case_id"])
        self.assertEqual(uuid.UUID(reports[0]["case_id"]).version, 4)
        checks = reports[0]["checks"]
        self.assertEqual(checks["exact_values"]["transition"], "reported_fix")
        self.assertEqual(checks["modality"]["transition"], "reported_regression")
        self.assertEqual(checks["identifier_mapping"]["transition"], "incomparable")
        self.assertEqual(reports[0]["evidence_status"], "reported_not_independently_verified")
        self.assertEqual(json.loads(source.read_bytes()), draft)

    def test_only_matched_known_observations_are_comparable(self):
        variants = [
            ("human", "human", "pass", "pass", "matched", "downstream_workbook", "both_pass"),
            ("deterministic", "deterministic", "fail", "fail", "matched", "prompt_semantics", "both_fail"),
            ("model_self_report", "model_self_report", "fail", "pass", "matched", "prompt_semantics", "incomparable"),
            ("human", "deterministic", "fail", "pass", "matched", "prompt_semantics", "incomparable"),
            ("human", "human", "fail", "pass", "unknown", "prompt_semantics", "incomparable"),
            ("human", "human", "fail", "pass", "different", "prompt_semantics", "incomparable"),
            ("human", "human", "fail", "pass", "matched", "unknown", "incomparable"),
        ]
        for first, second, before, after, conditions, stage, expected in variants:
            with self.subTest(variant=(first, second, conditions, stage)):
                draft = reviewed_draft()
                draft.update(conditions=conditions, stage=stage)
                draft["checks"]["exact_values"] = {"baseline": judged(before, first), "candidate": judged(after, second)}
                self.assertEqual(pf.report(draft)["checks"]["exact_values"]["transition"], expected)
        draft = reviewed_draft()
        draft["checks"]["exact_values"]["baseline"] = {"status": "not_applicable", "method": "human", "reason": "not_applicable"}
        draft["checks"]["exact_values"]["candidate"] = judged("pass")
        self.assertEqual(pf.report(draft)["checks"]["exact_values"]["transition"], "incomparable")

    def test_unknown_fields_types_and_inconsistent_states_are_rejected_without_echo(self):
        mutations = [
            lambda d: d.update(comment=CANARY),
            lambda d: d.update(case_id=CANARY),
            lambda d: d.update(schema_version=True),
            lambda d: d.update(stage=CANARY),
            lambda d: d.update(environment={"execution_host": "codex", "os": "windows", CANARY: "x"}),
            lambda d: d["checks"].pop("modality"),
            lambda d: d["checks"].update({CANARY: {}}),
            lambda d: d["checks"]["modality"].update(candidate=[]),
            lambda d: d["checks"]["modality"]["candidate"].update(status=[CANARY]),
            lambda d: d["checks"]["modality"]["candidate"].update(status="pass"),
            lambda d: d["checks"]["modality"]["candidate"].update(method="human"),
            lambda d: d["checks"]["modality"]["candidate"].update(reason=CANARY),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                draft = reviewed_draft()
                mutate(draft)
                output = self.folder / "report.json"
                run = self.cli("export", "--input", self.save_draft(draft), "--out", output, "--confirm-share")
                self.assertEqual(run.returncode, 2)
                self.assertNotIn(CANARY, run.stdout + run.stderr)
                self.assertNotIn("Traceback", run.stderr)
                self.assertFalse(output.exists())

    def test_invalid_json_duplicate_keys_numeric_and_size_boundaries_do_not_leak(self):
        payloads = [b"", b"\xff", b'{"secret":"' + CANARY.encode() + b'",', b'{"a":1,"a":1}',
                    b'{"schema_version":NaN}', b'{"schema_version":Infinity}', b'{"schema_version":1.0}',
                    b'{"schema_version":' + b"9" * 5000 + b'}', b"[" * 2000 + b"]" * 2000,
                    b" " * pf.MAX_BYTES + b"x"]
        for payload in payloads:
            with self.subTest(payload_length=len(payload)):
                source = self.folder / (CANARY + ".json")
                source.write_bytes(payload)
                output = self.folder / "report.json"
                run = self.cli("export", "--input", source, "--out", output, "--confirm-share")
                self.assertEqual(run.returncode, 2)
                self.assertNotIn(CANARY, run.stdout + run.stderr)
                self.assertNotIn("Traceback", run.stderr)
                self.assertFalse(output.exists())
        source.write_bytes(b'{}' + b' ' * (pf.MAX_BYTES - 2))
        with self.assertRaisesRegex(pf.FeedbackError, "invalid_fields"):
            pf.report(pf.read_draft(str(source)))

    def test_confirmation_and_cli_errors_never_reflect_arguments(self):
        source = self.save_draft(reviewed_draft())
        output = self.folder / "report.json"
        variants = [("export", "--input", source, "--out", output),
                    (CANARY,), ("template", "--out", output, "--" + CANARY),
                    ("export", "--input", source, "--out", output, "--confirm-share", CANARY)]
        for arguments in variants:
            run = self.cli(*arguments)
            self.assertEqual(run.returncode, 2)
            self.assertNotIn(CANARY, run.stdout + run.stderr)
            self.assertFalse(output.exists())
        captured = io.StringIO()
        with redirect_stderr(captured):
            self.assertEqual(pf.main(["template", "--out", "\0" + CANARY]), 2)
        self.assertNotIn(CANARY, captured.getvalue())

    def test_existing_output_is_not_replaced_and_io_errors_hide_paths(self):
        source = self.save_draft(reviewed_draft())
        output = self.folder / (CANARY + "-output.json")
        output.write_bytes(b"keep this original")
        variants = [("template", "--out", output),
                    ("export", "--input", source, "--out", output, "--confirm-share"),
                    ("template", "--out", self.folder / CANARY / "missing.json"),
                    ("export", "--input", self.folder / CANARY, "--out", output, "--confirm-share")]
        for arguments in variants:
            run = self.cli(*arguments)
            self.assertEqual(run.returncode, 1)
            self.assertNotIn(CANARY, run.stdout + run.stderr)
            self.assertEqual(output.read_bytes(), b"keep this original")

    def test_concurrent_writers_create_exactly_one_complete_file(self):
        output = self.folder / "contended.json"
        processes = [subprocess.Popen([sys.executable, "-B", str(SCRIPT), "template", "--out", str(output)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        for process in processes:
            process.communicate(timeout=10)
        self.assertEqual(sorted(process.returncode for process in processes), [0, 1, 1, 1])
        self.assertEqual(json.loads(output.read_bytes()), pf.template())

    def test_shipped_artificial_example_exports_without_changing_its_origin(self):
        source = SCRIPT.parent.parent / "references/private-feedback-example.json"
        output = self.folder / "example-report.json"
        run = self.cli("export", "--input", source, "--out", output, "--confirm-share")
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(output.read_bytes())
        self.assertEqual(report["origin"], "artificial_test")
        self.assertEqual(report["checks"]["exception_scope"]["transition"], "reported_regression")
        self.assertEqual(report["checks"]["numbers_units"]["transition"], "reported_fix")

    @unittest.skipIf(os.name == "nt", "creating symlinks requires Windows privileges; exclusive-create is tested on Windows")
    def test_input_and_output_symlinks_never_replace_targets(self):
        source = self.save_draft(reviewed_draft())
        link = self.folder / "link.json"
        link.symlink_to(source)
        output = self.folder / "report.json"
        self.assertEqual(self.cli("export", "--input", link, "--out", output, "--confirm-share").returncode, 2)
        self.assertEqual(self.cli("template", "--out", link).returncode, 1)
        self.assertEqual(json.loads(source.read_bytes()), reviewed_draft())
        link.unlink()
        link.symlink_to(self.folder / "missing.json")
        self.assertEqual(self.cli("template", "--out", link).returncode, 1)
        self.assertFalse((self.folder / "missing.json").exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is a POSIX file type")
    def test_fifo_input_fails_without_waiting_for_writer(self):
        source = self.folder / "pipe"
        os.mkfifo(source)
        run = subprocess.run([sys.executable, str(SCRIPT), "export", "--input", str(source),
                              "--out", str(self.folder / "report.json"), "--confirm-share"],
                             capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 2)

    def test_unicode_paths_and_bom_crlf_draft_work_with_ascii_console(self):
        source = self.folder / "会社 書類.json"
        source.write_bytes(b"\xef\xbb\xbf" + json.dumps(reviewed_draft(), indent=2).replace("\n", "\r\n").encode())
        output = self.folder / "評価 結果.json"
        run = subprocess.run([sys.executable, str(SCRIPT), "export", "--input", str(source),
                              "--out", str(output), "--confirm-share"], capture_output=True,
                             env=dict(os.environ, PYTHONIOENCODING="ascii:strict"))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(output.read_bytes())["origin"], "artificial_test")

    def test_three_product_lifecycles_preserve_workspace_reports_and_ship_tool(self):
        for target, prefix in (("claude-code", ".claude"), ("codex", ".agents"), ("antigravity", ".agents")):
            with self.subTest(target=target):
                project = self.folder / target
                project.mkdir()
                saved_report = project / "report.json"
                saved_report.write_text("keep")
                base = [sys.executable, "-B", str(ROOT / "install.py"), "--target", target, "--project", str(project)]
                installed = project / prefix / "skills/official-prompt-tailor"
                for options in ([], ["--update"], ["--uninstall"]):
                    if options == ["--update"]:
                        (installed / "scripts/private_feedback.py").write_text("# old copy\n")
                    run = subprocess.run(base + options, capture_output=True, text=True, encoding="utf-8")
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual(saved_report.read_text(), "keep")
                    if options != ["--uninstall"]:
                        self.assertEqual((installed / "scripts/private_feedback.py").read_bytes(), SCRIPT.read_bytes())
                        self.assertTrue((installed / "references/private-feedback.md").is_file())
                    else:
                        self.assertFalse(installed.exists())

    def test_release_zip_contains_tool_and_contract_but_no_reports(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "install.py"), "--zip", str(self.folder)],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        with zipfile.ZipFile(self.folder / "official-prompt-tailor.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("official-prompt-tailor/scripts/private_feedback.py"), SCRIPT.read_bytes())
            self.assertIn("official-prompt-tailor/references/private-feedback.md", archive.namelist())
            self.assertFalse(any("verification/" in name or "report.json" in name for name in archive.namelist()))


if __name__ == "__main__":
    unittest.main()
