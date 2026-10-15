# recommendations.json の完成例

SKILL.md「3. 本文を読み、最適化する」から参照する。項目の定義は `scripts/evidence.py` の `REC_F` と `CHG_F` が正。

原文 `READMEの誤字を直して。内容は変えないで。` を、次の `optimized.md` に改稿した場合。

```text
READMEの誤字を修正してください。
制約: 内容は変えないでください。
```

```json
{
  "schema_version": 1,
  "target_id": "claude-code",
  "target_surface": "unknown",
  "model_id": "unknown",
  "generation_status": "complete",
  "original_sha256": "<original.md の SHA-256>",
  "optimized_sha256": "<optimized.md の SHA-256>",
  "changes": [
    {
      "change_id": "c1",
      "kind": "editorial",
      "rule_id": null,
      "source_id": null,
      "body_sha256": null,
      "source_start_line": null,
      "source_end_line": null,
      "exact_excerpt": null,
      "before_start": 10,
      "before_end": 14,
      "after_start": 10,
      "after_end": 19,
      "after_excerpt": "修正してください。",
      "rationale": "依頼の文末を丁寧な形にそろえた"
    },
    {
      "change_id": "c2",
      "kind": "official",
      "rule_id": "claude-code-specific-context",
      "source_id": "claude-code-best-practices",
      "body_sha256": "<acquisition.json の body_sha256>",
      "source_start_line": 59,
      "source_end_line": 59,
      "exact_excerpt": "mention constraints",
      "before_start": 14,
      "before_end": 23,
      "after_start": 19,
      "after_end": 37,
      "after_excerpt": "\n制約: 内容は変えないでください。",
      "rationale": "原文の制約を「制約」として分けて明示した"
    }
  ]
}
```

- `<…>` の 3 か所は、その run の実測値に置き換える。SHA-256 は SKILL.md の Python、`body_sha256` は `acquisition.json` の値。
- offset と行番号は例の値を使わない。offset は Python で計算し、行番号と `exact_excerpt` は `body.txt` を `grep -n` して決める。
- `kind` が `editorial` の change は、出典の 6 項目(`rule_id` から `exact_excerpt` まで)をすべて `null` にする。変更前後の offset は `official` と同じく必須。

書くときの注意(独立した実行で、迷いや `verify` の失敗が出た点)。

- 原文と改稿の差分は、**すべての区間**を、change の `before_*` / `after_*` の区間で覆う。`verify` は、正規化した原文と改稿を `difflib.SequenceMatcher(None, 原文, 改稿, autojunk=False)` で比べ、`equal` 以外の区間(`get_opcodes()`)のそれぞれが、change の区間の和に入っているかを見る。同じ関数で先に確かめられる。段落を分ける空行だけの追加も、差分の区間になる。覆われていない区間は、`verify` が `unmapped_change` で止める(メッセージの `original[a:b] -> optimized[c:d]` が、覆われていない区間)。change 同士の区間(before 同士、after 同士)は重ねない。
- `after_excerpt` は、正規化した `optimized.md` の `after_start`〜`after_end` の区間と**完全に一致**させる(一部だけの抜粋だと、`verify` が `after_excerpt_mismatch` で止める)。`exact_excerpt`(資料の短い引用)とは別物。変更前の区間が空の追記なら、追記した文の全体になる。
- 追記だけの change(原文は何も消えない)は、`before_start` と `before_end` を同じ位置にした空の区間でも通る。ただし文字単位の差分は内容で整列し、同じ文字が繰り返されるとずれる。挿入位置の前後の既存の文を `before` に含めて、その文ごとの置換として記録すると確実。`find` で位置が 1 つに決まらないときは、前後の文脈ごと探す。
- `source_start_line`〜`source_end_line` は、規則の `source_excerpt` を含む行を含める。含まないと、`verify` が `lines_not_rule_evidence` で止める。`exact_excerpt` は、その行範囲にある短い引用で、change を直接支える部分でよい。
- 1 つの change に結べる規則は 1 つ。2 つ以上の規則で支えられるときは、その change により直接つながるほうを選び、選ばなかった規則は最終回答の「使わなかった規則」に書く。
