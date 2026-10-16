"""Synthetic packet tests; never read shipped held-out case contents."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import prepare_evaluation as prep


class TestPreparation(unittest.TestCase):
    def test_packet_preserves_bytes_and_pending_review_without_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = []
            for cid, split in (("dev", "development"), ("held", "held-out")):
                case = {"case_id": cid, "split": split, "target_id": "claude-code",
                        "target_surface": "unknown", "model_id": "unknown", "requirements": ["synthetic"],
                        "rubric": ["synthetic"], "status": "pending-review"}
                for kind in ("input", "original", "optimized"):
                    raw = (cid + kind + "\r\n").encode()
                    ref = cid + kind + ".md"
                    (root / ref).write_bytes(raw)
                    case[kind + "_ref"] = ref
                    case[kind + "_sha256"] = prep.ev.sha_bytes(raw)
                cases.append(case)
            source = root / "cases.jsonl"
            source.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
            plan = prep.ev.load_json(ROOT / "evals/issue-7/eval-plan.json")
            plan["cases"] = [{"case_id": c["case_id"], "split": c["split"]} for c in cases]
            plan_path = root / "plan.json"
            prep.ev.write_json(plan_path, plan)
            packet = root / "packet"
            prep.prepare(source, plan_path, packet)
            copied = prep.ev.load_json(packet / "cases.json")
            for case in copied:
                for kind in ("input", "original", "optimized"):
                    self.assertEqual(prep.ev.sha_file(packet / case[kind + "_ref"]), case[kind + "_sha256"])
            review = prep.ev.load_json(packet / "human-review.json")
            self.assertTrue(all(c["status"] == "pending-review" and c["reviewed_by"] is None for c in review["cases"]))
            self.assertEqual(review["plan_sha256"], prep.ev.sha_file(plan_path))
            self.assertFalse(list(root.rglob("evaluation.json")))
            self.assertFalse(list(root.rglob("eval-results.jsonl")))
            with self.assertRaises(FileExistsError):
                prep.prepare(source, plan_path, packet)
            (root / "heldinput.md").write_bytes(b"changed")
            with self.assertRaisesRegex(prep.ev.ContractError, "hash mismatch"):
                prep.prepare(source, plan_path, root / "tampered")

    def test_invalid_plan_is_rejected_before_packet_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.json"
            prep.ev.write_json(plan, {"schema_version": 1})
            with self.assertRaises(prep.ev.ContractError):
                prep.prepare(root / "missing-cases", plan, root / "packet")
            self.assertFalse((root / "packet").exists())


if __name__ == "__main__":
    unittest.main()
