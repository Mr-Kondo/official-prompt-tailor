# official-prompt-tailor

利用先(ChatGPT / ChatGPT Work / Codex / Claude / Cowork / Claude Code / Gemini / Antigravity / Gemini CLI / Google ADK)の公式資料を根拠にプロンプトを最適化し、**取得・引用・適用範囲・変更箇所への結合**をスクリプトで検証する スキル(`SKILL.md` 形式)です。Claude Code・Codex・Antigravity・Claude Cowork・ChatGPT Work で動かせるように作ってあります(実機で動かして確認したのは Claude Code・Codex・Antigravity CLI。違いと結果は「動かせる製品と制約」)。

```text
.claude/skills/official-prompt-tailor/
  SKILL.md                       入口・手順・表示規則
  references/registry.json       資料(20)・対象(11)・審査済み規則(34)
  references/evidence-contract.md / review-rubric.md
  scripts/evidence.py            fetch / verify / assess / compare(標準ライブラリのみ)
tests/test_official_prompt_tailor.py   evals/prompt-tailor-cases.jsonl   outputs/(Git 管理外)
```

## インストール

macOS・Linux・Windows で動きます(Python 3.9 以上、標準ライブラリのみ)。Windows では `python3` を `python` または `py -3` に読み替えてください。

```bash
python3 install.py --project <プロジェクト> --gitignore            # Claude Code(既定): <dir>/.claude/skills/
python3 install.py --user                                          # Claude Code: ~/.claude/skills/
python3 install.py --target codex --project <プロジェクト>         # Codex: <dir>/.agents/skills/(--user は ~/.agents/skills/)
python3 install.py --target antigravity --user                     # Antigravity アプリ・IDE: ~/.gemini/config/skills/
python3 install.py --target antigravity-cli --user                 # Antigravity CLI: ~/.gemini/antigravity-cli/skills/
python3 install.py --zip <出力先ディレクトリ>                      # Claude / Cowork 用: official-prompt-tailor.zip
python3 install.py --plugin <出力先ディレクトリ>                   # ChatGPT Work / Codex 用プラグイン: <dir>/official-prompt-tailor/
python3 install.py --uninstall --project <プロジェクト>            # 削除。他の指定(--user / --target / --plugin)もインストール時と同じ引数
```

- スキルのディレクトリをコピーするだけです。設定・hook・権限は変更しません。
- 既にある場合は止まります。`--force` で置き換え、旧版は `<skills の親>/official-prompt-tailor.bak-<日時>` に残します(削除しません)。`--dry-run` で内容だけ確認できます。
- コピー後に、コピー先のレジストリが読めるか自己検査し、失敗したら元に戻します。
- 引数の誤りは終了コード 2、インストールできない場合は 1 です。
- 既存のセッションには新しいスキルは出ません。新しいセッションを開いて呼んでください。
- 実行結果は、スキルを使うワークスペースの `outputs/official-prompt-tailor/` に出ます(`--gitignore` で `outputs/` を除外)。
- `--plugin` は `<dir>/official-prompt-tailor/` に `.codex-plugin/plugin.json` と `skills/official-prompt-tailor/` を作ります(`--author` でマニフェストの作者名を変えられます。既定は `official-prompt-tailor`)。マニフェストの形は OpenAI 同梱の `plugin-creator` の検証ツールで確認しています。ChatGPT Work へのプラグインの取り込み方は、公式資料で確認できていません。
- `--uninstall` は、インストールしたコピーを削除する唯一のコマンドです。このスクリプトのコピーだと `SKILL.md` の `name` などで確認できたものだけを消し、確認できないもの・このスクリプトが入れていないファイルが混じるもの(`--force` で消せます)は残します。シンボリックリンクはリンクだけを外します。`.bak-*` の退避と `.gitignore`、親のディレクトリは消しません。何もインストールされていなければ終了コード 1 です。`--zip` の ZIP は手で削除してください。

## 動かせる製品と制約

スキルを**動かす製品**(`input.json` の `execution_host`)と、プロンプトの**利用先**(`target_id`)は別です。置き場所は各製品の公式資料(2026-10-14 時点)によります。実機で動かしたのは Claude Code・Codex・Antigravity CLI です(Antigravity の結果は下の節)。Claude Cowork と ChatGPT Work は未確認です。

| 動かす製品(`execution_host`) | 配置 | 呼び出し | 注意 |
|---|---|---|---|
| Claude Code(`claude-code`) | `install.py`(既定) | `/official-prompt-tailor` | 読取りの観測ができる唯一のホスト |
| Codex(`codex`) | `--target codex` | `$official-prompt-tailor` か `/skills` | コマンドはサンドボックスでネットワークが制限されます。公式ドメインへの接続の承認が必要です |
| Antigravity(`antigravity`) | `--target antigravity` / `antigravity-cli` | `/official-prompt-tailor` | Python とネットワークの可否は公式資料に記載がありません。ヘッドレス CLI(`agy -p`)では許可ルールが必要です(下の節) |
| Claude Cowork(`claude-cowork`) | `--zip` で ZIP を作り、Customize > Skills > + > Upload a skill | 自動または指定 | コード実行の有効化が必要です。ネットワークは送信許可設定に従います |
| ChatGPT Work(`chatgpt-work`) | 単体スキルの配置先は資料にありません。`--plugin` でプラグインを作ります(取り込み方は未確認) | `@` で選択 | スクリプトの実行とネットワークの可否は公式資料で確認できていません |

- **読取りの観測は Claude Code だけ**です。他の製品では `verify` が `no_log_adapter_for_host` で `INCOMPLETE` になります。仕様で、ログを推測や自己申告で補いません。参照検証は最良でも `INCOMPLETE` のまま、取得・引用・適用範囲・変更の結合は検査されます。
- `--zip` は claude.ai の受け入れ条件(frontmatter は `name` / `description` など 6 キーのみ、description は 200 文字以内)を満たさないと作成を拒否します。claude.ai に上げたスキルは、API や Claude Code には同期されません。
- 必要な接続先(HTTPS): `code.claude.com`、`platform.claude.com`、`support.claude.com`、`learn.chatgpt.com`、`developers.openai.com`、`support.google.com`、`geminicli.com`、`adk.dev`、`www.antigravity.google`、`antigravity.google`、`ai.google.dev`。許可されないと `fetch` は `blocked` になります。許可設定はスキルから変更しません。
- 他の製品のログを読む adapter は未実装です。製品が公式にログ形式を示したら、`LOG_ADAPTER_HOSTS` と adapter を足します。

### Antigravity CLI での確認結果(2026-10-14)

`--target antigravity` でプロジェクトに配置したスキルを、`agy -p … --mode accept-edits` のヘッドレス実行で動かしました。`--sandbox` は付けていません。

**許可ルール。** ヘッドレス実行は対話で承認できません。許可ルールにないコマンドは自動で拒否され、その時点で実行が終わります(出力の `denied_actions`)。ルールはグローバル設定 `~/.gemini/antigravity-cli/settings.json` の `permissions.allow` に `command(<対象>)` の形で書きます。照合はコマンド列の先頭からのトークン単位で、`command(find .)` は `find .agents/…` に一致しませんでした。このスキルの手順に使う設定は次のとおりです。

```json
{"permissions":{"allow":[
  "command(python3 .agents/skills/official-prompt-tailor/scripts/evidence.py)",
  "command(python3 -I -)",
  "command(grep)",
  "command(ls)",
  "command(mkdir -p outputs/official-prompt-tailor)"
]}}
```

- 設定は全プロジェクトに効きます。`python3 -I -` は任意コードを実行できる権限です(SKILL.md の Python ブロック用)。`install.py` はこの設定を変更しません。`--dangerously-skip-permissions` は使っていません。
- エージェントが絶対パスで `evidence.py` を呼ぶことがあります。その場合と、`~/.gemini/antigravity-cli/skills/` に入れた場合は、そのパスのルールが別に要ります。
- この最小設定だけでの完走は確認していません。測定では `python3 -c`、`find`、`cat` などを測定中だけ追加し、終了後に戻しました。手順外のコマンド(`git status`、`python3 --version` など)を呼ぶと、拒否されて止まることがあります。

**結果。** 原文「READMEの誤字を直して。内容は変えないで。」、利用先 `claude-code`、`execution_host: antigravity`。

- `fetch`: `claude-code-best-practices` を live で取得しました(HTTP 200)。
- `verify`: 8 項目中 7 項目が PASS で、`read_observation` だけが `UNAVAILABLE`(`no_log_adapter_for_host`)、総合は `INCOMPLETE` です。検証まで進んだ 12 回の run のすべてで同じでした。
- 返答は「参照検証 INCOMPLETE / 意味審査 NOT_REVIEWED / 効果 NOT_EVALUATED」の 3 行で、案は「候補」と明記されました。

**エージェントの挙動と SKILL.md の改善。** 初期の SKILL.md では、エージェントが `evidence.py` のソースを `python3 -c` などで読み回して入力・出力の形を調べ、時間とトークンを使い、権限拒否で止まりやすくなりました。次の 3 つを足しました。

1. `input.json` の完成例(SKILL.md)。
2. `recommendations.json` の完成例(`references/recommendations-example.md`)。
3. 「`evidence.py` は実行するだけで、ソースは読まない」の一文(SKILL.md)。

同じ原文・同じプロンプトで、2 版を交互に実行して比べました(各 7 回)。権限拒否で最終回答の前に止まった回は、平均から除いています。

| 版 | 完走 | ソースを調べた回数 | 平均トークン | 平均所要時間 | `fetch` までの平均ステップ |
|---|---|---|---|---|---|
| v2(例 2 つ) | 6 / 7 | 7〜11 | 18.1 万 | 231 秒 | 24 |
| v3(v2 + ソースを読まない一文) | 5 / 7 | 0(7 回すべて) | 12.7 万 | 140 秒 | 13 |

- 測定は 1 つの原文、Antigravity CLI、`agy` の既定モデル(指定なし)での結果です。他のモデルや他の製品、他の原文には当てはまるとは限りません。
- 統計的な確証ではありません。所要時間とソース調べの回数は v2 と v3 で重なりませんが、トークンは分布が重なります(v3 の最大 17.6 万は v2 の最小 16.0 万より大きい)。
- v3 は、権限拒否による 6 ステップでの早期停止が 2 回ありました(v2 は 0 回)。一文の影響かどうかは、この数では区別できません。

## 3 つの結果は別物です

「資料を読んだ」「意味が合っている」「成果が出た」は別の事実なので、別々に表示します。

| 結果 | コマンド | 値 → 終了コード |
|---|---|---|
| 参照検証 | `verify` | `PASS_LIVE` / `PASS_CACHE` → 0、`FAIL` → 1、`INCOMPLETE` → 2 |
| 意味審査 | `assess` | `PASS` → 0、`FAIL` → 1、`INCOMPLETE`(未審査・校正不足・判断不足)→ 2 |
| 効果測定 | `compare` | `IMPROVED_ON_TEST_SET` → 0、`REGRESSED` / `NO_CLEAR_IMPROVEMENT` → 1、`NOT_EVALUATED` / `INCOMPLETE` → 2。契約違反は 1 |

`fetch` は 0=必須資料の取得・本文検査に成功、1=契約違反、2=blocked / missing / stale / target 未解決。**`fetch` の 0 は検証済みではありません。** 引数の誤りは argparse の 2 です。

推奨の採用には「参照検証 PASS」と「意味審査 PASS」の両方が必要です。「改善を実測済み」と書けるのは、その対象の `IMPROVED_ON_TEST_SET` があるときだけで、一般的な性能向上や統計的な確証は意味しません。

## 使い方

Claude Code で `/official-prompt-tailor` を呼ぶか、「このプロンプトを Claude Code 向けに公式資料に沿って最適化して」と頼みます。手動で流す場合:

**1. 通常の例(live)**

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py fetch --input outputs/official-prompt-tailor/input-run-001.json --out outputs/official-prompt-tailor/run-001 --mode live
# 取得した snapshots/<id>/body.txt を Claude Code で読み、optimized.md と recommendations.json を書く
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py verify --run outputs/official-prompt-tailor/run-001 --mode live --host-log <ユーザーが指定した実行ログ>
```

`input.json` の項目は `schema_version`(1)、`execution_host`(`claude-code` / `codex` / `chatgpt-work` / `antigravity` / `claude-cowork`)、`target_id`、`target_surface`、`model_id`、`purpose`、`original_path`、`constraints` です。surface と model が不明なら文字列 `unknown` にします(推定で埋めません)。

**2. cache の例** — 7 日以内の取得物を明示的に再検査します。`fetched_at` は元の取得日時のままで、再検査日時は `rechecked_at` に入ります。

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py fetch --input outputs/official-prompt-tailor/input-run-001.json --out outputs/official-prompt-tailor/run-002 --mode cache --cache outputs/official-prompt-tailor/run-001
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py verify --run outputs/official-prompt-tailor/run-002 --mode cache --host-log <...>   # PASS_CACHE
```

cache の run を `--mode live` で検証すると `INCOMPLETE`(live として扱いません)。

**3. INCOMPLETE の例** — 実行ログを渡さない場合、読取りを観測できないので、他がすべて合格でも総合は `INCOMPLETE` です。2026-10-14 に実際に取得した run-001/run-002 で確認した出力:

```text
verify: INCOMPLETE ['no_host_log'] (exit 2)                                          # run-002 --mode cache、ログなし
verify: INCOMPLETE ['mode_mismatch', 'no_host_log', 'status_not_for_mode'] (exit 2)  # run-002(cache)を --mode live で検証
```

`INCOMPLETE` の他の原因: blocked / missing / stale の資料、target が `unresolved`、provisional 生成、適用できる公式資料がない target(例: 通常の `gemini` / `antigravity` の一回の依頼)、外部ツール由来の本文(origin を独立確認できない)、規則の審査後に資料本文が変わった(`rereview_pending`)。

## 信頼境界(何を保証し、何を保証しないか)

- **保証するもの**: 登録 URL への取得イベント(スクリプトが HTTP 取得して本文を検査した)、raw / body の SHA-256 と決定的な再抽出、引用が本文の指定行に実在すること、target / surface / model / purpose と審査済み規則の一致、変更箇所と原文・改稿の span / hash の結合、レビュー記録・評価記録の構造と由来の整合。
- **保証しないもの**: モデルの認知的な理解、引用が主張を意味的に支持すること、改稿が原文の意味を保つこと(これは `assess` の記録を人または別文脈が判断する)、改稿の性能。`verify` の合格は意味審査・効果測定を兼ねません。
- **snapshot は改ざん耐性のある署名ではありません。** 同じユーザーが書き換えられるファイルシステム上の「再検査可能な取得物」です。
- **`--host-log`** は運用者が指定したパスを信頼します。署名や収集元の認証はしません。run ディレクトリ内のログは自作として `FAIL`、ログなしは `INCOMPLETE` です。**ログのパスをスキルが自分で探して渡してはいけません。** adapter(`claude-code-transcript-jsonl-v1`)は Claude Code 2.1.287 のトランスクリプトで観測した形式(`tool_use` / `tool_result` ブロック、`Read` 結果の行番号付き本文)を読みます。公式に保証された安定スキーマではないため、Claude Code の更新後は `tests/` の形式と実ログを見比べてください。
- **DNS の残存リスク**: 接続前に名前解決結果(IPv4/IPv6 すべて)を検査しますが、検査と実接続は別々の名前解決で、敵対的な DNS(DNS rebinding)を完全には防げません。プロキシ経由でローカルの名前解決ができない環境では、解決をプロキシに委ねます(`dns_delegated_to_proxy`)。リダイレクトは自動追跡せず、各 hop の接続前に登録 URL と照合します(最大 5 hop、timeout 15 秒、応答 5 MiB)。
- 新しい依存パッケージ、グローバル設定、hook、自動投稿・自動適用はありません。

## 規則・資料の再確認方法

公式資料は変わります。`registry.json` の URL・locator・規則を恒久仕様と扱わないでください。

1. `fetch --mode live` で取得し、`snapshots/<id>/body.txt` を読む。`blocked` ならエラー(`locators_missing:…` など)を見て、ページ構造が変わったか確認する。
2. 規則を再審査する: `source_excerpt` が本文に残り、意味・適用範囲(`surfaces` / `models` / `purposes`)が変わっていないかを確認し、`source_body_sha256`(= `acquisition.json` の `body_sha256`)、`reviewed_at`、`reviewer_id` を更新する。本文が変わると旧審査は引き継がれず `rereview_pending` になります。
3. 実行中に allowlist・scope・規則を緩めて合格させない。registry の SHA-256 は acquisition と verification に記録され、取得後に registry が変わると `FAIL` です。
4. 初期規則 34 件は実装時に実行主体(Claude Code, claude-sonnet-5-5)が取得本文と照合して審査しました。**人による審査ではありません**(`reviewer_id` に明記)。人が確認した場合だけ `reviewer_id` を人の識別子にしてください。

移転の確認(2026-10-14): ADK の旧 URL `google.github.io/adk-docs/…` は `adk.dev/agents/llm-agents/` へ 301、Gemini の旧 `answer/16596215` は `answer/17094507` へ 301(クエリに変動する `visit_id` が付く)。いずれも登録せず、`fetch` は新 URL から始めます。登録外への移転は取得前に停止し、`blocked`(`redirect_not_registered:…`)として報告します。

## 意味審査・効果測定

- 審査: `references/review-rubric.md`。別文脈(または人)が `semantic-review.json` を書き、`assess --run <run> --review <run>/semantic-review.json`。校正記録(`calibration_ref` / `calibration_sha256`)と、モデル審査では実行ログが必要です。無ければ `INCOMPLETE` です。`mock` は単体試験専用で本番の PASS になりません。
- 効果: `evals/prompt-tailor-cases.jsonl`(開発用 4 件・未見用 2 件。負例として要件改変・UI への API 機能の誤適用、境界例として曖昧な依頼と否定の保持を含む)に対し、評価計画 `eval-plan.json` を**実行前に固定**し、`compare --run <run> --plan eval-plan.json --results eval-results.jsonl` で集計します。事例の `status` は `pending-review`(要件の抽出を人がまだ確認していない)で配布しており、確認して `ready` にするまで `compare` は `INCOMPLETE` を返します。実行回数は 事例数×2案×反復数(例: 10 事例・各 3 回なら 1 対象 60 回)で、採点の呼出しは別です。

## テスト

```bash
python3 -m unittest discover -s tests -p 'test_official_prompt_tailor.py'
```

ネットワークは mock、fixture は合成データです。通ることは「検査コードが想定どおり動く」ことを示すだけで、本番の公式取得・独立ログ・認知的な読了・実際の skill 発火・対象製品での効果を確認したことにはなりません。

## 出典・ライセンス

このリポジトリは MIT License です(`LICENSE`)。`references/registry.json` の `source_excerpt` は、各公式資料の短い引用で、権利は各資料の提供元に帰属します。

発想は [記事](https://zenn.dev/toritori0318/articles/a26fea4638229e) と [skills-prompt-tailor](https://github.com/toritori0318/skills-prompt-tailor)(MIT, Copyright (c) 2026 toritsuyo)を参考にした独立実装です。コード・同梱助言・性能数値は取り込んでいません。他の比較候補の文面・コードも流用していません。
