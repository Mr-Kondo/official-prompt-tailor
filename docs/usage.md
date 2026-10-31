# 使い方(手動で流す場合)

Claude Code で `/official-prompt-tailor` を呼ぶか、「このプロンプトを Claude Code 向けに公式資料に沿って最適化して」と頼みます。ここでは、手動で流す場合の手順を示します。

## 実行前に用意するもの

以下は、リポジトリのルートで実行する手順のテンプレートです。インストール先から直接実行する場合は、`evidence.py` のパスを配置先に替えてください。Windowsでは `python3` を `py -3` または `python` に読み替えます。

- 原文ファイルと、それを `original_path` で指定する `input-run-001.json` を用意します。[入力の完成例と値の選び方](targets.md#依頼の例と決まる値)を参照してください。
- `--out` の run ディレクトリは `fetch` が作ります。先に作らず、再実行でも新しい run-id を使います。
- 本文を読み、`optimized.md` と `recommendations.json` を作る処理は自動ではありません。[recommendationsの完成例](../.claude/skills/official-prompt-tailor/references/recommendations-example.md)に従って作成します。
- `--host-log` は操作者が指定した、その実行ホストの対応ログに置き換えます。ログがなければ、この引数を省きます。対応版・形式は[ログの仕様](log-adapters.md)を参照してください。

## 1. 通常の例(live)

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py fetch --input outputs/official-prompt-tailor/input-run-001.json --out outputs/official-prompt-tailor/run-001 --mode live
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py drift --run outputs/official-prompt-tailor/run-001   # 審査後に変わった資料の規則を確認(通信しない)
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py rules --run outputs/official-prompt-tailor/run-001   # この run で選べる規則だけを出す(registry.json は読まない)
# 取得した snapshots/<id>/body.txt を Claude Code で読み、optimized.md と recommendations.json を書く
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py verify --run outputs/official-prompt-tailor/run-001 --mode live --host-log <ユーザーが指定した実行ログ>
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py sources --run outputs/official-prompt-tailor/run-001 --markdown --host-log <ユーザーが指定した実行ログ>   # どの資料を、なぜ、どこまで読んだか
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

## 3. どの資料を見たかを確かめる

`sources` は、どの資料を、なぜ、モデルに応じてどう選んだかを、スクリプトだけで出します。モデルの自己申告は使いません。実行の前と後で、使い方が違います。

```bash
# 実行の前: この入力なら、どの資料を見るか(通信しません。run も要りません)
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py sources --target claude --surface api --model claude-opus-5-5 --purpose agent-instruction --markdown
# モデルごとの違い: 共通の資料と、モデルごとに加わる資料
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py sources --target claude --surface api --all-models --markdown
# 実行の後: 取得した資料、本文のハッシュ、引用した規則と審査者、読んだ行
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py sources --run outputs/official-prompt-tailor/run-001 --host-log <実行ホストの対応ログのパス> --markdown
```

- モデル別の資料(モデル別ガイド)があるのは、Claude のモデルだけです。ほかの利用先では、`model_id` を変えても、選ばれる資料は変わりません。報告の「モデルによる選択」に、そう書かれます。
- Claude のモデル別ガイドは、`target_surface` が `api` で、`model_id` が完全に一致するときだけ選ばれます。`model_id` の綴りが違うときは(`claude-opus-5-5-20261001` など)、登録済みの ID を示しますが、選択には使いません。
- `--host-log` を渡すと、`verify` と同じ規則で、取得した本文をどの行まで読んだかを確かめます。ログを渡さないと、読取りは「未観測」になります。ログのパスは、あなたが指定します。スキルは探しません。
- Claude Codeの別のエージェント(サブエージェント)に実行させたときは、そのエージェント自身の記録(`~/.claude/projects/<プロジェクト>/<セッション>/subagents/agent-<id>.jsonl`)を渡します。メインのセッションの記録には、サブエージェントが読んだ行は入りません。`verify --host-log` にも同じ記録を渡せます。全メッセージが 1 つの `agentId` のものである記録は、そのエージェントの記録として全体を読みます。
- 判定は、`CONSISTENT`(0)、`INCONSISTENT`(1)、`INCOMPLETE`(2)です。本文のハッシュが記録と違う、選択が registry の規則と違う、範囲外の規則を引用している、などが `INCONSISTENT` です。

## 日本語のプロンプトの推敲

`japanese-readability-editor` が実行ホストで利用でき、その `SKILL.md` を読める場合だけ、日本語のプロンプト案をモードBで推敲します。原意・数値・条件・否定・例外・出力形式を保ち、重複や冗長な表現を整理します。利用できない場合は使わず、そのまま進めます。自動インストールや追加取得は行いません。推敲後の最終版で変更記録と検証を行い、推敲そのものを独立した意味審査や改善効果の証明にはしません。

v0.6.11から、エージェントは[推敲連携の手順](../.claude/skills/official-prompt-tailor/references/readability.md)で実行・スキップ・失敗をrun内の `readability.json` に自動保存します。通常の `verify` が `readability-verification.json` を自動保存し、最終版との一致と証拠の欠損・変更を検査します。利用者が記録を手入力する必要はありません。旧runは `NOT_RECORDED`、スキップは `SKIPPED_REPORTED`、実行記録が一致した場合は `CONSISTENT_EXECUTION_RECORD` と表示します。参照検証とは別の記録であり、モデルの理解・意味保存・品質向上は保証しません。

v0.6.12から、ツールが終了コード0を返しても、比較結果の入力パス・主要項目の型や、モードBの9検査が不正・欠損なら実行完了にしません。検証結果の `comparison_candidates` に、数値・否定など、見比べる候補の検査名も保存します。`[]` は候補なし、`null` は有効な候補一覧を示せない状態です。候補がなくても、意味審査や品質評価の合格にはなりません。失敗後に最終版を変更しても、失敗は `EXECUTION_FAILED` と表示します。

## 4. INCOMPLETE の例

動作や出力内容の問題を記録する場合は、[問題報告の作り方](feedback.md)と[テンプレート](templates/problem-report.md)を使います。

実行ログを渡さない場合、読取りを観測できません。他がすべて合格でも、総合は `INCOMPLETE` です。2026-10-14 に実際に取得した run-001 と run-002 で確認した出力は、次のとおりです。

```text
verify: INCOMPLETE ['no_host_log'] (exit 2)                                          # run-002 --mode cache、ログなし
verify: INCOMPLETE ['mode_mismatch', 'no_host_log', 'status_not_for_mode'] (exit 2)  # run-002(cache)を --mode live で検証
```

`INCOMPLETE` になる、その他の原因は次のとおりです。

- blocked / missing / stale の資料。
- target が `unresolved`。
- provisional 生成。
- 適用できる公式資料がない target(例: 通常の `gemini` の一回の依頼)。
- 外部ツール由来の本文(origin を独立確認できない)。
- 規則の審査後に、資料本文が変わった(`rereview_pending`)。

`sources --markdown` のログ案内は実行ホストに合わせて出ます。対応ホストでも、操作者が指定した対応版・形式のログが必要です。未対応ホストは未対応と表示し、別製品のログの投入を勧めません。v0.6.2からレポートは書式4です。旧書式の貼付けは `audit` で比較不能の `WARN` として扱います。

## 取得と確認の結果の読み方

`fetch` の終了コードは、0 が「必須資料の取得・本文検査に成功」、1 が「契約違反」、3 が「一部の資料だけ取得(取れた資料で最適化を続けられる。結果は候補で、検証済みではない)」、2 が「何も取得できない / target 未解決」です。**`fetch` の 0 は、検証済みではありません。** 引数の誤りは、argparse の 2 です。

資料が一部取れなくても、取れた資料で最適化を続けられます(終了コード3)。標準出力の `coverage` が、取れた資料と取れなかった資料(理由つき)を示します。live で取れなかった資料は、7日以内の前の run の取得物で自動的に補い、それでも足りないときは、ホストが製品名とモデル名だけで検索し、公式ドメインのページを `fetch --extra-source` で渡します。検索で見つけたページは審査済みの規則がないので、変更は `candidate` として記録し、参照検証は `INCOMPLETE` のままです。`registry.json` は書き換えません。手順は[補い方](../.claude/skills/official-prompt-tailor/references/fallback.md)、決定の理由は [adr/0001](../adr/0001-partial-evidence-and-search-fallback.md) にあります。

`drift` は、この 3 つとは別の、資料の更新確認です。規則の審査後に資料の本文が変わったかを、ハッシュと引用の有無で調べます。終了コードは、`CURRENT` → 0、`DRIFT` / `INCOMPLETE` → 2、契約違反 → 1 です。

`sources` も別物で、どの資料を、なぜ(利用先・`purpose`・`target_surface`・`model_id` に応じてどう選んで)見たかを、スクリプトだけで出します。実行の前は `--target` などで計画を、実行の後は `--run` で、取得した本文のハッシュ・引用した規則とその審査者・(ログを渡せば)読んだ行を確かめます。終了コードは、`CONSISTENT` → 0、`INCONSISTENT` → 1、`INCOMPLETE` → 2 です(計画は 0)。使い方は、上の「3. どの資料を見たかを確かめる」にあります。

## 会社の文書を持ち出せない場合

`scripts/private_feedback.py` で、原文を入力せずに選択式の評価票を作れます。社内で元プロンプトと改稿を確認し、行・列の対応、例外、予定表現などの判定だけを記入して共有用JSONへ出力します。自動送信は行わず、共有できるかは社内規則に従います。xlsxの検査や匿名化、独立した意味審査の代わりにはなりません。[操作・出力・エラー](../.claude/skills/official-prompt-tailor/references/private-feedback.md)を参照してください。開発時の根拠は[設計履歴](history/private-feedback-design.md)に残しています。
