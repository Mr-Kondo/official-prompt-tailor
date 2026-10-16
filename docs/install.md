# インストールの詳細

コマンドの一覧は [README](../README.md#インストール) にあります。ここでは、各オプションの動作を説明します。

## 共通の動作

- スキルのディレクトリをコピーするだけです。設定・hook・権限は変更しません。
- 既にある場合は止まります。`--update` か `--force` で置き換えると、旧版は `<skills の親>/official-prompt-tailor.bak-<日時>` に残ります(削除しません)。同じ秒に 2 回置き換えたときは、`-2`、`-3` を付けて、衝突を避けます。
- `--dry-run` で、実行内容だけを確認できます。
- コピー後に、コピー先のレジストリが読めるか自己検査します。失敗したら元に戻します。
- 終了コードは、引数の誤りが 2、インストールできない場合が 1 です。
- 既存のセッションには、新しいスキルは出ません。新しいセッションを開いて呼んでください。
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

`--zip` は、claude.ai の受け入れ条件を満たさないと作成を拒否します。条件は次の 2 つです。

- frontmatter は `name` / `description` など 6 キーのみ。
- description は 200 文字以内。

claude.ai に上げたスキルは、API や Claude Code には同期されません。

## `--plugin`(ChatGPT Work / Codex)

- `<dir>/official-prompt-tailor/` に、`.codex-plugin/plugin.json` と `skills/official-prompt-tailor/` を作ります。
- `--author` で、マニフェストの作者名を変えられます。既定は `official-prompt-tailor` です。
- マニフェストの形は、OpenAI 同梱の `plugin-creator` の検証ツールで確認しています。
- ChatGPT Work へのプラグインの取り込み方は、公式資料で確認できていません。

## `--uninstall`

- インストールしたコピーを削除する、唯一のコマンドです。
- このスクリプトのコピーだと確認できたものだけを消します。確認は `SKILL.md` の `name` などで行います。確認できないものは、`--force` を付けても消しません。
- このスクリプトが入れていないファイルが混じるコピーは、残します。`--force` を付けると消せます。
- シンボリックリンクは、リンクだけを外します。
- `.bak-*` の退避、`.gitignore`、親のディレクトリは消しません。
- 何もインストールされていなければ、終了コード 1 です。
- `--zip` の ZIP は、手で削除してください。
