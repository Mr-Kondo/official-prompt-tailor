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
- `audit` の `sources_report_pasted_as_printed` は、貼られた報告を、いまの `sources --markdown` の出力と比べます。上の採点は、文言を整える前の書式(版の行がない、書式 1)で行いました。2026-10-16 に、`sources --markdown` の先頭に書式の版の行を足しました(いまは書式 2)。5 件を採点し直すと、この検査は全件で `WARN`(書式 1 の報告は、書式 2 と比べられない)になり、ほかの検査は全件で合格のままです。`audit` 全体は `PASS` です。
- 同じ日に、registry の経路を直したので([maintenance.md](maintenance.md))、5 件の `sources` を、いまの registry で求め直すと `INCOMPLETE`(`registry_changed`: 取得したときの registry と違う)になります。採点したときは `CONSISTENT` でした。記録の中身(取得した本文、読取り)は変わっていません。

# 資料が 0 件の入力を通した結果(2026-10-15)

## 何を確かめたか

`target_surface` と `model_id` が不明なとき、13 組(利用先 × `purpose`)で、取得される資料が 0 件になります(v0.4.1)。SKILL.md にその説明がなく、別のエージェントが手順どおりに通せるかを、確かめていませんでした。先に SKILL.md と証拠契約(`evidence-contract.md` の「資料が 0 件の入力」)に説明を足し、別の文脈のエージェント(Claude Code のサブエージェント)に通させました。方法は上と同じです(依頼文と run-id だけを渡し、最終回答のあとに「手順への所見」を書かせ、記録を `sources`・`verify --host-log`・`audit` で検査)。

## 結果

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

## 見つかった問題と、直したこと

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

## 直さなかったこと

- `inference` と `editorial` の境界(箇条書きに分けるのは、どちらか)。3 件が迷いました。手順は決めていません。`verify` は件数を数えるだけで、分け方の妥当性を見ません。意味審査の仕事です。
- 通常の Gemini アプリで、毎回の会話に効かせる指示に当たる `target_surface` の値がないこと(zero-3)。`gems` は Gems 用で、通常のアプリの常設指示は、資料がありません。registry の作りの問題で、このスキルの手順では解けません。
- 「出力は日本語」が、最終報告の言語も指しうること(zero-3, 9)。生成するプロンプトの言語として扱う、と手順にあり、2 件とも、その読みに沿いました。
- `execution_host` に `unknown` を許すか(zero-7)。分からないまま推定で書かない、とあるだけです。
- 原文が決めていない点(「リンク切れ」の範囲、どの RFC か、など)。足さず、最終回答に書くようにしました。決めるのは利用者です。
- この実行環境のサンドボックスの分類器が、プロキシの状態を調べるコマンドを、認証情報の探索として拒否したこと(zero-4, 5)。スキルの手順の外の事情です。

## 限界

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
