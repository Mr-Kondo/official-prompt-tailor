import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("knowledge", ROOT / "tools" / "knowledge.py")
knowledge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knowledge)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "knowledge" / "records").mkdir(parents=True)
        (self.root / "evidence.md").write_text("Synthetic test evidence only.\n", encoding="utf-8")
        self.record = json.loads((ROOT / "knowledge" / "records" / "separate-maintenance-knowledge.json").read_text(encoding="utf-8"))
        for item in self.record["evidence"]:
            item["ref"] = "evidence.md"
        self.record["proposal"]["ref"] = "evidence.md"

    def save(self):
        path = self.root / "knowledge" / "records" / (self.record["id"] + ".json")
        path.write_text(json.dumps(self.record), encoding="utf-8")
        return path

    def accept(self):
        self.record["status"] = "confirmed"
        decision = self.record["decisions"][-1]
        decision.update(outcome="accepted", critical_checks="pass", baseline_ref="evidence.md",
                        candidate_ref="evidence.md", evaluation_refs=["evidence.md"], review_ref="evidence.md")

    def test_index_roundtrip_and_stale_detection(self):
        self.save()
        self.assertEqual(knowledge.main(["index", "--root", str(self.root), "--write"]), 0)
        self.assertEqual(knowledge.main(["check", "--root", str(self.root)]), 0)
        self.record["title"] = "Changed record"
        self.save()
        self.assertEqual(knowledge.main(["check", "--root", str(self.root)]), 1)

    def test_pending_needs_no_fabricated_evaluation(self):
        knowledge.validate(self.root, self.record)
        self.assertIsNone(self.record["decisions"][-1]["baseline_ref"])

    def test_missing_evidence_and_research_only_confirmation_fail(self):
        self.record["evidence"][0]["ref"] = "missing.md"
        with self.assertRaisesRegex(knowledge.KnowledgeError, "missing/outside"):
            knowledge.validate(self.root, self.record)
        self.record["evidence"] = [{"kind": "research", "ref": "evidence.md", "partition": "not-applicable"}]
        self.record["status"] = "confirmed"
        with self.assertRaisesRegex(knowledge.KnowledgeError, "observation"):
            knowledge.validate(self.root, self.record)

    def test_holdout_cannot_be_proposal_evidence(self):
        self.record["evidence"][0]["partition"] = "holdout"
        with self.assertRaisesRegex(knowledge.KnowledgeError, "holdout"):
            knowledge.validate(self.root, self.record)

    def test_acceptance_requires_all_declared_checks_and_references(self):
        self.accept()
        knowledge.validate(self.root, self.record)
        for field in ("baseline_ref", "candidate_ref", "review_ref", "evaluation_refs", "critical_checks"):
            candidate = copy.deepcopy(self.record)
            candidate["decisions"][-1][field] = [] if field == "evaluation_refs" else "unknown" if field == "critical_checks" else None
            with self.subTest(field=field), self.assertRaises(knowledge.KnowledgeError):
                knowledge.validate(self.root, candidate)

    def test_prior_acceptance_can_be_refuted_without_erasing_history(self):
        self.accept()
        self.record["status"] = "refuted"
        next_decision = copy.deepcopy(self.record["decisions"][-1])
        next_decision.update(outcome="rejected", reason="Counterexample found", critical_checks="fail")
        self.record["decisions"].append(next_decision)
        knowledge.validate(self.root, self.record)
        self.assertEqual(self.record["decisions"][0]["outcome"], "accepted")

    def test_private_traversal_and_external_references_fail(self):
        for ref in ("../outside.md", "/tmp/log", "feedback/log.md", "outputs/run.md", ".git/config",
                    "https://user:password@example.com/a", "https://example.com/a?token=value", "http://example.com/a"):
            with self.subTest(ref=ref), self.assertRaises(knowledge.KnowledgeError):
                knowledge.reference(self.root, ref)

    def test_symlink_escape_fails(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "private.md"
            target.write_text("private", encoding="utf-8")
            try:
                (self.root / "linked.md").symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(knowledge.KnowledgeError):
                knowledge.reference(self.root, "linked.md")

    def test_duplicate_id_and_filename_mismatch_fail(self):
        self.save()
        path = self.root / "knowledge" / "records" / "z-duplicate.json"
        path.write_text(json.dumps(self.record), encoding="utf-8")
        with self.assertRaisesRegex(knowledge.KnowledgeError, "duplicate id"):
            knowledge.load_records(self.root)
        path.unlink()
        original = self.root / "knowledge" / "records" / (self.record["id"] + ".json")
        original.rename(path)
        with self.assertRaisesRegex(knowledge.KnowledgeError, "filename"):
            knowledge.load_records(self.root)

    def test_symlink_to_private_artifact_inside_repo_fails(self):
        folder = self.root / "feedback"
        folder.mkdir()
        target = folder / "private.md"
        target.write_text("private", encoding="utf-8")
        try:
            (self.root / "public.md").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(knowledge.KnowledgeError, "private artifact"):
            knowledge.reference(self.root, "public.md")

    def test_bad_input_fails_without_writing_index(self):
        path = self.save()
        for content in ("{broken", "[]", '{"schema_version": true}'):
            path.write_text(content, encoding="utf-8")
            self.assertEqual(knowledge.main(["index", "--write", "--root", str(self.root)]), 1)
            self.assertFalse((self.root / "knowledge" / "index.md").exists())

    def test_public_record_and_index_are_consistent(self):
        self.assertEqual(knowledge.main(["check"]), 0)


if __name__ == "__main__":
    unittest.main()
