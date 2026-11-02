#!/usr/bin/env python3
"""Check maintenance knowledge and build its index (stdlib only; no network).

ADR: adr/0003-wikiskill-maintenance-knowledge.md
This checks declarations and references, not the truth of a claim or an evaluation.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATES = {"hypothesis", "confirmed", "refuted", "superseded"}
OUTCOMES = {"pending", "accepted", "rejected", "on-hold"}


class KnowledgeError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise KnowledgeError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def reference(root, value):
    require(text(value), "reference must be a nonempty string")
    require(value == value.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "reference contains whitespace/control characters")
    parsed = urlsplit(value)
    if parsed.scheme:
        require(parsed.scheme == "https" and parsed.hostname and parsed.username is None
                and parsed.password is None and not parsed.query and not any(c.isspace() for c in value),
                "use public HTTPS references without credentials/query/whitespace")
        return
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in value
            and not parsed.fragment and not parsed.query, "unsafe local reference")
    require(path.parts and path.parts[0] not in {"feedback", "outputs", ".git"}
            and not any(p.startswith(".") for p in path.parts), "private artifacts must not be knowledge references")
    dest = root / value
    require(root.resolve() in dest.resolve().parents and dest.is_file(), "missing/outside reference: " + value)
    resolved = dest.resolve().relative_to(root.resolve())
    require(not any(p in {"feedback", "outputs"} or p.startswith(".") for p in resolved.parts),
            "private artifact behind reference")


def validate(root, record):
    require(isinstance(record, dict), "record must be an object")
    fields = {"schema_version", "id", "title", "status", "problem", "cause_hypothesis", "conditions",
              "counterexamples", "scope", "evidence", "proposal", "decisions"}
    require(set(record) == fields, "record fields differ from schema")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1, "unsupported schema")
    require(text(record["id"]) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", record["id"]), "unsafe id")
    for key in ("title", "problem", "cause_hypothesis", "conditions", "counterexamples"):
        require(text(record[key]), "missing " + key)
    require(record["status"] in STATES, "invalid knowledge status")
    scope = record["scope"]
    require(isinstance(scope, dict) and set(scope) == {"revision", "hosts", "models"}, "invalid scope")
    require(text(scope["revision"]), "missing revision")
    for key in ("hosts", "models"):
        require(isinstance(scope[key], list) and scope[key] and all(text(v) for v in scope[key]), "missing scope " + key)
    evidence = record["evidence"]
    require(isinstance(evidence, list) and evidence, "evidence references required")
    for item in evidence:
        require(isinstance(item, dict) and set(item) == {"kind", "ref", "partition"}, "invalid evidence")
        require(item["kind"] in {"research", "observation", "report"}, "invalid evidence kind")
        require(item["partition"] in {"development", "not-applicable"}, "holdout data cannot inform proposals")
        reference(root, item["ref"])
    if record["status"] == "confirmed":
        require(any(e["kind"] == "observation" for e in evidence), "confirmed needs an observation, not research alone")
    proposal = record["proposal"]
    require(isinstance(proposal, dict) and set(proposal) == {"summary", "ref"} and text(proposal["summary"]), "invalid proposal")
    if proposal["ref"] is not None:
        reference(root, proposal["ref"])
    decisions = record["decisions"]
    require(isinstance(decisions, list) and decisions, "decision history required")
    for decision in decisions:
        required = {"date", "outcome", "reason", "criteria", "baseline_ref", "candidate_ref", "evaluation_refs",
                    "review_ref", "critical_checks", "cost_limit", "stop_condition"}
        require(isinstance(decision, dict) and set(decision) == required, "invalid decision")
        require(text(decision["date"]), "missing decision date")
        try:
            datetime.date.fromisoformat(decision["date"])
        except ValueError as exc:
            raise KnowledgeError("invalid decision date") from exc
        require(decision["outcome"] in OUTCOMES, "invalid outcome")
        for key in ("reason", "criteria", "cost_limit", "stop_condition"):
            require(text(decision[key]), "missing decision " + key)
        require(decision["critical_checks"] in {"pass", "fail", "unknown"}, "invalid critical checks")
        refs = decision["evaluation_refs"]
        require(isinstance(refs, list), "evaluation_refs must be a list")
        for value in refs:
            reference(root, value)
        for key in ("baseline_ref", "candidate_ref", "review_ref"):
            if decision[key] is not None:
                reference(root, decision[key])
        if decision["outcome"] == "accepted":
            require(any(e["kind"] == "observation" for e in evidence) and refs and decision["critical_checks"] == "pass"
                    and all(decision[k] is not None for k in ("baseline_ref", "candidate_ref", "review_ref")),
                    "accepted requires confirmed evidence, before/after evaluation, review and passing critical checks")
    if decisions[-1]["outcome"] == "accepted":
        require(record["status"] == "confirmed", "current acceptance requires confirmed status")
    return record


def load_records(root):
    folder = root / "knowledge" / "records"
    require(folder.is_dir() and not folder.is_symlink(), "missing/linked records directory")
    records, ids = [], set()
    for path in sorted(folder.glob("*.json")):
        require(not path.is_symlink(), "linked knowledge record")
        record = validate(root, json.loads(path.read_text(encoding="utf-8")))
        require(record["id"] not in ids, "duplicate id: " + record["id"])
        require(path.stem == record["id"], "filename must match id")
        ids.add(record["id"])
        records.append(record)
    require(records, "no knowledge records")
    return records


def index_text(records):
    lines = ["# 更新管理の知見索引", "", "<!-- Generated by tools/knowledge.py index --write; edit records, not this index. -->", "",
             "更新提案時だけ、該当する記録から原本へ進む。通常のスキル実行には注入しない。", "",
             "検査は記録形式と参照の整合だけを確認し、内容の正しさ・審査実施・改善効果を保証しない。", ""]
    for record in records:
        decision = record["decisions"][-1]
        title = " ".join(record["title"].split()).replace("[", "\\[").replace("]", "\\]")
        lines.append("- [%s](records/%s.json) — %s / %s" % (title, record["id"], record["status"], decision["outcome"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "index"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true", help="with index: replace the generated index")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        require(not args.write or args.command == "index", "--write requires index")
        root = args.root.resolve()
        require(not (root / "knowledge").is_symlink(), "linked knowledge directory")
        records = load_records(root)
        expected = index_text(records)
        index = root / "knowledge" / "index.md"
        require(not index.is_symlink(), "linked index")
        if args.command == "index":
            if args.write:
                with index.open("w", encoding="utf-8", newline="\n") as stream:
                    stream.write(expected)
            else:
                print(expected, end="")
        else:
            require(index.is_file() and index.read_text(encoding="utf-8") == expected, "stale/missing knowledge index")
            print("knowledge: %d records; declarations/references consistent; effectiveness NOT_EVALUATED" % len(records))
        return 0
    except (KnowledgeError, OSError, ValueError, TypeError) as exc:
        print("knowledge: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
