#!/usr/bin/env python3
"""CI glue for the weekly watch workflow (not part of the skill).

  ci_report.py drift|discover|kept|rereview RESULT.json [--issue]

Prints a Markdown report. With --issue, a result that needs a person (DRIFT / CANDIDATES) is also written to the
one open issue that carries the label of that kind (an issue is opened when none is). The body is replaced, not
appended to, so a week without action does not pile up comments. A closed issue stays closed: the next result opens a new one.
"""
import json
import subprocess
import sys

KINDS = {  # kind: (report heading, verdict that needs a person, issue title)
    "drift": ("公式資料の更新確認", "DRIFT", "公式資料が、規則の審査後に変わっています"),
    "discover": ("公式資料の新規ページ候補", "CANDIDATES", "公式資料に、登録していない新規ページの候補があります"),
    "kept": ("審査時の本文の保存", None, None),  # a report only: never an issue
    "rereview": ("再審査の見込み(旧本文との比較)", None, None),  # a report only: it quotes the pages, so it stays in the run's Summary
}
STATE = {"changed": "引用は残っているが、本文が変わった", "excerpt_missing": "引用が本文から消えた"}


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def drift_lines(r):
    rows = [x for x in r["rules"] if x["state"] in STATE]
    out = []
    if rows:
        out += ["| 規則 | 資料 | 状態 |", "|---|---|---|"]
        out += [f"| `{x['rule_id']}` | `{x['source_id']}` | {STATE[x['state']]} |" for x in rows]
        out += ["", "対応: 旧本文との比較(`rereview`)の結果は、この実行の Summary にあります。旧本文は Actions のキャッシュにあり、手元の clone にはないので、手元では `rereview --accept` で、読んで判断した規則を更新します(docs/maintenance.md の「再審査する」)。再審査したら、この Issue を閉じてください。", ""]
    return out + [f"- 取得できなかった資料: `{s['source_id']}`({cell(s['error'])})" for s in r["sources"] if s["body_sha256"] is None]


def discover_lines(r):
    rows = [(ix["index_id"], c) for ix in r["indexes"] for c in ix["candidates"]]
    out = []
    if rows:
        out += ["| 一覧 | ページ |", "|---|---|"]
        out += [f"| `{i}` | [{cell(c['title'])}]({c['url']}) |" for i, c in rows]
        out += ["", "対応: 採用するページは、`python3 tools/maintain.py propose --url <URL> --target <target_id>` で登録案を作ります。"
                "見送るページは、`python3 tools/maintain.py discover --mark-seen` で既読にして、`tools/discovery-seen.json` をコミットします"
                "(docs/maintenance.md の「新しい資料を見つける」)。", ""]
    return out + [f"- 取得できなかった一覧: `{ix['index_id']}`({cell(ix['error'])})" for ix in r["indexes"] if ix["status"] != "live"]


def kept_lines(r):
    out = [f"- 新しく保存した本文: {r['bodies_kept']} 件、不要になって消した本文: {r['bodies_pruned']} 件", ""]
    if r["without_old_body"]:
        out += ["旧本文が無い規則の資料(この資料が変わっても、`rereview` は旧本文と比べられません):", ""]
        out += [f"- `{x['source_id']}`(`{x['source_body_sha256'][:12]}…`)" for x in r["without_old_body"]]
    return out + [f"- 取得できなかった資料: `{s['source_id']}`({cell(s['error'])})" for s in r["sources"] if s["status"] == "not_fetched"]


REREVIEW_CLASS = {"context_unchanged": "引用とその前後は同じ(機械で更新できる)", "context_changed": "引用の前後が変わった(差分を読む)",
                  "old_body_unavailable": "旧本文がない(本文を読んで判断する)", "excerpt_missing": "引用が本文から消えた(更新できない)"}
SHOWN_HUNKS, SHOWN_LINES, WIDTH = 5, 3, 100  # a few lines of what changed, so that the page need not be fetched to judge; not the page


def short(text):
    text = cell(text)
    return text if len(text) <= WIDTH else text[:WIDTH - 1] + "…"


def rereview_lines(r):
    out = [f"判定: `{r['verdict']}`(registry は書き換えていません)", ""]
    if r["rules"]:
        out += ["| 規則 | 資料 | 見込み |", "|---|---|---|"]
        out += [f"| `{x['rule_id']}` | `{x['source_id']}` | {REREVIEW_CLASS.get(x['class'], x['class'])} |" for x in r["rules"]]
        out += ["", "手元で更新するときは、旧本文がキャッシュにしかないため、`python3 tools/maintain.py rereview --accept <rule_id> --reviewer \"…; not human\" --write` で、読んで判断した規則を指定します(docs/maintenance.md の「再審査する」)。", ""]
    for s in r["sources"]:
        for c in s.get("changes", []):
            if c.get("old_body") == "unavailable":
                out.append(f"- `{s['source_id']}`: 旧本文がありません(`{c['from_sha256'][:12]}…`)")
                continue
            out.append(f"- `{s['source_id']}`: 変わった所 {c['count']} か所(旧本文 `{c['from_sha256'][:12]}…`)")
            for h in c["hunks"][:SHOWN_HUNKS]:
                out.append(f"  - 旧 {h['old_lines'][0]}〜{h['old_lines'][1]} 行 → 新 {h['new_lines'][0]}〜{h['new_lines'][1]} 行")
                out += [f"    - 前: {short(x)}" for x in h["removed"][:SHOWN_LINES]] + [f"    - 後: {short(x)}" for x in h["added"][:SHOWN_LINES]]
    return out + [f"- 取得できなかった資料: `{s['source_id']}`({cell(s['error'])})" for s in r["sources"] if s["body_sha256"] is None]


def render(kind, r):
    lines = {"drift": drift_lines, "discover": discover_lines, "kept": kept_lines, "rereview": rereview_lines}[kind](r)
    head = [f"## {KINDS[kind][0]}: {r['verdict']}", "", f"確認日時(UTC): {r['checked_at']}", ""]
    return "\n".join(head + lines).rstrip("\n") + "\n"


def publish(label, title, body):
    def gh(*args, **kw):
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8", **kw).stdout.strip()

    number = gh("issue", "list", "--label", label, "--state", "open", "--json", "number", "--jq", ".[0].number // empty")
    if number:
        gh("issue", "edit", number, "--body-file", "-", input=body)
        return
    subprocess.run(["gh", "label", "create", label, "--description", f"automated: {label}"], capture_output=True, text=True, encoding="utf-8")  # fails when it exists: fine
    gh("issue", "create", "--title", title, "--label", label, "--body-file", "-", input=body)


def main(argv):
    kind, path = argv[1], argv[2]
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    report = render(kind, result)
    print(report)
    if "--issue" in argv and KINDS[kind][1] and result["verdict"] == KINDS[kind][1]:
        publish(kind, KINDS[kind][2], report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
