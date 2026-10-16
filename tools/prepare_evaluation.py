#!/usr/bin/env python3
"""Copy Issue #7 review materials without printing case contents or running models."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".claude/skills/official-prompt-tailor/scripts"))
import evidence as ev


def copy_case(case, source, destination):
    ev.shape(case, ev.CASE_F, "case")
    ev.safe_id(case["case_id"], "case_id")
    if case["split"] not in ("development", "held-out"):
        raise ev.ContractError("invalid case split")
    copied = dict(case)
    for kind in ("input", "original", "optimized"):
        raw = ev.safe_join(source, case[kind + "_ref"]).read_bytes()
        if ev.sha_bytes(raw) != case[kind + "_sha256"]:
            raise ev.ContractError("case file hash mismatch")
        ref = f"{case['split']}/{case['case_id']}/{kind}.md"
        target = destination / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        copied[kind + "_ref"] = ref
    return copied


def prepare(cases_path, plan_path, destination):
    plan = ev.load_json(plan_path)
    ev.validate_plan(plan)
    cases = ev.load_jsonl(cases_path)
    if [(c["case_id"], c["split"]) for c in cases] != [(c["case_id"], c["split"]) for c in plan["cases"]]:
        raise ev.ContractError("plan cases/splits differ from packet")
    destination.mkdir(parents=True, exist_ok=False)
    copied = [copy_case(c, cases_path.parent, destination) for c in cases]
    ev.write_json(destination / "cases.json", copied)
    for case in copied:
        lines = ["# " + case["case_id"], "", "Split: " + case["split"], "", "Status: pending-review", "",
                 "## Requirements", ""]
        lines += ["- " + str(r) for r in case["requirements"]]
        lines += ["", "## Rubric", ""] + ["- " + str(r) for r in case["rubric"]]
        lines += ["", "## Source files", ""]
        lines += [f"- [{kind}]({case[kind + '_ref']})" for kind in ("input", "original", "optimized")]
        lines += ["", "Confirm requirements and rubric against all three files; record corrections and reviewer/date in human-review.json.", ""]
        (destination / (case["case_id"] + ".md")).write_bytes("\n".join(lines).encode("utf-8"))
    reviews = [{"case_id": c["case_id"], "split": c["split"], "status": "pending-review",
                "reviewed_by": None, "reviewed_at": None, "requirements_valid": None,
                "rubric_valid": None, "notes": ""} for c in cases]
    ev.write_json(destination / "human-review.json", {"plan_sha256": ev.sha_file(plan_path),
                  "cases_sha256": ev.sha_file(cases_path), "cases": reviews,
                  "scorer_choice": "human", "scorer_id": None, "target_surface": None,
                  "model_id": None, "conditions_accepted": None, "development_locked_at": None,
                  "held_out_opened_at": None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(ROOT / "evals/prompt-tailor-cases.jsonl", ROOT / "evals/issue-7/eval-plan.json", args.output)
    print("Review packet copied; pending-review. No evaluation executed.")


if __name__ == "__main__":
    main()
