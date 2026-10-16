# Antigravity transcript adapter

`antigravity-transcript-jsonl-v1` supports the **observed agy CLI 1.3.5** format only. This is an observed local format, not an official versioned protocol. Relates to [#8](https://github.com/Mr-Kondo/official-prompt-tailor/issues/8) and [#15](https://github.com/Mr-Kondo/official-prompt-tailor/issues/15).

Use the completed native file at `~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript_full.jsonl` (or a byte-identical copy outside the run directory). `transcript.jsonl` may truncate fields; records with truncated read content are not counted. Stream-JSON stdout is a different protocol and is rejected. No automatic search of private history is performed.

The native transcript does not identify its producer version. Observe `rtk agy --version` **when producing the transcript**, then bind those immutable bytes to an explicit sidecar:

```sh
rtk python3 .claude/skills/official-prompt-tailor/scripts/pin_antigravity_log.py --log /absolute/path/transcript_full.jsonl --cli-version 1.3.5
```

Run this command from the repository root with an existing completed transcript. It creates `<log>.version.json`, requiring the exact format name, CLI version and SHA-256. It refuses to overwrite a sidecar. Unsupported versions, missing/mismatched sidecars and malformed records yield `host_log_invalid`; logs inside a run still yield `host_log_inside_run`. A sidecar is an **operator assertion**, not proof of product identity, authenticity or an older transcript's producer version. Do not stamp historical logs based only on today's installed version. Pass the native log, not the sidecar, to the existing `verify --host-log` and `sources --host-log` options.

## What counts

- Records must have strictly increasing integer `step_index`, an observed type (`USER_INPUT`, `PLANNER_RESPONSE`, `GENERIC`) and terminal status (`DONE`, `ERROR`). Active logs, empty files and new record types are rejected.
- Only one `view_file` call in a planner record can establish a read. Its immediately following record must be a successful `GENERIC` response at the next step, with no error or truncation marker. The requested absolute path must equal the `File Path: file://…` envelope (including the native backticks).
- Only the consecutive visible `N: original line` body lines within the declared `Showing lines A to B` range count. The existing observer compares their text to the source snapshot. Missing, changed, cut, batched, denied and ambiguously associated reads do not count.
- `write_to_file`, `replace_file_content` and `multi_replace_file_content` normalize to `Write` with `TargetFile`. `run_command.CommandLine` normalizes to `Bash`. Unsupported tools retain an opaque conservative barrier if their arguments name a generated artifact. Shell reads are not counted. Read results must precede the first generation **attempt**, including a denied write, matching the existing observer's conservative boundary.
- Conversation text and thinking do not contribute to read evidence. No provenance stronger than the supplied local transcript is claimed.

## Audit integration hooks (parallel audit integration)

The parallel Codex audit work (`b6115cf`) can integrate these hooks after merge:

1. The parent audit already dispatches tool events using `ev.parse_host_log(execution_host, transcript)`. Antigravity yields the existing `Read`/`Write`/`Bash` event shapes; `Bash.input.command` contains the actual `run_command.CommandLine` for the audit's existing command detector. Opaque `CodexExec` barriers have empty `cmds` and must not be treated as actual executed commands. To retain the same conservative generation boundary in audit, include their `input.script` artifact-name match in the audit `writes` list, or normalize only those opaque barriers to non-command write events. This is distinct from actual `Bash` command evidence.
2. In `transcript_texts(path, host="claude-code")`, add an Antigravity host branch returning `ev.antigravity_transcript_texts(path)` for `(first_user_input, last_non_tool_planner_text)`. It validates the same version sidecar and excludes thinking, tool output, truncated text and error records.
3. Keep `ev.sources_report(run, transcript)` as the read observer. The synthetic integration test verifies `PASS_LIVE` and `CONSISTENT`; this is not a genuine skill run.

## Genuine runtime evidence and limit

On 2026-10-17 JST a separate bounded `agy --print` invocation was allowed 45 seconds and asked only to read two synthetic lines using `view_file`, then create a synthetic `optimized.md` using `write_to_file`. No skill instructions or private source content were supplied. Read step 2 succeeded. Write step 4 failed because headless `write_file` permission was denied; the output explicitly forbade bypassing that denial. No bypass or permission change was attempted.

The adapter parsed that **actual native transcript** and observed lines `[1, 2]` before generation attempt step `3`. The requested generated file does not exist. agy exited `0` and reported `SUCCESS` with `denied_actions`; those outer statuses do not prove tool success. The exact log hash, executable hash, version, conversation ID and measured outcomes are in [the runtime evidence record](../tests/fixtures/antigravity-runtime-probe.json). The raw private transcript is not committed. [The fixture](../tests/fixtures/antigravity-1.3.5.jsonl) is synthetic and follows the observed envelope; it is not represented as the actual transcript.

**Full independent skill execution, `audit` PASS and real `sources --host-log` CONSISTENT remain unverified.** The permission block prevents the required generated run artifacts, and audit dispatch is reserved for the parallel integration. Completion of #8's full independent-run acceptance criterion must wait for a permitted runtime and the audit integration. Unit tests and CI cannot substitute for that result.
