# 利用先ごとの使い方

プロンプトを使う先(`target_id`)ごとに、取得される資料、使える規則、指定する値をまとめます。スキルを動かす製品(`execution_host`)は別で、[products.md](products.md) にあります。

表は、`registry.json` の 2026-10-15 時点の内容です。正は `registry.json` です。各資料の URL は `allowed_urls` にあり、規則の最新の一覧は、末尾の「規則を一覧する」で出せます。

## 決める 4 つの値

`input.json` に書く値です。不明な値は推定で埋めず、`unknown` のままにします。

| 項目 | 値 | 使い分け |
|---|---|---|
| `target_id` | 次の節の 11 種 | プロンプトを使う先 |
| `purpose` | `one-shot` | 貼り付けて使う、一回の依頼 |
| | `persistent-instructions` | AGENTS.md・Gems・Rule・GEMINI.md など、常に読まれる指示 |
| | `skill-instructions` | スキルの本文と説明 |
| | `agent-instruction` | エージェントへの指示(Google ADK の `instruction` など) |
| `target_surface` | `api` | OpenAI API・Claude API・Gemini API で使う場合 |
| | `gems` | Gemini Apps の Gems(Gemini Apps だけ) |
| | `unknown` | 不明な場合(既定) |
| `model_id` | モデルの ID | 不明な場合は `unknown` |

- `unknown` は、`surfaces` と `models` が `all` の資料・規則にだけ一致します。`api` 向けの資料と、モデルを限定した規則は、使われません。
- 依頼されていない種類への変換(通常の依頼を Gems・Rule・Skill の作成に変えるなど)は、しません。

## 利用先と、取得される資料

資料は、次のように選ばれます。

- 「常に取得」の資料は、`purpose` と `target_surface` に関係なく取得されます。
- 「条件付き」の資料は、`purpose` と `target_surface` の両方が合うときだけ、追加で取得されます。

### OpenAI

| 利用先 | `target_id` | 常に取得 | 条件付き |
|---|---|---|---|
| ChatGPT Chat | `chatgpt` | `openai-prompting` | `api`: `openai-api-prompting` |
| ChatGPT Work | `chatgpt-work` | `openai-prompting`、`openai-work` | `api`: `openai-api-prompting` |
| Codex | `codex` | `openai-prompting` | `persistent-instructions`: `openai-agents-md`、`skill-instructions`: `openai-skills`、`api`: `openai-api-prompting` |

### Anthropic

| 利用先 | `target_id` | 常に取得 | 条件付き |
|---|---|---|---|
| Claude チャット | `claude` | `claude-chat-help` | `api`: `claude-model-prompting` |
| Claude Cowork | `claude-cowork` | `claude-cowork-help` | `api`: `claude-model-prompting` |
| Claude Code | `claude-code` | `claude-code-best-practices` | `skill-instructions`: `claude-code-skills`、`api`: `claude-model-prompting` |

### Google

| 利用先 | `target_id` | 常に取得 | 条件付き |
|---|---|---|---|
| Gemini Apps(通常・Gems・Apps skill) | `gemini` | なし | `persistent-instructions` かつ `gems`: `gemini-gems`、`skill-instructions`: `gemini-app-skills`、`api`: `gemini-api-prompting` |
| Antigravity | `antigravity` | なし | `persistent-instructions`: `antigravity-rules`、`skill-instructions`: `antigravity-skills`、`api`: `gemini-api-prompting` |
| Gemini Apps の Tasks | `gemini-agent-app` | `gemini-tasks` | `api`: `gemini-api-prompting` |
| Gemini CLI | `gemini-cli` | `gemini-cli-memory` | `skill-instructions`: `gemini-cli-skills`、`api`: `gemini-api-prompting` |
| Google ADK のエージェント | `google-adk-agent` | `google-adk-instructions` | `api`: `gemini-api-prompting` |

「Gemini の agent」が Tasks・CLI・ADK のどれか分からないときは、`target_id` を `unresolved` にします。この間は、検証済みの最適化は出ません。

## 使える規則の件数

`target_surface` と `model_id` が `unknown` のときの、`official` 規則の件数です。

| 利用先 | one-shot | persistent-instructions | skill-instructions | agent-instruction |
|---|---|---|---|---|
| ChatGPT Chat | 3 | 0 | 0 | 0 |
| ChatGPT Work | 4 | 0 | 0 | 0 |
| Codex | 4 | 1 | 1 | 0 |
| Claude チャット | 3 | 0 | 0 | 0 |
| Claude Cowork | 2 | 0 | 0 | 0 |
| Claude Code | 4 | 1 | 2 | 1 |
| Gemini Apps | 資料なし | 資料なし | 2 | 資料なし |
| Antigravity | 資料なし | 1 | 2 | 資料なし |
| Gemini Apps の Tasks | 2 | 0 | 0 | 0 |
| Gemini CLI | 0 | 1 | 1 | 0 |
| Google ADK | 0 | 0 | 0 | 4 |

- `0`: 資料は取得されますが、その `purpose` に使える規則がありません。`official` の変更は作れず、変更は `editorial` か `inference` になります。
- `資料なし`: 取得される資料がありません。`fetch` は 0 で終わり、`verify` が `no_applicable_official_source` で `INCOMPLETE` にします。

## `api` と `model_id` を指定すると

`target_surface` を `api` にすると、API 向けの資料が加わり、次の規則が使えるようになります。

| 利用先 | 加わる規則 | 対象の `purpose` |
|---|---|---|
| ChatGPT Chat・ChatGPT Work・Codex | `openai-api-instructions-param` | `one-shot`、`agent-instruction` |
| Gemini Apps・Antigravity・Gemini Apps の Tasks・Gemini CLI・Google ADK | `gemini-api-clear-specific` | すべて |
| Claude チャット・Claude Cowork・Claude Code | `claude-api-clear-direct`(下記の `model_id` のとき) | すべて |

モデルを限定した規則は、`claude-api-clear-direct` だけです。`target_surface` が `api` で、`model_id` が次のいずれかのときだけ使えます。他のモデルと `unknown` では、使われません。

- `claude-fable-5-1`
- `claude-opus-5-5`
- `claude-sonnet-5-5`
- `claude-haiku-5-5`

## 利用先ごとの注意

- ChatGPT Chat・ChatGPT Work: 常に取得する資料の規則は、すべて `one-shot` 用です。常設指示などを頼んでも、`official` の根拠は付きません。
- Codex: AGENTS.md は `persistent-instructions`、スキルは `skill-instructions` で扱います。それぞれ専用の資料と規則があります。
- Claude Code: `claude-code-verification` だけが、4 つの `purpose` すべてに使えます。他の 3 規則は `one-shot` 用です。
- Gemini Apps: `one-shot` 用の資料はありません。Gems の常設指示は、`target_surface` を `gems` にします。
- Antigravity: `one-shot` 用の資料はありません。Antigravity CLI での実測は [antigravity.md](antigravity.md) にあります。
- Gemini CLI: `GEMINI.md` などの常設指示と、スキルが対象です。`one-shot` では、`official` の根拠は付きません。
- Google ADK: `agent-instruction` だけが対象です。

## 依頼の例と、決まる値

| 依頼の例 | `target_id` | `purpose` | `target_surface` / `model_id` |
|---|---|---|---|
| 「この依頼文を、Codex 向けに公式資料に沿って最適化して」 | `codex` | `one-shot` | `unknown` / `unknown` |
| 「この AGENTS.md を、公式資料に沿って直して」 | `codex` | `persistent-instructions` | `unknown` / `unknown` |
| 「この SKILL.md を、Claude Code 向けに最適化して」 | `claude-code` | `skill-instructions` | `unknown` / `unknown` |
| 「この Gems の指示を、公式資料に沿って最適化して」 | `gemini` | `persistent-instructions` | `gems` / `unknown` |
| 「この ADK エージェントの instruction を最適化して」 | `google-adk-agent` | `agent-instruction` | `unknown` / `unknown` |
| 「Claude の API 向けに、モデルは `claude-sonnet-5-5` で最適化して」 | `claude` | `one-shot` | `api` / `claude-sonnet-5-5` |

`input.json` の完成例です。Claude Code でスキルを動かし、Codex の AGENTS.md を直す場合を示します。

```json
{
  "schema_version": 1,
  "execution_host": "claude-code",
  "target_id": "codex",
  "target_surface": "unknown",
  "model_id": "unknown",
  "purpose": "persistent-instructions",
  "original_path": "outputs/official-prompt-tailor/original-<run-id>.md",
  "constraints": ["内容は変えない", "出力言語は日本語"]
}
```

Claude の API 向けに最適化する場合は、次の 4 項目を変えます。

- `target_id`: `claude`
- `purpose`: `one-shot`
- `target_surface`: `api`
- `model_id`: `claude-sonnet-5-5`

## 規則を一覧する

`registry.json` から、利用先の規則を出します。`models` が `['all']` 以外の規則が、モデル限定です。

```bash
python3 -I - <<'PY'
import json
reg = json.load(open(".claude/skills/official-prompt-tailor/references/registry.json", encoding="utf-8"))
target = "claude-code"
for r in reg["rules"]:
    if target in r["target_ids"]:
        print(r["rule_id"], r["source_id"], r["purposes"], r["surfaces"], r["models"], r["review_status"])
PY
```
