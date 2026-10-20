# 実行・検証の履歴

各節は記載された日付・版の結果です。現在の操作手順や対応範囲は[利用手順](../usage.md)・[製品の制約](../products.md)・[ログの仕様](../log-adapters.md)を参照してください。古い許可設定は、現在の推奨設定ではありません。

- [Antigravity 初期確認](#antigravity-initial)
- [独立実行](#independent-runs)
- [Antigravity adapter の実ランタイム検証](#adapter-runtime)

<a id="antigravity-initial"></a>

## Antigravity CLI での確認結果(2026-10-14)

`--target antigravity` でプロジェクトに配置したスキルを、`agy -p … --mode accept-edits` のヘッドレス実行で動かしました。`--sandbox` は付けていません。

### 許可ルール

ヘッドレス実行は、対話で承認できません。許可ルールにないコマンドは自動で拒否され、その時点で実行が終わります(出力の `denied_actions`)。

ルールは、グローバル設定 `~/.gemini/antigravity-cli/settings.json` の `permissions.allow` に、`command(<対象>)` の形で書きます。照合は、コマンド列の先頭からトークン単位で行われます。`command(find .)` は、`find .agents/…` に一致しませんでした。

このスキルの手順に使う設定は、次のとおりです。

```json
{"permissions":{"allow":[
  "command(python3 .agents/skills/official-prompt-tailor/scripts/evidence.py)",
  "command(python3 -I -)",
  "command(grep)",
  "command(ls)",
  "command(mkdir -p outputs/official-prompt-tailor)"
]}}
```

- 設定は、全プロジェクトに効きます。`python3 -I -` は、任意コードを実行できる権限です(SKILL.md の Python ブロック用)。
- `install.py` は、この設定を変更しません。`--dangerously-skip-permissions` は使っていません。
- エージェントが、絶対パスで `evidence.py` を呼ぶことがあります。その場合と、`~/.gemini/antigravity-cli/skills/` に入れた場合は、そのパスのルールが別に要ります。
- この最小設定だけで完走するかは、実行ごとにばらつきました。v0.1.1 を 8 回実行して、最終回答まで完走したのは 3 回です(4 回で 0 回、別の日の 4 回で 3 回)。止まった原因は毎回変わり、拒否されたのは `python3 -c`、`cat`、複合コマンドの `ls` でした。詳細は「最終版(v0.1.1)の再確認」にあります。
- 測定では、`python3 -c`、`find`、`cat` などを測定中だけ追加し、終了後に戻しました。
- 手順外のコマンド(`git status`、`python3 --version` など)を呼ぶと、拒否されて止まることがあります。

### 結果

条件は、原文「READMEの誤字を直して。内容は変えないで。」、利用先 `claude-code`、`execution_host: antigravity` です。

- `fetch`: `claude-code-best-practices` を live で取得しました(HTTP 200)。
- `verify`: 8 項目中 7 項目が PASS でした。`read_observation` だけが `UNAVAILABLE`(`no_log_adapter_for_host`)で、総合は `INCOMPLETE` です。検証まで進んだ 12 回の run のすべてで、同じ結果でした。
- 返答: 「参照検証 INCOMPLETE / 意味審査 NOT_REVIEWED / 効果 NOT_EVALUATED」の 3 行を出し、案は「候補」と明記しました。

### エージェントの挙動と SKILL.md の改善

初期の SKILL.md では、エージェントが `evidence.py` のソースを `python3 -c` などで読み回し、入力・出力の形を調べました。そのため、時間とトークンを使い、権限拒否で止まりやすくなりました。

次の 3 つを足しました。

1. `input.json` の完成例(SKILL.md)。
2. `recommendations.json` の完成例(`references/recommendations-example.md`)。
3. 「`evidence.py` は実行するだけで、ソースは読まない」の一文(SKILL.md)。

### 版の比較

同じ原文・同じプロンプトで、2 版を交互に実行して比べました(各 7 回)。権限拒否で最終回答の前に止まった回は、平均から除いています。

| 版 | 完走 | ソースを調べた回数 | 平均トークン | 平均所要時間 | `fetch` までの平均ステップ |
|---|---|---|---|---|---|
| v2(例 2 つ) | 6 / 7 | 7〜11 | 18.1 万 | 231 秒 | 24 |
| v3(v2 + ソースを読まない一文) | 5 / 7 | 0(7 回すべて) | 12.7 万 | 140 秒 | 13 |

### 最終版(v0.1.1)の再確認

公開リポジトリのタグ `v0.1.1` をクローンし、`install.py --target antigravity` で配置して、同じ原文・同じプロンプトで実行しました。許可ルールは、2 条件です。

- 条件 A: このページの最小設定(5 件)のみ。
- 条件 B: 最小設定に、`python3 -c`、`python3 -`、`cat`、`head`、`tail`、`wc`、`sed -n`、`find`、`echo`、`pwd` と、各 run のパスのルールを、測定中だけ追加。

| 条件 | 実行日 | 最終回答まで完走 | 止まった理由 |
|---|---|---|---|
| A | 2026-10-14 | 0 / 4 | `python3 -c`(3 回)、`cat`(1 回)が拒否されました。`fetch` には 3 回届きました。 |
| A | 2026-10-15 | 3 / 4 | 残る 1 回は、`ls … \|\| echo "not found"`(複合コマンド)が拒否されました。 |
| B | 2026-10-14 | 3 / 4 | 残る 1 回は、検証の後に `git status` が拒否されました。 |

- 完走した回は、すべて `verify` まで進みました。総合は `INCOMPLETE` で、`INCOMPLETE` の理由は `read_observation`(`no_log_adapter_for_host`)だけです。
- 条件 B の改稿は 4 回とも同じで、変更は `editorial` 1 件と `official` 1 件(規則 `claude-code-specific-context`)でした。
- 2026-10-14 の 8 回(条件 A・B 各 4 回)のうち、`evidence.py` のソースを調べたのは 2 回(各条件 1 回)で、どちらも 1 件でした。2026-10-15 の 4 回は、0 回です。
- 条件 B の完走は 3 回で、トークンは 10.2 万・10.7 万・20.5 万、所要時間は 147〜189 秒でした。
- 2026-10-15 の条件 A の完走 3 回は、トークンの平均が 11.8 万、所要時間の平均が 139 秒でした。

### 結果の限界

- 測定は、1 つの原文、Antigravity CLI、`agy` の既定モデル(指定なし)での結果です。他のモデル・他の製品・他の原文には、当てはまるとは限りません。
- 統計的な確証ではありません。所要時間とソース調べの回数は、v2 と v3 で重なりません。トークンは分布が重なります(v3 の最大 17.6 万は、v2 の最小 16.0 万より大きい)。
- v3 は、権限拒否による 6 ステップでの早期停止が 2 回ありました(v2 は 0 回)。一文の影響かどうかは、この数では区別できません。
- 同じ版・同じ条件でも、日によって結果が変わりました(最小設定での完走は、2026-10-14 が 0 / 4、2026-10-15 が 3 / 4)。止まる原因も、毎回違うコマンドでした。最小設定だけで確実に動くとは、言えません。

<a id="independent-runs"></a>

## 別のエージェントで手順を通した結果(2026-10-15)

### 何を確かめたか

v0.3.0 までの実機の確認 3 件は、規則を書いたのと同じエージェント(Claude Code、claude-sonnet-5-5)が行いました。SKILL.md の手順だけで、別の文脈のエージェントが同じ結果にたどり着けるかは、確かめていませんでした。

### 方法

- Claude Code のサブエージェント(新しい文脈)に、依頼文と run-id だけを渡して、スキルを呼ばせました。手順のヒントは渡していません。最終回答のあとに「手順への所見」を書かせました。
- 依頼は 4 種類です。Claude の API(Opus 5.5)の `agent-instruction`、Claude Code の CLAUDE.md、Antigravity の一回の依頼、Codex の AGENTS.md です。
- 各エージェント自身の記録(トランスクリプト)を、スクリプトだけで検査しました。`evidence.py verify --host-log`、`evidence.py sources --run … --host-log`、`tools/maintain.py audit` です([maintenance.md](../maintenance.md) の「別のエージェントで手順を通す」)。

### 結果

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

### 実行できなかったもの

- `fable` は、利用上限(クレジット不足)で、始まる前に止まりました。
- `opus` と `sonnet` は、最初の依頼(Claude の API、`agent-instruction`)の原文にある「推論の過程をすべて書き出してください」という一文が、安全機構(`reasoning_extraction`)に止められました。この一文を「作業を始める前に、変更の計画を書き出してください」に替え、同じ経路(`claude` / `api` / `claude-opus-5-5` / `agent-instruction`)で、`sonnet` が実行しました(indep-1)。`opus` は、別の依頼で実行しました(indep-4)。
- したがって、4 件のモデルの内訳は、sonnet 2 件、haiku 1 件、opus 1 件です。Anthropic 以外のモデルでは、試していません。

### 見つかった問題と、直したこと

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

### 更新した手順での確認(indep-5)

上の問題を直したあと、最初の `verify` が `FAIL` になった依頼(indep-1)と同じ経路を、更新した SKILL.md で、sonnet の新しい文脈に通しました。

- 入力は、SKILL.md の記述に従って決まりました。エージェントは、`sources --target claude --surface api --all-models` で登録済みの `model_id` を調べ、`target_surface=api`、`model_id=claude-opus-5-5` にしました。記録のどこでも、README と docs/ は読んでいません。
- 最初の `verify` は、`FAIL` にならず、`INCOMPLETE`(`no_host_log` だけ)でした(indep-1 は `unmapped_change` で `FAIL`)。`verify` を 2 回実行したのは、変更 c3 の内容を作り直したためです。
- `sources` は `CONSISTENT` で、選ばれた 3 資料の全行の読取りを観測しました。`verify` に自身の記録を渡すと `PASS_LIVE`、`audit` は全項目合格でした。
- エージェントが挙げた迷いは、3 点が残りました。
  - 規則の除外条件(「リスクのある操作や取り消せない操作の確認を省く場合」)が、確認を省く原文に当たるか。
  - 「原文の範囲」の線引き(新しい否定を足す変更が範囲内か)。
  - 変更の区間の事前確認。`verify` の差分が `autojunk=False` であることが、手順から分かりません。これは、完成例に追記しました。
  - 上の 2 点は規則の解釈で、手順では決まりません。意味審査の仕事です。
- `claude-chat-help` は、`claude` では常に取得される資料です。この依頼では 14 行で、使える規則は 0 件でした。選ばれた資料はすべて読む決まりなので、毎回読むことになります。この所見をきっかけに、2026-10-16 に直しました。`claude-chat-help` の規則は `one-shot` にしかないので、`one-shot` のときだけ取得します。同じ種類のずれが、ほかにも 17 組ありました(利用先 × 資料。ChatGPT・Codex・Claude Cowork・Gemini・ADK の資料と、Claude のモデル別の資料)。すべて、規則のある入力にだけ取得する経路に直し、`registry_problems` が今後のずれを拒否します([maintenance.md](../maintenance.md))。使える規則は、全 1,188 入力で変わっていません。

### 直さなかったこと

- 規則の `preconditions` の解釈の幅(例: 「原文が、作業中の報告の頻度や形を求めている場合」)。当てはまるかの判断は、生成側と意味審査に残ります。
- 個人向けの規則(ChatGPT の個人設定)が、Codex の AGENTS.md の `rules` に出ること。`purpose` に、リポジトリの指示と個人の指示の区別がないためです。`preconditions` を読んで外せました。
- `execution_host` に、Agent SDK やサブエージェントの選択肢がないこと。4 件とも `claude-code` と答えました。

### 限界

- 4 件で、Anthropic のモデルだけです。サブエージェントは、同じ製品(Claude Code)の中で動きます。Codex・Antigravity・Claude Cowork・ChatGPT Work で、別のエージェントが動かした結果ではありません。
- 「手順への所見」は、各エージェントの自己申告です。`audit` が確かめたものではありません。
- `audit` は、手順を守ったか、最終回答が記録と合っているかを見ます。改稿の良し悪しは見ません。意味審査と効果測定は、していません。
- `sources` と `audit` は、同じ作者(Claude Code のエージェント)が書きました。同じ思い込みを共有している可能性があります。
- `audit` の `sources_report_pasted_as_printed` は、貼られた報告を、いまの `sources --markdown` の出力と比べます。上の採点は、文言を整える前の書式(版の行がない、書式 1)で行いました。2026-10-16 に、`sources --markdown` の先頭に書式の版の行を足しました(いまは書式 2)。5 件を採点し直すと、この検査は全件で `WARN`(書式 1 の報告は、書式 2 と比べられない)になり、ほかの検査は全件で合格のままです。`audit` 全体は `PASS` です。
- 同じ日に、registry の経路を直したので([maintenance.md](../maintenance.md))、5 件の `sources` を、いまの registry で求め直すと `INCOMPLETE`(`registry_changed`: 取得したときの registry と違う)になります。採点したときは `CONSISTENT` でした。記録の中身(取得した本文、読取り)は変わっていません。

## 資料が 0 件の入力を通した結果(2026-10-15)

### 何を確かめたか

`target_surface` と `model_id` が不明なとき、13 組(利用先 × `purpose`)で、取得される資料が 0 件になります(v0.4.1)。SKILL.md にその説明がなく、別のエージェントが手順どおりに通せるかを、確かめていませんでした。先に SKILL.md と証拠契約(`evidence-contract.md` の「資料が 0 件の入力」)に説明を足し、別の文脈のエージェント(Claude Code のサブエージェント)に通させました。方法は上と同じです(依頼文と run-id だけを渡し、最終回答のあとに「手順への所見」を書かせ、記録を `sources`・`verify --host-log`・`audit` で検査)。

### 結果

9 件を実行しました。資料が 0 件の入力になったのは 7 件です。

| run | 利用先 / `purpose` | エージェント | SKILL.md | 選ばれた資料 | `verify`(ログあり) | `audit` |
|---|---|---|---|---|---|---|
| zero-1 | `chatgpt` / `agent-instruction` | haiku | 初版 | 0 | `INCOMPLETE` | `PASS` |
| zero-2 | `gemini-cli` / `one-shot` | sonnet | 初版 | 0 | `INCOMPLETE` | `PASS` |
| zero-3 | `gemini` / `persistent-instructions` | opus | 初版 | 0 | `INCOMPLETE` | `PASS` |
| zero-6 | `antigravity` / `agent-instruction` | haiku | 更新後 | 0 | `INCOMPLETE` | `PASS` |
| zero-7 | `codex` / `agent-instruction` | sonnet | 更新後 | 0 | `INCOMPLETE` | `PASS` |
| zero-8 | `chatgpt-work` / `agent-instruction` | opus | 更新後 | 0 | `INCOMPLETE` | `PASS` |
| zero-9 | `gemini-cli` / `one-shot` | haiku | 更新後 + 書式 3 | 0 | `INCOMPLETE` | `PASS`(警告なし) |

- 7 件とも、`fetch` は終了コード 0、`selected_sources` は `[]`、`sources` は `CONSISTENT` でした。`verify` の `errors` は、`no_applicable_official_source` と `provisional_generation` の 2 つだけでした(ログを渡したので `no_host_log` は出ません)。`audit` の `selected_bodies_were_read` は `NA` です。
- 7 件とも、最終回答に 3 行(参照検証 `INCOMPLETE`・意味審査 `NOT_REVIEWED`・効果 `NOT_EVALUATED`)があり、改稿を「候補」と書き、`official` の変更を書かず、`generation_status` を `provisional` にしました。変更は `editorial` だけが 4 件、`editorial` と `inference` が 3 件です。
- 「初版」の 3 件(zero-1〜3)は、`verify` が `INCOMPLETE` になる理由を、SKILL.md の説明どおりに読みました。「更新後」は、下の問題を直したあとの SKILL.md です。`purpose` の説明を足したあと、3 件とも `agent-instruction` を選び、資料が付く別の `purpose` を最終回答に書くだけにしました(寄せませんでした)。
- `audit` は、2026-10-15 に、`sources --markdown` の書式 2 の貼り付けで、7 件のうち 6 件を採点しました。そのあと書式を 3 に上げたので、いまの `audit` は、この 6 件で `sources_report_pasted_as_printed` が `WARN`(書式 2 の報告は、書式 3 と比べられない)になり、全体は `PASS` のままです。

資料が 0 件ではなかった 2 件があります。

| run | 利用先 / `purpose` | エージェント | 結果 |
|---|---|---|---|
| zero-4 | `codex` / `one-shot` | haiku | 資料 2 件が `blocked`(`network_error`)。`audit` は `FAIL`(`selected_bodies_were_read`) |
| zero-5 | `antigravity` / `one-shot` | sonnet | 資料 1 件が `blocked`。`audit` は `FAIL`(`selected_bodies_were_read`) |

どちらも、依頼の言葉から `one-shot` を選んだ結果、資料が選ばれましたが、この実行環境が `learn.chatgpt.com` と `antigravity.google` への接続を許可していなかったので、取得できませんでした。エージェントは、取得できなかったことを最終回答に書き、読んでいない資料を根拠にしませんでした。`audit` の `FAIL` は、資料を読めなかったという事実を、そのまま指しています。したがって、資料があって取得できる入力は、今回は通していません(v0.4.0 の 5 件が最後です)。

### 見つかった問題と、直したこと

| 問題 | 出どころ | 直したこと |
|---|---|---|
| `purpose` の選び方で、資料の有無が変わる。`agent-instruction` の説明が、Google ADK の例だけ | zero-1, 4, 5, 7, 8 | SKILL.md に、`agent-instruction`(エージェントの役割を定める指示)と `skill-instructions` の説明、「決めた値と理由を最終回答に書く」「資料が付く値に寄せない」を足した |
| 「`target_surface`(例: `api`)を示すと資料が加わる」が、Gemini CLI の一回の依頼のように、`api` が当てはまらない利用先に合わない | zero-2 | 依頼が示す値が利用先に当てはまるときだけ、と書き直した。確かめ方(`sources --target … --surface …`)を足した |
| run ディレクトリを先に作ると `fetch` が止まる。`blocked` のあとの再実行も同じ | zero-1, 4, 5 | `--out` は `fetch` が作る(先に作らない)、`blocked` からのやり直しは新しい run-id、と書いた |
| 手順 1 の `original-<run-id>.md` と、手順 3 の `<run>/original.md` の対応が分からない | zero-2, 3, 5 | `fetch` が複製する、と書いた |
| `after_excerpt` を短い抜粋にすると `after_excerpt_mismatch` で止まる | zero-6, 8 | SKILL.md と `recommendations-example.md` に、区間と完全に一致させる、と書いた |
| 差分の全区間を覆う決まりが、手順の本文にない | zero-2 | 完成例を参照する文に一言足した |
| `provisional_generation` が、0 件の節に出てこない。`scope` の `UNAVAILABLE` の意味が分からない | zero-3, 2 | 証拠契約の表に足した |
| `rules --run` が、取得できなかった資料の規則も出すのに、「取得した資料のもの」と書いてある | zero-4 | 「選ばれた資料のもの(取得できなかった資料の規則も出るが、使わない)」に直した |
| 「選んだ資料(0 件)」の空の表が、故障に見える | zero-7(と、検査した側) | `sources --markdown` が、空の表の代わりに理由の 1 行を出す(書式 3)。zero-9 で、貼り付けと `audit` を確かめた |
| `audit`: 最終報告を `SubagentHandback` ツールで返すエージェントの最終回答を、読めなかった | 検査中に発見 | その `message` を最終回答として読む |
| `audit`: `S=…/evidence.py; python3 $S drift` のようにシェル変数で書いたコマンドや、`for c in drift rules; do … $c …; done` のループを、数えなかった | 同上(zero-1, 2, 5) | 同じコマンドの中で代入した変数を展開し、ループは語ごとに書き出して探す |
| `audit`: 「検証済みではなく候補」を、「検証済み」の主張と読んだ | 同上(zero-5) | 否定の語に「なく」を足した |
| `audit`: 資料が 0 件の入力で、`selected_bodies_were_read` が必ず `FAIL` になる | 実装中に発見 | 読むものがないので `NA` にした。`drift`・`rules` の実行は、これまでどおり求める |

### 直さなかったこと

- `inference` と `editorial` の境界(箇条書きに分けるのは、どちらか)。3 件が迷いました。手順は決めていません。`verify` は件数を数えるだけで、分け方の妥当性を見ません。意味審査の仕事です。
- 通常の Gemini アプリで、毎回の会話に効かせる指示に当たる `target_surface` の値がないこと(zero-3)。`gems` は Gems 用で、通常のアプリの常設指示は、資料がありません。registry の作りの問題で、このスキルの手順では解けません。
- 「出力は日本語」が、最終報告の言語も指しうること(zero-3, 9)。生成するプロンプトの言語として扱う、と手順にあり、2 件とも、その読みに沿いました。
- `execution_host` に `unknown` を許すか(zero-7)。分からないまま推定で書かない、とあるだけです。
- 原文が決めていない点(「リンク切れ」の範囲、どの RFC か、など)。足さず、最終回答に書くようにしました。決めるのは利用者です。
- この実行環境のサンドボックスの分類器が、プロキシの状態を調べるコマンドを、認証情報の探索として拒否したこと(zero-4, 5)。スキルの手順の外の事情です。

### 限界

- 9 件で、Anthropic のモデル(haiku・sonnet・opus)だけです。すべて同じ製品(Claude Code)のサブエージェントです。Codex・Antigravity・Claude Cowork・ChatGPT Work で、別のエージェントが動かした結果ではありません。
- 資料が 0 件の入力は、7 件を通しました。13 組のうち、通した組は 7 つです(`chatgpt` の `agent-instruction`、`gemini-cli` の `one-shot`、`gemini` の `persistent-instructions`、`antigravity`・`codex`・`chatgpt-work` の `agent-instruction`)。残りの組は、通していません。
- 「手順への所見」は、エージェントの自己申告です。`audit` が確かめたものではありません。
- `audit` は、手順を守ったか、最終回答が記録と合っているかを見ます。改稿の良し悪しは見ません。意味審査と効果測定は、していません。
- `audit` と、この節の直しは、同じ作者(Claude Code のエージェント)が書きました。同じ思い込みを共有している可能性があります。`audit` の読み違い(上の表の `audit` の 4 行)は、通らなかった記録を見て、または実装中に気づいて直しました。直したあとの `audit` が、そのほかの読み違いを持たないとは言えません。


## Codex rollout adapter の独立実行 (2026-10-17 JST)

Issue #9・#12。実行ホスト Codex CLI **0.155.1**、モデル `gpt-6-sol`、利用先 `claude`、purpose `one-shot`。実際の別プロセスで `fetch` → `drift` / `rules` → 全本文の読取り → 改稿 → `verify --host-log` → `sources --markdown --host-log` → 最終回答を実行した。最初の二回はモデル選択で実行できず、成功した回の記録を評価した。

- run: `outputs/official-prompt-tailor/indep-codex-20261017-005512050166` (未追跡)。純正 rollout ID: `01a1456d-e434-71f0-a94f-d1d4d13624aa`。ログや個人情報を含む全文はコミットしない。
- 5 資料の本文を単一の `rtk proxy cat <絶対パス>` で読み、生成前に **228/228 行**を観測した。内訳: `claude-cowork-and-chat` 135、`claude-custom-visuals` 71、`claude-chat-prompt-design` 5、`claude-chat-response-too-brief` 3、`claude-chat-help` 14 行。
- 参照検証 **PASS_LIVE**、資料報告 **CONSISTENT**。手順監査 `audit` は **PASS (15/15 項目)**。最終回答に候補プロンプトと実際の資料報告を含む。
- 実行で、RTK proxy 読取りの未対応、audit の Claude Code 専用解析、ログ付き資料報告をログ無し報告と比較する不具合を発見し、修正後に同じ真正なログ・run で検証をやり直した。成功のためにログを合成・編集していない。
- 意味審査 **NOT_REVIEWED**、効果 **NOT_EVALUATED**。これは手順と参照の検証であり、改稿の意味の正しさや効果を証明しない。1 回・macOS の結果であり、Windows 実機の Codex 起動は未確認。

## Antigravity CLI 1.3.5 の限定検証 (2026-10-17 JST)

独立した `agy --print` を45秒制限で実行しました。合成本文2行の `view_file` は成功し、実ログを新 adapter で解析して行 `[1, 2]` を観測しました。`write_to_file` はヘッドレスの `write_file` 権限拒否で失敗し、生成物はありません。CLI の終了コード0・`SUCCESS` はツール成功の証拠にしていません。

これはスキルの通し実行ではなく、`audit` と実 run の `sources --host-log` の合格は未確認です。権限拒否は回避していません。[実ランタイム検証の詳細](#adapter-runtime)に記録しました。#8 の独立実行の完了条件は未達のままです。



## Antigravity CLI 1.4.0 の独立した通し実行 (2026-10-17 JST)

Issue #8。通常の `--mode accept-edits`、専用の作業用プロジェクトで、取得→drift/rules→view_file→改稿→verify→sources→最終回答を実行した。グローバル権限設定と許可スキップは使わない。会話IDは `e31110ee-aa49-41b7-b9dd-4d49851561c5`。生ログは未追跡のワークスペース外に複製し、観測した1.4.0とSHA-256をsidecarへ固定した。ログを編集・合成していない。

参照検証 **PASS_LIVE**、資料報告 **CONSISTENT**、手順監査 **PASS (15/15項目)**。3資料の観測行数は、claude-code-best-practices 334、claude-code-prompt-library 800、claude-context-claude-md 131、計1,265行。prompt-libraryは全1,613行のうち表示された800行だけを観測し、残りの行や全文の読了を確認したとは扱わない。観測は生成前に表示された行だけで、認知的な読了を証明しない。実ログで現れた純粋な複数読取りと再開時のSYSTEM_MESSAGEに対応し、同じ真正なログで再検証した。記録したハッシュと結果は `tests/fixtures/antigravity-140-e2e-evidence.json`。

最終回答は候補と明記し、資料報告と3つの状態を提示した。意味審査 **NOT_REVIEWED**、効果 **NOT_EVALUATED**。1.3.5での権限拒否の記録は当時の結果として保持する。1回・macOS・1.4.0の結果であり、別の版やWindowsへの一般化はしない。

<a id="adapter-runtime"></a>

## Antigravity adapter の実ランタイム検証 (2026-10-17 JST)


On 2026-10-17 JST a separate bounded `agy --print` invocation was allowed 45 seconds and asked only to read two synthetic lines using `view_file`, then create a synthetic `optimized.md` using `write_to_file`. No skill instructions or private source content were supplied. Read step 2 succeeded. Write step 4 failed because headless `write_file` permission was denied; the output explicitly forbade bypassing that denial. No bypass or permission change was attempted.

The adapter parsed that **actual native transcript** and observed lines `[1, 2]` before generation attempt step `3`. The requested generated file does not exist. agy exited `0` and reported `SUCCESS` with `denied_actions`; those outer statuses do not prove tool success. The exact log hash, executable hash, version, conversation ID and measured outcomes are in [the runtime evidence record](../../tests/fixtures/antigravity-runtime-probe.json). The raw private transcript is not committed. [The fixture](../../tests/fixtures/antigravity-1.3.5.jsonl) is synthetic and follows the observed envelope; it is not represented as the actual transcript.

**At the initial 1.3.5 verification point, full independent skill execution, `audit` PASS and real `sources --host-log` CONSISTENT were unverified.** The permission block prevents the required generated run artifacts, although audit dispatch is implemented and tested with synthetic records. The independent 1.4.0 run above records completion of that criterion in a permitted workspace. Unit tests and CI cannot substitute for that result.


### agy 1.4.0 の追加確認 (2026-10-17 JST)

インストール済みCLIが1.4.0へ更新されたため、別の専用ワークスペースで通常の `--mode accept-edits` による実ログを確認した。2行の読取りと `optimized.md` の作成が成功し、adapterでも生成前の2行を観測した。グローバル権限設定や許可スキップのオプションは変更していない。実ログの形式・版・ハッシュ・観測結果は `tests/fixtures/antigravity-140-runtime-probe.json`、対応する合成fixtureは `tests/fixtures/antigravity-1.4.0.jsonl` に記録した。1.3.5での拒否の記録は当時の結果として保持する。これは限定的なプローブであり、完全なスキル実行の成功を意味しない。

<a id="registry-history"></a>

## registry の保守履歴 (2026-10-14〜16)

規則の審査は、これまですべて実行主体(Claude Code, claude-sonnet-5-5)が、取得本文と照合して行いました。初期の 34 件は実装時、追加の 70 件(2026-10-15 に、候補 59 件のうち 31 資料から採用した分)は採用時です。このうち 11 件は、資料の本文が変わったため、2026-10-16 に再審査しました(`reviewer_id` は `agent re-review`。旧本文との差分で、引用とその前後が変わっていないことを確かめました)。人による審査ではありません。

### 移転の確認(2026-10-14)

- ADK の旧 URL `google.github.io/adk-docs/…` は、`adk.dev/agents/llm-agents/` へ 301 で移転しました。
- Gemini の旧 `answer/16596215` は、`answer/17094507` へ 301 で移転しました(クエリに、変動する `visit_id` が付きます)。

いずれの旧 URL も登録せず、`fetch` は新 URL から始めます。登録外への移転は、取得前に停止し、`blocked`(`redirect_not_registered:…`)として報告します。

## Codex の形式調査と限定プローブ (2026-10-16)

公式の文書の確認(2026-10-16): openai/codex リポジトリの `docs/` の一覧と、developers.openai.com の検索結果を確認した範囲では、rollout の形式を説明した文書は見つかりませんでした。全ファイルを調べた結果ではありません。確認できた公式の説明は `codex exec --json` の標準出力のイベント([Non-interactive mode](https://developers.openai.com/codex/noninteractive))だけで、これは `~/.codex/sessions` のファイルとは別の形式なので、読みません。

実際の `codex exec`0.155.1 で本文を `cat` させた記録を読んで、見えた行(出力が途中で切られ、334 行のうち 51 行)だけが数えられることを確かめました。


<a id="product-reexecution"></a>

## 評価票と通常フローの再実行（2026-10-20〜21 JST）

公開版v0.6.1を一時プロジェクトへ配置し、macOSで架空データだけを使って確認しました。各製品の実行結果と、インストーラーの成功を区別しています。詳細と未実施対象は [Issue #29](https://github.com/Mr-Kondo/official-prompt-tailor/issues/29#issuecomment-6293629906) で追跡しています。

| 実行環境 | 確認できたこと | 制約・未確認 |
|---|---|---|
| Codex CLI 0.163.0 | 評価票の作成・共有用JSON出力・上書き拒否 | 通常フローはDNSエラーとログ未指定で `INCOMPLETE`。Desktopは別途未確認 |
| agy CLI 1.4.3 | 通常フローで3資料をlive取得し、最終回答まで進行 | `--host-log` 未指定で `INCOMPLETE`。評価票のコマンドはヘッドレス権限拒否で未生成 |
| Antigravityアプリ 2.24.0 | 評価票の作成・共有用JSON出力・上書き拒否 | 通常フローはHTTP 403とログ未指定で `INCOMPLETE`。評価票と同じ会話での確認 |
| Claude Code 2.1.304 | 導入・更新・削除の機械的確認 | スキルの再実行は利用上限で開始できず。過去の実行結果とは区別 |
| Claude Cowork・ChatGPT Work | 今回の実行確認なし | 専用の取り込み・実行環境を確保できず |

4種類の配置先でinstall/update/uninstallを確認しました。実ユーザーの導入環境は変更していません。意味審査と効果測定は未実施です。この結果をWindows・Linuxや別バージョンへ一般化しません。

同じ調査で見つかった案内の不一致と対応版の不足は、v0.6.2・v0.6.3で修正しました。2026-10-21にCodex CLI 0.163.0とagy CLI 1.4.3で行った2行の読取り・別呼出しでの書込みの限定プローブは、[証拠の要約](../../tests/fixtures/current-cli-runtime-probes.json)に残しています。v0.6.4ではagy 1.4.3の欠番ログを拒否する条件を加え、保存済みの真正プローブを再解析しました。これらのadapter検証は、新たなスキル通し実行の成功とは別です。
