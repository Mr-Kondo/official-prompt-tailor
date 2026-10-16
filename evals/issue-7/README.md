# Issue #7: human review packet (experiment not started)

[Issue #7](https://github.com/Mr-Kondo/official-prompt-tailor/issues/7) remains open.
The plan is a concrete **candidate**, not permission to execute. All six source cases
and all review records remain `pending-review`. No human labels or calibration were generated.

## Review now

Open these development dossiers first. Each links the input, original prompt and
optimized prompt and lists the extracted requirements and rubric:

- [dev-date-bug](review/dev-date-bug.md)
- [dev-ambiguous](review/dev-ambiguous.md)
- [dev-number-altered](review/dev-number-altered.md)
- [dev-ui-misapplied](review/dev-ui-misapplied.md)

For each case, confirm **requirements and rubric against all three files**, or give
specific corrections. Record the exact case ID, `human:<name>`, actual UTC review date,
`requirements_valid`, `rubric_valid`, and notes in [human-review.json](review/human-review.json).
A blanket approval without named cases is insufficient. The packet is a copy; editing
it does not automatically mark the source cases ready.

Before opening held-out dossiers, finish development-only adjustments, commit the
locked development prompts and scoring instructions, and record that commit and actual
`development_locked_at` in the review notes. The preparation agent copied held-out
bytes without viewing their content; this is not evidence of anyone else's prior access.
The human must state whether they previously saw held-out content; if the optimizer
already used it, stop and arrange fresh held-out cases rather than claiming an unseen test.

Then record `held_out_opened_at` and review the two held-out dossiers without feeding
their contents or case-specific corrections back into prompt tuning:

- [held-user-signup](review/held-user-signup.md)
- [held-negation](review/held-negation.md)

## Confirm the candidate plan

[eval-plan.json](eval-plan.json) proposes Claude Code, six cases, three repeats per arm
(36 executions), at most 21600 recorded execution seconds, and **human scoring**.
The primary metric is binary success: all validated requirements/rubric checks pass.
Adoption uses only the two held-out cases: improvement at least 0.20, no losses,
no uncertain cases, no optimized critical violations across all cases, and at most
50% more mean execution time. These are predeclared policy choices, not measured effects.
Two held-out cases support only a small descriptive comparison, not population inference.

The human must provide the actual Claude Code **surface, exact model ID, product version**,
accept or correct execution conditions/thresholds, and name the human scorer. The scorer
must later perform label-blind paired scoring, alternate presentation position by repeat
0/1/0, and repeat with reversed order; actual disagreement remains uncertain.
Human scoring needs no model calibration. Choosing a model scorer instead requires a
revised committed plan and genuine human-labeled calibration samples/agreement before
execution; do not create labels on the human's behalf or substitute code scoring to bypass review.

## After human confirmation, before any execution

1. Reconcile confirmed case-specific corrections with the source JSONL/files. Update
   hashes for changed files; only confirmed cases become `ready`. Keep the named reviewer,
   actual date and decisions in the review ledger (extra fields in source cases are rejected).
2. Replace plan `unknown` surface/model and `human:pending-review`; match target/surface/model
   in all six source case records. Record exact product version/conditions in the plan.
   Update plan `created_at` to the real freeze time, and refresh ledger plan/case SHA-256s.
3. Commit the final plan, source cases, ledger and development-lock evidence **before**
   creating execution outputs. Record this commit in subsequent execution logs. A timestamp
   or hash alone does not prove Git commit ordering. Do not amend this commit after runs start.
4. Only after that commit and the explicit human confirmation may actual paired runs start.
   Keep result artifacts in a later commit; include both failures and successes. Collect
   real execution and scoring evidence required by `ROW_F` in `evidence.py`. Do not treat
   synthetic unit-test results as effect evidence.

This preparation tool cannot execute, approve, score or mark source cases ready.
To regenerate a fresh packet in a new directory (existing destinations are refused):

```sh
python3 tools/prepare_evaluation.py --output outputs/issue-7-review
```

The tool validates the plan structure with the same validator used by `compare`, checks
source-file hashes, and copies bytes into separate development/held-out directories.
It prints no case content. `review/cases.json` contains all cases for human inspection;
optimizers should not open it. The ledger binds the candidate plan and source JSONL hashes.
Schema validity does not establish human review, final condition selection or readiness.
