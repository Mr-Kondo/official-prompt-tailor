# 動かせる製品と制約

スキルを動かす製品(`execution_host`)と、プロンプトの利用先(`target_id`)は別です。配置と呼出しの一覧は [README](../README.md#動かせる製品)、各インストールオプションは [install.md](install.md) を参照してください。

## 実行条件

- Claude Code・Codex CLI・Antigravity CLIには実機確認の記録があります。Antigravityアプリは評価票出力と通常フローの限定確認です。Codex Desktop・Claude Cowork・ChatGPT Workは別途未確認です。日付・版・検査結果は[検証履歴](history/runtime-verification.md#product-reexecution)に残しています。意味審査・効果測定の完了とは別です。
- スクリプトの実行と、[公式ドメインへのHTTPS接続](../README.md#必要な接続先https)が必要です。ネットワークが許可されないと `fetch` は `blocked` になります。スキルやインストーラーは許可設定を変更しません。
- Codex のコマンド実行は、利用環境のサンドボックス・ネットワーク制限に従います。
- Antigravity のヘッドレスCLIでは、その実行環境でコマンドを許可する必要があります。拒否された処理を、別のコマンドで回避しません。過去の許可設定と失敗例は[初期確認](history/runtime-verification.md#antigravity-initial)にあります。
- Claude Cowork はコード実行の有効化が必要です。ChatGPT Workへの[公式のプラグイン導入手順](install.md#--pluginchatgpt-work--codex)はありますが、この出力の取り込みと、スクリプト・ネットワークの実行可否は本プロジェクトで未確認です。

## 読取り観測

Claude Code・Codex・Antigravity の対応ログを解析します。対応する版・形式、Codexのコマンド制限、Antigravityのsidecarは [log-adapters.md](log-adapters.md) にまとめています。

ログがない場合は `no_host_log`、未対応の製品は `no_log_adapter_for_host` で、参照検証は `INCOMPLETE` になります。対応製品でも、ログの形式や読取りが条件を満たさなければ合格にはなりません。自己申告で読取りを補いません。
