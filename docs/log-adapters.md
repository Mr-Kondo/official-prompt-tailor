# ログ adapter の対応範囲

対応ホストは Claude Code・Codex・Antigravity です。ログのパスは操作者または独立ランナーが指定し、自動探索しません。ログは run の外に置きます。署名や収集元の認証は行いません。明示されたRead/Write/Editのパスはresolve比較に加え、存在する場合はファイルの同一性も照合します。これは検証時点のファイルシステムによる照合で、過去の状態の証明ではありません。Bash等の生成検出は、引き続きコマンド中の生成物名に基づく保守的な判定です。

## Claude Code

`claude-code-transcript-jsonl-v1` は、Claude Code 2.1.287 のトランスクリプトで観測した `tool_use` / `tool_result` と `Read` の行番号付き本文を読みます。版番号を検査して将来版の互換性を保証する仕組みではありません。製品更新時は実ログと fixture を照合してください。

## Codex

`codex-rollout-jsonl-v1` は `~/.codex/sessions` の rollout JSONL を読みます。`codex exec --json` の標準出力は別形式のため対象外です。

- 対応版は `0.155.1`・`0.162.<数字>`・`0.163.0` です。`0.162.<数字>` は `-alpha.<数字>[.<数字>...]` 付きも受理します（例: `0.162.0-alpha.17.2`）。
- 読取りに数えるのは、`exec` 内の `exec_command` が1つで、本文パスを指定する単一の `cat` / `nl` / `head` / `tail` / `sed -n` / `bat` / `rtk read` です。先頭の `rtk proxy` は1回だけ取り除きます。入れ子のラッパー、複数ファイルの `cat`、複数コマンドは数えません。
- `;` `&` `|` `<` `>` `$` を含むコマンドは数えません。`workdir` を含むスクリプトは相対パスの基準を確定できませんが、絶対パスの読取りは対象になります。
- 本文と一致する連続行だけを数えます。切り詰め記号の前後の行や、本文の2か所に一致する行は数えません。Windows形式のパス（`\`）は未対応です。
- `optimized.md` / `recommendations.json` の名前を含むスクリプトは、生成の書込みの検出対象です。関連するツールID・content・textの形式が不正なら `host_log_invalid` です。


## Antigravity

`antigravity-transcript-jsonl-v1` は、実際に観測した agy CLI 1.3.5・1.4.0・1.4.3 の形式だけを読みます。製品公式の版付きプロトコルではありません。関連: [#8](https://github.com/Mr-Kondo/official-prompt-tailor/issues/8)・[#15](https://github.com/Mr-Kondo/official-prompt-tailor/issues/15)。

Use the completed native file at `~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript_full.jsonl` (or a byte-identical copy outside the run directory). `transcript.jsonl` may truncate fields; records with truncated read content are not counted. Stream-JSON stdout is a different protocol and is rejected. No automatic search of private history is performed.

The native transcript does not identify its producer version. Observe `rtk agy --version` **when producing the transcript**, then bind those immutable bytes to an explicit sidecar:

```sh
rtk python3 .claude/skills/official-prompt-tailor/scripts/pin_antigravity_log.py --log /absolute/path/transcript_full.jsonl --cli-version 1.3.5
```

Use `--cli-version 1.4.0` or `--cli-version 1.4.3` instead when that exact version produced the log. Run this command from the repository root with an existing completed transcript. It creates `<log>.version.json`, requiring the exact format name, CLI version and SHA-256. It refuses to overwrite a sidecar. Unsupported versions, missing/mismatched sidecars and malformed records yield `host_log_invalid`; logs inside a run still yield `host_log_inside_run`. A sidecar is an **operator assertion**, not proof of product identity, authenticity or an older transcript's producer version. Do not stamp historical logs based only on today's installed version. Pass the native log, not the sidecar, to the existing `verify --host-log` and `sources --host-log` options.

### 読取りとして数える条件

- Records must have strictly increasing integer `step_index`, an observed type (`USER_INPUT`, `PLANNER_RESPONSE`, `GENERIC`) and terminal status (`DONE`, `ERROR`). Active logs, empty files and new record types are rejected.
- In 1.3.5, only one `view_file` call in a planner record can establish a read. Its immediately following record must be a successful `GENERIC` response at the next step, with no error or truncation marker. The requested absolute path must equal the `File Path: file://…` envelope (including the native backticks).
- Only the consecutive visible `N: original line` body lines within the declared `Showing lines A to B` range count. The existing observer compares their text to the source snapshot. Missing, changed, cut, denied and ambiguously associated reads do not count. Batches count only under the 1.4.0 conditions below.
- `write_to_file`, `replace_file_content` and `multi_replace_file_content` normalize to `Write` with `TargetFile`. `run_command.CommandLine` normalizes to `Bash`. Unsupported tools retain an opaque conservative barrier if their arguments name a generated artifact. Shell reads are not counted. Read results must precede the first generation **attempt**, including a denied write, matching the existing observer's conservative boundary.
- Conversation text and thinking do not contribute to read evidence. No provenance stronger than the supplied local transcript is claimed.

### 1.4.0 の複数読取りと再開メッセージ

実際の独立実行で、一つのplannerに複数の `view_file` があり、その直後に各ファイルのGENERIC応答が連続する形式を確認した。1.4.0だけで、全呼出しが読取り・要求パスが重複しない・応答数と順序が一致する・各出力のファイル名が要求パスに一致する場合に限って観測する。一つでも失敗・切り詰め・パス違いがあればバッチ全体を数えない。書込みとの混在と同一ファイルの複数窓は未対応のまま。1.3.5は従来どおり単一読取りだけを扱う。

再開時の `SYSTEM_MESSAGE` は1.4.0で `source=SYSTEM`・`status=DONE`・ツール呼出しなしの場合だけ受理し、読取り・コマンド証拠として数えません。

### 1.4.3 の単一読取り

`read_file` の絶対パス `Path` と、その直後の成功した `GENERIC` 応答を照合します。planner内の呼出しがその1つだけで、要求と応答が未切詰め、stepが連続し、続けて別の `GENERIC` 応答がなく、`Showing lines A-B of N` と行番号付き本文がある場合に限ります。引数は `Path`・任意の `StartLine` / `EndLine` に限定します。未知の引数は拒否します。行番号は正の整数で、表示窓が要求範囲に収まる必要があります。本文一致と生成前の条件も従来どおりです。

step列は先頭0から欠番なく続く必要があります。欠番には生成書込みが隠れている可能性があるため、ログ全体を `host_log_invalid` として拒否します。stepの振り直しやログの偽造を検出する機能ではありません（[Issue #37](https://github.com/Mr-Kondo/official-prompt-tailor/issues/37)）。

応答にパス・呼出しIDがないため、関連付けは単一呼出しと直後の応答という観測形式に依存します。複数読取り、書込みとの混在は数えません（[Issue #34](https://github.com/Mr-Kondo/official-prompt-tailor/issues/34)）。ログや製品の真正性を認証する仕組みではありません。`write_file` の `Path` は生成試行として扱います。1.4.0のパス付き複数読取りを1.4.3へ推定適用せず、未観測の再開形式も受理しません。

2026-10-21 JSTに、Codex 0.163.0とagy 1.4.3の実際の別プロセスで、2行を読んだ後に別呼出しで書き込む限定プローブを実施しました。両者で生成前の2行だけを観測しました。[実行証拠の要約](../tests/fixtures/current-cli-runtime-probes.json)と[合成fixture](../tests/fixtures/antigravity-1.4.3.jsonl)は別物です。完全なスキルの通し監査やWindows/Linuxの起動確認、意味審査・改善効果の証明ではありません。

### audit との統合

`maintain.py audit` dispatches the same native tool events and uses `antigravity_transcript_texts` to read user requests and the final planner answer, excluding thinking and tool output. Native `run_command` events supply command evidence; opaque unsupported tools can establish a conservative generation barrier, but cannot stand in for executed commands. Synthetic tests cover the integrated audit; dated runtime results and their limits are kept in [the verification history](history/runtime-verification.md#adapter-runtime).


## 観測形式を固定する方針

未知の形式・版は推測で受理しません。対応範囲はコードと fixture に固定し、公式の形式説明が確認できたら差分を確認します。これは製品公式の安定スキーマという主張ではありません。完了条件は [Issue #8](https://github.com/Mr-Kondo/official-prompt-tailor/issues/8) を参照してください。
