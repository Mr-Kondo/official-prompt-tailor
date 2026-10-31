# 0002. 資料ごとに別形式 URL を持たせ、最初のページが取れないときに切り替える

- 状態: 採用(2026-10-31)
- 関連: Issue #61、ADR 0001 の決定7を実装する
- 置き換える決定: なし(ADR 0001 は有効。決定7の「見送り」を、この ADR で解く)

## 背景

ADR 0001 の時点では、全50資料が `allowed_urls` を1つしか持たず、`fetch_live` は先頭の URL だけを試した。同じページの `.md` 版がある資料(`code.claude.com` など)でも、HTML が取れない(403・DNS 失敗)と資料を読めなかった。

## 仮説と指標

- H1: 同じホストの別形式 URL を順に試せば、最初のページが取れない環境でも、同じ資料を読める。
- 指標: 最初のページが 403 でも、別形式で取得でき、`acquisition.json` の `requested_url`・`final_url`・`extraction_version` が実際に取れた組を示す。
- 撤退条件: 規則の再審査の手間が、資料1件につき二重になって増えすぎるなら、別形式を使うのをやめ、キャッシュと検索だけを残す。

## 決定

1. 資料に、省略できる `alternates`(`{url, extraction}` の配列)を足す。`url` は同じ資料の `allowed_urls` に含まれ、1つ目と同じホストの、新しい https のページ(レジストリの検証が拒否する)。`extraction` は既存の抽出方式。先頭の `allowed_urls[0]` と `extraction` が1つ目の組で、`alternates` が続く。`alternates` のない資料と既存の取得物は変わらない(移行不要)。
2. `fetch_live(..., alternates=True)` は、1つ目が `live` でなければ、組を順に試し、最初に読めた組の取得物を返す。どれも読めないときは、1つ目の組のエラーと取得物を返す。通信の回数は、組の数まで。`alternates=True` を渡すのは、利用者の run(`fetch`)だけ。`drift`・`rereview`・`adopt`・`propose` は、1つ目のページだけを見る(別形式の本文で、規則の本文ハッシュを書き換えないため)。
3. `verify` とキャッシュは、取得物の `requested_url` と `extraction_version` が `alternates` の組と一致するときに、その組の抽出方式で本文を作り直して確かめる。一致しない取得物は、従来どおり抽出方式の不一致になる。
4. 規則に、省略できる `alt_body_sha256`(配列)を足す。規則は、本文のハッシュが `source_body_sha256` と一致するか、別形式から読んだ本文のハッシュが `alt_body_sha256` と一致し、引用が本文にあるときだけ使える。`alt_body_sha256` は、取得物の `requested_url` が別形式のときだけ数える(`drift`・`rereview` は数えない。1つ目のページの変化を隠さないため)。取得物が別形式の URL に1つ目の抽出方式を組み合わせていると、`verify` は拒否する(組は登録された対のまま)。別形式の本文に引用がない規則には、ハッシュを書かない。その規則は、別形式を読んだ run で「再審査待ち」(`rereview_pending`)になり、`PASS_LIVE` にならない。
5. `tools/maintain.py alternates --source <id> [--url <URL> --extraction <方式>] [--write]` が、別形式の登録と `alt_body_sha256` の記録を行う。本文が2回の取得で一致すること(`observe`)と、規則の引用が本文にあることを機械で確かめる。意味の審査は、1つ目のページで済んでいる審査を引き継ぐ。別形式の本文で意味が変わっていないことの人の確認は、この ADR の範囲外とする(未確認事項)。

## 結果

- 登録: `claude-code-best-practices` に `https://code.claude.com/docs/en/best-practices.md`(`markdown-v1`)を追加した。実通信で、この本文は規則3件のうち `claude-code-verification` の引用を含み、他の2件(`claude-code-specific-context`・`claude-code-symptom-location`)の引用は含まなかった(HTML と Markdown で表記が違う)。含まない2件は、別形式を読んだ run では使われない。
- 再審査の手間: 別形式を足した資料1件につき、`alternates` コマンド1回。人の再審査の件数は増えない(1つ目のページの審査は変えない)。

## レビュー

Codex(gpt-6-sol、読み取り専用、2026-10-31)の指摘3件を採用した: 別ホストの別形式、別形式のハッシュによる1つ目のページの変化の見逃し、URL と抽出方式の組の偽造。いずれもテストを足した。Windows と Python 3.9 の実行はレビューでは未確認(CI と手元の 3.9・3.10・3.15 で確認)。

## 未解決

- 引用が見つからなかった規則を、別形式の本文に合わせて再審査するかどうか。
- 他の資料への展開(`.md` 版の有無を資料ごとに確かめる必要がある)。
