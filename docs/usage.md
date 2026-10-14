# 使い方(手動で流す場合)

Claude Code で `/official-prompt-tailor` を呼ぶか、「このプロンプトを Claude Code 向けに公式資料に沿って最適化して」と頼みます。ここでは、手動で流す場合の手順を示します。

## 1. 通常の例(live)

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py fetch --input outputs/official-prompt-tailor/input-run-001.json --out outputs/official-prompt-tailor/run-001 --mode live
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py drift --run outputs/official-prompt-tailor/run-001   # 審査後に変わった資料の規則を確認(通信しない)
# 取得した snapshots/<id>/body.txt を Claude Code で読み、optimized.md と recommendations.json を書く
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py verify --run outputs/official-prompt-tailor/run-001 --mode live --host-log <ユーザーが指定した実行ログ>
```

`input.json` の項目は、次のとおりです。

- `schema_version`: 1
- `execution_host`: `claude-code` / `codex` / `chatgpt-work` / `antigravity` / `claude-cowork`
- `target_id`
- `target_surface` と `model_id`: 不明なら、文字列 `unknown` にします(推定で埋めません)。
- `purpose`
- `original_path`
- `constraints`

## 2. cache の例

7 日以内の取得物を、明示的に再検査します。`fetched_at` は元の取得日時のままで、再検査日時は `rechecked_at` に入ります。

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py fetch --input outputs/official-prompt-tailor/input-run-001.json --out outputs/official-prompt-tailor/run-002 --mode cache --cache outputs/official-prompt-tailor/run-001
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py verify --run outputs/official-prompt-tailor/run-002 --mode cache --host-log <...>   # PASS_CACHE
```

cache の run を `--mode live` で検証すると `INCOMPLETE` です(live として扱いません)。

## 3. INCOMPLETE の例

実行ログを渡さない場合、読取りを観測できません。他がすべて合格でも、総合は `INCOMPLETE` です。2026-10-14 に実際に取得した run-001 と run-002 で確認した出力は、次のとおりです。

```text
verify: INCOMPLETE ['no_host_log'] (exit 2)                                          # run-002 --mode cache、ログなし
verify: INCOMPLETE ['mode_mismatch', 'no_host_log', 'status_not_for_mode'] (exit 2)  # run-002(cache)を --mode live で検証
```

`INCOMPLETE` になる、その他の原因は次のとおりです。

- blocked / missing / stale の資料。
- target が `unresolved`。
- provisional 生成。
- 適用できる公式資料がない target(例: 通常の `gemini` / `antigravity` の一回の依頼)。
- 外部ツール由来の本文(origin を独立確認できない)。
- 規則の審査後に、資料本文が変わった(`rereview_pending`)。
