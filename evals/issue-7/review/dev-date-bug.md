# dev-date-bug

Split: development

Status: pending-review

## Requirements

- 公開APIを変更しない
- npm test で確認する
- src/date.ts は推測の位置であり断定しない

## Rubric

- 不正な日付を拒否する
- 既存の呼出しとの互換性が保たれる
- 無関係な差分がない
- npm test が通る

## Source files

- [input](development/dev-date-bug/input.md)
- [original](development/dev-date-bug/original.md)
- [optimized](development/dev-date-bug/optimized.md)

Confirm requirements and rubric against all three files; record corrections and reviewer/date in human-review.json.
