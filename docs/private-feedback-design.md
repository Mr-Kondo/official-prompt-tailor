# 機密原文を収集しない評価票の設計判断

Issue [#27](https://github.com/Mr-Kondo/official-prompt-tailor/issues/27)。基準は2026-10-20、コミット `d5e357b49a4f15c6b2605155a23b5cde85fc8a0a`。操作・選択肢・制約は、配布物にも含む[利用手順](../.claude/skills/official-prompt-tailor/references/private-feedback.md)にまとめています。

## 対象範囲

実文書を共有できない利用者が、社内で評価した結果だけを記録する機能です。原文の収集、xlsx解析、LLM採点、自動修正、自動送信は行いません。[#24](https://github.com/Mr-Kondo/official-prompt-tailor/issues/24)の広いフィードバック改善ループを完了したものではありません。

まず固定の選択式票で有用性を確かめ、その後に必要なら社内検査アダプターを検討します。初期段階でxlsxのXML解析や大きな匿名化基盤を導入すると、評価の有用性を確かめる前に対応形式・依存・運用の負担が増えるため見送りました。

## 関連文献・類似実装

| 調査対象 | 確認範囲と判断 |
|---|---|
| Garfinkelほか、NIST SP 800-188（2023）、[本文](https://doi.org/10.6028/NIST.SP.800-188) §4.4–4.6 | 架空テスト・社内検証・結果開示の有用性と保護を別に確認する考え方を採用。企業秘密の持出し許可や本ツールの安全性を認定する根拠ではない |
| Great Expectations、[結果形式](https://docs.greatexpectations.io/docs/core/trigger_actions_based_on_results/choose_a_result_format/)と[シリアライズ実装](https://github.com/fivetran/great_expectations/blob/develop/great_expectations/core/expectation_validation_result.py) | 合否に絞る発想を採用。ただし設定・meta・例外を含む結果全体はコピーしない。ライブラリ導入は不要と判断。閲覧時の文書は1.24.0、コードはdevelopで版固定の実行検証ではない |
| Presidio、[公式の制約](https://presidio.dataprivacystack.org/)と[Anonymizer](https://github.com/data-privacy-stack/presidio/blob/main/presidio-anonymizer/presidio_anonymizer/anonymizer_engine.py) | 全機密の検出を保証しないため、黒塗りして共有する方式を主方式にしない。未導入・未実行 |
| Hypothesis、[公式文書](https://hypothesis.readthedocs.io/en/latest/) | 架空ケースの境界生成に使えるが、今回の固定スキーマには標準unittestの変種検査で対応。依存追加は保留 |
| Python 3.9、[json](https://docs.python.org/3.9/library/json.html)・[os](https://docs.python.org/3.9/library/os.html#os.open) | 容量制限、重複キーと特殊数値の拒否、排他的作成に標準APIを使用 |

文献の主張とこの実装の効果は別です。実文書での改善効果は未測定です。

## 既存スキル・コードの調査

- official-prompt-tailor: 短いfrontmatterとSKILL.md、scripts/referencesの分離、取得・意味・効果の別判定を維持しました。
- japanese-readability-editor（Claude Code・Gemini配置とプラグイン）: 数値・条件・例外・予定の保持を検査カテゴリへ取り込みました。文章長の目安は、今回の合否基準に使いません。
- 同スキルの `make_feedback.py / feedback_loop.py`: 固定キーと確認状態の分離を参考にしました。原文・結果・引用・原文由来ハッシュを持つ票は、今回の持出し用には流用しません。
- contextual-terminology-review（共通配置）: 原資料、本人の申告、モデルの説明、確認済みの事実を区別する考え方を採用しました。

既存の3製品向けinstall/update/uninstallへ同梱物を追加し、新たなホスト専用hookやネットワークサービスは導入しません。

## 他エージェントの参考意見

実装前の固定スキーマ案を、業務内容・原文・実行ログを含めず、同一の抽象的説明で相談しました。ツール実行やファイル変更は求めていません。

- Antigravity CLI 1.4.3: 参考意見を取得しました。boolとintの区別、特殊数値・重複キーの拒否、固定エラー、UUID4、方法と条件が一致した場合だけの比較を採用しました。JSONの編集や選択肢の組合せによる意図的な情報埋込みまで防げない点も明記しました。
- Claude Code 2.1.304: 実際に照会しましたが、session limitで参考意見は取得できませんでした。賛成や合意を推定していません。両製品の設計確認が揃ったという結果ではありません。

参考意見のうち、全例外の一括捕捉、Windowsを含む厳密なファイル隔離、途中ファイルの無条件削除は採用しません。容量を限定した入力境界と想定されるエラーを扱い、管理下のディレクトリで使う条件を示します。途中ファイルの無条件削除は、差替えられたファイルの削除を招きうるため、成功していない出力を共有しない手順にしました。独立した一般コードレビューの代用として、多数決は使いません。

## 検証と残る限界

検査対象は、許可キー・列挙値だけの出力、初期票の未確認状態、方法・条件・状態ごとの遷移、不正入力の非反射、容量境界、重複キー、BOM/CRLF、日本語パス、上書き拒否、3製品へのinstall/update/uninstall、ZIP同梱物です。実データの代わりに架空の選択肢とカナリアを使います。

票は操作者の申告です。原本の真正性、社内実行の有無、会社の承認、判定の正しさ、一般的な改善率は証明しません。有限のカナリア検査も、漏洩ゼロの保証ではありません。Python/OS/エージェント自身のログや悪意ある端末の制御は対象外です。
