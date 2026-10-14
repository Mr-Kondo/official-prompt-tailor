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
- 他の製品のログを読む adapter は、未実装です。製品が公式にログ形式を示したら、`LOG_ADAPTER_HOSTS` と adapter を足します。
- `--zip` と `--plugin` の条件は、[install.md](install.md) にあります。
- 必要な接続先は、[README](../README.md#必要な接続先https) にあります。許可されないと、`fetch` は `blocked` になります。許可設定は、スキルから変更しません。
