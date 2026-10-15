#!/usr/bin/env python3
"""CI glue for the weekly watch workflow (not part of the skill).

  ci_report.py drift|discover RESULT.json [--issue]

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
        out += ["", "対応: 変更箇所を読み、規則を再審査します(docs/maintenance.md の「更新を確かめる」)。再審査したら、この Issue を閉じてください。", ""]
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


def render(kind, r):
    lines = drift_lines(r) if kind == "drift" else discover_lines(r)
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
    if "--issue" in argv and result["verdict"] == KINDS[kind][1]:
        publish(kind, KINDS[kind][2], report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
