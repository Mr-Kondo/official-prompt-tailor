# インストール・更新・削除の詳細

## Skills CLI で管理する

Skills CLI は取得元とスキルフォルダのハッシュをロックファイルに記録し、変更がある場合に再配置します。手動コピーや v0.9.3 までの `install.py`（v0.10.0 で廃止）で導入したものは登録されません。CLI 1.7.6（Node.js 22.20.0以上）で確認した手順です。Gitと、スキルの実行用にPython 3.9以上も必要です。

ユーザー配置の初回登録:

```sh
npx skills add Mr-Kondo/official-prompt-tailor --skill official-prompt-tailor --global --agent claude-code codex antigravity antigravity-cli --yes
```

使う製品だけを `--agent` に残せます。更新・一覧・削除:

```sh
npx skills update official-prompt-tailor --global
npx skills list --global
npx skills remove official-prompt-tailor --global
```

`npx skills update` だけの場合は、対象スキルとユーザー／プロジェクト配置を対話で選ぶ場合があります。上の更新例はこのスキルのユーザー配置に限定します。ブランチを省くと既定ブランチ（main）を取得します。公開リリースのタグだけに追従する手順ではありません。タグを指定した導入は、そのタグに固定され、新タグへ自動で乗り換えません。

### 配置先と管理の違い

CLIのユーザー配置の本体は `~/.agents/skills/official-prompt-tailor/` です。Claude Code向けは通常、そこへのシンボリックリンクを `~/.claude/skills/` に作ります。Codex・Antigravity・Antigravity CLI はCLIの共通配置を使います。CLIの配置成功と、各製品が実際に読み込むことは別です。とくに v0.9.3 までの `install.py` が使っていたAntigravity用 `~/.gemini/config/skills/` と `~/.gemini/antigravity-cli/skills/` は別の場所なので、そのコピーはCLIで更新されません。既存パスを使う環境では、退避後、そのスキルのパスだけを共通配置へのリンクにしてください。リンクにしないと、そのコピーは更新されず古い版のままです。

`--copy` を付けるとリンクではなくコピーします。更新時の製品選択は、その時に検出した環境から再決定されるので、初回に指定した全製品への再配置を常に保証するものではありません。更新後は `list` と実際の配置を確認します。

プロジェクト配置は、導入先プロジェクトのルートで初回登録から `--global` を外し、更新時に `--project` を指定します。`skills-lock.json` をそのプロジェクトで管理します。**この配布元リポジトリ自身でプロジェクト登録しないでください。配布用 `.claude/skills/official-prompt-tailor/` と衝突します。**

ユーザー配置のロックファイルは通常 `~/.agents/.skill-lock.json`（`XDG_STATE_HOME` 設定時はその下の `skills/.skill-lock.json`）です。CLIに生成・更新させ、手書きで登録しません。

### 既存コピーを Skills CLI へ移行する

1. 使っている配置先と、既存のロックファイルを確認します。上記の共通配置・Claude Code配置・Antigravityの既存パスに加え、プロジェクト配置や独自のパスも確認します。
2. 移行するスキルのコピーとロックファイルを別の場所へ退避します。ローカル変更があれば、取得元と比較して保持します。退避せずに `skills add --yes` を実行しないでください。CLIの置換には、自動の退避・復元機構はありません。
3. 上のGitHub取得元から初回登録します。ローカルパスからの登録では、GitHubの更新元を正しく記録できません。
4. `npx skills list --global` と `npx skills update official-prompt-tailor --global` を実行し、登録・更新を確認します。配置されたファイルと取得元のスキルフォルダを照合し、`VERSION` を確認します。各製品では新しいセッションを開いてください。
5. 既存のAntigravity用パスに互換リンクを置いた場合、そのリンクはCLI自身の管理外です。アンインストール時はCLIの `remove` 後にリンクだけを外します。退避を戻す場合も、ほかのスキルや変更されたロックをまとめて上書きしないでください。

macOS・Linuxでの退避例です。最初に退避先を作り、**実在する配置の行だけ**を実行します。コピーに失敗した場合は移行を止めます。`XDG_STATE_HOME` を設定している場合、最後の行の取得元を実際のロックの場所へ置き換えます。プロジェクト配置もあれば、別名で退避してください。

```sh
backup_dir="$(mktemp -d "$HOME/official-prompt-tailor-backup.XXXXXX")"
cp -pR "$HOME/.agents/skills/official-prompt-tailor" "$backup_dir/shared"
cp -pR "$HOME/.claude/skills/official-prompt-tailor" "$backup_dir/claude-code"
cp -pR "$HOME/.gemini/config/skills/official-prompt-tailor" "$backup_dir/antigravity"
cp -pR "$HOME/.gemini/antigravity-cli/skills/official-prompt-tailor" "$backup_dir/antigravity-cli"
cp -p "$HOME/.agents/.skill-lock.json" "$backup_dir/skill-lock.json"
```

Windowsではエクスプローラーなどで同じ対象を別のフォルダへコピーしてから登録します。上のシェル例はPowerShell用ではありません。

CLIの一次資料: [導入と更新](https://github.com/vercel-labs/skills/tree/v1.7.6)、[更新処理](https://github.com/vercel-labs/skills/blob/7a5e920bead4b1039a6d6df5d7e1163b7b09423a/src/update.ts)、[配置処理](https://github.com/vercel-labs/skills/blob/7a5e920bead4b1039a6d6df5d7e1163b7b09423a/src/installer.ts)。
