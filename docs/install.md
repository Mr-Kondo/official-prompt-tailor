# インストール・更新・削除の詳細

初めてユーザー配置で導入する場合は、[READMEのコピー用コマンド](../README.md#インストール)を使ってください。ここでは、既存の配置先を使う更新、各オプション、失敗時の確認方法を説明します。

## ソースの取得とスキルの配置は別の操作

`git clone` / `git pull` は、このリポジトリのソースを取得します。`install.py` は、その手元のソースを、製品が読む場所へコピーします。`--update` だけでは、GitHubから最新版を取得しません。

| 操作 | 変わる場所 |
|---|---|
| `git clone` / `git pull` | ソースを置いたリポジトリ |
| `install.py --user` | 下表のユーザー配置先。全プロジェクトで使用 |
| `install.py --project DIR` | 指定したプロジェクトの配置先。そのプロジェクトで使用 |

| `--target` | `--user` の配置先 | `--project DIR` の配置先 |
|---|---|---|
| `claude-code`（既定） | `~/.claude/skills/official-prompt-tailor/` | `DIR/.claude/skills/official-prompt-tailor/` |
| `codex` | `~/.agents/skills/official-prompt-tailor/` | `DIR/.agents/skills/official-prompt-tailor/` |
| `antigravity` | `~/.gemini/config/skills/official-prompt-tailor/` | `DIR/.agents/skills/official-prompt-tailor/` |
| `antigravity-cli` | `~/.gemini/antigravity-cli/skills/official-prompt-tailor/` | `DIR/.agents/skills/official-prompt-tailor/` |

`~` は自分のホームディレクトリです。CodexとAntigravityのプロジェクト配置先は共通ですが、ユーザー配置先は異なります。更新時は、導入時と同じ `--target` と `--user` / `--project` を使ってください。`--project` で入れたコピーは、`--user` では更新されません。

## 既存の場所・配置先を使って更新する

既に clone してある場合は、そのリポジトリを使います。READMEの取得場所へ新たに clone する必要はありません。以下は、パスを自分の環境に置き換えて使うテンプレートです。取得元のブランチは `main` を想定しています。

macOS・Linuxで、Codexのプロジェクト配置を更新する例:

```sh
git -C "/path/to/official-prompt-tailor" pull --ff-only &&
python3 "/path/to/official-prompt-tailor/install.py" --target codex --project "/path/to/my-project" --update
```

Windows PowerShell 5.1／PowerShell 7の例（**2種類のパスの置換が必要**）:

`C:/path/to/official-prompt-tailor` は既存cloneの場所、`C:/path/to/my-project` は導入先の既存プロジェクトへ置き換えます。`python --version` で3.9以上が表示されることを確認し、`& {` から最後の `}` までまとめて実行してください。cmd.exe用ではありません。

```powershell
& {
    git -C "C:/path/to/official-prompt-tailor" pull --ff-only
    if (-not $? -or $LASTEXITCODE -ne 0) { throw "Git update failed; installation stopped." }
    python -X utf8 "C:/path/to/official-prompt-tailor/install.py" --target codex --project "C:/path/to/my-project" --update
    if (-not $? -or $LASTEXITCODE -ne 0) { throw "Skill update failed." }
}
```

Claude Codeなら `--target claude-code`、Antigravityなら `--target antigravity` または `--target antigravity-cli` にします。ユーザー配置の更新は、`--project "…"` を `--user` に替えます。更新後は、利用する製品で新しいセッションを開いてください。

公開版に固定して使う場合は、[Releases](https://github.com/Mr-Kondo/official-prompt-tailor/releases)でタグを選び、そのタグのソースを取得してから `install.py` を実行します。タグのソースから更新するときも、新しいタグのソースを取得し直す必要があります。タグを直接チェックアウトした状態では、READMEの `git pull` 手順は使いません。リリースのスキルZIPはアップロード用で、`install.py` は含みません。

## 各オプションの例

次のコマンドは、リポジトリのルートで実行するテンプレートです。`/path/to/my-project` と `/path/to/export` は、実在するディレクトリへ置き換えてください。Windowsでは `python3` を `python` に読み替えます。`py -3` だけが使える環境では、Windows例の `python` も `py -3` に置き換えます。

```sh
python3 install.py --target claude-code --project "/path/to/my-project" --gitignore
python3 install.py --target claude-code --user
python3 install.py --target codex --project "/path/to/my-project"
python3 install.py --target antigravity --user
python3 install.py --target antigravity-cli --user
python3 install.py --zip "/path/to/export"
python3 install.py --plugin "/path/to/export"
python3 install.py --target codex --project "/path/to/my-project" --update
python3 install.py --target codex --project "/path/to/my-project" --uninstall
```

実行内容だけを確認するには `--dry-run` を追加します。`--gitignore` が追加するのは、プロジェクトの `.gitignore` の `outputs/` です。スキルの配置先を除外するオプションではありません。

## 更新に失敗した場合

- `git clone` が既存ディレクトリのために止まったら、既存のリポジトリか確認します。既に取得したリポジトリなら、cloneを繰り返さず更新へ進みます。
- `git pull --ff-only` が失敗したら、配置の更新も止めます。`git -C "ソースの場所" status` と `git -C "ソースの場所" branch --show-current` で、ローカル変更とブランチを確認してください。この手順のブランチは `main` です。変更を破棄したり、強制的に上書きしたりするコマンドは使いません。
- `not recognised as this skill` で止まったら、配置先がこのスクリプトで導入したコピーか確認します。通常の更新で `--force` を追加して回避しません。
- プロジェクト配置とユーザー配置の両方がある場合、使わない方を削除するときも、そのコピーを導入した `--target` と配置引数で `--uninstall` します。

## 共通の動作

- スキルのディレクトリをコピーするだけです。設定・hook・権限は変更しません。
- 既にある場合は止まります。`--update` か `--force` で置き換えると、旧版は `<skills の親>/official-prompt-tailor.bak-<日時>` に残ります(削除しません)。同じ秒に 2 回置き換えたときは、`-2`、`-3` を付けて、衝突を避けます。
- `--dry-run` で、実行内容だけを確認できます。
- コピー後に、コピー先のレジストリが読めるか自己検査します。失敗したら元に戻します。
- 終了コードは、引数の誤りが 2、インストールできない場合が 1 です。
- 導入・更新後は、新しいセッションを開いて呼び出してください。実行中のセッションへの反映方法は、製品や配置方法によって異なります。
- 実行結果は、スキルを使うワークスペースの `outputs/official-prompt-tailor/` に出ます。`--gitignore` で `outputs/` を除外できます。

## `--update`(更新)

インストールと同じ引数(`--project` / `--user` / `--target` / `--plugin`)に `--update` を付けます。Claude Code・Codex・Antigravity(アプリ・IDE と CLI)のどれにも使えます。

- 入っているコピーが、このスキルと確認できて、ソースと内容が違うときだけ、置き換えます。旧版は `.bak-<日時>` に残します。確認は `--uninstall` と同じで、`SKILL.md` の `name` などで行います。
- ソースと同じ内容(ファイルの有無と中身が一致)なら、何もしません(`up to date`)。バックアップも作りません。
- 何も入っていなければ、インストールします。
- このスキルと確認できないもの(別のスキルや、シンボリックリンク)は、置き換えません。`--force` なら、旧版を残して置き換えます。
- `--plugin` では、`plugin.json` の `version` が、このスクリプトの `PLUGIN_VERSION` と違うときも、更新の対象です。
- `--zip` では、ZIP を作り直すだけです(旧 ZIP は `.zip.bak-<日時>` に残ります)。`--uninstall` とは組み合わせられません。
- `--dry-run` で、実行内容だけを確認できます。

## `--zip`(Claude / Cowork)

`--zip` は、このスクリプトのアップロード前検査を通らないと作成を拒否します。検査するのは次の2点です。

- frontmatterで使えるキーは `name` / `description` / `license` / `compatibility` / `metadata` / `allowed-tools` の6種類。
- `description` は1〜200文字。

`--zip` はアップロード用のファイルを作るだけです。アップロード、アカウントでの有効化、APIへの登録は行いません。

Claude Codeの[公式の同期手順](https://code.claude.com/docs/en/skills#skills-synced-from-claudeai)では、claude.aiで有効なスキルを、サインイン方法・設定・版に応じて同期すると説明されています。このインストーラーによるローカル配置とは別の経路です。一方、[API向けの企業利用ガイド](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/enterprise#cross-surface-considerations)には製品間で同期しないとの記載があり、説明に差があります。本プロジェクトでは同期経由の実行は未確認です。

## `--plugin`(ChatGPT Work / Codex)

- `<dir>/official-prompt-tailor/` に、`.codex-plugin/plugin.json` と `skills/official-prompt-tailor/` を作ります。
- `--author` で、マニフェストの作者名を変えられます。既定は `official-prompt-tailor` です。
- マニフェストの形は、OpenAI 同梱の `plugin-creator` の検証ツールで確認しています。
- [公式のプラグイン作成・導入手順](https://developers.openai.com/plugins/build/plugins)では、ローカルのmarketplaceへ登録し、ChatGPTアプリのPlugins Directoryからインストールする方法が説明されています。このスクリプトはmarketplaceへの登録やアプリへのインストールは行いません。
- この出力は、同公式文書が互換形式として扱う `.codex-plugin/plugin.json` を使います。本プロジェクトでは、ChatGPT Workへの取り込みと実際のスキル実行は未確認です。

## `--uninstall`

- インストールしたコピーを削除する、インストーラー内で唯一のコマンドです。
- このスクリプトのコピーだと確認できたものだけを消します。確認は `SKILL.md` の `name` などで行います。確認できないものは、`--force` を付けても消しません。
- このスクリプトが入れていないファイルが混じるコピーは、残します。`--force` を付けると消せます。
- シンボリックリンクは、リンクだけを外します。
- `.bak-*` の退避、`.gitignore`、親のディレクトリは消しません。
- 何もインストールされていなければ、終了コード 1 です。
- `--zip` の ZIP は、手で削除してください。

## コマンドの参照資料

- [Git: git pull](https://git-scm.com/docs/git-pull): `--ff-only` は、履歴が分岐した場合に更新を止めます。
- [Microsoft: PowerShell 5.1の自動変数](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_automatic_variables?view=powershell-5.1): Windows例の `$?` と `$LASTEXITCODE` で、Gitの成功を確認します。

- [Microsoft: WindowsでのPython](https://learn.microsoft.com/en-us/windows/dev-environment/python): Pythonの導入方法によって `py` ランチャーがない構成があるため、Windows例は `python` を使います。
