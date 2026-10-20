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

## rules

`rules --run RUN` は、その run で選べる規則だけを出す(通信しない)。`registry.json` は規則が増えて大きいので、全体を読まずに済ませるための一覧であり、規則を選ぶ判断は含まない。

条件(`verify` が適用範囲として確かめるものと同じ): `review_status` が `reviewed`、`input.json` の `target_id` と `purpose` が規則と資料に合う、`target_surface` と `model_id` が規則の `surfaces` / `models`(資料の `surfaces`)に合う(`unknown` は `all` にだけ合う)、規則の資料が `acquisition.json` の `selected_sources` にある。`preconditions` と `exclusions` は判定しない。

出力 JSON: `run_id`、`target_id`、`target_surface`、`model_id`、`purpose`、`selected_sources`、`rules`(`rule_id` `source_id` `kind` `purposes` `surfaces` `models` `source_excerpt` `meaning` `allowed_change` `preconditions` `exclusions` `reviewer_id` `human_reviewed`)、`review`(`rules` の件数、`human_reviewed` の件数、`reviewer_id` ごとの件数 `reviewers`)。終了コードは 0。

審査者: `reviewer_id` が `human:` で始まり、そのあとに名前か役割がある規則だけが、人の審査(`human_reviewed=true`)。agent の審査(`claude-code/… (agent review …; not human)` など)を含め、それ以外は人として数えない。registry は、`human:` のあとが空の `reviewer_id` を契約違反にする。registry が取得のあとで変わっていれば契約違反(1)、run のファイルがなければ実行不能(2)。`drift` の `changed` / `excerpt_missing` は反映しないので、先に `drift --run` を実行する。

HTML の本文から、相対時刻の行(`Updated today`、`Updated 3 days ago` など)を除く。毎日変わってハッシュを動かすため。絶対日付(`Last updated: …`)は残す。

## sources

`sources` は、どの資料を、なぜ見たか(見るか)を、スクリプトだけで出す。モデルが書いたものは信用せず、run のファイルと registry から求め直す(通信しない)。

```text
sources --target T [--surface S] [--model M] [--purpose P] [--all-models]   # 計画: run なし
sources --input input.json [--all-models]                                   # 計画: input.json から
sources --run RUN [--host-log LOG]                                          # run の報告
```

既定は JSON(標準出力)。`--markdown` は、同じ内容を Markdown にする(スクリプトが組み立てる)。Markdown の先頭の行は、書式の版を示す見えない行(`<!-- sources-report format N -->`)で、そのまま貼る。`--run` は、他の指定と一緒には使えない。`--host-log` は `--run` と一緒に使う(契約違反は 1)。

計画(`mode: plan`):
- `input`、`registry_sha256`、`selected`(`source_id` `models` `route` = `required` / `conditional`、条件付きなら `purposes` `surfaces`)、`skipped`(選ばれなかった資料と `reason`)、`model`、`--all-models` のときの `models`。
- `reason`: `route_not_matched`(`purpose` か `target_surface` が合わない。`purpose_ok` `surface_ok` がどちらが合わないかを示す)/ `model_mismatch`(モデル限定の資料で、`model_id` がそのモデルでない)/ `model_unknown`(モデル限定の資料で、`model_id` が `unknown`)。
- `model`: `model_specific_sources`(その利用先にあるモデル限定の資料の数。0 なら `model_id` で選択は変わらない)、`guides`(その資料ごとの `status` = `selected` / `skipped` / `not_routed`)、`spelling_hint`(綴りだけが違う、登録済みの `model_id`。選択には使わない)。
- `models`: `base`(`model_id` が `unknown` のときの資料)と、`added`(`model_id` ごとに加わる資料)。

報告(`mode: run`)は、計画の `model` `skipped` に加えて、次を出す。`input` に `execution_host` が加わる。
- `selected`: `acquisition.json` の `selected_sources` ごとに、`source_id` `models` `route` `url` `status` `fetched_at` `body_sha256`、`integrity`(`ok` / `mismatch` / `missing` / `unavailable`。`body.txt` のハッシュを記録と比べる)、`rules`(`applicable` = その run で選べる規則の数、`cited` = 変更が引用した規則)、`read`。
- `read`: `status`(`observed` / `not_observed` / `self_reported` / `unavailable`)、`reason`、`lines_seen` `lines_total` `ranges`(読んだ行)、`cited_lines_covered`。`--host-log` の対応ホストのログから、`verify` と同じ規則(成功した `Read` が、生成物の最初の書込みより前に、その行を示した)で求める。ログがなければ `no_host_log`、対応 adapter がない `execution_host` なら `no_log_adapter_for_host` で、`unavailable`。
- `citations`: 変更ごとの `rule_id` `source_id` `lines` `reviewer_id` `human_reviewed` `in_scope`(その run で選べる規則か)。`review`: 引用した規則の審査者の集計(`rules` の `review` と同じ形)。
- `problems`(`severity` = `fail` / `incomplete`、`code`、`message`)と `verdict`。

`code`: `fail` は `input_hash_mismatch` `selected_sources_mismatch` `unknown_source` `body_mismatch` `body_missing` `cited_source_not_selected` `unknown_rule` `cited_rule_out_of_scope` `host_log_inside_run` `host_log_invalid`。`incomplete` は `registry_changed` `source_blocked` / `source_missing` / `source_stale` `read_not_observed` `generation_write_not_found`。ログを渡さないだけでは、問題にしない。

`verdict`: `CONSISTENT`(0)/ `INCONSISTENT`(1。`fail` が 1 件でもある)/ `INCOMPLETE`(2)。計画は 0。引数や run の構造の契約違反は 1、run やログが見つからないときは 2。

確かめるのは、記録と registry の整合です。規則の解釈が正しいこと、資料を理解したこと、効果は確かめません。読取り観測には Claude Code・Codex・Antigravity の対応ログが必要です。対応する版・形式は「読取り観測」を参照してください。

## 資料が 0 件の入力

`target_surface` と `model_id` が `unknown` のとき、利用先 × `purpose` の一部の組は、`select_sources` が空を返す。その入力で使える `official` 規則がなく、規則のない資料は取得しない決まりだからである(registry の検査が、使える規則のない資料の取得を拒否する)。どの組が空かは、`sources --target T --purpose P`(計画。通信しない)の `selected` が空かどうかで分かる。`target_surface` や `model_id` を指定すると、資料と規則が加わることがある。

各コマンドの振る舞い:

| コマンド | 資料が 0 件のとき | 終了コード |
|---|---|---|
| `fetch` | `acquisition.json` の `selected_sources` と `sources` が空。通信しない | 0 |
| `drift --run` | `CURRENT`。`sources` と `rules` が空 | 0 |
| `rules --run` | `rules` が空(件数 0) | 0 |
| `verify` | `INCOMPLETE`。`errors` は `no_applicable_official_source`、`generation_status` が `provisional` なら `provisional_generation`。ログがなければ `no_host_log` も出る。`--host-log` を渡しても `INCOMPLETE` のまま。`checks` の `scope` は `UNAVAILABLE`(`FAIL` ではなく、判定する資料がないという意味) | 2 |
| `sources --run` | `CONSISTENT`。「選んだ資料(0 件)」は、空の表の代わりに、資料がない理由の 1 行(使える公式規則がない、故障ではない)を出す | 0 |

- `official` の change は書けない。引用できる規則がないからである(書くと `verify` が `FAIL`)。`editorial` か `inference` だけにする。
- `generation_status` は `provisional` にする(公式資料で裏づけられない案のため)。`complete` と書いても、`verify` の結果は `INCOMPLETE` のままで変わらない。
- `verify` の `INCOMPLETE` は、再取得や再審査では解消しない。参照検証は `INCOMPLETE` のまま返し、改稿は候補と明記する。「検証済み」と書かない。
- 最終回答の(3)には、資料が 0 件であることと、理由(使える公式規則がない)を書く。依頼が示す `target_surface` や `model_id` が利用先に当てはまるなら、指定すると資料が加わることがある(`api` は、その利用先が API で使われるときだけ。たとえば Gemini CLI の一回の依頼には当てはまらない)。`target_surface` と `model_id` は、依頼にないものを推定で埋めない。
- `purpose` の選び方で、資料が 0 件かどうかが変わる(同じ利用先でも、`one-shot` では資料があり `agent-instruction` では 0 件、など)。依頼の言葉から決め、決めた値と理由を最終回答に書く。資料が付く値に寄せない。別の値なら資料が付くことは、(3)に書く。

## recommendations.json の change

`kind`: `official`(規則・出典必須)/ `inference`(出典を付ける場合は全項目、付けない場合はすべて null)/ `editorial`(出典なし)。
`before_*` は正規化済み原文、`after_*` は正規化済み改稿の Python 文字 offset(半開区間)。正規化は UTF-8 strict + CRLF/CR→LF。
`original_sha256` / `optimized_sha256` は Python で計算する(`hashlib.sha256` を正規化後の UTF-8 bytes に)。

## 読取り観測

`verify --host-log <path>` は、`execution_host` に対応する adapter でログを読み、成功した読取り結果を共通形式で照合します。次をすべて満たす場合だけ `read_status=observed`。

- `Read` が `snapshots/<id>/body.txt` を対象にし、結果が成功(`is_error` でない)。
- adapter が抽出した行番号と本文が `body.txt` の該当行と一致し、採用した根拠の行をすべて含む。
- その結果が `optimized.md` / `recommendations.json` への最初の書込みより前にある。
- ログが run ディレクトリの外にある。

`execution_host` は `claude-code` / `codex` / `chatgpt-work` / `antigravity` / `claude-cowork`。読取りの adapter があるのは `claude-code`(`claude-code-transcript-jsonl-v1`)と `codex`(`codex-rollout-jsonl-v1`、`session_meta.cli_version` が `0.155.1` か `0.162.*` のときだけ。ほかの版は `host_log_invalid`)と `antigravity`(`antigravity-transcript-jsonl-v1`、agy 1.3.5・1.4.0 の観測形式。版と SHA-256 の sidecar が必須)で、他のホストでは `no_log_adapter_for_host`(総合 `INCOMPLETE`)になる。ログを渡しても解釈しない。

Antigravity では、ログを作ったときに `agy --version` を確認する。完了した `transcript_full.jsonl` を run の外に保存し、`python3 <skill-dir>/scripts/pin_antigravity_log.py --log <絶対パス> --cli-version 1.3.5` で `<ログ>.version.json` を作る(1.4.0で生成したログは `--cli-version 1.4.0`)。同梱 helper は版・形式名・SHA-256 を記録し、上書きしない。これは運用者による申告で、真正性の認証ではない。過去ログの版を、現在のインストール版から推定して書かない。`--host-log` には sidecar ではなくログ本文のパスを渡す。

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

`compare` の `scorers[].method=human` はモデル校正ファイルを必要としません。人の確認・採点を代行したことにはなりません。人の採点でもラベルを隠した比較、逆順での再確認、提示位置の変更が必要です。モデル等の非コード・非人手採点には引き続き校正ハッシュと rubric_version が必要です。`validate_plan(plan)` は結果を書き出さず計画の構造だけを検証します。
