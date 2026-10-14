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
