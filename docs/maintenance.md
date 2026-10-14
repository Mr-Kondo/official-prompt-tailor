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
