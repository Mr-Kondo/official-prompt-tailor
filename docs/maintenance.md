# 規則・資料のメンテナンスと、審査・効果測定

## 規則・資料の再確認方法

公式資料は変わります。`registry.json` の URL・locator・規則を、恒久仕様と扱わないでください。

1. `fetch --mode live` で取得し、`snapshots/<id>/body.txt` を読む。`blocked` なら、エラー(`locators_missing:…` など)を見て、ページ構造が変わったか確認する。
2. 規則を再審査する。
   - `source_excerpt` が本文に残り、意味・適用範囲(`surfaces` / `models` / `purposes`)が変わっていないかを確認する。
   - `source_body_sha256`(= `acquisition.json` の `body_sha256`)、`reviewed_at`、`reviewer_id` を更新する。
   - 本文が変わると、旧審査は引き継がれず `rereview_pending` になります。
3. 実行中に、allowlist・scope・規則を緩めて合格させない。registry の SHA-256 は acquisition と verification に記録され、取得後に registry が変わると `FAIL` です。
4. 規則の審査は、これまですべて実行主体(Claude Code, claude-sonnet-5-5)が、取得本文と照合して行いました。初期の 34 件は実装時、追加の 70 件(2026-10-15 に、候補 59 件のうち 31 資料から採用した分)は採用時です。人による審査ではありません(`reviewer_id` に明記)。人が確認した場合だけ、`reviewer_id` を人の識別子にしてください。

### 移転の確認(2026-10-14)

- ADK の旧 URL `google.github.io/adk-docs/…` は、`adk.dev/agents/llm-agents/` へ 301 で移転しました。
- Gemini の旧 `answer/16596215` は、`answer/17094507` へ 301 で移転しました(クエリに、変動する `visit_id` が付きます)。

いずれの旧 URL も登録せず、`fetch` は新 URL から始めます。登録外への移転は、取得前に停止し、`blocked`(`redirect_not_registered:…`)として報告します。

## 資料の更新と追加を取り込む

更新の確認と、新しいページの発見は、自動化されています(毎週の GitHub Actions でも動きます)。資料と規則の追加、規則の審査は手作業です。自動化は、人が見るべきものを挙げるだけで、registry を書き換えません。

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

### 新しい資料を足す

新しい公式ページ(新しい製品やモデルのガイドなど)を使えるようにする手順です。`references/registry.json` を編集します。

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
3. `targets` の `required_sources` か `conditional_sources` に、`source_id` を加えます。`required_sources` は常に取得され、`conditional_sources` は `purposes` と `surfaces` が合うときだけ取得されます。
4. `fetch` で取得します。`blocked` にならないこと、`snapshots/<id>/body.txt` が本文だけになっていることを確かめます。
5. `rules` に規則を足します。既存の規則をひな形にし、全キーを埋めます。
   - 最初は `review_status` を `unreviewed` にします。`source_body_sha256`・`reviewed_at`・`reviewer_id` は `null` です。
   - `source_excerpt` は、本文にそのまま現れる短い引用にします。
   - 本文と照合して審査したら、`"reviewed"` に変えて、3 項目を埋めます。`source_body_sha256` は、手順 4 の `acquisition.json` の `body_sha256` です。
   - `unreviewed` の規則を根拠に引用すると、`verify` は `FAIL` です。テストは、registry の規則がすべて `reviewed` であることを求めるので、コミットの前に審査します。
6. 新しいドメインがあれば、README の「必要な接続先」に足します。
7. テストを実行します。

### 新しい利用先を足す

利用先(`target_id`)の追加は、registry だけでは済みません。`evidence.py` の `TARGET_IDS` にも足します。registry は、`TARGET_IDS` のすべてを `targets` に定義する決まりだからです。SKILL.md の対象の表、README、[targets.md](targets.md) も更新します。

### 自動実行(GitHub Actions)

`.github/workflows/watch.yml` が、毎週月曜の 03:17(UTC)に `drift` と `discover` を実行します。Actions の画面から、手動でも実行できます。

- 人の判断が要る結果(`drift` の `DRIFT`、`discover` の `CANDIDATES`)だけが、`drift` または `discover` のラベルを付けた Issue になります。開いている Issue があれば、本文を最新の報告に置き換えます。
- `INCOMPLETE`(取得できない資料や一覧がある)は、実行の Summary に出るだけで、Issue にはなりません。通信エラーは、再実行で解消することがあります。
- 再審査(または既読化)が済んだら、Issue を閉じます。閉じた Issue は開き直しません。次の週にまだ残っていれば、新しい Issue を立てます。

`.github/workflows/test.yml` は、push と pull request で、Ubuntu は Python 3.9〜3.14、macOS と Windows は 3.9 と 3.14 のテストを実行します。どの組み合わせが失敗しても、workflow は失敗になります。

3 つの workflow の Ubuntu は、`ubuntu-24.04` に固定しています。`ubuntu-latest` が Ubuntu 26 へ移り始めるのが 2026-10-19 で、`watch.yml` の定期実行と同じ日だからです(`test.yml` には、Python 3.9 のビルドが Ubuntu 26.04 にない、という理由もあります)。外すのは、`ubuntu-latest` で手動実行(`watch.yml`)が通ったあとです。

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
