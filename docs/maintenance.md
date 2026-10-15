# 規則・資料のメンテナンスと、審査・効果測定

## 規則・資料の再確認方法

公式資料は変わります。`registry.json` の URL・locator・規則を、恒久仕様と扱わないでください。

1. `fetch --mode live` で取得し、`snapshots/<id>/body.txt` を読む。`blocked` なら、エラー(`locators_missing:…` など)を見て、ページ構造が変わったか確認する。
2. 規則を再審査する。
   - `source_excerpt` が本文に残り、意味・適用範囲(`surfaces` / `models` / `purposes`)が変わっていないかを確認する。
   - `source_body_sha256`(= `acquisition.json` の `body_sha256`)、`reviewed_at`、`reviewer_id` を更新する。
   - 本文が変わると、旧審査は引き継がれず `rereview_pending` になります。
3. 実行中に、allowlist・scope・規則を緩めて合格させない。registry の SHA-256 は acquisition と verification に記録され、取得後に registry が変わると `FAIL` です。
4. 規則の審査は、これまですべて実行主体(Claude Code, claude-sonnet-5-5)が、取得本文と照合して行いました。初期の 34 件は実装時、追加の 70 件(2026-10-15 に、候補 59 件のうち 31 資料から採用した分)は採用時です。人による審査ではありません(`reviewer_id` に明記)。人が確認した場合だけ、`reviewer_id` を `human:<名前か役割>` にしてください。`rules` と `sources` は、`human:` で始まる `reviewer_id` だけを人の審査として数えます(`human:` のあとが空だと、registry の検査が拒否します)。`tests/test_official_prompt_tailor.py` の `test_no_rule_claims_a_human_review_that_nobody_has_made` は、エージェントが自分で人の審査を書くのを防ぐ番人です。人が実際に審査したときは、その規則に合わせて、このテストを直します。

### 移転の確認(2026-10-14)

- ADK の旧 URL `google.github.io/adk-docs/…` は、`adk.dev/agents/llm-agents/` へ 301 で移転しました。
- Gemini の旧 `answer/16596215` は、`answer/17094507` へ 301 で移転しました(クエリに、変動する `visit_id` が付きます)。

いずれの旧 URL も登録せず、`fetch` は新 URL から始めます。登録外への移転は、取得前に停止し、`blocked`(`redirect_not_registered:…`)として報告します。

## 資料の更新と追加を取り込む

更新の確認と、新しいページの発見は、自動化されています(毎週の GitHub Actions でも動きます)。`drift` と `discover` は、人が見るべきものを挙げるだけで、registry を書き換えません。資料と規則の追加は、審査者が spec(どのページの、どの引用を、どの規則にするか)を書き、`adopt` が registry に書き込みます。どの記述が規則になり、何を意味するかを決める審査は、審査者の仕事です。

### 更新を確かめる

`drift` は、資料の本文が、規則の審査後に変わったかを調べます。比べるのはハッシュと引用の有無だけで、変更が意味を変えたかは判定しません。

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py drift
```

- 引数なしだと、登録されたすべての資料を取得して、規則に記録したハッシュと比べます(通信します)。`--source <source_id>` で、資料を絞れます。
- `--run <run>` は、その run の `acquisition.json` と `snapshots/` を使います(通信しません)。`fetch` のあと、規則を選ぶ前に実行します。
- 引数なしでは、`changed`・`excerpt_missing` になった資料と、取得できなかった資料を、2 秒待って 1 回だけ取得し直し、2 回目の結果を採ります。配信の切り替え中の一時的な差異や、通信エラーを、報告しないためです。

規則ごとの `state` は、次のとおりです。

| state | 意味 | 対応 |
|---|---|---|
| `current` | 本文のハッシュが、審査時と同じ | なし |
| `changed` | ハッシュが違い、引用は本文に残っている | 変更箇所を読み、再審査する |
| `excerpt_missing` | 引用が本文から消えた | 先に再審査する |
| `unreviewed` | 未審査の規則 | 審査する |
| `unverifiable` | 資料を取得できなかった | `error` を見て、再実行するか、人がブラウザで確かめる |

`verdict` は、`changed` か `excerpt_missing` があれば `DRIFT` です。なければ、取得できない資料があるとき `INCOMPLETE`、それ以外は `CURRENT` です。終了コードは、`CURRENT` が 0、`DRIFT` と `INCOMPLETE` が 2、契約違反(`--run` の本文がハッシュと合わないなど)が 1 です。

`changed` と `excerpt_missing` の規則は、「規則・資料の再確認方法」の手順 2 以降で再審査します。

「Updated 3 days ago」のような相対時刻の行は、本文から除かれます。記事が更新されなくても、毎日変わるためです。「Last updated: …」のような絶対日付は、ページが更新されたときだけ変わるので、本文に残ります。

### 新しい資料を見つける

`tools/maintain.py discover` は、各公式サイトの `llms.txt`(ページの一覧)を読みます。キーワードに合い、まだ登録も既読にもしていないページを挙げます。スキルには同梱しない、保守用のツールです。

```bash
python3 tools/maintain.py discover
```

- 一覧とキーワードは `tools/discovery.json` にあります。一覧ごとのキーは、`index_id`、`url`(`llms.txt` の URL)、`scope`(見るパスの先頭)です。`keywords` と `exclude`(正規表現)は任意で、絞り込みに使います。
- `www.`、`.md`、`/index`、末尾の `/`、クエリ、http と https の違いは、同じページとして扱います。他のホストへのリンクは数えません。
- 取得できなかった一覧は、2 秒待って 1 回だけ取得し直し、2 回目の結果を採ります(`drift` と同じです)。通信エラーや、配信の切り替え中の応答で、その一覧の候補が報告から消えないようにするためです。
- 見送るページは、`--mark-seen` で既読にして、`tools/discovery-seen.json` をコミットします。採用するページは、先に登録するか、次の「登録案を作る」へ進みます。
- 採用するのは、本文に、規則にできる記述(引用できる、指示の書き方についての助言)があるページだけです。テストは、すべての資料が配線され、`reviewed` の規則を 1 件以上持つことを求めます。規則のない資料は、`drift` でも変更を検知できません(`drift` が比べるのは、規則に記録したハッシュです)。管理・安全・構成・操作手順の説明だけのページは、見送ります。2026-10-15 の候補 59 件は、`llms.txt` のキーワードに合っただけのページを多く含んでいました。
- `verdict` は、`CANDIDATES`、`INCOMPLETE`、`NONE` のいずれかです。候補が 1 件でもあれば、取得できない一覧があっても `CANDIDATES` です(取得できない一覧は、`indexes` の `status` と `error` に出ます)。候補がなく、取得できない一覧があるときが `INCOMPLETE` で、どちらもなければ `NONE` です。終了コードは、`NONE` が 0、それ以外が 2 です。

`llms.txt` がないサイトは、対象外です。手で確かめてください。

### 登録案を作る

`propose` は、候補の URL を取得して、`sources` に足せる形に変えます。

```bash
python3 tools/maintain.py propose --url https://code.claude.com/docs/en/memory.md --target claude-code
```

- `extraction` は、HTML 系の 3 種と `markdown-v1` を順に試して、最初に通るものを選びます。
- `body_locators` は、本文の見出しから 2 件を選びます。選んだ組で、`fetch` や `verify` と同じ検査を通します。
- 2 回取得して、本文のハッシュが同じかも確かめます(`stable`)。違うと終了コード 2 です。時刻や乱数のような、毎回変わる語が本文に入っています。
- `--source-id`(省略すると URL から作ります)、`--surface`(繰り返せます)、`--category` を指定できます。
- 取得できるのは、registry か `tools/discovery.json` に既にあるホストの、https のページだけです。登録済みのページや、未知の `--target` は、契約違反(終了コード 1)です。

出力の `source` を、`sources` に貼ります。`targets` への配線、`rules` の追加、審査は、次の「新しい資料を足す」の手順 3 以降です。

### 新しい資料を足す(`adopt`)

新しい公式ページ(新しい製品やモデルのガイドなど)を使えるようにする手順です。審査者が spec を書き、`adopt` が registry に書き込みます。

1. 「登録案を作る」の `propose` で、ページの `source`(`extraction` と `body_locators` を含みます)を作ります。
2. ページを読み、規則にできる記述(指示の書き方についての助言)を選んで、spec を書きます。`sources` の各要素は、`propose` の `source` のキーに、`rules` を加えたものです。
3. `adopt` を、`--write` なしで実行します(登録案の確認だけで、何も書きません)。問題がなければ、`--write` を付けて書き込みます。
4. `docs --write` で、README と `docs/targets.md` の件数と表を直します。テストを実行します。

```bash
python3 tools/maintain.py adopt --spec spec.json            # 確認だけ
python3 tools/maintain.py adopt --spec spec.json --write    # registry.json に書く
python3 tools/maintain.py docs --write
```

```json
{
  "schema_version": 1,
  "reviewer_id": "claude-code/claude-sonnet-5-5 (agent review at adoption; not human)",
  "sources": [{
    "source_id": "claude-prompting-example", "allowed_urls": ["https://platform.claude.com/docs/en/<page>.md"],
    "target_ids": ["claude", "claude-cowork", "claude-code"], "surfaces": ["api"], "models": ["claude-example-5"],
    "category": "api-prompting", "extraction": "markdown-v1", "body_locators": ["<見出し 1>", "<見出し 2>"],
    "rules": [{
      "rule_id": "claude-api-example-rule", "target_ids": ["claude", "claude-cowork", "claude-code"], "surfaces": ["api"],
      "models": ["claude-example-5"], "purposes": ["one-shot", "agent-instruction"],
      "preconditions": ["規則を使える条件"], "exclusions": ["使ってはいけない場合"],
      "source_excerpt": "本文にそのまま現れる引用(400 文字以内)", "meaning": "規則の意味", "allowed_change": "許される変更"
    }]
  }]
}
```

- `reviewer_id` は、審査した主体です。エージェントなら `…; not human` と明記し、人なら `human:<名前か役割>` にします。`rules` と `sources` は、`human:` で始まるものだけを人の審査として数えます。
- `adopt` が確かめることは、次のとおりです。どれか 1 つでも違えば、何も書かずに終わります(契約違反は終了コード 1、本文を安定して取得できないときは 2)。
  - `registry.json` が、`dump_registry` の書式(1 行に 1 レコード)であること。
  - 取得先が https で、registry か `tools/discovery.json` が使うホストにあり、登録済みのページでも `source_id` でもないこと。
  - 本文が、2 回の取得で一致すること(最大 6 回まで取り直します。失敗した取得は数えません)。
  - 各規則の `source_excerpt` が、取得した本文にそのまま現れ、400 文字以内であること。規則の `target_ids` が資料の範囲内であること。
  - API 限定の資料の規則は、`surfaces` が `["api"]` であること。モデル限定の資料の規則は、`models` がその資料の `models` に含まれること。ほかの検査(`registry_problems`)も通ること。
  - 経路と規則が合っていること(`registry_problems`)。利用先 × `purpose` × `target_surface` × `model_id` の全入力(約 1,200 通り)について、選ばれた資料に、その入力で使える規則が 1 件以上あること。どの入力でも使えない規則がないこと。`verify` は、選ばれた資料のすべてを読んだことを求めるので、規則のない入力で資料を取得すると、読むだけで終わります。
- `adopt` が書くのは、`source_body_sha256`(取得した本文)、`reviewed_at`、`reviewer_id`、`review_status: reviewed`、`source_locator`(= 引用)と、`targets` への経路です。経路は、規則の `purposes` の和集合に対してだけ作ります。規則のない `purpose` では、その資料は取得されません。規則がすべて特定のモデルに限られる資料は、`models` にもそのモデルを書きます(書かないと、ほかのモデルでも取得され、`registry_problems` が拒否します)。`registry_problems` がエラーを出したら、spec の `purposes`・`surfaces`・`models` を、資料が実際に使われる範囲に直します。
- Claude Code のサンドボックスは、`.claude/skills` の下に書けません。`--write` が `Operation not permitted` で止まるときは、ターミナルで実行します。

#### 手で足す場合

`adopt` が使えないときの手順です。`references/registry.json` を直接編集します。書式を崩すと、`adopt` が使えなくなります(`dump_registry` が、現在のファイルをそのまま再現できる必要があります)。

1. 公式のドメインにあり、https で、ログインなしに読めるページか確かめます。
2. `sources` に追加します。「登録案を作る」の `source` を貼るか、次のキーで書きます。すべて必須で、未知のキーは拒否されます。
   - `source_id`: 英数字・`.`・`_`・`-` で、64 文字以内。
   - `allowed_urls`: https の URL。ホスト・パス・クエリが完全一致で照合され、`#` は使えません。`fetch` は先頭の URL から取得し、リダイレクト先は、いずれかの URL と一致するときだけ許可されます。
   - `target_ids`・`surfaces`・`models`: 資料が当てはまる範囲。限定しないときは `["all"]`。特定のモデルのガイドは、`models` にそのモデルの ID(例: `["claude-opus-5-5"]`)を書きます。この資料は、`model_id` がそのモデルのときだけ取得されます。その資料の規則の `models` も、これに含まれるものだけにします(テストが確かめます)。
   - `category`: 既存に合わせて `product-prompting` / `product-feature` / `api-prompting`。
   - `extraction`: 本文の取り出し方。次のいずれかです。
     - `html-main-v1`: 最初の `<main>`
     - `html-article-v1`: 最初の `<article>`
     - `html-body-v1`: `<body>`
     - `markdown-v1`: Markdown かプレーンテキスト
   - `body_locators`: 本文に必ず含まれる見出しなどの文字列を、2 件以上。見つからないと、`fetch` は `locators_missing` で `blocked` にします。
3. `targets` の `required_sources` か `conditional_sources` に、`source_id` を加えます。`required_sources` は常に取得され、`conditional_sources` は `purposes` と `surfaces` が合うときだけ取得されます。規則のある範囲にだけ経路を作ります。`required_sources` に置けるのは、その利用先のすべての `purpose` に使える規則を持つ資料だけです(`registry_problems` が確かめます)。
4. `fetch` で取得します。`blocked` にならないこと、`snapshots/<id>/body.txt` が本文だけになっていることを確かめます。
5. `rules` に規則を足します。既存の規則をひな形にし、全キーを埋めます。
   - 最初は `review_status` を `unreviewed` にします。`source_body_sha256`・`reviewed_at`・`reviewer_id` は `null` です。
   - `source_excerpt` は、本文にそのまま現れる短い引用にします。
   - 本文と照合して審査したら、`"reviewed"` に変えて、3 項目を埋めます。`source_body_sha256` は、手順 4 の `acquisition.json` の `body_sha256` です。
   - `unreviewed` の規則を根拠に引用すると、`verify` は `FAIL` です。テストは、registry の規則がすべて `reviewed` であることを求めるので、コミットの前に審査します。
6. 新しいドメインがあれば、README の「必要な接続先」に足します。
7. テストを実行します。

### 新しい利用先を足す

利用先(`target_id`)の追加は、registry だけでは済みません。`evidence.py` の `TARGET_IDS` にも足します。registry は、`TARGET_IDS` のすべてを `targets` に定義する決まりだからです。SKILL.md の対象の表、README、[targets.md](targets.md) も更新します。`tools/maintain.py` の `ROUTING_LABELS` と `VENDORS` にも足してください(足りないと、`docs` が契約違反として知らせます)。`docs --write` が、[targets.md](targets.md) の表を直します。

### 自動実行(GitHub Actions)

`.github/workflows/watch.yml` が、毎週月曜の 03:17(UTC)に `drift` と `discover` を実行します。Actions の画面から、手動でも実行できます。

- 人の判断が要る結果(`drift` の `DRIFT`、`discover` の `CANDIDATES`)だけが、`drift` または `discover` のラベルを付けた Issue になります。開いている Issue があれば、本文を最新の報告に置き換えます。
- `INCOMPLETE`(取得できない資料や一覧がある)は、実行の Summary に出るだけで、Issue にはなりません。通信エラーは、再実行で解消することがあります。
- 再審査(または既読化)が済んだら、Issue を閉じます。閉じた Issue は開き直しません。次の週にまだ残っていれば、新しい Issue を立てます。

`.github/workflows/test.yml` は、push と pull request で、Ubuntu は Python 3.9〜3.14、macOS と Windows は 3.9 と 3.14 のテストを実行します。どの組み合わせが失敗しても、workflow は失敗になります。

3 つの workflow の Ubuntu は、`ubuntu-24.04` に固定しています。`ubuntu-latest` が Ubuntu 26 へ移り始めるのが 2026-10-19 で、`watch.yml` の定期実行と同じ日だからです(`test.yml` には、Python 3.9 のビルドが Ubuntu 26.04 にない、という理由もあります)。外すのは、`ubuntu-latest` で手動実行(`watch.yml`)が通ったあとです。

## 別のエージェントで手順を通す

実機の確認を、規則を書いたエージェント自身が行うと、SKILL.md の読み違いを見落とします。別の文脈のエージェント(Claude Code のサブエージェントなど)に、依頼文と run-id だけを渡して通させ、その記録を規則だけで検査します。結果と、見つかった問題は [independent-runs.md](independent-runs.md) にあります。

1. エージェントには、依頼文とスキルの呼び出しだけを渡します。手順のヒントは渡しません。最終回答のあとに、手順で迷った点を書かせます。
2. 終わったら、そのエージェント自身の記録(`~/.claude/projects/<プロジェクト>/<セッション>/subagents/agent-<id>.jsonl`)で検査します。

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py sources --run outputs/official-prompt-tailor/<run-id> --host-log <記録> --markdown
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py verify --run outputs/official-prompt-tailor/<run-id> --mode live --host-log <記録>   # verification.json を上書きする
python3 tools/maintain.py audit --run outputs/official-prompt-tailor/<run-id> --transcript <記録>
```

`audit` が確かめるのは、手順を守ったか、最終回答が記録と合っているかです。各検査は `PASS` / `FAIL` / `WARN` / `NA` で、`FAIL` が 1 つでもあれば全体が `FAIL`(終了コード 1)です。

| 検査 | 確かめること |
|---|---|
| `fetched`・`wrote_the_result` | `evidence.py fetch` を実行し、`optimized.md` か `recommendations.json` を書いた |
| `drift_before_writing`・`rules_before_writing` | `drift --run` と `rules --run` を、最初の書込みより前に実行した |
| `verified_after_writing` | 最初の書込みのあとに `verify` を実行し、`verification.json` が残っている |
| `sources_after_verify` | 書込みのあとの最初の `verify` より後に、`sources --run` を実行した |
| `selected_bodies_were_read` | 選ばれた資料のすべてが、最初の書込みの前に、成功した `Read` で示された(`verify` と同じ規則) |
| `no_log_the_user_did_not_give` | 依頼文にないログのパスを、`--host-log` に使っていない |
| `registry_not_read_whole` | `registry.json` を全体では読んでいない(`limit` なしの `Read`、`cat` など) |
| `answer_has_three_lines` | 最終回答に、参照検証・意味審査・効果の 3 行がある |
| `answer_matches_the_records` | 3 行が、`verification.json`・`semantic-review.json`(と `semantic-verification.json`)・`evaluation.json` の記録と同じ。記録がないものは「未実施」と読む(`assess` を実行していなければ `NOT_REVIEWED`、`evaluation.json` がなければ `NOT_EVALUATED`) |
| `no_overclaim` | 採用できない案を「候補」と書き、「検証済み」「改善済み」と言っていない(同じ文の中の否定は除く)。採用できる案でも、実測(`IMPROVED_ON_TEST_SET`)がなければ「改善済み」と言っていない |
| `copy_prompt_in_answer` | `optimized.md` の本文が、最終回答にそのままある |
| `sources_report_pasted_as_printed` | `sources --markdown` の出力が、スクリプトが出すとおりに貼ってある(空行の違いは無視) |
| `original_literals_kept` | 原文のコマンド・数値・固有名詞(英数字)が、`optimized.md` に残っている(`WARN` のみ) |

`audit` は、改稿の良し悪しを見ません。意味審査と効果測定の代わりにはなりません。`sources_report_pasted_as_printed` は、いまの `sources --markdown` の書式と比べるので、書式を変えたあとは、古い記録でこの検査だけが失敗します。

## 意味審査

審査基準は `references/review-rubric.md` です。

- 別文脈(または人)が `semantic-review.json` を書きます。
- そのあと、`assess --run <run> --review <run>/semantic-review.json` を実行します。
- 校正記録(`calibration_ref` / `calibration_sha256`)が必要です。モデル審査では、実行ログも必要です。無ければ `INCOMPLETE` です。
- `mock` は単体試験専用で、本番の PASS になりません。

## 効果測定

対象の事例は、`evals/prompt-tailor-cases.jsonl` です。開発用 4 件と未見用 2 件があり、負例と境界例を含みます。

- 負例: 要件改変、UI への API 機能の誤適用。
- 境界例: 曖昧な依頼、否定の保持。

手順は次のとおりです。

1. 評価計画 `eval-plan.json` を、実行前に固定します。
2. `compare --run <run> --plan eval-plan.json --results eval-results.jsonl` で集計します。

事例の `status` は `pending-review` で配布しています。要件の抽出を、人がまだ確認していないためです。確認して `ready` にするまで、`compare` は `INCOMPLETE` を返します。

実行回数は、事例数 × 2 案 × 反復数です。たとえば、10 事例・各 3 回なら、1 対象で 60 回です。採点の呼出しは別です。

## リリース

1. 変更をコミットします。
2. `install.py` の `PLUGIN_VERSION` を上げ、`release-notes/vX.Y.Z.md` にリリースノートを書いて、コミットします。ノートは「変更点」「確認できていること」「確認できていないこと」の 3 節です。
3. タグ `vX.Y.Z` を付けて、`main` とタグを push します。
4. `.github/workflows/release.yml` が、タグと `PLUGIN_VERSION` の一致を確かめ、テストを通し、`install.py --zip` で ZIP を作って、GitHub Release を公開します。ノートは、タグの `release-notes/vX.Y.Z.md` です。無ければ自動生成になります。

手元から公開するときは、`install.py --zip dist` で ZIP を作り、次を実行します。

```bash
gh release create vX.Y.Z dist/official-prompt-tailor.zip --title vX.Y.Z --notes-file release-notes/vX.Y.Z.md
```

### 公開した ZIP を確かめる

ZIP は、同じファイルから、どの機械でも、いつ作っても同じバイト列になります(ファイルの並びを名前順にし、時刻・権限・作成元を固定して、圧縮しません)。そのため、タグから作り直した ZIP の sha256 を、公開された ZIP の `digest` と比べるだけで、ダウンロードせずに確かめられます。

```bash
git archive vX.Y.Z | tar -x -C <空のディレクトリ>
mkdir <出力先> && python3 <空のディレクトリ>/install.py --zip <出力先>    # 最後の行 "sha256: …" を控える
curl -s https://api.github.com/repos/Mr-Kondo/official-prompt-tailor/releases/tags/vX.Y.Z    # assets の digest が "sha256:…" で一致する
```

- `install.py --zip` は、最後に `sha256:` を出すので、`release.yml` のログにも残ります。`test.yml` も、コミットごとに同じ ZIP を作って `sha256:` を出します。同じコミットなら、10 ジョブのすべてで同じ値になります。
- この方法で確かめられるのは、この変更を入れた後のリリースからです。それ以前の ZIP は、更新日時と圧縮が入っているため、作り直しても一致しません。
- 一致しないときは、ZIP の中身(名前・サイズ・内容)を `unzip -l` と `diff -r` で比べます。ファイルの改行が CRLF になっていると一致しません(`.gitattributes` が LF に固定しているので、通常は起きません)。
