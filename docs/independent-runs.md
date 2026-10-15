# 別のエージェントで手順を通した結果(2026-10-15)

## 何を確かめたか

v0.3.0 までの実機の確認 3 件は、規則を書いたのと同じエージェント(Claude Code、claude-sonnet-5-5)が行いました。SKILL.md の手順だけで、別の文脈のエージェントが同じ結果にたどり着けるかは、確かめていませんでした。

## 方法

- Claude Code のサブエージェント(新しい文脈)に、依頼文と run-id だけを渡して、スキルを呼ばせました。手順のヒントは渡していません。最終回答のあとに「手順への所見」を書かせました。
- 依頼は 4 種類です。Claude の API(Opus 5.5)の `agent-instruction`、Claude Code の CLAUDE.md、Antigravity の一回の依頼、Codex の AGENTS.md です。
- 各エージェント自身の記録(トランスクリプト)を、スクリプトだけで検査しました。`evidence.py verify --host-log`、`evidence.py sources --run … --host-log`、`tools/maintain.py audit` です([maintenance.md](maintenance.md) の「別のエージェントで手順を通す」)。

## 結果

| run | 利用先 / `target_surface` / `model_id` / `purpose` | エージェント | 選ばれた資料 | 読取り(観測) | `sources` | `audit` | `verify` + 記録 |
|---|---|---|---|---|---|---|---|
| indep-1 | `claude` / `api` / `claude-opus-5-5` / `agent-instruction` | sonnet | 3 | 3 / 3 | `CONSISTENT` | 全項目合格 | `PASS_LIVE` |
| indep-2 | `claude-code` / `unknown` / `unknown` / `persistent-instructions` | sonnet | 3 | 3 / 3 | `CONSISTENT` | 全項目合格 | `PASS_LIVE` |
| indep-3 | `antigravity` / `unknown` / `unknown` / `one-shot` | haiku | 1 | 1 / 1 | `CONSISTENT` | 全項目合格 | `PASS_LIVE` |
| indep-4 | `codex` / `unknown` / `unknown` / `persistent-instructions` | opus | 4 | 4 / 4 | `CONSISTENT` | 全項目合格 | `PASS_LIVE` |
| indep-5(確認。下の節) | `claude` / `api` / `claude-opus-5-5` / `agent-instruction` | sonnet | 3 | 3 / 3 | `CONSISTENT` | 全項目合格 | `PASS_LIVE` |

- 採点は、2026-10-15 に、`sources --markdown` の文言を整える前の書式で行いました(下の「限界」)。
- 「読取り」は、選ばれた資料のうち、生成の前に、成功した `Read` で全行が示されたと観測できた数です(各エージェント自身の記録から)。
- エージェント自身は、`--host-log` を付けずに `verify` を実行しました(手順どおり)。そのため、各 run の `verification.json` は `INCOMPLETE`(`no_host_log`)です。あとから、各エージェント自身の記録を `--host-log` に渡すと、4 件とも `PASS_LIVE` でした。
- 4 件とも、意味審査は未実施(`NOT_REVIEWED`)、効果測定も未実施(`NOT_EVALUATED`)です。4 件の改稿は、すべて候補です。引用した規則の審査者は、すべて agent で、人の審査は 0 件です。
- indep-1 は、最初の `verify` が `FAIL`(`unmapped_change` が 3 件)になりました。追記した文と空行が、どの変更の区間にも入っていなかったためです。エージェントは再審査 1 回(上限 2 回のうち)で直しました。

## 実行できなかったもの

- `fable` は、利用上限(クレジット不足)で、始まる前に止まりました。
- `opus` と `sonnet` は、最初の依頼(Claude の API、`agent-instruction`)の原文にある「推論の過程をすべて書き出してください」という一文が、安全機構(`reasoning_extraction`)に止められました。この一文を「作業を始める前に、変更の計画を書き出してください」に替え、同じ経路(`claude` / `api` / `claude-opus-5-5` / `agent-instruction`)で、`sonnet` が実行しました(indep-1)。`opus` は、別の依頼で実行しました(indep-4)。
- したがって、4 件のモデルの内訳は、sonnet 2 件、haiku 1 件、opus 1 件です。Anthropic 以外のモデルでは、試していません。

## 見つかった問題と、直したこと

| 問題 | 出どころ | 直したこと |
|---|---|---|
| `target_surface` と `model_id` の値と決め方が、SKILL.md にない(docs/ を同梱しない環境では分からない) | indep-1, indep-2 | SKILL.md の手順 1 に、値と、登録済みの ID を調べる `sources --all-models` を追記 |
| 「選ばれた資料をそれぞれ読む」と「無関係な資料は読まない」が両立しない | indep-1, indep-2, indep-4 | 選ばれた資料の**すべて**を読む、と明記(ログを渡したとき、`verify` は資料ごとに読取りを求めるため)。選ばれていない資料と registry は読まない |
| 追記だけの変更の書き方。差分の全区間(空行だけの追加を含む)を、変更の区間で覆う必要がある | indep-1(`verify` が `FAIL`), indep-2 | `recommendations-example.md` に追記。空の `before` でも通ることと、覆われていないと `unmapped_change` になることを、テストで確かめた |
| 行範囲は規則の `source_excerpt` を含める必要があり、`exact_excerpt` はその範囲の短い引用でよい。1 つの変更に結べる規則は 1 つ | indep-4, indep-2 | `recommendations-example.md` に追記 |
| `rules` の出力に、規則の `surfaces` と `models` がない | indep-3, indep-4 | 追加(`reviewer_id` と `human_reviewed` も) |
| 規則の助言(確認を運用側に残す、など)が、原文や「確認ステップを足さない」とぶつかったときの扱い | indep-1 | 原文と禁止を優先し、助言は最終回答に書く、と明記 |
| `verify` が通信するのか分からない | indep-4 | 通信しない(取得済みの snapshot を検査する)と明記 |
| 審査を記録していないのに `assess` を実行すべきか。ログのない run が必ず `INCOMPLETE` になる理由 | indep-3 | `assess` は審査の記録があるときだけ。ログがなければ必ず `INCOMPLETE`(仕様)と明記 |
| `sources --markdown` の、どの出力を添えるか(標準出力か、標準エラーか) | indep-4 | 標準出力だけ、と明記 |
| 「出力は日本語で」が、生成するプロンプトの言語か、エージェントの出力言語か | indep-1, indep-2 | `constraints` の出力言語は、生成するプロンプトの言語、と明記 |
| サブエージェントの記録を `--host-log` に渡しても、何も観測されない(全メッセージが sidechain のため) | 検査中に発見 | 全メッセージが 1 つの `agentId` の sidechain である記録を、そのエージェント自身の記録として全体を読むようにした |

## 更新した手順での確認(indep-5)

上の問題を直したあと、最初の `verify` が `FAIL` になった依頼(indep-1)と同じ経路を、更新した SKILL.md で、sonnet の新しい文脈に通しました。

- 入力は、SKILL.md の記述に従って決まりました。エージェントは、`sources --target claude --surface api --all-models` で登録済みの `model_id` を調べ、`target_surface=api`、`model_id=claude-opus-5-5` にしました。記録のどこでも、README と docs/ は読んでいません。
- 最初の `verify` は、`FAIL` にならず、`INCOMPLETE`(`no_host_log` だけ)でした(indep-1 は `unmapped_change` で `FAIL`)。`verify` を 2 回実行したのは、変更 c3 の内容を作り直したためです。
- `sources` は `CONSISTENT` で、選ばれた 3 資料の全行の読取りを観測しました。`verify` に自身の記録を渡すと `PASS_LIVE`、`audit` は全項目合格でした。
- エージェントが挙げた迷いは、3 点が残りました。
  - 規則の除外条件(「リスクのある操作や取り消せない操作の確認を省く場合」)が、確認を省く原文に当たるか。
  - 「原文の範囲」の線引き(新しい否定を足す変更が範囲内か)。
  - 変更の区間の事前確認。`verify` の差分が `autojunk=False` であることが、手順から分かりません。これは、完成例に追記しました。
  - 上の 2 点は規則の解釈で、手順では決まりません。意味審査の仕事です。
- `claude-chat-help` は、`claude` では常に取得される資料です。この依頼では 14 行で、使える規則は 0 件でした。選ばれた資料はすべて読む決まりなので、毎回読むことになります。この所見をきっかけに、2026-10-16 に直しました。`claude-chat-help` の規則は `one-shot` にしかないので、`one-shot` のときだけ取得します。同じ種類のずれが、ほかにも 17 組ありました(利用先 × 資料。ChatGPT・Codex・Claude Cowork・Gemini・ADK の資料と、Claude のモデル別の資料)。すべて、規則のある入力にだけ取得する経路に直し、`registry_problems` が今後のずれを拒否します([maintenance.md](maintenance.md))。使える規則は、全 1,188 入力で変わっていません。

## 直さなかったこと

- 規則の `preconditions` の解釈の幅(例: 「原文が、作業中の報告の頻度や形を求めている場合」)。当てはまるかの判断は、生成側と意味審査に残ります。
- 個人向けの規則(ChatGPT の個人設定)が、Codex の AGENTS.md の `rules` に出ること。`purpose` に、リポジトリの指示と個人の指示の区別がないためです。`preconditions` を読んで外せました。
- `execution_host` に、Agent SDK やサブエージェントの選択肢がないこと。4 件とも `claude-code` と答えました。

## 限界

- 4 件で、Anthropic のモデルだけです。サブエージェントは、同じ製品(Claude Code)の中で動きます。Codex・Antigravity・Claude Cowork・ChatGPT Work で、別のエージェントが動かした結果ではありません。
- 「手順への所見」は、各エージェントの自己申告です。`audit` が確かめたものではありません。
- `audit` は、手順を守ったか、最終回答が記録と合っているかを見ます。改稿の良し悪しは見ません。意味審査と効果測定は、していません。
- `sources` と `audit` は、同じ作者(Claude Code のエージェント)が書きました。同じ思い込みを共有している可能性があります。
- `audit` の `sources_report_pasted_as_printed` は、貼られた報告を、いまの `sources --markdown` の出力と比べます。上の採点は、文言を整える前の書式で行いました。整えたあとの書式で 5 件を採点し直すと、この検査だけが全件で失敗します(貼られた報告が、いまの書式と違うため)。ほかの検査は、全件で合格のままです。
