# 規則・資料のメンテナンスと、審査・効果測定

## 規則・資料の再確認方法

公式資料は変わります。`registry.json` の URL・locator・規則を、恒久仕様と扱わないでください。

1. `fetch --mode live` で取得し、`snapshots/<id>/body.txt` を読む。`blocked` なら、エラー(`locators_missing:…` など)を見て、ページ構造が変わったか確認する。
2. 規則を再審査する。
   - `source_excerpt` が本文に残り、意味・適用範囲(`surfaces` / `models` / `purposes`)が変わっていないかを確認する。
   - `source_body_sha256`(= `acquisition.json` の `body_sha256`)、`reviewed_at`、`reviewer_id` を更新する。
   - 本文が変わると、旧審査は引き継がれず `rereview_pending` になります。
   - 旧本文との差分で、変わった所が引用の前後に及ぶかを確かめる。これは `tools/maintain.py rereview` が行います([再審査する](#再審査するrereview))。旧本文は、記録した `source_body_sha256` と同じ SHA-256 の本文です。見つからないときは、新しい本文で引用の前後を読んで判断します。資料の全文はコミットしません(著作権)。
   - 見出しが変わって `locators_missing` になったときは、`body_locators` を新しい見出しに直します(2026-10-16 の `antigravity-cli-best-practices` が、この例です)。
3. 実行中に、allowlist・scope・規則を緩めて合格させない。registry の SHA-256 は acquisition と verification に記録され、取得後に registry が変わると `FAIL` です。
4. 規則の審査は、これまですべて実行主体(Claude Code, claude-sonnet-5-5)が、取得本文と照合して行いました。初期の 34 件は実装時、追加の 70 件(2026-10-15 に、候補 59 件のうち 31 資料から採用した分)は採用時です。このうち 11 件は、資料の本文が変わったため、2026-10-16 に再審査しました(`reviewer_id` は `agent re-review`。旧本文との差分で、引用とその前後が変わっていないことを確かめました)。人による審査ではありません(`reviewer_id` に明記)。人の審査は、`tools/maintain.py review` が `human:<名前>` として記録します([人が審査する](#人が審査するreview))。`rules` と `sources` は、`human:` で始まる `reviewer_id` だけを人の審査として数えます(`human:` のあとが空だと、registry の検査が拒否します)。`tests/test_official_prompt_tailor.py` の `test_no_rule_claims_a_human_review_that_nobody_has_made` は、エージェントが自分で人の審査を書くのを防ぐ番人です。`human:` の規則は、`tools/human-reviews.json` に同じ審査の記録があるときだけ通ります。

### 移転の確認(2026-10-14)

- ADK の旧 URL `google.github.io/adk-docs/…` は、`adk.dev/agents/llm-agents/` へ 301 で移転しました。
- Gemini の旧 `answer/16596215` は、`answer/17094507` へ 301 で移転しました(クエリに、変動する `visit_id` が付きます)。

いずれの旧 URL も登録せず、`fetch` は新 URL から始めます。登録外への移転は、取得前に停止し、`blocked`(`redirect_not_registered:…`)として報告します。

## 資料の更新と追加を取り込む

更新の確認と、新しいページの発見は、自動化されています(毎週の GitHub Actions でも動きます)。`drift` と `discover` は、人が見るべきものを挙げるだけで、registry を書き換えません。資料と規則の追加は、審査者が spec(どのページの、どの引用を、どの規則にするか)を書き、`adopt` が registry に書き込みます。どの記述が規則になり、何を意味するかを決める審査は、審査者の仕事です。

### 更新を確かめる

`drift` は、資料の本文が、規則の審査後に変わったかを調べます。比べるのはハッシュと引用の有無だけで、変更が意味を変えたかは判定しません。

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py drift
```

- 引数なしだと、登録されたすべての資料を取得して、規則に記録したハッシュと比べます(通信します)。`--source <source_id>` で、資料を絞れます。
- `--run <run>` は、その run の `acquisition.json` と `snapshots/` を使います(通信しません)。`fetch` のあと、規則を選ぶ前に実行します。
- 引数なしでは、`changed`・`excerpt_missing` になった資料と、取得できなかった資料を、2 秒待って 1 回だけ取得し直し、2 回目の結果を採ります。配信の切り替え中の一時的な差異や、通信エラーを、報告しないためです。

規則ごとの `state` は、次のとおりです。

| state | 意味 | 対応 |
|---|---|---|
| `current` | 本文のハッシュが、審査時と同じ | なし |
| `changed` | ハッシュが違い、引用は本文に残っている | `rereview` で旧本文と比べ、再審査する |
| `excerpt_missing` | 引用が本文から消えた | 先に再審査する |
| `unreviewed` | 未審査の規則 | 審査する |
| `unverifiable` | 資料を取得できなかった | `error` を見て、再実行するか、人がブラウザで確かめる |

`verdict` は、`changed` か `excerpt_missing` があれば `DRIFT` です。なければ、取得できない資料があるとき `INCOMPLETE`、それ以外は `CURRENT` です。終了コードは、`CURRENT` が 0、`DRIFT` と `INCOMPLETE` が 2、契約違反(`--run` の本文がハッシュと合わないなど)が 1 です。

`changed` と `excerpt_missing` の規則は、「規則・資料の再確認方法」の手順 2 以降で再審査します。`rereview` が、この作業の機械的な部分を行います。

「Updated 3 days ago」のような相対時刻の行は、本文から除かれます。記事が更新されなくても、毎日変わるためです。「Last updated: …」のような絶対日付は、ページが更新されたときだけ変わるので、本文に残ります。

### 再審査する(`rereview`)

毎週の `watch` が `DRIFT` を出すたびに、同じ作業が要ります。変わった資料の旧本文と新本文を比べ、規則の引用とその前後が変わっていないかを確かめる作業です。`rereview` が、その比較をします。

```bash
python3 tools/maintain.py rereview                      # 確認だけ。registry は書き換えない
python3 tools/maintain.py rereview --write              # 引用とその前後が同じ規則だけ、審査を更新する
python3 tools/maintain.py rereview --source <source_id> --rule <rule_id>   # 絞る(繰り返せる)
python3 tools/maintain.py rereview --run outputs/official-prompt-tailor/<run-id>   # 通信せず、その run の本文で比べる
```

`drift` と同じように資料を取得し(変わったように見える資料は、2 秒待ってもう一度見ます)、`changed` と `excerpt_missing` の規則ごとに、旧本文と新本文の引用の位置を比べます。`class` は次のとおりです。

| `class` | 意味 | `--write` |
|---|---|---|
| `context_unchanged` | 引用、その前後 5 行(`--context`)、直上の見出し、引用の出現回数が、旧本文と新本文で同じ。資料は別の所が変わった | 審査を更新する |
| `section_risk` | 引用の前後は同じだが、引用がある節に、「deprecated」「no longer」「非推奨」などの語を含む行が増えた(`added` に出る)。窓の外の変更を見落とさないための検査 | 更新しない。`added` を読んで、`--accept` で更新する |
| `context_changed` | 前後のどれかが変わった。`diff` に差分を出す | 更新しない。差分を読んで、`--accept <rule_id> --reviewer "…; not human"` で更新するか、規則を直す |
| `old_body_unavailable` | 旧本文が見つからない | 更新しない(同上) |
| `excerpt_missing` | 引用が本文から消えた | 更新できない。規則の引用を直す(`--accept` でも拒否します) |

更新するのは、`source_body_sha256`・`reviewed_at`・`reviewer_id` の 3 項目だけです。規則の意味と適用範囲は変えません。`reviewer_id` は、既定で `tools/maintain.py rereview (excerpt and surrounding lines unchanged; mechanical; not human)` です。`--reviewer` で言い換えられますが、`human:` で始まるもの、または `not human` を含まないものは拒否します。再審査は人の審査を書きません。

- 出力の `sources[].changes` に、資料全体の差分(変わった行の範囲と、先頭の数行)が出ます。引用から遠い所の変更も、ここで読めます。
- `verdict`: `CURRENT`(変わった規則がない)/ `READY_TO_STAMP`(すべて `context_unchanged`。`--write` で更新できる)/ `NEEDS_REVIEW`(人が読む規則が残っている)/ `STAMPED`(`--write` で更新した)/ `INCOMPLETE`(取得できない資料がある)。終了コードは、`CURRENT` と `STAMPED` が 0、それ以外が 2 です。
- 比べるのは、引用の前後の行だけです。窓の外にある、規則の意味を変える記述(「この節は非推奨」など)は見ません。`sources[].changes` を読んで、窓の外の変更も確かめてください。`drift` と同じく、変更が意味を変えたかは判定しません。
- 人が審査した規則(`human:`)は、本文が変わって再審査すると、機械の審査に置き換わります。人が読んだのは、変わる前の本文だからです。

#### 旧本文の残し方

比べるには旧本文が要りますが、資料の全文はコミットしません(著作権)。次のように決めました。

- `rereview` は、規則が審査された本文(規則に記録したハッシュと同じハッシュの、いまの本文)を、`outputs/reviewed-bodies/<source_id>/<sha256>.txt` に残します。`outputs/` は未追跡(`.gitignore`)です。変わっていない間に実行するだけで、残ります(`--write` は要りません)。
- 旧本文は、次の順に探します。残した本文、`outputs/` の過去の run(`snapshots/<source_id>/body.txt`)、`--old <ディレクトリ>`(繰り返せる)。見つかった本文でも、SHA-256 が規則に記録したものと違えば、旧本文として使いません。
- 残るのは、その clone の中だけです。新しい clone や別の機械には、ありません。毎週の `watch` は、`tools/maintain.py keep` で、規則が審査された本文を GitHub Actions のキャッシュに残します([自動実行](#自動実行github-actions))。キャッシュは、Actions の外からは取り出せません。`watch` のキャッシュにある本文を手元で使うには、手元で `rereview` を実行して残すか、`--old` で渡します。旧本文がないときは `old_body_unavailable` になり、新しい本文で引用の前後を読んで判断します。
- `keep` は、各資料の本文が、規則に記録したハッシュと同じ間に、その本文を残します。規則のどれも参照しなくなった本文(再審査で規則のハッシュが新しくなったあとの、古い本文)は消します。`--source` で絞ったときは消しません。ページが変わった後は、残した本文を上書きしません。旧本文を持つ資料が無いときは、`PARTIAL`(終了コード 2)と、その資料を報告します。
- 手元で本文が変わる前に、一度 `rereview`(または `keep`)を実行しておくと、次の変更を比べられます。登録済みの規則の旧本文は、`keep` を最初に実行した時点より前のものは、どこにも残っていません(過去の run があれば、そこから探せます)。
- 残した本文を消しても、規則や registry は壊れません。比べられなくなるだけです。

### 新しい資料を見つける

`tools/maintain.py discover` は、各公式サイトの `llms.txt`(ページの一覧)か、`llms.txt` のないサイトの `sitemap.xml` を読みます。キーワードに合い、まだ登録も既読にもしていないページを挙げます。スキルには同梱しない、保守用のツールです。

```bash
python3 tools/maintain.py discover
```

- 一覧とキーワードは `tools/discovery.json` にあります。一覧ごとのキーは、`index_id`、`url`(`llms.txt` か `sitemap.xml` の URL)、`scope`(見るパスの先頭)です。`keywords` と `exclude`(正規表現)は任意で、絞り込みに使います。`format` は `llms`(既定)か `sitemap` です。
- `www.`、`.md`、`/index`、末尾の `/`、クエリ、http と https の違いは、同じページとして扱います。他のホストへのリンクは数えません。
- 取得できなかった一覧は、2 秒待って 1 回だけ取得し直し、2 回目の結果を採ります(`drift` と同じです)。通信エラーや、配信の切り替え中の応答で、その一覧の候補が報告から消えないようにするためです。
- 見送るページは、`--mark-seen` で既読にして、`tools/discovery-seen.json` をコミットします。採用するページは、先に登録するか、次の「登録案を作る」へ進みます。
- 採用するのは、本文に、規則にできる記述(引用できる、指示の書き方についての助言)があるページだけです。テストは、すべての資料が配線され、`reviewed` の規則を 1 件以上持つことを求めます。規則のない資料は、`drift` でも変更を検知できません(`drift` が比べるのは、規則に記録したハッシュです)。管理・安全・構成・操作手順の説明だけのページは、見送ります。2026-10-15 の候補 59 件は、`llms.txt` のキーワードに合っただけのページを多く含んでいました。
- `verdict` は、`CANDIDATES`、`INCOMPLETE`、`NONE` のいずれかです。候補が 1 件でもあれば、取得できない一覧があっても `CANDIDATES` です(取得できない一覧は、`indexes` の `status` と `error` に出ます)。候補がなく、取得できない一覧があるときが `INCOMPLETE` で、どちらもなければ `NONE` です。終了コードは、`NONE` が 0、それ以外が 2 です。

#### sitemap の一覧(`"format": "sitemap"`)

`developers.openai.com` と `ai.google.dev` には `llms.txt` がないので、`sitemap.xml` から新しいページを探します。

- `<urlset>` なら、その `<loc>` を、`<sitemapindex>` なら、`<loc>` が指す子の sitemap(同じホストの https のものだけ、最大 30 件)を読んで、`<loc>` を集めます。ほかのホストの子は読まず、`skipped` に残します。子の中の索引は、たどりません。
- sitemap にはページの題がないので、パスの最後の部分(`prompt-engineering` → `prompt engineering`)を題の代わりにします。`keywords`・`exclude` は、題とパスに当てます。同じページが言語ごとに `?hl=de` のようなクエリ付きで並ぶサイトでは、クエリを除いた同じページを 1 件にまとめ、クエリのない住所があればそれを使います。
- XML は構文解析せず、`<loc>` の中身だけを取り出します。DOCTYPE や実体参照は展開しません。内容の種類は `application/xml`・`text/xml`・`text/plain`、文字コードは UTF-8 だけです。gzip は読めません。1 つの応答は 40 MB までです(`SITEMAP_MAX_BYTES`)。ページの 5 MB より大きいのは、`ai.google.dev` の sitemap が 18.7 MB あるためで、この上限は sitemap を読む間だけ使います。
- 読めなかった子がある一覧は、`status` が `partial` で、`error` に子の URL と理由が出ます。読めたページの候補は、そのまま出ます。候補がなく、`partial` か `blocked` の一覧があるときは `INCOMPLETE` です。
- **403 などの拒否は、そのまま報告します。** 取得し直すのは、`llms.txt` と同じく 1 回だけで、同じ URL に同じ方法です。ボット検査を避ける工夫はしません。拒否される時間帯は、`INCOMPLETE` のまま残ります。
- 2026-10-15 に、GitHub Actions の `watch` を手動実行して、実機で確かめました。`developers.openai.com` の sitemap は `robots.txt` が示す `/sitemap-index.xml` で(`/sitemap.xml` は 404)、`ai.google.dev` の子 sitemap は 18.7 MB、6,900 件のうち 4,541 件が `/gemini-api/docs/` 以下でした(大半が `?hl=` の言語違い)。登録の URL と上限は、これに合わせて直しました。`platform.claude.com` と `code.claude.com` の `sitemap.xml` では、読取りを確かめました(それぞれ 3,937 件、2,713 件)。

### 登録案を作る

`propose` は、候補の URL を取得して、`sources` に足せる形に変えます。

```bash
python3 tools/maintain.py propose --url https://code.claude.com/docs/en/memory.md --target claude-code
```

- `extraction` は、HTML 系の 3 種と `markdown-v1` を順に試して、最初に通るものを選びます(`markdown-v1` が通ったとき、署名付き画像があって本文が変わるなら、`markdown-v2` にします)。
- `body_locators` は、本文の見出しから 2 件を選びます。選んだ組で、`fetch` や `verify` と同じ検査を通します。
- 2 回取得して、本文のハッシュが同じかも確かめます(`stable`)。違うと終了コード 2 です。時刻や乱数のような、毎回変わる語が本文に入っています。
- `--source-id`(省略すると URL から作ります)、`--surface`(繰り返せます)、`--category` を指定できます。
- 取得できるのは、registry か `tools/discovery.json` に既にあるホストの、https のページだけです。登録済みのページや、未知の `--target` は、契約違反(終了コード 1)です。

出力の `source` を、`sources` に貼ります。`targets` への配線、`rules` の追加、審査は、次の「新しい資料を足す」の手順 3 以降です。

### 人が審査する(`review`)

規則 104 件の審査は、すべて agent が行い、人の審査は 0 件でした。`review` は、人が規則を 1 件ずつ審査して、記録するための道具です。

```bash
python3 tools/maintain.py review                          # 人がまだ審査していない規則を、順に出す
python3 tools/maintain.py review --rule <rule_id> --source <source_id> --limit 10
```

- **対話端末でだけ動きます。** 標準入力と標準出力が端末で、環境変数 `CI`・`GITHUB_ACTIONS`・`CLAUDECODE`・`CLAUDE_CODE_ENTRYPOINT` のどれも設定されていないときだけです。スクリプトやエージェントのツール呼出し、CI では、何も書かずに終了コード 2 で終わります。これは、プロセスの起動のされ方の検査です。擬似端末(pty)を使うプログラムが、人の代わりに入力することは、防げません(独立レビューで再現しました)。
- 規則ごとに、資料の URL、対象、使える前提、使えない場合、意味、許される変更、引用、いまの本文の該当行(前後 5 行)を出します。本文が審査したときと変わっていれば、そう表示します。引用が本文にないときは、記録できません。
- 「審査した人の名前」を入力した規則にだけ、`reviewer_id` を `human:<名前>` として記録します(確認で `y`)。空は飛ばし、`q` は終了です。1 件ずつ書き込むので、途中で止めても、記録済みの分は残ります。
- 記録するのは、`source_body_sha256`(見せた本文のハッシュ)、`reviewed_at`、`reviewer_id` と、`tools/human-reviews.json` への 1 行です。この記録がない `human:` の規則は、テスト(`test_no_rule_claims_a_human_review_that_nobody_has_made`)が拒否します。エージェントが `registry.json` を直接書き換えて、人の審査を名乗るのを防ぐためです。
- 入力した名前が本人かどうかは、確かめられません。端末で入力されたことを、記録しているだけです。コミットの作者と合わせて読んでください。
- 台帳(`tools/human-reviews.json`)は署名のない平文で、registry と一緒に書き換えれば、テストを通せます。台帳を先に書き、registry を書けなかったときは台帳の追記を取り消します。`adopt` は、spec の `reviewer_id` が `human:` で始まるものを拒否します(人の審査を記録するのは `review` だけです)。
- registry を書く直前に、読んだときのバイト列と比べます。人が入力している間などに registry が変わっていたら、何も書かずに止まります(`adopt`・`rereview --write`・`review` に共通)。
- 人の審査を記録したら、`python3 tools/maintain.py docs --write` で、README と `docs/trust-boundary.md` の件数を直します。

### 新しい資料を足す(`adopt`)

新しい公式ページ(新しい製品やモデルのガイドなど)を使えるようにする手順です。審査者が spec を書き、`adopt` が registry に書き込みます。

1. 「登録案を作る」の `propose` で、ページの `source`(`extraction` と `body_locators` を含みます)を作ります。
2. ページを読み、規則にできる記述(指示の書き方についての助言)を選んで、spec を書きます。`sources` の各要素は、`propose` の `source` のキーに、`rules` を加えたものです。
3. `adopt` を、`--write` なしで実行します(登録案の確認だけで、何も書きません)。問題がなければ、`--write` を付けて書き込みます。
4. `docs --write` で、README と `docs/targets.md` の件数と表を直します。テストを実行します。

```bash
python3 tools/maintain.py adopt --spec spec.json            # 確認だけ
python3 tools/maintain.py adopt --spec spec.json --write    # registry.json に書く
python3 tools/maintain.py docs --write
```

```json
{
  "schema_version": 1,
  "reviewer_id": "claude-code/claude-sonnet-5-5 (agent review at adoption; not human)",
  "sources": [{
    "source_id": "claude-prompting-example", "allowed_urls": ["https://platform.claude.com/docs/en/<page>.md"],
    "target_ids": ["claude", "claude-cowork", "claude-code"], "surfaces": ["api"], "models": ["claude-example-5"],
    "category": "api-prompting", "extraction": "markdown-v1", "body_locators": ["<見出し 1>", "<見出し 2>"],
    "rules": [{
      "rule_id": "claude-api-example-rule", "target_ids": ["claude", "claude-cowork", "claude-code"], "surfaces": ["api"],
      "models": ["claude-example-5"], "purposes": ["one-shot", "agent-instruction"],
      "preconditions": ["規則を使える条件"], "exclusions": ["使ってはいけない場合"],
      "source_excerpt": "本文にそのまま現れる引用(400 文字以内)", "meaning": "規則の意味", "allowed_change": "許される変更"
    }]
  }]
}
```

- `reviewer_id` は、審査した主体です。エージェントなら `…; not human` と明記し、人なら `human:<名前か役割>` にします。`rules` と `sources` は、`human:` で始まるものだけを人の審査として数えます。
- `adopt` が確かめることは、次のとおりです。どれか 1 つでも違えば、何も書かずに終わります(契約違反は終了コード 1、本文を安定して取得できないときは 2)。
  - `registry.json` が、`dump_registry` の書式(1 行に 1 レコード)であること。
  - 取得先が https で、registry か `tools/discovery.json` が使うホストにあり、登録済みのページでも `source_id` でもないこと。
  - 本文が、2 回の取得で一致すること(最大 6 回まで取り直します。失敗した取得は数えません)。
  - 各規則の `source_excerpt` が、取得した本文にそのまま現れ、400 文字以内であること。規則の `target_ids` が資料の範囲内であること。
  - API 限定の資料の規則は、`surfaces` が `["api"]` であること。モデル限定の資料の規則は、`models` がその資料の `models` に含まれること。ほかの検査(`registry_problems`)も通ること。
  - 経路と規則が合っていること(`registry_problems`)。利用先 × `purpose` × `target_surface` × `model_id` の全入力(約 1,200 通り)について、選ばれた資料に、その入力で使える規則が 1 件以上あること。どの入力でも使えない規則がないこと。`verify` は、選ばれた資料のすべてを読んだことを求めるので、規則のない入力で資料を取得すると、読むだけで終わります。
- `adopt` が書くのは、`source_body_sha256`(取得した本文)、`reviewed_at`、`reviewer_id`、`review_status: reviewed`、`source_locator`(= 引用)と、`targets` への経路です。経路は、規則の `purposes` の和集合に対してだけ作ります。規則のない `purpose` では、その資料は取得されません。規則がすべて特定のモデルに限られる資料は、`models` にもそのモデルを書きます(書かないと、ほかのモデルでも取得され、`registry_problems` が拒否します)。`registry_problems` がエラーを出したら、spec の `purposes`・`surfaces`・`models` を、資料が実際に使われる範囲に直します。
- Claude Code のサンドボックスは、`.claude/skills` の下に書けません。`--write` が `Operation not permitted` で止まるときは、ターミナルで実行します。

#### 手で足す場合

`adopt` が使えないときの手順です。`references/registry.json` を直接編集します。書式を崩すと、`adopt` が使えなくなります(`dump_registry` が、現在のファイルをそのまま再現できる必要があります)。

1. 公式のドメインにあり、https で、ログインなしに読めるページか確かめます。
2. `sources` に追加します。「登録案を作る」の `source` を貼るか、次のキーで書きます。すべて必須で、未知のキーは拒否されます。
   - `source_id`: 英数字・`.`・`_`・`-` で、64 文字以内。
   - `allowed_urls`: https の URL。ホスト・パス・クエリが完全一致で照合され、`#` は使えません。`fetch` は先頭の URL から取得し、リダイレクト先は、いずれかの URL と一致するときだけ許可されます。
   - `target_ids`・`surfaces`・`models`: 資料が当てはまる範囲。限定しないときは `["all"]`。特定のモデルのガイドは、`models` にそのモデルの ID(例: `["claude-opus-5-5"]`)を書きます。この資料は、`model_id` がそのモデルのときだけ取得されます。その資料の規則の `models` も、これに含まれるものだけにします(テストが確かめます)。
   - `category`: 既存に合わせて `product-prompting` / `product-feature` / `api-prompting`。
   - `extraction`: 本文の取り出し方。次のいずれかです。
     - `html-main-v1`: 最初の `<main>`
     - `html-article-v1`: 最初の `<article>`
     - `html-body-v1`: `<body>`
     - `markdown-v1`: Markdown かプレーンテキスト
     - `markdown-v2`: `markdown-v1` から、署名付き画像のクエリ(`?expires=…&signature=…`)を除いたもの。`support.claude.com` は、記事の画像に署名を付け、1 時間ごとに付け直します(2026-10-16 に `claude-cowork-projects` で確認)。`markdown-v1` では、同じ記事でも本文のハッシュが 1 時間ごとに変わり、`drift` が `changed` を出し続けます。`propose` は、署名付き画像のある Markdown に、自動で `markdown-v2` を選びます。
   - `body_locators`: 本文に必ず含まれる見出しなどの文字列を、2 件以上。見つからないと、`fetch` は `locators_missing` で `blocked` にします。
3. `targets` の `required_sources` か `conditional_sources` に、`source_id` を加えます。`required_sources` は常に取得され、`conditional_sources` は `purposes` と `surfaces` が合うときだけ取得されます。規則のある範囲にだけ経路を作ります。`required_sources` に置けるのは、その利用先のすべての `purpose` に使える規則を持つ資料だけです(`registry_problems` が確かめます)。
4. `fetch` で取得します。`blocked` にならないこと、`snapshots/<id>/body.txt` が本文だけになっていることを確かめます。
5. `rules` に規則を足します。既存の規則をひな形にし、全キーを埋めます。
   - 最初は `review_status` を `unreviewed` にします。`source_body_sha256`・`reviewed_at`・`reviewer_id` は `null` です。
   - `source_excerpt` は、本文にそのまま現れる短い引用にします。
   - 本文と照合して審査したら、`"reviewed"` に変えて、3 項目を埋めます。`source_body_sha256` は、手順 4 の `acquisition.json` の `body_sha256` です。
   - `unreviewed` の規則を根拠に引用すると、`verify` は `FAIL` です。テストは、registry の規則がすべて `reviewed` であることを求めるので、コミットの前に審査します。
6. 新しいドメインがあれば、README の「必要な接続先」に足します。
7. テストを実行します。

### 新しい利用先を足す

利用先(`target_id`)の追加は、registry だけでは済みません。`evidence.py` の `TARGET_IDS` にも足します。registry は、`TARGET_IDS` のすべてを `targets` に定義する決まりだからです。SKILL.md の対象の表、README、[targets.md](targets.md) も更新します。`tools/maintain.py` の `ROUTING_LABELS` と `VENDORS` にも足してください(足りないと、`docs` が契約違反として知らせます)。`docs --write` が、[targets.md](targets.md) の表を直します。

### 自動実行(GitHub Actions)

`.github/workflows/watch.yml` が、毎週月曜の 03:17(UTC)に `drift` と `discover` を実行します。Actions の画面から、手動でも実行できます。

- `drift` のあと、`keep` が、規則が審査された本文を `outputs/reviewed-bodies` に残し、そのディレクトリを `actions/cache` が次の週へ引き継ぎます。結果は Summary に出るだけで、Issue にはなりません。キャッシュは、既定ブランチの実行で保存したものだけが、次の定期実行で使えます。ブランチから手動実行した分は、`main` の実行からは見えません。7 日間使われないキャッシュは Actions が消すので、週ごとの実行が保っています。
- `drift` が `DRIFT` のとき、`rereview`(読み取りのみ。registry は書き換えません)を実行し、キャッシュの旧本文と比べた結果を、Summary に出します。規則ごとの見込み(引用の前後が同じ / 変わった / 旧本文がない / 引用が消えた)と、変わった所の数行です。資料の引用を含むので、Issue には載せません。Issue は、この Summary を見るよう案内します。旧本文はキャッシュにしかなく、手元の clone にはないので、手元で更新するときは、Summary の差分を読んで判断した規則を `rereview --accept` で指定します。
- 手動実行のとき、`report_only` を選ぶと、Issue を作らず、編集もしません。報告だけが Summary に出ます。
- 人の判断が要る結果(`drift` の `DRIFT`、`discover` の `CANDIDATES`)だけが、`drift` または `discover` のラベルを付けた Issue になります。開いている Issue があれば、本文を最新の報告に置き換えます。
- `INCOMPLETE`(取得できない資料や一覧がある)は、実行の Summary に出るだけで、Issue にはなりません。通信エラーは、再実行で解消することがあります。
- 再審査(または既読化)が済んだら、Issue を閉じます。閉じた Issue は開き直しません。次の週にまだ残っていれば、新しい Issue を立てます。

`.github/workflows/test.yml` は、push と pull request で、Ubuntu は Python 3.9〜3.15、macOS と Windows は 3.9 と 3.14 のテストを実行します。どの組み合わせが失敗しても、workflow は失敗になります。

3 つの workflow の Ubuntu は、`ubuntu-24.04` に固定しています。`ubuntu-latest` が Ubuntu 26 へ移り始めるのが 2026-10-19 で、`watch.yml` の定期実行と同じ日だからです(`test.yml` には、Python 3.9 のビルドが Ubuntu 26.04 にない、という理由もあります)。外すのは、`ubuntu-latest` で手動実行(`watch.yml`)が通ったあとです。

## 別のエージェントで手順を通す

実機の確認を、規則を書いたエージェント自身が行うと、SKILL.md の読み違いを見落とします。別の文脈のエージェント(Claude Code のサブエージェントなど)に、依頼文と run-id だけを渡して通させ、その記録を規則だけで検査します。結果と、見つかった問題は [independent-runs.md](independent-runs.md) にあります。

1. エージェントには、依頼文とスキルの呼び出しだけを渡します。手順のヒントは渡しません。最終回答のあとに、手順で迷った点を書かせます。
2. 終わったら、そのエージェント自身の記録(`~/.claude/projects/<プロジェクト>/<セッション>/subagents/agent-<id>.jsonl`)で検査します。

```bash
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py sources --run outputs/official-prompt-tailor/<run-id> --host-log <記録> --markdown
python3 .claude/skills/official-prompt-tailor/scripts/evidence.py verify --run outputs/official-prompt-tailor/<run-id> --mode live --host-log <記録>   # verification.json を上書きする
python3 tools/maintain.py audit --run outputs/official-prompt-tailor/<run-id> --transcript <記録>
```

`audit` が確かめるのは、手順を守ったか、最終回答が記録と合っているかです。各検査は `PASS` / `FAIL` / `WARN` / `NA` で、`FAIL` が 1 つでもあれば全体が `FAIL`(終了コード 1)です。

- 最終回答は、記録の最後の assistant の文章です。最終報告を `SubagentHandback` ツールで返すエージェントは、その `message` が最終回答です。
- コマンドは、`S=…/evidence.py; python3 $S drift` のように、同じコマンドの中で代入したシェル変数を展開し、`for c in drift rules; do …; done` のループは語ごとに書き出してから探します。

| 検査 | 確かめること |
|---|---|
| `fetched`・`wrote_the_result` | `evidence.py fetch` を実行し、`optimized.md` か `recommendations.json` を書いた |
| `drift_before_writing`・`rules_before_writing` | `drift --run` と `rules --run` を、最初の書込みより前に実行した |
| `verified_after_writing` | 最初の書込みのあとに `verify` を実行し、`verification.json` が残っている |
| `sources_after_verify` | 書込みのあとの最初の `verify` より後に、`sources --run` を実行した |
| `selected_bodies_were_read` | 選ばれた資料のすべてが、最初の書込みの前に、成功した `Read` で示された(`verify` と同じ規則)。資料が 0 件の入力では `NA`(読むものがない) |
| `no_log_the_user_did_not_give` | 依頼文にないログのパスを、`--host-log` に使っていない |
| `registry_not_read_whole` | `registry.json` を全体では読んでいない(`limit` なしの `Read`、`cat` など) |
| `answer_has_three_lines` | 最終回答に、参照検証・意味審査・効果の 3 行がある |
| `answer_matches_the_records` | 3 行が、`verification.json`・`semantic-review.json`(と `semantic-verification.json`)・`evaluation.json` の記録と同じ。記録がないものは「未実施」と読む(`assess` を実行していなければ `NOT_REVIEWED`、`evaluation.json` がなければ `NOT_EVALUATED`) |
| `no_overclaim` | 採用できない案を「候補」と書き、「検証済み」「改善済み」と言っていない(同じ文の中の否定は除く)。採用できる案でも、実測(`IMPROVED_ON_TEST_SET`)がなければ「改善済み」と言っていない |
| `copy_prompt_in_answer` | `optimized.md` の本文が、最終回答にそのままある |
| `sources_report_pasted_as_printed` | `sources --markdown` の出力が、スクリプトが出すとおりに貼ってある(空行と、先頭の書式の版の行の違いは無視)。別の書式の報告は照合できないので `WARN` |
| `original_literals_kept` | 原文のコマンド・数値・固有名詞(英数字)が、`optimized.md` に残っている(`WARN` のみ) |

`audit` は、改稿の良し悪しを見ません。意味審査と効果測定の代わりにはなりません。

`sources --markdown` の先頭には、書式の版の行(`<!-- sources-report format N -->`)があります。`sources_report_pasted_as_printed` は、貼られた報告を、いまの書式の出力と比べます。貼られた報告の版が違うとき(版の行がない古い報告は 1 と数えます)は、比べられないので `WARN` にします。書式(文言か並び)を変えるときは、`evidence.py` の `SOURCES_FORMAT` を上げてください。上げないと、同じ版のまま本文が違う報告になり、古い記録が `FAIL` になります。報告が見当たらないときも `FAIL` です。

## 意味審査

審査基準は `references/review-rubric.md` です。

- 別文脈(または人)が `semantic-review.json` を書きます。
- そのあと、`assess --run <run> --review <run>/semantic-review.json` を実行します。
- 校正記録(`calibration_ref` / `calibration_sha256`)が必要です。モデル審査では、実行ログも必要です。無ければ `INCOMPLETE` です。
- `mock` は単体試験専用で、本番の PASS になりません。

## 効果測定

対象の事例は、`evals/prompt-tailor-cases.jsonl` です。開発用 4 件と未見用 2 件があり、負例と境界例を含みます。

- 負例: 要件改変、UI への API 機能の誤適用。
- 境界例: 曖昧な依頼、否定の保持。

手順は次のとおりです。

1. 評価計画 `eval-plan.json` を、実行前に固定します。
2. `compare --run <run> --plan eval-plan.json --results eval-results.jsonl` で集計します。

事例の `status` は `pending-review` で配布しています。要件の抽出を、人がまだ確認していないためです。確認して `ready` にするまで、`compare` は `INCOMPLETE` を返します。

実行回数は、事例数 × 2 案 × 反復数です。たとえば、10 事例・各 3 回なら、1 対象で 60 回です。採点の呼出しは別です。

## リリース

1. 変更をコミットします。
2. `install.py` の `PLUGIN_VERSION` を上げ、`release-notes/vX.Y.Z.md` にリリースノートを書いて、コミットします。ノートは「変更点」「確認できていること」「確認できていないこと」の 3 節です。
3. タグ `vX.Y.Z` を付けて、`main` とタグを push します。
4. `.github/workflows/release.yml` が、タグと `PLUGIN_VERSION` の一致を確かめ、テストを通し、`install.py --zip` で ZIP を作って、GitHub Release を公開します。ノートは、タグの `release-notes/vX.Y.Z.md` です。無ければ自動生成になります。

手元から公開するときは、`install.py --zip dist` で ZIP を作り、次を実行します。

```bash
gh release create vX.Y.Z dist/official-prompt-tailor.zip --title vX.Y.Z --notes-file release-notes/vX.Y.Z.md
```

### 公開した ZIP を確かめる

ZIP は、同じファイルから、どの機械でも、いつ作っても同じバイト列になります(ファイルの並びを名前順にし、時刻・権限・作成元を固定して、圧縮しません)。そのため、タグから作り直した ZIP の sha256 を、公開された ZIP の `digest` と比べるだけで、ダウンロードせずに確かめられます。

```bash
git archive vX.Y.Z | tar -x -C <空のディレクトリ>
mkdir <出力先> && python3 <空のディレクトリ>/install.py --zip <出力先>    # "sha256: …" の行を控える
curl -s https://api.github.com/repos/Mr-Kondo/official-prompt-tailor/releases/tags/vX.Y.Z    # assets の digest が "sha256:…" で一致する
```

- `install.py --zip` は、最後に `sha256:` を出すので、`release.yml` のログにも残ります。`test.yml` も、コミットごとに同じ ZIP を作って `sha256:` を出します。同じコミットなら、10 ジョブのすべてで同じ値になります。
- この方法で確かめられるのは、この変更を入れた後のリリースからです。それ以前の ZIP は、更新日時と圧縮が入っているため、作り直しても一致しません。
- 一致しないときは、ZIP の中身(名前・サイズ・内容)を `unzip -l` と `diff -r` で比べます。ファイルの改行が CRLF になっていると一致しません(`.gitattributes` が LF に固定しているので、通常は起きません)。
