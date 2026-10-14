# 規則・資料のメンテナンスと、審査・効果測定

## 規則・資料の再確認方法

公式資料は変わります。`registry.json` の URL・locator・規則を、恒久仕様と扱わないでください。

1. `fetch --mode live` で取得し、`snapshots/<id>/body.txt` を読む。`blocked` なら、エラー(`locators_missing:…` など)を見て、ページ構造が変わったか確認する。
2. 規則を再審査する。
   - `source_excerpt` が本文に残り、意味・適用範囲(`surfaces` / `models` / `purposes`)が変わっていないかを確認する。
   - `source_body_sha256`(= `acquisition.json` の `body_sha256`)、`reviewed_at`、`reviewer_id` を更新する。
   - 本文が変わると、旧審査は引き継がれず `rereview_pending` になります。
3. 実行中に、allowlist・scope・規則を緩めて合格させない。registry の SHA-256 は acquisition と verification に記録され、取得後に registry が変わると `FAIL` です。
4. 初期規則 34 件は、実装時に実行主体(Claude Code, claude-sonnet-5-5)が、取得本文と照合して審査しました。人による審査ではありません(`reviewer_id` に明記)。人が確認した場合だけ、`reviewer_id` を人の識別子にしてください。

### 移転の確認(2026-10-14)

- ADK の旧 URL `google.github.io/adk-docs/…` は、`adk.dev/agents/llm-agents/` へ 301 で移転しました。
- Gemini の旧 `answer/16596215` は、`answer/17094507` へ 301 で移転しました(クエリに、変動する `visit_id` が付きます)。

いずれの旧 URL も登録せず、`fetch` は新 URL から始めます。登録外への移転は、取得前に停止し、`blocked`(`redirect_not_registered:…`)として報告します。

## 資料の更新と追加を取り込む

現状は、更新の検知も、資料と規則の追加も、手作業です。本文の更新を知らせる仕組みは、`verify` の `rereview_pending` だけです。これは、その run が引用した規則の `source_body_sha256` と、今回取得した本文のハッシュを比べた結果です。引用されなかった規則と、取得しなかった資料の更新は、どこにも出ません。

### 更新を確かめる

1. 確かめたい利用先の `input.json` で、`fetch --mode live` を実行します。取得されるのは、その入力で選ばれる資料だけです([targets.md](targets.md))。
2. 取得した本文のハッシュと、規則に記録したハッシュを比べます。`<run-id>` は、手順 1 の run の ID に置き換えます。

```bash
python3 -I - <<'PY'
import json
run = "outputs/official-prompt-tailor/<run-id>"
reg = json.load(open(".claude/skills/official-prompt-tailor/references/registry.json", encoding="utf-8"))
acq = json.load(open(f"{run}/acquisition.json", encoding="utf-8"))
body = {e["source_id"]: open(f"{run}/{e['body_path']}", encoding="utf-8").read() for e in acq["sources"] if e["body_path"]}
hashes = {e["source_id"]: e["body_sha256"] for e in acq["sources"] if e["body_sha256"]}
for r in reg["rules"]:
    sid = r["source_id"]
    if sid in hashes and r["review_status"] == "reviewed" and r["source_body_sha256"] != hashes[sid]:
        kept = r["source_excerpt"] in body[sid]
        print("要再審査:", r["rule_id"], "引用は本文に残っている" if kept else "引用が本文から消えた")
print("比較した資料:", ", ".join(hashes) or "なし")
PY
```

3. `要再審査` の規則は、「規則・資料の再確認方法」の手順 2 以降で再審査します。`引用が本文から消えた` 規則を、先に見てください。

`blocked` の資料は、比べられません。原因は `acquisition.json` の `error` にあります。更新の有無は、人がブラウザなどで確かめます。

### 新しい資料を足す

新しい公式ページ(新しい製品やモデルのガイドなど)を使えるようにする手順です。`references/registry.json` を編集します。

1. 公式のドメインにあり、https で、ログインなしに読めるページか確かめます。
2. `sources` に、次のキーで追加します。すべて必須で、未知のキーは拒否されます。
   - `source_id`: 英数字・`.`・`_`・`-` で、64 文字以内。
   - `allowed_urls`: https の URL。ホスト・パス・クエリが完全一致で照合され、`#` は使えません。`fetch` は先頭の URL から取得し、リダイレクト先は、いずれかの URL と一致するときだけ許可されます。
   - `target_ids`・`surfaces`・`models`: 資料が当てはまる範囲。限定しないときは `["all"]`。
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
   - 最初は `review_status` を `unreviewed` にし、`source_body_sha256`・`reviewed_at`・`reviewer_id` を `null` にします。
   - `source_excerpt` は、本文にそのまま現れる短い引用にします。
   - 本文と照合して審査したら、`"reviewed"` に変えて、3 項目を埋めます。`source_body_sha256` は、手順 4 の `acquisition.json` の `body_sha256` です。
   - `unreviewed` の規則を根拠に引用すると、`verify` は `FAIL` です。
6. 新しいドメインがあれば、README の「必要な接続先」に足します。
7. テストを実行します。

### 新しい利用先を足す

利用先(`target_id`)の追加は、registry だけでは済みません。`evidence.py` の `TARGET_IDS` にも足します。registry は、`TARGET_IDS` のすべてを `targets` に定義する決まりだからです。SKILL.md の対象の表、README、[targets.md](targets.md) も更新します。

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
