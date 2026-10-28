# 2026-10-27 公式資料への追従更新

Issue #55、v0.6.13。対象は既存の登録資料50件。取得処理や適用範囲を緩めず、レジストリの見出し・引用・審査記録を更新した。

## 取得と規則

Claude Code Best practices、Claude Opus 5.5、Claude Code Scheduled tasksで、登録済みの必須見出しが改称されていた。現行見出しへ変更したところ、50件すべてのライブ取得・本文検査が成立した。

取得不能だった資料も含めて再比較すると、104規則中36件は審査時と同じ本文、62件は引用が残った本文変更、6件は引用欠落だった。

- 引用欠落の5件は、現行本文の同じ助言へ引用を差し替えた。`claude-code-specific-context` の意味・許容変更は「対象ファイル・症状・完了状態」に絞った。
- `claude-code-plan-scope` は、元の「小さい変更なら計画を省く」という主張の根拠を現行本文で確認できず、削除した。別の助言を根拠に審査を引き継がない。
- 引用周辺不変の35件は、既存の `rereview --write` による機械比較で審査記録を更新した。
- 文脈変更・節のリスク語検出・引用差し替えの32件は、現在の引用周辺、旧本文との差分、意味・前提条件・除外条件をagentが確認して更新した。人による審査とは記録しない。

規則は合計103件。URL制限、本文検査、API・モデル・用途の限定、利用者の原文にない作業を加えない制約を維持した。過去の実行記録は変更していない。

更新後のライブ `drift` は `CURRENT`。50資料がすべて `live`、103規則がすべて `current` で、取得エラー・引用欠落はなかった。ローカルの全360テスト、文書生成結果の整合、ZIPの生成・同梱検査を確認した。

## 新規ページ候補の一次仕分け

`discover` では9一覧を取得し、22候補があった。今回は既存資料への追従を優先し、候補は採用も既読化もせずIssue #25に残した。次表は題名・用途による一次仕分けで、全22本文の意味審査を済ませたという記録ではない。

| 候補 | 見送った理由・残る確認 |
|---|---|
| ChatGPT Edu workspace configuration | ワークスペース管理。プロンプトの書き方を支持する記述の有無は未審査 |
| evaluation best practices / fine tuning best practices / production best practices | 評価・学習・本番運用。今回の既存規則の追従とは別の用途 |
| prompt caching / diagnostics / promptfoo codex | 実行・計測・キャッシュの運用。指示文の編集規則としての採用は未審査 |
| safety best practices / federation rules | 安全・認証管理。取得対象を無条件には広げない |
| frontend prompt / image prompting / live prompting / voice prompting | UI・画像・音声の専用ガイド。用途別の取得条件と適用範囲の設計が必要。frontend promptはGPT-5.5を対象としていることを公式本文で確認 |
| Gemini Live API best practices / lyria prompt guide | 音声・音楽の専用ガイド。既存のテキスト向け規則へ一般化しない |
| prompt generation / prompt optimizer / prompting / migrate from prompt object / tools skills | 既存資料との重複、生成・移行手順と助言の区別を本文で確認してから採用する |
| prompt guidance / reasoning best practices | モデル・推論設定の適用範囲を確認する必要がある。prompt guidanceはUsing GPT-6へのリダイレクトを公式サイトで確認 |

候補の採用を先送りしたため、公式サイト全体を網羅した最新版対応とは主張しない。取得・引用の追従は、最適化効果の実測でも、独立ホストでの実行観測でもない。

## 調査した既存スキルと構成

- `official-prompt-tailor`：descriptionの用途・起動条件、段階的な参照、`references/` と `scripts/` の分割、既存の `rereview`・インストーラーを継続利用した。
- `skill-creator`：依頼範囲の保持と、必要な部分だけを更新する方針を採用した。新しい抽出方式・依存関係・共通抽象化・補助ディレクトリは不要だった。
- Claude Code・Codex・Gemini Antigravity向けの既存配布先と、ユーザー配置の同じスキル構成を確認した。インストール・更新・アンインストールは既存の `install.py` を使う。

## 公式参照

- [Claude Code Best practices](https://code.claude.com/docs/en/best-practices)
- [Claude Opus 5.5 prompting](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5.md)
- [Claude Code Scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks.md)
- [OpenAI Frontend prompt instructions](https://developers.openai.com/api/docs/guides/frontend-prompt)
- [OpenAI Using GPT-6](https://developers.openai.com/api/docs/guides/latest-model)

生の取得本文・比較JSONはGit除外対象の `feedback/` に保持し、公開しない。
# 2026-10-29の再取得

公開前の再取得で、Antigravity Rulesがwwwなしの公式URLへ移転していたため、登録URLを現行URLへ置き換えた。ドメイン制限や本文検査は緩めていない。

引用2件のASCIIアポストロフィを現行本文の曲線アポストロフィへ更新した。追加で変更された47規則のうち、10規則は引用周辺不変の機械比較、37規則はagentが現行文脈と適用条件を再確認した。人による審査ではない。意味・適用条件の追加変更や新規資料の採用はしていない。

移転先: https://antigravity.google/docs/rules/
