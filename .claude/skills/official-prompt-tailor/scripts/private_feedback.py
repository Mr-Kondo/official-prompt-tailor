#!/usr/bin/env python3
"""Create a fixed-choice questionnaire and export reported checks, never source text."""
import argparse
import json
import os
import stat
import sys
import uuid
from pathlib import Path

MAX_BYTES = 16384
CHECKS = ("identifier_mapping", "column_mapping", "exact_values", "exception_scope", "append_vs_replace",
          "numbers_units", "modality", "evidence_alignment", "untouched_cells")
STATUSES = ("pass", "fail", "unknown", "not_applicable")
METHODS = ("human", "deterministic", "model_self_report", "unavailable")
REASONS = ("none", "mismatch", "not_run", "missing_artifact", "ambiguous_requirement",
           "unsupported", "not_applicable")
TOP_KEYS = {"schema_version", "kind", "origin", "stage", "conditions", "environment", "checks"}


class FeedbackError(ValueError):
    """Only fixed, non-reflective diagnostic codes may cross the CLI boundary."""


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise FeedbackError("invalid_arguments")


def template():
    return {
        "schema_version": 1, "kind": "private_feedback_draft", "origin": "unknown",
        "stage": "unknown", "conditions": "unknown",
        "environment": {"execution_host": "unknown", "os": "unknown"},
        "checks": {check: {arm: {"status": "unknown", "method": "unavailable", "reason": "not_run"}
                           for arm in ("baseline", "candidate")} for check in CHECKS},
    }


def exact_keys(document, expected):
    if type(document) is not dict or set(document) != set(expected):
        raise FeedbackError("invalid_fields")


def choice(selected, allowed):
    if type(selected) is not str or selected not in allowed:
        raise FeedbackError("invalid_choice")
    return selected


def observation(reported):
    exact_keys(reported, ("status", "method", "reason"))
    status = choice(reported["status"], STATUSES)
    method = choice(reported["method"], METHODS)
    reason = choice(reported["reason"], REASONS)
    if status in ("pass", "fail", "not_applicable"):
        expected = {"pass": "none", "fail": "mismatch", "not_applicable": "not_applicable"}[status]
        if method == "unavailable" or reason != expected:
            raise FeedbackError("inconsistent_observation")
    elif reason not in ("not_run", "missing_artifact", "ambiguous_requirement", "unsupported"):
        raise FeedbackError("inconsistent_observation")
    if reason == "not_run" and method != "unavailable":
        raise FeedbackError("inconsistent_observation")
    return {"status": status, "method": method, "reason": reason}


def checked_environment(environment):
    exact_keys(environment, ("execution_host", "os"))
    return {
        "execution_host": choice(environment["execution_host"],
                                 ("claude-code", "codex", "antigravity", "unknown")),
        "os": choice(environment["os"], ("windows", "macos", "linux", "unknown")),
    }


def checked_draft(draft):
    exact_keys(draft, TOP_KEYS)
    if type(draft["schema_version"]) is not int or draft["schema_version"] != 1:
        raise FeedbackError("invalid_schema")
    choice(draft["kind"], ("private_feedback_draft",))
    origin = choice(draft["origin"], ("private_case_report", "artificial_test"))
    stage = choice(draft["stage"], ("prompt_semantics", "downstream_workbook", "unknown"))
    conditions = choice(draft["conditions"], ("matched", "different", "unknown"))
    environment = checked_environment(draft["environment"])
    exact_keys(draft["checks"], CHECKS)
    checks = {}
    for check in CHECKS:
        exact_keys(draft["checks"][check], ("baseline", "candidate"))
        checks[check] = {arm: observation(draft["checks"][check][arm]) for arm in ("baseline", "candidate")}
    return {"origin": origin, "stage": stage, "conditions": conditions,
            "environment": environment, "checks": checks}


def transition(pair, context):
    baseline, candidate = pair["baseline"], pair["candidate"]
    if (context["conditions"] != "matched" or context["stage"] == "unknown"
            or baseline["method"] != candidate["method"]
            or baseline["method"] not in ("human", "deterministic")
            or any(arm["status"] not in ("pass", "fail") for arm in (baseline, candidate))):
        return "incomparable"
    outcomes = {("pass", "pass"): "both_pass", ("fail", "fail"): "both_fail",
                ("fail", "pass"): "reported_fix", ("pass", "fail"): "reported_regression"}
    return outcomes[(baseline["status"], candidate["status"])]


def report(draft):
    context = checked_draft(draft)
    for pair in context["checks"].values():
        pair["transition"] = transition(pair, context)
    return {"schema_version": 1, "kind": "private_feedback_report", "case_id": str(uuid.uuid4()),
            "comparison": "original_vs_optimized", "evidence_status": "reported_not_independently_verified",
            **context}


def unique_object(pairs):
    parsed = {}
    for key, entry in pairs:
        if key in parsed:
            raise FeedbackError("duplicate_key")
        parsed[key] = entry
    return parsed


def schema_integer(token):
    if token != "1":
        raise FeedbackError("invalid_number")
    return 1


def reject_number(token):
    raise FeedbackError("invalid_number")


def read_draft(path):
    if "\0" in path:
        raise FeedbackError("invalid_path")
    if Path(path).is_symlink():
        raise FeedbackError("unsupported_input")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise FeedbackError("unsupported_input")
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise FeedbackError("input_too_large")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object,
                          parse_int=schema_integer, parse_float=reject_number, parse_constant=reject_number)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise FeedbackError("invalid_json") from error


def write_new(path, document):
    if "\0" in path:
        raise FeedbackError("invalid_path")
    encoded = (json.dumps(document, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode("ascii")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(encoded)


def arguments(argv):
    parser = QuietParser(prog="private_feedback.py", description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=QuietParser)
    create = commands.add_parser("template", allow_abbrev=False, help="create an unreviewed fixed-choice draft")
    create.add_argument("--out", required=True)
    export = commands.add_parser("export", allow_abbrev=False, help="validate choices and create a new report")
    export.add_argument("--input", required=True)
    export.add_argument("--out", required=True)
    export.add_argument("--confirm-share", action="store_true", help="operator has checked the proposed disclosure")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        options = arguments(argv)
        if options.command == "template":
            write_new(options.out, template())
        else:
            if not options.confirm_share:
                raise FeedbackError("share_not_confirmed")
            write_new(options.out, report(read_draft(options.input)))
    except FeedbackError as error:
        print("private_feedback: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("private_feedback: io_error", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("private_feedback: interrupted", file=sys.stderr)
        return 2
    print("private_feedback: written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
