# official-prompt-tailor

プロンプトを、利用先の公式資料に基づいて最適化するスキル(`SKILL.md` 形式)です。**取得・引用・適用範囲・変更箇所への結合**は、スクリプトで検証します。

- 利用先: ChatGPT / ChatGPT Work / Codex / Claude / Cowork / Claude Code / Gemini / Antigravity / Gemini CLI / Google ADK
- 動かせる製品: Claude Code・Codex・Antigravity・Claude Cowork・ChatGPT Work(これらで動かせるように作ってあります)
- 実機での確認記録: Claude Code・Codex CLI・Antigravity CLI。Antigravity アプリは限定確認です。[日付・版・未確認範囲](docs/history/runtime-verification.md#product-reexecution)を参照してください。

```text
.claude/skills/official-prompt-tailor/
  SKILL.md                       入口・手順・表示規則
  references/registry.json       資料(50)・対象(11)・審査済み規則(103)
  references/evidence-contract.md / review-rubric.md
  scripts/evidence.py            fetch / verify / assess / compare / drift / rules / sources(標準ライブラリのみ)
  scripts/private_feedback.py    原文を収集しない評価票の template / export
tools/maintain.py                保守用(スキルには同梱しない): discover / propose / adopt / keep / rereview / review / docs / audit
.github/workflows/               test(Python 3.9〜3.15)・watch(週次の drift と discover)・release
tests/   evals/prompt-tailor-cases.jsonl   outputs/(Git 管理外)
```

## 文書の読み方

通常は、このREADMEから[インストール](docs/install.md)・[利用手順](docs/usage.md)・[利用先の選び方](docs/targets.md)へ進みます。製品の制約とログの詳細は仕様、`docs/history/` は過去の記録です。規則の更新や公開を行う開発者は[メンテナンス手順](docs/maintenance.md)を参照してください。

## インストール

Git と Python 3.9 以上が必要です。Pythonの追加パッケージは不要です。

Windows の例は **Windows PowerShell 5.1／PowerShell 7 用**です。コマンドプロンプト（cmd.exe）では実行しないでください。`python --version` で Python 3.9 以上が表示される環境を使います。`py -3` だけが使える場合は、例の `python` を `py -3` に置き換えてください。

以下は、全プロジェクトで使う **ユーザー配置** の手順です。既に別の場所へ clone している場合や、`--project` で導入した場合は、[既存の場所・配置先を使う更新手順](docs/install.md#既存の場所配置先を使って更新する)を参照してください。配置方法を変えると、既存コピーの更新ではなく、別の場所への導入になります。

### 1. 初回だけソースを取得する

次のコマンドは macOS・Linux・Windows PowerShell 共通です。ホームディレクトリの `official-prompt-tailor` に、このリポジトリの `main` を取得します。

```sh
git clone --branch main https://github.com/Mr-Kondo/official-prompt-tailor.git "$HOME/official-prompt-tailor"
```

この手順で取得済みなら、clone は繰り返さず、次へ進みます。clone に失敗した場合は、原因を確認してから進めてください。

### 2. コマンドを実行する

**使う OS のブロックを1つ選び、そのままコピーして実行してください。** 以下は Claude Code 用です。Codex・Antigravity アプリ・IDE・Antigravity CLI は `--target` だけが違うので、[製品別のコピー用コマンド](docs/install.md#製品別のコピー用コマンド)を使います。現在の作業ディレクトリには依存しません。初回導入と更新で、同じコマンドを使えます。PowerShellでは `& {` から最後の `}` まで、ブロック全体をコピーします。

最初の `git pull --ff-only` が `main` の最新版を取得し、成功した場合だけ `install.py --update` が配置先を更新します。これは公開タグへの固定ではなく、`main` に追従する手順です。`--update` だけでは最新版をダウンロードしません。

macOS・Linux:

```sh
git -C "$HOME/official-prompt-tailor" pull --ff-only &&
python3 "$HOME/official-prompt-tailor/install.py" --target claude-code --user --update
```

Windows PowerShell:

```text powershell
& {
    git -C "$HOME/official-prompt-tailor" pull --ff-only
    if (-not $? -or $LASTEXITCODE -ne 0) { throw "Git update failed; installation stopped." }
    python -X utf8 "$HOME/official-prompt-tailor/install.py" --target claude-code --user --update
    if (-not $? -or $LASTEXITCODE -ne 0) { throw "Skill update failed." }
}
```

### 実行後の確認

- 未導入ならインストールし、同じ内容なら `up to date` と表示して何もしません。差替え時は旧版を `.bak-<日時>` へ退避します。このスキルと確認できない既存コピーは更新しません。
- 設定・hook・権限は変更しません。利用する製品で新しいセッションを開き、下の「動かせる製品」にある方法で呼び出してください。
- 実行結果は、スキルを使うワークスペースの `outputs/official-prompt-tailor/` に出ます。

プロジェクトへの配置、ZIP・プラグインの作成、削除、更新に失敗した場合は [docs/install.md](docs/install.md) を参照してください。

## 動かせる製品

スキルを動かす製品(`input.json` の `execution_host`)と、プロンプトの利用先(`target_id`)は別です。

| 動かす製品 | 配置 | 呼び出し | 実機確認 |
|---|---|---|---|
| Claude Code | `install.py`(既定) | `/official-prompt-tailor` | 済み |
| Codex | `--target codex` | `$official-prompt-tailor` か `/skills` | CLI。Desktop は別途未確認 |
| Antigravity | `--target antigravity` / `antigravity-cli` | `/official-prompt-tailor` | CLI。アプリは限定確認 |
| Claude Cowork | `--zip` で ZIP を作って、アップロード | 自動または指定 | 未確認 |
| ChatGPT Work | `--plugin` で互換形式を作り、[公式の導入手順](docs/install.md#--pluginchatgpt-work--codex)へ | `@` で選択 | 未確認 |

読取り観測は Claude Code・Codex・Antigravity の対応ログで行います。対応する版・形式には制限があり、ログがない場合や未対応の製品では、参照検証は `INCOMPLETE` になります。[製品ごとの制約](docs/products.md)と[ログの仕様](docs/log-adapters.md)を参照してください。

### 必要な接続先(HTTPS)

`code.claude.com`、`platform.claude.com`、`support.claude.com`、`learn.chatgpt.com`、`developers.openai.com`、`support.google.com`、`geminicli.com`、`adk.dev`、`www.antigravity.google`、`antigravity.google`、`ai.google.dev`。

許可されないと、`fetch` は `blocked` になります。許可設定は、スキルから変更しません。

## 3 つの結果は別物です

「資料を読んだ」「意味が合っている」「成果が出た」は別の事実なので、別々に表示します。

| 結果 | コマンド | 値 → 終了コード |
|---|---|---|
| 参照検証 | `verify` | `PASS_LIVE` / `PASS_CACHE` → 0、`FAIL` → 1、`INCOMPLETE` → 2 |
| 意味審査 | `assess` | `PASS` → 0、`FAIL` → 1、`INCOMPLETE`(未審査・校正不足・判断不足)→ 2 |
| 効果測定 | `compare` | `IMPROVED_ON_TEST_SET` → 0、`REGRESSED` / `NO_CLEAR_IMPROVEMENT` → 1、`NOT_EVALUATED` / `INCOMPLETE` → 2。契約違反は 1 |

推奨を採用するには、「参照検証 PASS」と「意味審査 PASS」の両方が必要です。「改善を実測済み」と書けるのは、その対象の `IMPROVED_ON_TEST_SET` があるときだけです。これは、一般的な性能向上や統計的な確証を意味しません。

`fetch` の終了コード、資料が一部しか取れないときの扱い、`drift`、`sources` は、[取得と確認の結果の読み方](docs/usage.md#取得と確認の結果の読み方)を参照してください。

## 使い方

Claude Code で `/official-prompt-tailor` を呼ぶか、「このプロンプトを Claude Code 向けに公式資料に沿って最適化して」と頼みます。手動で流す手順、cache の例、`INCOMPLETE` の例は [docs/usage.md](docs/usage.md) にあります。利用先ごとの取得資料・使える規則と、`purpose` / `target_surface` / `model_id` の決め方は [docs/targets.md](docs/targets.md) にあります。

原文を持ち出せない会社の文書では、`scripts/private_feedback.py` で選択式の評価票を作れます。手順は[会社の文書を持ち出せない場合](docs/usage.md#会社の文書を持ち出せない場合)を参照してください。

## 何を保証するか

`verify` が保証するのは、取得・引用・適用範囲・変更箇所の結合までです。モデルの認知的な理解、引用が主張を意味的に支持すること、改稿が原文の意味を保つこと、改稿の性能は保証しません。snapshot は、改ざん耐性のある署名ではありません。詳細は [docs/trust-boundary.md](docs/trust-boundary.md) にあります。

## メンテナンスと審査

公式資料は変わります。更新の確認(`drift`)と新規ページの発見(`tools/maintain.py discover`)は、毎週 GitHub Actions でも実行されます。規則の再確認方法、資料の追加方法、意味審査、効果測定、リリースの手順は [docs/maintenance.md](docs/maintenance.md) にあります。別のエージェントに手順を通させた結果と、その検査(`audit`)は [独立実行の履歴](docs/history/runtime-verification.md#independent-runs) にあります。

## テスト

```bash
python3 -m unittest discover -s tests
```

テストが示すのは、検査コードが想定どおり動くことだけです。本番の公式取得・独立ログ・skill の実発火・対象製品での効果は、示しません。[何を示すか](docs/trust-boundary.md#テストが示すこと)と[検証履歴](docs/history/runtime-verification.md)を参照してください。

## 出典・ライセンス

このリポジトリは MIT License です(`LICENSE`)。`references/registry.json` の `source_excerpt` は、各公式資料の短い引用で、権利は各資料の提供元に帰属します。

発想は、[記事](https://zenn.dev/toritori0318/articles/a26fea4638229e) と [skills-prompt-tailor](https://github.com/toritori0318/skills-prompt-tailor)(MIT, Copyright (c) 2026 toritsuyo)を参考にした独立実装です。コード・同梱助言・性能数値は取り込んでいません。他の比較候補の文面・コードも流用していません。
