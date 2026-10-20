# 原文を持ち出さずに評価を報告する

`scripts/private_feedback.py` は、社内で確認した結果を選択式の票へ記録し、共有用のJSONを新規作成するPython 3.9以降のツールです。追加パッケージと通信は不要です。xlsx・業務md・原文プロンプト・会話ログを入力しません。自動匿名化やxlsxの検査は行いません。

## 1. 社内で票を作る

以下の `<skill-dir>` は、この参照文書を同梱するスキルのディレクトリに置き換えます。Windowsでは `python3` を `python` または `py -3` に読み替えます。ローカル／リモートとも、原文を扱うことが認められた社内環境で評価し、票を作ります。

```bash
python3 <skill-dir>/scripts/private_feedback.py template --out draft.json
```

`draft.json` をテキストエディターで開き、選択肢だけを記入します。ファイル名は任意ですが、本文に名前やパスを追加しないでください。普段のプロンプト最適化で票の作成を必須にするものではありません。

| 項目 | 記入する値 |
|---|---|
| `schema_version` | 整数 `1` のまま |
| `kind` | `private_feedback_draft` のまま |
| `origin` | 実資料の評価報告は `private_case_report`、架空テストは `artificial_test`。初期値 `unknown` のままではexportできない |
| `stage` | 改稿内容の確認は `prompt_semantics`、実際のxlsx編集結果の確認は `downstream_workbook`。不明なら `unknown` |
| `conditions` | 元と改稿を同条件で比較したなら `matched`、条件が違うなら `different`、確認できなければ `unknown` |
| `environment.execution_host` | `claude-code` / `codex` / `antigravity` / `unknown` |
| `environment.os` | `windows` / `macos` / `linux` / `unknown` |

`baseline` は元プロンプト、`candidate` は改稿です。旧版スキルと新版スキルの比較には流用しません。両方を実行する場合、同じ編集前ファイルの別々のコピーを使います。モデル、推論設定、評価条件等を社内で記録・照合し、確認できない場合は `conditions: unknown` にします。票にはモデル名や詳細な環境情報を追加できません。

改稿の確認と実行成果の確認は別の票にします。ある段階で失敗したという報告は、その段階が根本原因だという認定ではありません。

## 2. 九つの項目を記入する

| `check_id` | 確かめること |
|---|---|
| `identifier_mapping` | IDと対象行が対応しているか |
| `column_mapping` | 指定した列と対応しているか |
| `exact_values` | 似た綴りの値も含め、指定値が保たれているか |
| `exception_scope` | 例外の対象や条件が変わっていないか |
| `append_vs_replace` | 追記と置換が入れ替わっていないか |
| `numbers_units` | 数値・単位・範囲が保持されているか |
| `modality` | 予定・推量を確定事項や実施済みに変えていないか |
| `evidence_alignment` | 根拠と記述が対応しているか |
| `untouched_cells` | 対象外セルが保持されているか |

全項目と両案の欄を残します。原文を見ていない人に推定で埋めさせません。改稿だけの確認では対象外セルを検査できないため、`unknown` 等を使います。

各欄は次の形です。これは架空の記入例で、実際の失敗報告ではありません。

```json
{"status": "fail", "method": "human", "reason": "mismatch"}
```

| `status` | `method` | `reason` |
|---|---|---|
| `pass` | `human` / `deterministic` / `model_self_report` | `none` |
| `fail` | 同上 | `mismatch` |
| `not_applicable` | 同上 | `not_applicable` |
| `unknown` | `human` / `deterministic` / `model_self_report` / `unavailable` | `not_run` / `missing_artifact` / `ambiguous_requirement` / `unsupported` |

`not_run` だけは `method: unavailable` が必須です。初期値は `unknown / unavailable / not_run`。対象外と確認できたものだけ `not_applicable` にします。判定できなければ `unknown` に残します。

`human` は人の確認、`deterministic` は別の決定的な検査の結果、`model_self_report` は生成モデル自身の申告です。このツールはそれらの検査を実行せず、申告の正しさも認証しません。単なる語句一致で、例外・因果・根拠の意味まで合格としないでください。

## 3. 内容と持出し条件を確認して出力する

```bash
python3 <skill-dir>/scripts/private_feedback.py export --input draft.json --out report.json --confirm-share
```

`--confirm-share` は、出力内容と社内の持出し条件を操作者が確認したことを示す操作です。会社の承認を認証する機能ではありません。実データを含まない票でも、共有できるかは社内規則によります。自動送信は行いません。

動作だけを試すときは、同梱の[架空の記入例](private-feedback-example.json)を使えます。実資料やモデルの実行成果に基づく票ではなく、遷移の表示を確かめる人工例です。

```bash
python3 <skill-dir>/scripts/private_feedback.py export --input <skill-dir>/references/private-feedback-example.json --out example-report.json --confirm-share
```

共有前に `report.json` を開いて確認します。既存のrunディレクトリを丸ごと送らず、この共有用ファイルだけを対象にします。原文、根拠資料、元の実行ログ、ファイル名、シート名、列名、セル座標、業務数値、正確な件数、原文由来ハッシュ、自由記述は出力対象にありません。未知キーや任意の文字列を足すと拒否します。

出力は検証した選択肢から組み直し、入力にないランダムなUUIDを `case_id` として生成します。時刻、入力パス、本文ハッシュをIDに使いません。再出力では別のIDになり、自動重複排除はできません。同じ原本の票を別の独立事例と数えないための照合は社内で行います。

## 4. 出力を改良に使う

共有用JSONの `kind` は `private_feedback_report`、`comparison` は `original_vs_optimized`、`evidence_status` は `reported_not_independently_verified` です。各項目の `transition` は次のとおりです。

| 元 → 改稿 | `transition` |
|---|---|
| 不合格 → 合格 | `reported_fix` |
| 合格 → 不合格 | `reported_regression` |
| 合格 → 合格 | `both_pass` |
| 不合格 → 不合格 | `both_fail` |
| 比較条件を満たさない | `incomparable` |

比較できるのは、`conditions: matched`、`stage` が既知、両案とも `pass` または `fail`、同じ `human` または `deterministic` の方法、をすべて満たす場合だけです。`unknown`・`not_applicable`・モデルの自己申告は改善証拠にしません。方法が一致しても採点の正しさは保証されません。

総合点や改善率は出しません。悪化を他項目の修復で相殺せず、比較不能を合格に数えません。実際の内容を共有しない票は、外部の評価者による独立した意味審査や再現証拠ではありません。既存の `verify / assess / compare` の入力形式・合格結果にもなりません。

指摘された種類から、社外で架空の小さな検査例を新規作成できます。実文書をコピーして名前だけ置換する方法は避けます。架空例を社内へ持ち込んで同じ検査を行い、実文書との対応は社内で確認します。架空例が合格しても、実文書で直ったとは扱いません。元にした失敗票と派生例は開発用とし、未使用の評価例を別に用意します。

選ばれた報告だけから母集団の失敗率は求められません。判定の組合せ自体が機密や識別の手掛かりになる可能性もあります。任意の質問や無制限の反復収集を認める仕組みではなく、意図的な情報埋込みや悪意ある端末からの流出を防ぐものでもありません。

## 5. 入出力とエラー

- 入力はUTF-8（BOM可）の通常ファイル、最大16,384バイト。CRLFでも読めます。重複キー、浮動小数、NaN等、整数1以外の数値、未知キー、型・状態の不整合を拒否します。
- 入力のシンボリックリンクと通常ファイル以外を拒否します。利用者が管理するディレクトリで実行してください。親ディレクトリを別プロセスが差し替える状況を隔離する機能はありません。
- 出力先の親フォルダは先に用意します。既存ファイル・シンボリックリンクを上書きせず、排他的に新規作成します。書込み失敗や中断では不完全なファイルが残る場合があるため、成功終了していない出力は共有せず、社内で削除して新しい名前でやり直します。
- 正常終了は0、入出力エラーは1、引数・入力・確認不足は2。成功時の標準出力は固定の `private_feedback: written`。受け付けた入力に起因する診断は固定ASCIIコードだけで、値やパスを反射しません。Python起動前のエラーやOS・エディター・エージェント自身のログは、この制御の対象外です。
- 例: `share_not_confirmed` は共有確認不足、`invalid_choice` は選択値の不正、`inconsistent_observation` は状態の矛盾、`duplicate_key` は重複キー、`input_too_large` は容量超過、`io_error` は読書き失敗です。機密原本をそのまま入力してエラー内容を調べないでください。

既存の `install.py` でスキル全体をインストール・更新・アンインストールすると、このスクリプトと参照文書も対象になります。票はインストール先ではなく利用ワークスペースに保存します。そうすればスキルの削除で票が消えません。保持する必要がなくなった票は、社内の保存規則に従って自分で削除します。
