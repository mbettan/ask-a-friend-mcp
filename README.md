<p align="center">
  <img src="docs/assets/og-image.jpg" alt="Ask-a-Friend — Even your AI needs a second opinion" width="720" />
</p>

<p align="center">
  <strong>Your coding agent is confident. Confidently wrong, sometimes.</strong><br>
  Give it a way to phone a friend.
</p>

<p align="center">
  <a href="https://github.com/mbettan/ask-a-friend-mcp/stargazers"><img src="https://img.shields.io/github/stars/mbettan/ask-a-friend-mcp?style=flat&color=yellow" alt="Stars"></a>
  <a href="https://github.com/mbettan/ask-a-friend-mcp/commits/main"><img src="https://img.shields.io/github/last-commit/mbettan/ask-a-friend-mcp?style=flat" alt="Last Commit"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg?style=flat" alt="License"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-Streamable%20HTTP%20%2B%20SSE-green.svg?style=flat" alt="MCP"></a>
  <a href="https://cloud.google.com/run"><img src="https://img.shields.io/badge/GCP-Cloud%20Run%20%2B%20Vertex%20AI-orange.svg?style=flat" alt="Cloud Run"></a>
</p>

<p align="center">
  <a href="https://mbettan.github.io/ask-a-friend-mcp/">live demo</a> •
  <a href="#whats-an-mcp">what's an MCP?</a> •
  <a href="#why-bother">why bother</a> •
  <a href="#deploy">deploy</a> •
  <a href="#connect-clients">connect clients</a> •
  <a href="#what-you-get">what you get</a> •
  <a href="#how-it-works">how it works</a>
</p>

---

Ask-a-Friend is an open-source **MCP server** you host on your own Google Cloud project. It gives
your AI agent one extra tool: `ask_a_friend`.

When your agent calls it, the question goes to a **completely different frontier model** — one that
hasn't seen the conversation, doesn't share the first model's assumptions, and has no reason to
agree with it. The friend reviews the work, says what it actually thinks, and the answer comes back
inline. One tool call.

Works with **Claude Web & Desktop**, **Claude Code CLI**, **ChatGPT**, **Cursor IDE**, and
**Gemini / Antigravity CLI**. Friends are **Claude Opus 5.5** and **Gemini 3.8 Flash**, running on
**Vertex AI (`global`)**, with PII scrubbed before anything leaves your server.

---

## What's an MCP?

Skip this if you already know.

Your AI agent is smart but sealed in a box. It can write about your database, but it can't query it.
It can describe an API call, but it can't make one. Every capability beyond "generate text" has to be
handed to it from outside.

**Model Context Protocol** is the standard for handing things over. Think of it like a USB port. Before
USB, every device needed its own proprietary connector. Now there's one shape, and anything that fits
just works — no driver hunting, no per-app integration.

MCP is the same idea for AI tools. Write a server once, and Claude, ChatGPT, Cursor, and Gemini can
all use it. No custom glue for each one.

> **MCP is just the plug shape. Ask-a-Friend is what you plug in.**

---

## Why bother

Your agent is one model. One training run, one set of habits, one set of blind spots. When it
misses something, asking it again doesn't help — you get the same blind spot back, phrased
differently. That's not review. That's an echo chamber.

<table>
<tr>
<td width="50%">

### 🗣️ One model, on its own

> The agent retries the same bug against its own assumptions, and ships raw credentials and
> unredacted code straight to external APIs:
> * 🔓 Raw secrets (`sk-ant-...`, `AKIA...`, emails) sent in plain text
> * 🧠 Single-model blind spots on subtle concurrency, security, or tax/math edge cases
> * 📅 Stale training cutoffs, with no live CVE or SDK doc verification
> * 💸 Identical prompts re-sent over the wire on every debug iteration

</td>
<td width="50%">

### 🤝 One `ask_a_friend` call

> A FastMCP proxy scrubs sensitive identifiers before transit, grounds the answer in live web
> search, and caches deterministically:
> * 🛡️ **Pre-transit PII scrubbing** swaps secrets for placeholders (`__PII_REDACTED_1__`) and rehydrates locally
> * 🔬 **Adaptive High Thinking + live web search** (`opus-5-5` & `gemini-3.8-flash`)
> * ⚡ **Dual-layer caching** (`~1ms` SHA-256 local cache + `~90%` cheaper Vertex AI ephemeral prompt cache)
> * 🔄 **Multi-provider failover** across Anthropic and Google GenAI

</td>
</tr>
</table>

**A different model. Grounded in today's web. Your secrets never leave the building.**

```
┌────────────────────────────────────────────────────────┐
│  pre-transit PII scrubbing  ██████ 100% (__PII_REDACTED)│
│  adaptive thinking effort   ██████ HIGH (128K output)  │
│  real-time web search       ██████ Brave + Google      │
│  prompt cache savings       ██████ ~1ms SHA256 / -90% $│
│  provider failover ladder   ██████ Opus 5.5 ➔ Gemini 3.8│
└────────────────────────────────────────────────────────┘
```

---

## Deploy

It's your server, your Google Cloud project, your bill. Nothing routes through anyone else.

```bash
chmod +x deploy.sh
./deploy.sh <YOUR_GCP_PROJECT_ID>
```

*What `deploy.sh` automates:*
1. Enables required GCP APIs (`run`, `aiplatform`, `secretmanager`, `cloudbuild`, `artifactregistry`, `orgpolicy`).
2. Configures Vertex AI Organization Policies (`vertexai.allowedPartnerModelFeatures` & `vertexai.allowedModels`) so Anthropic Web Search and Model Garden partner models work out-of-the-box.
3. Provisions a least-privilege runtime Service Account (`roles/aiplatform.user`, `roles/secretmanager.secretAccessor`).
4. Generates and stores your `MCP_API_KEY` (`aaf_...`) in Google Cloud Secret Manager (`mcp-api-key`).
5. Builds and deploys the pure MCP server container (`docs/` excluded via `.gcloudignore` / `.dockerignore`) to Cloud Run.

Grab your generated `MCP_API_KEY` anytime:

```bash
gcloud secrets versions access latest --secret=mcp-api-key --project=<YOUR_GCP_PROJECT_ID>
```

> [!IMPORTANT]
> `MCP_API_KEY` is **required** in `api_key` and `oauth2` auth modes — the server refuses to boot
> without it rather than starting up unauthenticated. See [`.env.example`](.env.example).

---

## Connect clients

Swap `your-cloud-run-url.run.app` for the URL `deploy.sh` prints at the end.

### 1. Claude Custom Connector (Claude Web & Claude Desktop UI)
1. Open **Settings &rarr; Connectors &rarr; Add custom connector**.
2. Fill in the connector modal:
   - **Name:** `ask-a-friend`
   - **MCP server URL:** `https://your-cloud-run-url.run.app/mcp`
3. Click **Continue** — Claude auto-discovers the server's OAuth 2.0 and transport settings:
   - **Authentication:** Keep **Sign in now** (`Detected`) selected.
   - **OAuth client:** Keep **Register automatically** (`Detected` via RFC 7591) selected.
   - **Advanced &rarr; Transport:** Keep **Streamable HTTP** selected.
4. Click **Add / Connect**, paste your `MCP_API_KEY` into the **🤝 Ask-a-Friend MCP** authorization window, and click **Authorize Client &rarr;**.

### 2. Claude Code CLI (`claude`)
Register the remote Streamable HTTP MCP server across all workspaces (`--scope user`):
```bash
claude mcp add --scope user --transport http ask-a-friend \
  https://your-cloud-run-url.run.app/mcp \
  --header "X-MCP-API-Key: YOUR_MCP_API_KEY"
```
Verify inside Claude Code with `claude mcp list` or `/mcp`.

### 3. ChatGPT Native MCP Connector (SSE + OAuth 2.0)
- **Name:** `Ask-a-Friend`
- **Server URL:** `https://your-cloud-run-url.run.app/sse`
- **Authentication:** `OAuth` *(Auto-negotiated via RFC 7591 Dynamic Client Registration)*

### 4. ChatGPT Custom GPT Action (OpenAPI 3.1.0 REST)
- **Import Schema URL:** `https://your-cloud-run-url.run.app/openapi.yaml`
- **Authentication:** `API Key` &rarr; `Bearer` &rarr; `<YOUR_MCP_API_KEY>`

### 5. Cursor IDE (`.cursor/mcp.json`) & Gemini CLI (`~/.gemini/settings.json`)
```json
{
  "mcpServers": {
    "ask-a-friend": {
      "url": "https://your-cloud-run-url.run.app/mcp",
      "headers": {
        "X-MCP-API-Key": "YOUR_MCP_API_KEY"
      }
    }
  }
}
```

---

## Use

Once connected, just ask. Your agent picks the tool up on its own, or you can name it explicitly:

```text
"Ask a friend (opus-5-5) to audit this JWT verification middleware for timing leaks and recent CVEs."
"Use ask_a_friend with task_type='spec_critique' to find race conditions in our Redis cache invalidation design."
"Call ask_a_friend with friend_model='gemini-3.8-flash' and task_type='build_tests' to write pytest-asyncio edge cases."
```

---

## What you get

### The friends (enabled out-of-the-box)

| Model Alias | Vertex AI Target (`global`) | Enabled Default Features | Max Output |
|---|---|---|---|
| `opus-5-5` *(Default)* | `claude-opus-5-5` | **Adaptive Thinking (`effort="high"`)**, **Real-Time Web Search (`web_search_20250305`)**, **Ephemeral Prompt Caching (`cache_control`)**, **1M Token Context (`context-1m-2025-08-07`)** | `128,000` tokens |
| `sonnet-5` | `claude-sonnet-5` | **Adaptive Thinking (`effort="high"`)**, **Real-Time Web Search**, **Ephemeral Prompt Caching**, **1M Token Context** | `128,000` tokens |
| `gemini-3.8-flash` / `gemini-pro` | `gemini-3.8-flash` | **`HIGH` Thinking (`thinking_level="HIGH"`)**, **Google Search Grounding (`googleSearch`)**, **URL Context (`urlContext`)**, automatic provider failover | `65,536` tokens |
| `gemini-3.5-flash-lite` | `gemini-3.5-flash-lite` | Ultra-low-latency analytical inference & lightweight verification | `65,536` tokens |

### What's inside

| Component | Module | Description |
|---|---|---|
| **Dual-Transport FastMCP** | [`src/server.py`](src/server.py) | Exposes `ask_a_friend` & `list_friends` tools, `askfriend://` resources, and prompts over `/mcp` (Streamable HTTP), `/sse` (SSE), and `/api/v1/` (REST). |
| **OAuth 2.0 & Auth Guard** | [`src/auth.py`](src/auth.py) | RFC 7591 Dynamic Client Registration, PKCE, HMAC-SHA256 stateless tokens, and constant-time (`secrets.compare_digest`) API key validation. |
| **Security & Taint Interceptor** | [`src/interceptor.py`](src/interceptor.py) | Blocks inbound prompt injection attempts and tracks secret taint to guarantee no raw secret escapes outbound. |
| **Pre-Transit PII Scrubber** | [`scripts/pii.py`](scripts/pii.py) | Regex scrubber that replaces API keys, AWS credentials, Bearer tokens, and emails with `__PII_REDACTED_N__` before leaving your server and rehydrates them on return. |
| **Vertex AI Model Engine** | [`scripts/agent_platform.py`](scripts/agent_platform.py) | Dispatches requests via `AnthropicVertex` / `google-genai` SDKs + REST fallback with Adaptive Thinking, Web Search, Ephemeral Caching, and automatic failover. |
| **SHA-256 Response Cache** | [`scripts/cache.py`](scripts/cache.py) | Deterministic in-memory SHA-256 cache returning repeat queries in `<1ms` with zero token cost. |

---

## How it works

```mermaid
sequenceDiagram
    autonumber
    participant Client as MCP Client (Claude / ChatGPT / Cursor)
    participant Auth as OAuth 2.0 & Security Interceptor
    participant Scrubber as PII Scrubber & SHA-256 Cache
    participant Vertex as Vertex AI Global (Claude Opus 5.5 / Gemini 3.8)

    Client->>Auth: POST /mcp or /sse (ask_a_friend payload)
    Note over Auth: Verifies Bearer / X-MCP-API-Key.<br/>Scans for prompt injection & secret taint.
    Auth->>Scrubber: Validated request
    Note over Scrubber: Replaces keys/emails with __PII_REDACTED_N__.<br/>Checks SHA-256 cache (~1ms hit).
    Scrubber->>Vertex: Dispatches sanitized prompt + Adaptive High Thinking + Web Search
    Note over Vertex: Executes real-time web_search_20250305 / googleSearch<br/>+ ephemeral prompt caching (128K max output).
    Vertex-->>Scrubber: Returns dense, cited technical critique
    Note over Scrubber: Rehydrates __PII_REDACTED_N__ tokens locally<br/>& stores in SHA-256 cache.
    Scrubber-->>Client: Clean, exact, sanitized peer review response
```

1. **Check who's asking.** Your agent calls `ask_a_friend` over Streamable HTTP (`/mcp`), SSE (`/sse`), or OpenAPI REST (`/api/v1/ask`). [`SecurityInterceptor`](src/interceptor.py) blocks prompt-override attacks on the way in.
2. **Scrub your secrets.** [`scrub_pii`](scripts/pii.py) replaces sensitive keys and emails with safe tokens (`__PII_REDACTED_1__`) *before* anything leaves your server.
3. **Check if we've asked this before.** [`ask_a_friend`](scripts/ask_friend.py) hits the SHA-256 cache first; on a miss, it routes to `opus-5-5` (with automatic failover to `gemini-3.8-flash`).
4. **The friend thinks it over.** `claude-opus-5-5` runs with **Adaptive Thinking (`effort="high"`)**, **Ephemeral Prompt Caching**, **1M Context**, and **server-side web search (`web_search_20250305`)**.
5. **Put your secrets back.** Placeholders are restored to your original variable and identifier names locally, then the answer goes back to your agent.

---

## Vertex AI org policies

To enable **Anthropic Server-Side Web Search (`web_search_20250305`)** and **Vertex AI Partner Models** in your GCP project (`deploy.sh` also runs this automatically):

```bash
export PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
gcloud services enable orgpolicy.googleapis.com --project="${PROJECT_ID}"

# 1. Allow Anthropic Partner Model Features (Server-Side Web Search)
cat <<EOF > /tmp/vertex_partner_features.yaml
name: projects/${PROJECT_ID}/policies/vertexai.allowedPartnerModelFeatures
spec:
  rules:
  - allowAll: true
EOF
gcloud org-policies set-policy /tmp/vertex_partner_features.yaml --project="${PROJECT_ID}"

# 2. Allow Vertex AI Model Garden Models
cat <<EOF > /tmp/vertex_allowed_models.yaml
name: projects/${PROJECT_ID}/policies/vertexai.allowedModels
spec:
  rules:
  - allowAll: true
EOF
gcloud org-policies set-policy /tmp/vertex_allowed_models.yaml --project="${PROJECT_ID}"
```
*(If a project is inside a VPC Service Controls perimeter that blocks outbound search traffic, [`scripts/agent_platform.py`](scripts/agent_platform.py) automatically catches `FAILED_PRECONDITION` and retries cleanly without `web_search`.)*

---

## Local development & testing

```bash
# 1. Install dependencies
uv sync --extra dev

# 2. Run full test suite, linter, and type checker
uv run pytest -v && uv run ruff check . && uv run mypy src scripts

# 3. Run local MCP server
AUTH_MODE=none PORT=8080 uv run python -m src.server
```

---

## License

Apache License 2.0 — free and open-source. See [LICENSE](LICENSE) for full details.
