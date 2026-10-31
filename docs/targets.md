# 利用先ごとの使い方

プロンプトを使う先(`target_id`)ごとに、取得される資料、使える規則、指定する値をまとめます。スキルを動かす製品(`execution_host`)は別で、[products.md](products.md) にあります。

表は `registry.json` から作ります(`python3 tools/maintain.py docs --write`。マーカーの間は手で直しません)。正は `registry.json` です。各資料の URL は `allowed_urls` にあり、規則の最新の一覧は、末尾の「規則を一覧する」で出せます。

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

- `unknown` は、`surfaces` が `all` の資料・規則にだけ一致します。`api` 向けの資料は使われません。モデルを限定した資料・規則は、`model_id` が一致すれば `target_surface` が `unknown` でも使われます(Claude チャット・Claude Cowork・Claude Code のモデル別ガイド)。
- 依頼されていない種類への変換(通常の依頼を Gems・Rule・Skill の作成に変えるなど)は、しません。

## 利用先と、取得される資料

資料は、次のように選ばれます。

- 「常に取得」の資料は、`purpose` と `target_surface` に関係なく取得されます。規則が一部の `purpose` にしかない資料は、ここに入りません。
- 「条件付き」の資料は、`purpose` と `target_surface` の両方が合うときだけ、追加で取得されます。条件は、その資料の規則が使われる `purpose` と `target_surface` です。規則のない入力で読ませないためで、`registry_problems` が確かめます([maintenance.md](maintenance.md))。
- 特定のモデルに限った資料(モデル別ガイド)は、`model_id` がそのモデルのときだけ取得されます。`target_surface` は問いません。次の表には出ません。「`api` と `model_id` を指定すると」にあります。

表の「条件付き」は、`target_surface` と `model_id` が `unknown` のときの追加分と、`target_surface` を `api` にしたときの追加分です。

### OpenAI

<!-- generated:routing-openai begin (python3 tools/maintain.py docs --write) -->
| 利用先 | `target_id` | 常に取得 | 条件付き |
|---|---|---|---|
| ChatGPT Chat | `chatgpt` | なし | `one-shot`: `openai-tasks-memory`、`openai-prompting`<br>`persistent-instructions`: `openai-personalize`<br>`skill-instructions`: `openai-skills-plugins`<br>`api`(`one-shot`・`agent-instruction`): `openai-api-prompting` |
| ChatGPT Work | `chatgpt-work` | なし | `one-shot`: `openai-tasks-memory`、`openai-prompting`、`openai-work`<br>`persistent-instructions`: `openai-personalize`<br>`skill-instructions`: `openai-skills-plugins`<br>`api`(`one-shot`・`agent-instruction`): `openai-api-prompting` |
| Codex | `codex` | なし | `one-shot`: `openai-best-practices`、`openai-prompting`<br>`persistent-instructions`: `openai-agents-md`、`openai-personalize`、`openai-best-practices`<br>`skill-instructions`: `openai-skills`、`openai-skills-plugins`、`openai-best-practices`<br>`api`(`one-shot`・`agent-instruction`): `openai-api-prompting` |
<!-- generated:routing-openai end -->

### Anthropic

<!-- generated:routing-anthropic begin (python3 tools/maintain.py docs --write) -->
| 利用先 | `target_id` | 常に取得 | 条件付き |
|---|---|---|---|
| Claude チャット | `claude` | なし | `one-shot`: `claude-cowork-and-chat`、`claude-custom-visuals`、`claude-chat-prompt-design`、`claude-chat-response-too-brief`、`claude-chat-help`<br>`persistent-instructions`: `claude-cowork-and-chat`<br>`skill-instructions`: `claude-skills-best-practices`、`claude-skills-enterprise` |
| Claude Cowork | `claude-cowork` | なし | `one-shot`: `claude-cowork-and-chat`、`claude-custom-visuals`、`claude-cowork-computer-use`、`claude-cowork-schedule`、`claude-cowork-help`<br>`persistent-instructions`: `claude-cowork-and-chat`、`claude-cowork-projects`<br>`skill-instructions`: `claude-skills-best-practices`、`claude-skills-enterprise` |
| Claude Code | `claude-code` | `claude-code-best-practices` | `one-shot`: `claude-code-prompt-library`、`claude-context-claude-md`<br>`persistent-instructions`: `claude-code-memory`、`claude-code-scheduled-tasks`<br>`skill-instructions`: `claude-code-skills`、`claude-skills-best-practices`、`claude-skills-enterprise` |
<!-- generated:routing-anthropic end -->

### Google

<!-- generated:routing-google begin (python3 tools/maintain.py docs --write) -->
| 利用先 | `target_id` | 常に取得 | 条件付き |
|---|---|---|---|
| Gemini Apps(通常・Gems・Apps skill) | `gemini` | なし | `skill-instructions`: `gemini-app-skills`<br>`api`: `gemini-api-prompting`<br>`gems`(`persistent-instructions`): `gemini-gems` |
| Antigravity | `antigravity` | なし | `one-shot`: `antigravity-cli-best-practices`<br>`persistent-instructions`: `antigravity-rules`、`antigravity-cli-best-practices`<br>`skill-instructions`: `antigravity-skills`<br>`api`(`one-shot`・`persistent-instructions`・`skill-instructions`): `gemini-api-prompting`<br>`api`(`agent-instruction`): `gemini-api-prompting`、`antigravity-sdk-personas` |
| Gemini Apps の Tasks | `gemini-agent-app` | なし | `one-shot`: `gemini-tasks`<br>`api`: `gemini-api-prompting` |
| Gemini CLI | `gemini-cli` | なし | `persistent-instructions`: `gemini-cli-memory-tutorial`、`gemini-cli-extension-best-practices`、`gemini-cli-system-prompt`、`gemini-cli-memory`<br>`skill-instructions`: `gemini-cli-skills`、`gemini-cli-skills-best-practices`<br>`api`: `gemini-api-prompting` |
| Google ADK のエージェント | `google-adk-agent` | なし | `agent-instruction`: `google-adk-instructions`<br>`api`: `gemini-api-prompting` |
<!-- generated:routing-google end -->

「Gemini の agent」が Tasks・CLI・ADK のどれか分からないときは、`target_id` を `unresolved` にします。この間は、検証済みの最適化は出ません。

## 使える規則の件数

`target_surface` と `model_id` が `unknown` のときの、`official` 規則の件数です。

<!-- generated:rule-counts begin (python3 tools/maintain.py docs --write) -->
| 利用先 | one-shot | persistent-instructions | skill-instructions | agent-instruction |
|---|---|---|---|---|
| ChatGPT Chat | 4 | 1 | 2 | 資料なし |
| ChatGPT Work | 5 | 1 | 2 | 資料なし |
| Codex | 5 | 4 | 5 | 資料なし |
| Claude チャット | 7 | 1 | 6 | 資料なし |
| Claude Cowork | 6 | 2 | 6 | 資料なし |
| Claude Code | 7 | 4 | 8 | 1 |
| Gemini Apps | 資料なし | 資料なし | 2 | 資料なし |
| Antigravity | 2 | 2 | 2 | 資料なし |
| Gemini Apps の Tasks | 2 | 資料なし | 資料なし | 資料なし |
| Gemini CLI | 資料なし | 4 | 2 | 資料なし |
| Google ADK | 資料なし | 資料なし | 資料なし | 4 |
<!-- generated:rule-counts end -->

- 数字: その `purpose` で使える `official` 規則の件数です。資料は、その規則が使える入力にだけ取得されるので、資料が取得されるのに使える規則が 0 件、という組み合わせは作れません。
- `資料なし`: 取得される資料がありません。使える `official` 規則がないからです。`fetch` は 0 で終わり、`verify` が `no_applicable_official_source` で `INCOMPLETE` にします。変更は `editorial` か `inference` になり、「検証済み」とは言えません。`target_surface` や `model_id` を指定すると、資料と規則が加わることがあります(次の節)。手順は、SKILL.md と証拠契約(`references/evidence-contract.md`)の「資料が 0 件」にあります。

## `api` と `model_id` を指定すると

`target_surface` を `api` にすると、API 向けの資料が加わり、次の規則が使えるようになります。

| 利用先 | 加わる規則 | 対象の `purpose` |
|---|---|---|
| ChatGPT Chat・ChatGPT Work・Codex | `openai-api-instructions-param` | `one-shot`、`agent-instruction` |
| Gemini Apps・Antigravity・Gemini Apps の Tasks・Gemini CLI・Google ADK | `gemini-api-clear-specific` | すべて |
| Antigravity | `antigravity-sdk-templated-instructions` | `agent-instruction` |
| Claude チャット・Claude Cowork・Claude Code | モデルを限定した規則(下記) | 規則による |

モデルを限定した規則は 33 件です。`model_id` が規則の `models` に含まれるときだけ使えます。`target_surface` は問いません。他のモデルと `unknown` では、使われません。モデルを限定した資料(モデル別ガイド)も、同じ条件でだけ取得されます。33 件はすべて、指示の書き方の規則で、前提で原文の内容を確かめます。

<!-- generated:model-guides begin (python3 tools/maintain.py docs --write) -->
| `model_id` | 取得される、モデル限定の資料 | 使える規則 |
|---|---|---|
| `claude-fable-5` | `claude-prompting-fable-5` | 4 件 |
| `claude-fable-5-1` | `claude-model-prompting`、`claude-prompting-fable-5-1` | 5 件 |
| `claude-haiku-5-5` | `claude-model-prompting`、`claude-prompting-haiku-5-5` | 4 件 |
| `claude-opus-4-8` | `claude-prompting-opus-4-8` | 4 件 |
| `claude-opus-5` | `claude-prompting-opus-5` | 4 件 |
| `claude-opus-5-5` | `claude-model-prompting`、`claude-prompting-opus-5-5` | 5 件 |
| `claude-sonnet-5` | `claude-prompting-sonnet-5` | 4 件 |
| `claude-sonnet-5-5` | `claude-model-prompting`、`claude-prompting-sonnet-5-5` | 6 件 |
<!-- generated:model-guides end -->

- 件数は、`purpose` を問わない合計です。`purpose` によって、使える規則は変わります。
- `claude-model-prompting` の規則は `claude-api-clear-direct` の 1 件だけで、上の 4 つのモデルにだけ付いています。そのため、この資料も、この 4 つのモデルのときだけ取得されます。
- Claude のモデル別ガイドの規則は、プロンプトの指示の書き方(範囲の明示、完了条件、冗長さ、進捗の報告など)に限ります。API のパラメーター(`effort` や `max_tokens` など)の設定は、プロンプトの書き換えではないので、規則にしていません。

## 利用先ごとの注意

- ChatGPT Chat・ChatGPT Work: `openai-prompting`(ChatGPT Work では `openai-work` も)の規則は、すべて `one-shot` 用なので、`one-shot` のときだけ取得されます。常設指示(カスタム指示)とスキルの規則は、条件付きの資料にあります(上の表)。`agent-instruction` に使える規則は、`api` のときの `openai-api-instructions-param` だけです。
- Codex: AGENTS.md は `persistent-instructions`、スキルは `skill-instructions` で扱います。それぞれ専用の資料と規則があります。`openai-best-practices` は、`one-shot`・`persistent-instructions`・`skill-instructions` のいずれにも使えます。
- Claude チャット・Claude Cowork: 全会話に適用する好みの指示は、`claude-cowork-and-chat` の規則で扱います。Cowork のプロジェクトの指示は、`claude-cowork-projects` の規則です。`agent-instruction` に使える規則は、`api` とモデルを指定したときの、モデルを限定した規則だけです。
- Claude Code: `claude-code-verification` だけが、4 つの `purpose` すべてに使えます。CLAUDE.md は `persistent-instructions`、スキルは `skill-instructions` で扱います。それぞれ専用の資料と規則があります。
- Gemini Apps: `one-shot` 用の資料はありません。Gems の常設指示は、`target_surface` を `gems` にします。
- Antigravity: `one-shot` 用の資料は、`antigravity-cli-best-practices` だけです。Antigravity CLI での実測は [検証履歴](history/runtime-verification.md#antigravity-initial) にあります。
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
