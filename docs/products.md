# 動かせる製品と制約

スキルを動かす製品(`input.json` の `execution_host`)と、プロンプトの利用先(`target_id`)は別です。置き場所は、各製品の公式資料(2026-10-14 時点)によります。

実機で動かしたのは Claude Code・Codex・Antigravity CLI です。Claude Cowork と ChatGPT Work は未確認です。Antigravity CLI の結果は [antigravity.md](antigravity.md) にあります。

| 動かす製品(`execution_host`) | 配置 | 呼び出し | 注意 |
|---|---|---|---|
| Claude Code(`claude-code`) | `install.py`(既定) | `/official-prompt-tailor` | 読取りの観測ができる唯一のホスト |
| Codex(`codex`) | `--target codex` | `$official-prompt-tailor` か `/skills` | コマンドはサンドボックスでネットワークが制限されます。公式ドメインへの接続の承認が必要です |
| Antigravity(`antigravity`) | `--target antigravity` / `antigravity-cli` | `/official-prompt-tailor` | Python とネットワークの可否は公式資料に記載がありません。ヘッドレス CLI(`agy -p`)では許可ルールが必要です([antigravity.md](antigravity.md)) |
| Claude Cowork(`claude-cowork`) | `--zip` で ZIP を作り、Customize > Skills > + > Upload a skill | 自動または指定 | コード実行の有効化が必要です。ネットワークは送信許可設定に従います |
| ChatGPT Work(`chatgpt-work`) | 単体スキルの配置先は資料にありません。`--plugin` でプラグインを作ります(取り込み方は未確認) | `@` で選択 | スクリプトの実行とネットワークの可否は公式資料で確認できていません |

## 制約

- 読取りの adapter があるのは、Claude Code・Codex・Antigravity です。Antigravity は agy 1.3.5・1.4.0 の観測形式を固定した実装で、版とハッシュを記した sidecar が必要です([antigravity-adapter.md](antigravity-adapter.md))。1.4.0で独立した通し検証を完了し、PASS_LIVE・CONSISTENT・audit 15/15 PASSを確認しました。他の製品では、`verify` が `no_log_adapter_for_host` で `INCOMPLETE` になります。これは仕様で、ログを推測や自己申告で補いません。参照検証は最良でも `INCOMPLETE` のままですが、取得・引用・適用範囲・変更の結合は検査されます。
- Codex の adapter(`codex-rollout-jsonl-v1`、Codex `cli_version` が `0.155.1`(`codex exec`)と `0.162.*`(Desktop)、[Issue #9](https://github.com/Mr-Kondo/official-prompt-tailor/issues/9))は、`~/.codex/sessions` の rollout JSONL を読みます。読取りに数えるのは、`exec` のスクリプトに `exec_command` が 1 つだけあり、そのコマンドが `cat` / `nl` / `head` / `tail` / `sed -n` / `bat` / `rtk read` の単純な 1 つで、本文のパスを名指すものです(`;` `&` `|` `<` `>` `$` を含むコマンド、複数コマンドのスクリプトは数えない。出力をコマンドに割り当てられないため)。出力は、本文の行と一致する連続した行だけを数え、切り詰めの印(`…N tokens truncated…`)の前後の行と、2 か所に当てはまる行は数えません。Windows 形式のパス(`\`)は読めず、数え落とします。生成の書き込みは、スクリプトに `optimized.md` / `recommendations.json` の名前があれば検出します。実際の `codex exec` 0.155.1 で本文を `cat` させた記録を読んで、見えた行(出力が途中で切られ、334 行のうち 51 行)だけが数えられることを確かめました。2026-10-17 には独立した Codex 0.155.1 でスキルの手順を通し、5資料228行の読取り、PASS_LIVE・CONSISTENT・audit PASS を確認しました([independent-runs.md](independent-runs.md))。意味審査・効果測定は未実施です。

Codex attribution also accepts exactly one leading `rtk proxy` prefix before the same plain read commands, for example `rtk proxy cat /absolute/path/body.txt`. The prefix is removed once; nested proxy wrappers, shell pipelines, compound commands and multi-file `cat` are not credited. A script containing `workdir` cannot establish a relative read; an absolute read remains eligible. Supported versions are exactly `0.155.1` or `0.162.<numeric patch>`, optionally followed by `-alpha.<number>[.<number>...]` (including `0.162.0-alpha.17.2`). Malformed relevant tool IDs, content or text are reported as `host_log_invalid`.

- 公式の文書の確認(2026-10-16): openai/codex リポジトリの `docs/` の一覧と、developers.openai.com の検索結果を確認した範囲では、rollout の形式を説明した文書は見つかりませんでした。全ファイルを調べた結果ではありません。確認できた公式の説明は `codex exec --json` の標準出力のイベント([Non-interactive mode](https://developers.openai.com/codex/noninteractive))だけで、これは `~/.codex/sessions` のファイルとは別の形式なので、読みません。
- Antigravity の adapter は `antigravity-transcript-jsonl-v1`、対応製品版は実ログで確認した `agy 1.3.5`・`1.4.0`です。実ランタイムの合成本文の読取りは確認済みですが、1.3.5の書込みは権限拒否でしたが、1.4.0では通常の専用ワークスペースで通し検証を完了しました([観測結果と制限](antigravity-adapter.md))。方針(2026-10-16 に決定): 既存調査の範囲では、Codex・Antigravity のセッションログの形式を公式に説明した文書は見つかっていません。文書が存在しないという判断ではありません。そのため、製品が公式に形式を示すのを待たず、**観測した形式を、版の名前をつけて固定して読む**ことを許します。条件は次のとおりです。
  - 形式の版の名前(例: `codex-rollout-jsonl-v1`)と、観測した製品の版の範囲を、コードとこの文書に書く。
  - 知らない形式・版は、推測せず `host_log_invalid` で止める。
  - 公式の説明が出たら、その URL をここに記し、固定した形式との差を確かめる。
  - adapter の完了条件は [Issue #8](https://github.com/Mr-Kondo/official-prompt-tailor/issues/8) にあります。
- `--zip` と `--plugin` の条件は、[install.md](install.md) にあります。
- 必要な接続先は、[README](../README.md#必要な接続先https) にあります。許可されないと、`fetch` は `blocked` になります。許可設定は、スキルから変更しません。
