# 0004. Skills CLIでmain最新版へ追従する

- 状態: 採用（2026-11-05、ユーザーがmain追従とMac移行を指定）
- 置き換え: install.py・ZIP・プラグイン出力に関する部分は [ADR 0005](0005-remove-install-py.md) で廃止した。
- 関連: Issue #76。ADR 0001〜0003のプロンプト最適化・証拠契約は変更しない。

## 仮説・要件調査

H1: 標準のSkills CLIへGitHub取得元を初回登録すれば、独自の更新処理を追加せず `npx skills update` で配布元と同じファイルを取得できる。現行の `.claude/skills/official-prompt-tailor/` はCLI 1.7.6の探索対象で、実際の `add --list` でも検出した。既存手動コピーはMacのglobal lockに未登録だった。

調査した既存スキル: `skill-creator` の標準フォルダと段階的な参照、`find-skills` のGitHub取得元からの登録、`mece-cluster-advisor` の独立したVERSIONファイル、Antigravity配置の本スキルのname/descriptionのみのfrontmatterとscripts/references分離。既存の実行構成を維持し、版識別だけをVERSIONへ追加する。クラスタリング・新しいruntime router・独自の更新ラッパーは要件に不要なので採用しない。設計の選択肢をgrillingで確認し、ADRの背景・制約・実装計画・検証条件をadr-skillに従って記録する。

ユーザーは当初の公開リリース限定を撤回しmain追従を指定。公開成功時だけ進める専用ブランチ案は不要となった。Skills CLIの `owner/repo@name` はスキル名であり、ref指定は `#main` を使う。

## 決定・README・モデル

取得元（GitHub＋main）、管理記録（CLIのlock）、配置（共通の本体＋製品別リンク／コピー）、版識別（VERSION）を区別する。最新版とはmainのスキルフォルダであり、GitHub Releaseのlatestタグではない。

READMEに初回 `skills add` と以後の対象を絞った `skills update` を追加する。詳細文書に移行・削除・global/projectの区別・既存インストーラとの境界を置く。既存install.pyの導入・更新・削除・ZIP・プラグイン機能は維持する。

選択肢は文書と標準CLIの利用、独自ロック生成機能、外部CLIの改変。後二者はロック仕様への依存と保守コストを増やすため不採用。取得元をローカルパスにせず、CLI自身にGitHub由来のlockを生成させる。

## 制約・非機能要件

CLI 1.7.6はNode.js 22.20.0以上。既存Python経路の利用者にはNodeを必須にしない。追加のPython依存・API課金なし。CLIの上書きには既存installer同等の自動退避がないため、Mac移行前に関連コピーとlockを退避し、無関係なスキル・ローカル変更を保護する。検証時はCLIのtelemetryを無効にし、ログと退避をignored `feedback/` に置き、秘密・個人情報を公開しない。ソースをコピーせず上流実装へのリンクを記録するため追加コードのライセンス移入なし。手順はテキストで読め、製品UIや色に依存しない。

Antigravity系の既存パスはCLIの共通配置と異なる。Macではスキル単位の互換リンクで共通配置へ接続し、CLI管理外であることと削除手順を記録する。配置・ファイル照合と、実製品の自然言語起動・実行品質を混同しない。

## 実装計画・成功指標・撤退条件

1. README、docs/install.mdに標準CLIの手順を追加する。独自更新コードは追加しない。
2. 配布フォルダにVERSIONを追加し、install.pyの公開版と一致することを既存unittestで検証する。
3. 隔離HOMEでGitHub由来の導入・lock・更新を確認する。既存installerによる配置とCLIによる配置を同じ配布元でファイル単位に照合する（配布経路の同等性比較であり、プロンプト品質のA/B評価ではない）。
4. Antigravityレビュー後、CI・マージ・v0.9.3公開、Macの登録・更新・ファイル照合を行う。ブランチ・今回の一時環境・確認済みバックアップを後片付けする。

成功条件は登録済み取得元がGitHub/mainで、更新後の配布ファイルが確認済みmainと一致し、既存回帰試験と版整合検査が成功すること。内容の品質向上は主張しない。重要なローカル変更・出所不明の配置・CLIでの更新失敗があれば、その対象を上書きせず原因を記録する。繰り返し同じ失敗で再実行しない。製品が共通配置も互換リンクも読めない場合は既存installer経路を保持し未解決として報告する。

## 検証記録

実行記録は `feedback/`、最終結果とレビュー採否は本ADRとIssue #76へ追記する。

- 変更前408件・変更後409件のunittest成功。文書整合検査成功。隔離環境のGitHub/main登録・既存13ファイルのバイト照合・変更なし更新が成功。旧tree hashを保持してマージ後の実更新を検証する。
- Antigravity Gemini 4 Argon Mediumは個人利用枠の上限でレビュー未実施（失敗をレビュー済みと扱わない）。Claude Code Sonnet 5.5 Medium・plan modeでレビューを完了し、確認済み不具合なし。レビュー自身はテストを再実行していない。
- 採用: VERSIONの形式・リリースノート存在検査、具体的な退避コマンド。非採用: インストーラのリンク拒否のためだけの追加テスト。`copy_state` がリンクを未認識とし、既存の更新拒否検査でその分岐を使う実装を確認でき、ロックを書き換えるコードもないため、文書表現を維持する。プロンプト品質の改善は未評価。

## 参考

- [Skills CLI 1.7.6](https://github.com/vercel-labs/skills/tree/7a5e920bead4b1039a6d6df5d7e1163b7b09423a)
- [探索](https://github.com/vercel-labs/skills/blob/7a5e920bead4b1039a6d6df5d7e1163b7b09423a/src/skills.ts)
- [更新](https://github.com/vercel-labs/skills/blob/7a5e920bead4b1039a6d6df5d7e1163b7b09423a/src/update.ts)
