# 証拠契約

`scripts/evidence.py` が検査する成果物と終了コードの要約。詳細な構造は `scripts/evidence.py` の `*_F` 定義(未知キー・型違い・重複 ID を拒否)が正。

## 3 つの別々の結果

| 結果 | 出す処理 | 値 | 終了コード |
|---|---|---|---|
| 参照検証 | `verify` | `PASS_LIVE` / `PASS_CACHE` / `FAIL` / `INCOMPLETE` | 0 / 0 / 1 / 2 |
| 意味審査 | `assess` | `PASS` / `FAIL` / `INCOMPLETE` | 0 / 1 / 2 |
| 効果測定 | `compare` | `IMPROVED_ON_TEST_SET` / `REGRESSED` / `NO_CLEAR_IMPROVEMENT` / `NOT_EVALUATED` / `INCOMPLETE` | 0 / 1 / 1 / 2 / 2 |

`fetch`: 0=当該 mode の必須資料の取得・本文検査が成功、1=契約違反、2=blocked / missing / stale / target 未解決。`fetch` の 0 は生成前の取得成功だけを示す。

`compare` は契約違反を検出した場合も終了コード 1(`contract_violations` が非空)。引数誤りは argparse の 2。

## run ディレクトリ

```text
input.json  original.md  optimized.md  recommendations.json
acquisition.json  snapshots/<source-id>/{raw.bin,body.txt}
verification.json  semantic-review.json  semantic-verification.json  evaluation.json
```

`fetch` が作る: `input.json` `original.md` `acquisition.json` `snapshots/` と、未実施を明記した `semantic-review.json` (NOT_REVIEWED) と `evaluation.json` (NOT_EVALUATED)。
生成側が作る: `optimized.md` `recommendations.json`。
スクリプトが作る: `verification.json` `semantic-verification.json` `evaluation.json`。

## 取得状態

`live` / `cache` / `blocked` / `missing` / `stale`。

- `live`: その run で `fetch` が許可 URL へ HTTP 取得し、本文検査を完了した。
- `cache`: 既存 run の取得物を再検査した。`fetched_at` は元の取得日時、`rechecked_at` が再検査日時。
- `stale`: 取得から 7 日超(ローカル方針。ベンダー規則ではない)。未来日時は契約違反。
- 外部ツール(`method=external-tool`)で得た本文は `cache`・`origin_status=unobserved` として扱い、`PASS` にしない。

## drift

`drift [--run RUN] [--source ID]...` は、資料の本文が規則の審査後に変わったかを、ハッシュと引用の有無だけで調べる。意味の変化は判定しない。`--run` は、その run の `acquisition.json` と `snapshots/` を使う(通信しない)。指定しなければ、登録された全資料を取得する。

出力 JSON: `mode`(`live` / `run`)、`verdict`、`sources`(`source_id` `status` `error` `body_sha256`)、`rules`(`rule_id` `source_id` `state`)。

`state`: `current`(ハッシュが審査時と同じ)/ `changed`(ハッシュが違い、引用は残る)/ `excerpt_missing`(引用が本文から消えた)/ `unreviewed` / `unverifiable`(資料を取得できなかった)。

`verdict`: `DRIFT`(`changed` か `excerpt_missing` がある)/ `INCOMPLETE`(取得できない資料がある)/ `CURRENT`。終了コードは `CURRENT` が 0、`DRIFT` と `INCOMPLETE` が 2、契約違反(`--run` の本文がハッシュと合わない、未知の `--source`)が 1。

`--run` なしでは、`changed` / `excerpt_missing` になった資料と取得できなかった資料を、2 秒待って 1 回だけ取得し直し、2 回目の観測を採る(一時的な差異や通信エラーを報告しないため)。

HTML の本文から、相対時刻の行(`Updated today`、`Updated 3 days ago` など)を除く。毎日変わってハッシュを動かすため。絶対日付(`Last updated: …`)は残す。

## recommendations.json の change

`kind`: `official`(規則・出典必須)/ `inference`(出典を付ける場合は全項目、付けない場合はすべて null)/ `editorial`(出典なし)。
`before_*` は正規化済み原文、`after_*` は正規化済み改稿の Python 文字 offset(半開区間)。正規化は UTF-8 strict + CRLF/CR→LF。
`original_sha256` / `optimized_sha256` は Python で計算する(`hashlib.sha256` を正規化後の UTF-8 bytes に)。

## 読取り観測

`verify --host-log <path>` で渡す Claude Code のトランスクリプト(JSONL, adapter `claude-code-transcript-jsonl-v1`)から、次をすべて満たす場合だけ `read_status=observed`。

- `Read` が `snapshots/<id>/body.txt` を対象にし、結果が成功(`is_error` でない)。
- 結果の行番号付き本文が `body.txt` の該当行と一致し、採用した根拠の行をすべて含む。
- その結果が `optimized.md` / `recommendations.json` への最初の書込みより前にある。
- ログが run ディレクトリの外にある。

`execution_host` は `claude-code` / `codex` / `chatgpt-work` / `antigravity` / `claude-cowork`。読取りの adapter があるのは `claude-code` だけで、他のホストでは `no_log_adapter_for_host`(総合 `INCOMPLETE`)になる。ログを渡しても解釈しない。

信頼境界: ログのパスは運用者が指定する。署名や独立収集元の認証はしない。運用者が独立した収集元を確認できない場合は `--host-log` を付けず、総合 `INCOMPLETE` を受け入れる。

## 校正記録 (assess)

```json
{"schema_version":1,"reviewer_id":"...","model_id":null,"rubric_version":"1.0",
 "case_set_sha256":"sha256(json.dumps(sorted(case_ids)))",
 "labels":[{"case_id":"c1","human_label":"supported","human_by":"person-or-role","scorer_label":null}],
 "counts":{"agree":0,"disagree":0,"undetermined":1},
 "tolerance":{"min_agreement":null,"max_undetermined":0,"min_cases":3},
 "disagreement_handling":"how severe disagreements are resolved","created_at":"2026-10-14T00:00:00Z"}
```

人のレビュー(`review_method=human`)は `scorer_label=null`、counts は `agree=0, disagree=0, undetermined=ラベル数`、`min_agreement=null`。モデルのレビューは `scorer_label` を持ち、counts はラベルから再計算した値と一致させる。

## 評価計画と結果 (compare)

`eval-plan.json`、`eval-results.jsonl`、`evals/prompt-tailor-cases.jsonl` の構造は `scripts/evidence.py` の `PLAN_F` `ROW_F` `CASE_F` を参照。計画は実行前に固定し、結果の各行に `plan_sha256` を結ぶ。`development` 事例は採用判断に使わず、`held-out` 事例だけで採用基準を判定する。
