"""Synthetic native tool records; no private conversations are fixtures."""
import copy
import json
import subprocess
import sys
from pathlib import Path

from test_official_prompt_tailor import Base, ROOT, codes, ev


class TestAntigravityAdapter(Base):
    def setUp(self):
        super().setUp()
        self.run, _ = self.good_run("agy", execution_host="antigravity")
        self.body = self.run / self.entry(self.run)["body_path"]
        self.rows = [json.loads(line) for line in
                     (ROOT / "tests/fixtures/antigravity-1.3.5.jsonl").read_text(encoding="utf-8").splitlines()]
        self.rows[1]["tool_calls"][0]["args"]["AbsolutePath"] = str(self.body)
        lines = self.body.read_text(encoding="utf-8").splitlines()
        self.rows[2]["content"] = (f"File Path: `file://{self.body}`\nShowing lines 1 to {len(lines)}\n"
                                   + "\n".join(f"{n}: {line}" for n, line in enumerate(lines, 1)))
        self.rows[3]["tool_calls"][0]["args"]["TargetFile"] = str(self.run / "optimized.md")
        self.log = self.tmp / "native.jsonl"

    def save(self, version="1.3.5"):
        self.log.write_text("".join(json.dumps(row) + "\n" for row in self.rows), encoding="utf-8")
        Path(str(self.log) + ".version.json").write_text(json.dumps({
            "format": "antigravity-transcript-jsonl-v1", "cli_version": version,
            "sha256": ev.sha_bytes(self.log.read_bytes())}), encoding="utf-8")
        return self.log

    def observed(self):
        return ev.observed_read_lines(ev.parse_host_log("antigravity", self.save()), self.body,
                                      [self.run / "optimized.md", self.run / "recommendations.json"])

    def test_successful_native_read_passes_verify_and_sources(self):
        log = self.save()
        report = self.verify(self.run, "live", log)
        self.assertEqual(report["verdict"], "PASS_LIVE", report["errors"])
        report = ev.sources_report(self.run, log)
        self.assertEqual(report["verdict"], "CONSISTENT", report)
        self.assertTrue(report["selected"][0]["read"]["cited_lines_covered"])

    def test_missing_or_denied_or_truncated_read_is_not_observed(self):
        baseline = copy.deepcopy(self.rows)
        for alteration in ("no_read", "denied", "truncated", "wrong_path", "batched", "missing_result"):
            with self.subTest(alteration):
                self.rows = copy.deepcopy(baseline)
                if alteration == "no_read":
                    self.rows[1]["tool_calls"] = []
                elif alteration == "denied":
                    self.rows[2]["status"] = "ERROR"
                elif alteration == "truncated":
                    self.rows[2]["truncated_fields"] = ["content"]
                elif alteration == "wrong_path":
                    self.rows[2]["content"] = self.rows[2]["content"].replace(str(self.body), "/wrong/file")
                elif alteration == "batched":
                    self.rows[1]["tool_calls"] *= 2
                else:
                    del self.rows[2]
                self.assertEqual(self.observed()[0], set())

    def test_partial_output_counts_only_complete_visible_matching_lines(self):
        self.rows[2]["content"] = (f"File Path: `file://{self.body}`\nShowing lines 1 to 3\n"
                                   + "1: " + self.body.read_text(encoding="utf-8").splitlines()[0] + "\n2: cut")
        self.assertEqual(self.observed()[0], {1})

    def test_read_after_write_and_no_generation(self):
        self.rows[0] = copy.deepcopy(self.rows[3])
        self.rows[0]["step_index"] = 0
        self.assertEqual(self.observed()[0], set())
        del self.rows[0]
        del self.rows[-1]
        self.assertIsNone(self.observed()[1])
        self.assertIn("generation_write_not_found", codes(self.verify(self.run, "live", self.log)))

    def test_sidecar_missing_wrong_version_wrong_format_and_changed_bytes(self):
        log = self.save()
        sidecar = Path(str(log) + ".version.json")
        for field, value in (("cli_version", "1.3.6"), ("format", "unknown"), ("sha256", "0" * 64)):
            with self.subTest(field):
                self.save()
                manifest = json.loads(sidecar.read_text(encoding="utf-8"))
                manifest[field] = value
                sidecar.write_text(json.dumps(manifest), encoding="utf-8")
                self.assertIn("host_log_invalid", codes(self.verify(self.run, "live", log)))
        for invalid in (None, [], "not-an-object"):
            sidecar.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertIn("host_log_invalid", codes(self.verify(self.run, "live", log)))
        sidecar.unlink()
        self.assertIn("host_log_invalid", codes(self.verify(self.run, "live", log)))

    def test_invalid_records_fail_closed(self):
        baseline = copy.deepcopy(self.rows)
        for mutation in ("step", "type", "status", "args", "non_object"):
            with self.subTest(mutation):
                self.rows = copy.deepcopy(baseline)
                if mutation == "step":
                    self.rows[2]["step_index"] = 1
                elif mutation == "type":
                    self.rows[2]["type"] = "FUTURE_TOOL"
                elif mutation == "status":
                    self.rows[2]["status"] = "ACTIVE"
                elif mutation == "args":
                    self.rows[1]["tool_calls"][0]["args"] = []
                else:
                    self.rows[2] = []
                self.assertIn("host_log_invalid", codes(self.verify(self.run, "live", self.save())))

    def test_inside_run_log_is_rejected(self):
        self.log = self.run / "forged.jsonl"
        self.assertIn("host_log_inside_run", codes(self.verify(self.run, "live", self.save())))

    def test_pin_cli_creates_usable_sidecar_and_refuses_overwrite(self):
        log = self.save()
        sidecar = Path(str(log) + ".version.json")
        sidecar.unlink()
        command = [sys.executable, str(ROOT / ".claude/skills/official-prompt-tailor/scripts/pin_antigravity_log.py"),
                   "--log", str(log), "--cli-version", "1.3.5"]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.verify(self.run, "live", log)["verdict"], "PASS_LIVE")
        before = sidecar.read_bytes()
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(sidecar.read_bytes(), before)
        command[-1] = "1.3.6"
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_audit_hooks_expose_commands_writes_and_texts_without_thinking(self):
        self.rows[0]["content"] = "request"
        self.rows.append({"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE",
                          "tool_calls": [{"name": "run_command", "args": {"CommandLine": "rtk python3 evidence.py sources"}}]})
        self.rows.append({"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE",
                          "content": "answer", "thinking": "must not appear"})
        log = self.save()
        self.assertEqual(ev.antigravity_transcript_texts(log), ("request", "answer"))
        events = ev.parse_host_log("antigravity", log)
        self.assertIn("rtk python3 evidence.py sources", [e["input"]["command"] for e in events if e.get("name") == "Bash"])
        self.assertIn(str(self.run / "optimized.md"), [e["input"]["file_path"] for e in events if e.get("name") == "Write"])

    def test_observed_140_envelope_observes_read_before_generation(self):
        self.rows = [json.loads(line) for line in
                     (ROOT / "tests/fixtures/antigravity-1.4.0.jsonl").read_text(encoding="utf-8").splitlines()]
        probe = self.tmp / "probe.txt"
        probe.write_text("agy140 bounded probe alpha\nagy140 bounded probe beta\n", encoding="utf-8")
        for row in self.rows:
            if "content" in row:
                row["content"] = row["content"].replace("/workspace/probe.txt", str(probe))
            for call in row.get("tool_calls", []):
                if call["name"] == "view_file":
                    call["args"]["AbsolutePath"] = str(probe)
                elif call["name"] == "write_to_file":
                    call["args"]["TargetFile"] = str(self.run / "optimized.md")
        log = self.save("1.4.0")
        observed, first_write = ev.observed_read_lines(ev.parse_host_log("antigravity", log), probe,
                                                      [self.run / "optimized.md"])
        self.assertEqual(observed, {1, 2})
        self.assertEqual(first_write, 3)
        sidecar = Path(str(log) + ".version.json")
        sidecar.unlink()
        command = [sys.executable, str(ROOT / ".claude/skills/official-prompt-tailor/scripts/pin_antigravity_log.py"),
                   "--log", str(log), "--cli-version", "1.4.0"]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(ev.parse_host_log("antigravity", log)[0]["name"], "Read")

    def test_140_distinct_pure_read_batch_requires_ordered_path_bound_results(self):
        other = self.tmp / "second.txt"
        baseline = copy.deepcopy(self.rows)
        calls = copy.deepcopy(baseline[1])
        calls["tool_calls"].append({"name": "view_file", "args": {"AbsolutePath": str(other)}})
        second = {"step_index": 3, "type": "GENERIC", "status": "DONE",
                  "content": f"File Path: `file://{other}`\nShowing lines 1 to 1\n1: second"}
        write = copy.deepcopy(baseline[3]); write["step_index"] = 4
        batch = [baseline[0], calls, baseline[2], second, write]
        for variant in ("valid", "wrong_path", "wrong_order", "duplicate", "failed", "legacy"):
            with self.subTest(variant=variant):
                self.rows = copy.deepcopy(batch)
                if variant == "wrong_path":
                    self.rows[3]["content"] = self.rows[3]["content"].replace(str(other), "/wrong")
                elif variant == "wrong_order":
                    self.rows[2]["content"], self.rows[3]["content"] = self.rows[3]["content"], self.rows[2]["content"]
                elif variant == "duplicate":
                    self.rows[1]["tool_calls"][1]["args"]["AbsolutePath"] = str(self.body)
                elif variant == "failed":
                    self.rows[3]["status"] = "ERROR"
                log = self.save("1.3.5" if variant == "legacy" else "1.4.0")
                seen, _ = ev.observed_read_lines(ev.parse_host_log("antigravity", log), self.body, [self.run / "optimized.md"])
                self.assertEqual(bool(seen), variant == "valid")
