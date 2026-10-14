# Antigravity CLI での確認結果(2026-10-14)

`--target antigravity` でプロジェクトに配置したスキルを、`agy -p … --mode accept-edits` のヘッドレス実行で動かしました。`--sandbox` は付けていません。

## 許可ルール

ヘッドレス実行は、対話で承認できません。許可ルールにないコマンドは自動で拒否され、その時点で実行が終わります(出力の `denied_actions`)。

ルールは、グローバル設定 `~/.gemini/antigravity-cli/settings.json` の `permissions.allow` に、`command(<対象>)` の形で書きます。照合は、コマンド列の先頭からトークン単位で行われます。`command(find .)` は、`find .agents/…` に一致しませんでした。

このスキルの手順に使う設定は、次のとおりです。

```json
{"permissions":{"allow":[
  "command(python3 .agents/skills/official-prompt-tailor/scripts/evidence.py)",
  "command(python3 -I -)",
  "command(grep)",
  "command(ls)",
  "command(mkdir -p outputs/official-prompt-tailor)"
]}}
```

- 設定は、全プロジェクトに効きます。`python3 -I -` は、任意コードを実行できる権限です(SKILL.md の Python ブロック用)。
- `install.py` は、この設定を変更しません。`--dangerously-skip-permissions` は使っていません。
- エージェントが、絶対パスで `evidence.py` を呼ぶことがあります。その場合と、`~/.gemini/antigravity-cli/skills/` に入れた場合は、そのパスのルールが別に要ります。
- この最小設定だけでの完走は、確認していません。測定では、`python3 -c`、`find`、`cat` などを測定中だけ追加し、終了後に戻しました。
- 手順外のコマンド(`git status`、`python3 --version` など)を呼ぶと、拒否されて止まることがあります。

## 結果

条件は、原文「READMEの誤字を直して。内容は変えないで。」、利用先 `claude-code`、`execution_host: antigravity` です。

- `fetch`: `claude-code-best-practices` を live で取得しました(HTTP 200)。
- `verify`: 8 項目中 7 項目が PASS でした。`read_observation` だけが `UNAVAILABLE`(`no_log_adapter_for_host`)で、総合は `INCOMPLETE` です。検証まで進んだ 12 回の run のすべてで、同じ結果でした。
- 返答: 「参照検証 INCOMPLETE / 意味審査 NOT_REVIEWED / 効果 NOT_EVALUATED」の 3 行を出し、案は「候補」と明記しました。

## エージェントの挙動と SKILL.md の改善

初期の SKILL.md では、エージェントが `evidence.py` のソースを `python3 -c` などで読み回し、入力・出力の形を調べました。そのため、時間とトークンを使い、権限拒否で止まりやすくなりました。

次の 3 つを足しました。

1. `input.json` の完成例(SKILL.md)。
2. `recommendations.json` の完成例(`references/recommendations-example.md`)。
3. 「`evidence.py` は実行するだけで、ソースは読まない」の一文(SKILL.md)。

## 版の比較

同じ原文・同じプロンプトで、2 版を交互に実行して比べました(各 7 回)。権限拒否で最終回答の前に止まった回は、平均から除いています。

| 版 | 完走 | ソースを調べた回数 | 平均トークン | 平均所要時間 | `fetch` までの平均ステップ |
|---|---|---|---|---|---|
| v2(例 2 つ) | 6 / 7 | 7〜11 | 18.1 万 | 231 秒 | 24 |
| v3(v2 + ソースを読まない一文) | 5 / 7 | 0(7 回すべて) | 12.7 万 | 140 秒 | 13 |

## 結果の限界

- 測定は、1 つの原文、Antigravity CLI、`agy` の既定モデル(指定なし)での結果です。他のモデル・他の製品・他の原文には、当てはまるとは限りません。
- 統計的な確証ではありません。所要時間とソース調べの回数は、v2 と v3 で重なりません。トークンは分布が重なります(v3 の最大 17.6 万は、v2 の最小 16.0 万より大きい)。
- v3 は、権限拒否による 6 ステップでの早期停止が 2 回ありました(v2 は 0 回)。一文の影響かどうかは、この数では区別できません。
