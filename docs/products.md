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

- 読取りの観測ができるのは、Claude Code だけです。他の製品では、`verify` が `no_log_adapter_for_host` で `INCOMPLETE` になります。これは仕様で、ログを推測や自己申告で補いません。参照検証は最良でも `INCOMPLETE` のままですが、取得・引用・適用範囲・変更の結合は検査されます。
- 他の製品のログを読む adapter は、未実装です。方針(2026-10-16 に決定): Codex・Antigravity のセッションログの形式を公式に説明した文書は、見つかっていません(第三者の解説と GitHub の議論だけ)。そのため、製品が公式に形式を示すのを待たず、**観測した形式を、版の名前をつけて固定して読む**ことを許します。条件は次のとおりです。
  - 形式の版の名前(例: `codex-session-jsonl-v1`)と、観測した製品の版の範囲を、コードとこの文書に書く。
  - 知らない形式・版は、推測せず `host_log_invalid` で止める。
  - 公式の説明が出たら、その URL をここに記し、固定した形式との差を確かめる。
  - adapter の完了条件は [Issue #8](https://github.com/Mr-Kondo/official-prompt-tailor/issues/8) にあります。
- `--zip` と `--plugin` の条件は、[install.md](install.md) にあります。
- 必要な接続先は、[README](../README.md#必要な接続先https) にあります。許可されないと、`fetch` は `blocked` になります。許可設定は、スキルから変更しません。
