# 取れない資料を補う

SKILL.md の手順2から来る。

`fetch` は、live で取れなかった資料を、同じ場所にある7日以内の前の run の取得物(`cache`)で自動的に補う。それでも `coverage.missing` が残る、または資料がまったく無いときは、次へ進む。

1. ホストに検索ツールがあるときだけ、製品名とモデル名だけで検索する(例: `Claude Code Sonnet 5.5 prompting guide`)。原文・機密・制約を検索語にしない。ツールがなければ検索せず、その旨を最終回答に書く。
2. 見つけた URL のうち、README に列挙した公式ドメインのものだけを、新しい run-id の `fetch ... --extra-source <URL>`(複数可)に渡す。他のドメインは取得せず、最終回答に候補として報告する。
3. 取れたページは `<run>/discovered-sources.json`(`discovered-N`)と `snapshots/` にあり、ファイルを読むツールで読む。ここから作る変更は `kind: candidate`(`source_id`・`body_sha256`・行範囲・`exact_excerpt` が必須、`rule_id` なし)で記録する。審査済みの規則がないので `official` にはしない。参照検証は `INCOMPLETE`(`candidate_source_unreviewed`)で、最終回答に「公式候補(未審査)」と書く。
4. 次回から使えるよう登録するのはメンテナーの作業で、`python3 tools/maintain.py intake --run <run>` が Issue の本文を作る。スキルは `registry.json` を書き換えない。
