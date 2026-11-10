# 0005. install.py と ZIP・プラグイン出力を廃止する

- 状態: 採用（2026-11-11、ユーザーが指定）
- 置き換え: [ADR 0004](0004-skills-cli-updates.md) のうち、既存インストーラとの併存、ZIP・プラグイン配布を引き続き使う部分。
- 関連: ADR 0001〜0003 のプロンプト最適化・証拠契約は変更しない。

## 仮説・要件調査

H1: `npx skills add` でユーザー配置・プロジェクト配置・更新・削除が足りるなら、独自のインストーラは利用者の手順を増やすだけで、保守コスト（約400行の本体、約460行のテスト、Windows向けの文書コマンド検査）に見合わない。

確認したこと:

- Skills CLI は Claude Code・Codex・Antigravity・Antigravity CLI への配置と更新に対応している（ADR 0004）。
- install.py だけが持っていた機能は、Claude Cowork 向けの ZIP 出力と、ChatGPT Work 向けのプラグイン出力だった。どちらも本プロジェクトでは実機未確認だった。
- リリースの版は install.py の `PLUGIN_VERSION` と配布フォルダの `VERSION` の二重管理だった。

## 決定

- install.py、その専用テスト、ZIP・プラグイン出力、ZIP の sha256 照合を削除する。
- 版の唯一の出どころを、スキルフォルダの `VERSION` にする。`release.yml` は、タグと `VERSION` の一致を確かめる。
- GitHub Release は、ノートだけを公開する（ZIP は付けない）。
- Windows 向けの文書コマンド検査は、`docs/feedback.md` の PowerShell だけを対象に残す。
- v0.9.3 までの `install.py` で導入した既存コピーは、Skills CLI に登録されない。移行手順は `docs/install.md` に残す。
- 退避・復元（`.bak-*`）、自己検査、上書き拒否は、install.py とともになくなる。Skills CLI の置換には自動の退避機構がない。

## 影響と撤退条件

- Claude Cowork と ChatGPT Work には、配置の手段がなくなる。両方とも実機未確認のままだった。必要になったら、v0.9.3 までのタグの `install.py --zip` / `--plugin` で作れる。需要が確認できたら、別の ADR で再検討する。
- 撤退条件: Skills CLI の更新で配置が壊れる、または対象製品が CLI の配置先に対応しなくなったときは、このADRを置き換える。

## 検証記録

- install.py 専用の28件のテストを除き、381件のunittestが成功した。
- 文書から `install.py`・`--zip`・`--plugin` の案内がなくなったことを、全文検索で確かめた（過去のリリースノート・履歴文書・ADR は記録として残す）。
