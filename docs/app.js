/**
 * Ask-a-Friend Consumer MCP — Interactive Portal Application
 * Strictly enforces secure DOM manipulation (no innerHTML).
 */

document.addEventListener("DOMContentLoaded", () => {
  // Preset simulation data for interactive playground
  const SIMULATION_PRESETS = {
    code_review: {
      prompt: "Review this authentication middleware for race conditions and timing leaks.",
      context: `async function verifyToken(req, res, next) {\n  const token = req.headers['authorization'];\n  if (token === process.env.API_SECRET) {\n    return next();\n  }\n  return res.status(401).json({ error: 'Unauthorized' });\n}`,
      model: "opus-5-5 (Vertex AI global, max tokens)",
      response: `🗣️ **opus-5-5**:\n\nL3: 🔴 bug: Direct string equality (===) comparison against API_SECRET is vulnerable to timing side-channel attacks. Use crypto.timingSafeEqual() with constant-length buffers.\nL2: 🟡 warning: Missing Bearer scheme parsing; header may include 'Bearer ' prefix.\nL4: 🔵 nit: Consider caching normalized tokens to reduce CPU overhead under high concurrency.`,
    },
    security_audit: {
      prompt: "Audit this user lookup query for injection and data leakage.",
      context: `app.get('/api/users', async (req, res) => {\n  const username = req.query.username;\n  const query = "SELECT id, username, email, password_hash FROM users WHERE username = '" + username + "'";\n  const result = await db.query(query);\n  res.json(result.rows);\n});`,
      model: "opus-5-5 (Vertex AI global, max tokens)",
      response: `🗣️ **opus-5-5**:\n\n- **Severity**: CRITICAL\n- **CWE ID**: CWE-89 (SQL Injection)\n- **Vulnerable Line**: L3 (String concatenation in raw SQL query)\n- **Remediation**: Use parameterized query:\n  \`const result = await db.query('SELECT id, username, email FROM users WHERE username = $1', [username]);\`\n\n- **Severity**: HIGH\n- **CWE ID**: CWE-200 (Exposure of Sensitive Information)\n- **Vulnerable Line**: L3 (Returning password_hash in API response)\n- **Remediation**: Exclude password_hash from SELECT projection.`,
    },
    build_tests: {
      prompt: "Generate comprehensive pytest test cases covering edge cases.",
      context: `def parse_semver(v: str) -> tuple[int, int, int]:\n    parts = v.lstrip('v').split('.')\n    if len(parts) != 3:\n        raise ValueError('Invalid semver')\n    return tuple(int(p) for p in parts)`,
      model: "opus-5-5 (Vertex AI global, max tokens)",
      response: `🗣️ **opus-5-5**:\n\n\`\`\`python\nimport pytest\nfrom parser import parse_semver\n\n@pytest.mark.parametrize("input_str,expected", [\n    ("1.2.3", (1, 2, 3)),\n    ("v1.2.3", (1, 2, 3)),\n    ("0.0.0", (0, 0, 0)),\n    ("10.200.3000", (10, 200, 3000)),\n])\ndef test_parse_semver_valid(input_str, expected):\n    assert parse_semver(input_str) == expected\n\n@pytest.mark.parametrize("invalid_str", [\n    "1.2",\n    "1.2.3.4",\n    "v1.2.x",\n    "",\n    "1.2.-3",\n])\ndef test_parse_semver_invalid(invalid_str):\n    with pytest.raises((ValueError, Exception)):\n        parse_semver(invalid_str)\n\`\`\``,
    },
    second_opinion: {
      prompt: "What's a better approach to rate limiting than token bucket here?",
      context: `class TokenBucket:\n    def __init__(self, rate, capacity):\n        self.rate = rate\n        self.capacity = capacity\n        self.tokens = capacity\n        self.last_refill = time.time()\n\n    def consume(self, tokens=1):\n        self._refill()\n        if self.tokens >= tokens:\n            self.tokens -= tokens\n            return True\n        return False`,
      model: "opus-5-5 (Vertex AI global, max tokens)",
      response: `🗣️ **opus-5-5**:\n\nToken bucket is fine for single-node; consider these alternatives for your use case:\n\n1. **Sliding Window Log** — More accurate at boundaries. Store timestamps of each request in a sorted set. Count entries within the window. O(log n) per request.\n\n2. **Fixed Window Counter with sub-sharding** — If you want simplicity + Redis, use INCR with TTL per window. Add sub-second shards to smooth boundary spikes.\n\n3. **Leaky Bucket (as meter)** — Better for smoothing bursty traffic. Your current impl already allows bursts up to capacity.\n\n**Recommendation:** If distributed, use Redis + sliding window. If single-process, your TokenBucket is already optimal — just add \`threading.Lock\` for thread safety (missing in current impl).`,
    },
    spec_critique: {
      prompt: "Poke holes in this caching layer design for a multi-tenant SaaS.",
      context: `## Cache Design\n- Global in-memory LRU cache (maxsize=10000)\n- Cache key = SHA256(tenant_id + query)\n- TTL: 1 hour\n- Eviction: LRU\n- No cache invalidation on data mutation\n- Shared across all tenants`,
      model: "opus-5-5 (Vertex AI global, max tokens)",
      response: `🗣️ **opus-5-5**:\n\n**CRITICAL — Tenant Isolation Violation:**\n- SHA256(tenant_id + query) is collision-resistant but the cache is *shared memory*. A noisy tenant filling 9000/10000 slots starves all others. Use per-tenant LRU pools or weighted fair-share eviction.\n\n**HIGH — Stale Read on Mutation:**\n- "No cache invalidation on data mutation" means writes are invisible for up to 1 hour. For SaaS this is unacceptable — user edits a record, refreshes, sees old data. Add write-through invalidation or event-driven cache busting.\n\n**MEDIUM — Memory Bound:**\n- 10K entries × avg 50KB = ~500MB. On a 2GB Cloud Run instance this is 25% of RAM. Consider offloading to Redis/Memorystore for horizontal scaling.\n\n**LOW — No Metrics:**\n- No hit/miss ratio tracking. You're flying blind on whether this cache is even useful.`,
    },
  };

  // Client configuration snippets
  const CLIENT_CONFIGS = {
    claude_connector: `# 1. Claude Custom Connector (Claude Web & Desktop UI)
Settings -> Connectors -> Add custom connector:
- Name: ask-a-friend
- MCP server URL: https://your-cloud-run-url.run.app/mcp
- Authentication: Sign in now (Detected)
- OAuth client: Register automatically (Detected via RFC 7591)
- Advanced -> Transport: Streamable HTTP

# 2. Claude Code CLI (claude)
claude mcp add --scope user --transport http ask-a-friend \\
  https://your-cloud-run-url.run.app/mcp \\
  --header "X-MCP-API-Key: YOUR_MCP_API_KEY"`,

    chatgpt_mcp: `# ChatGPT Native MCP Connector Settings
Name: Ask-a-Friend
Server URL: https://your-cloud-run-url.run.app/sse
Authentication: OAuth (Auto-negotiated via RFC 7591)
Scope: mcp:tools`,

    chatgpt_action: `# Custom GPT Action Configuration
Import Schema URL:
https://your-cloud-run-url.run.app/openapi.yaml

Authentication:
Type: API Key (Bearer)
Key: <YOUR_MCP_API_KEY>`,

    claude: `{
  "mcpServers": {
    "ask-a-friend": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-cloud-run-url.run.app/mcp",
        "--header",
        "X-MCP-API-Key: YOUR_MCP_API_KEY"
      ]
    }
  }
}`,

    cursor: `{
  "mcpServers": {
    "ask-a-friend": {
      "url": "https://your-cloud-run-url.run.app/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_API_KEY"
      }
    }
  }
}`,

    gemini: `{
  "mcpServers": {
    "ask-a-friend": {
      "url": "https://your-cloud-run-url.run.app/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_API_KEY"
      }
    }
  }
}`,
  };

  // Setup Task Preset Switcher in Playground
  const taskSelect = document.getElementById("task-select");
  const promptInput = document.getElementById("prompt-input");
  const contextInput = document.getElementById("context-input");
  const runBtn = document.getElementById("run-sim-btn");
  const outputWindow = document.getElementById("output-window");
  const resolvedModelBadge = document.getElementById("resolved-model-badge");

  // --- Pipeline Animation Engine ---

  const PIPELINE_STAGES = [
    { key: "auth",      detail: "✓ Token verified",                   ms: 180  },
    { key: "injection",  detail: "✓ No injection detected",           ms: 220  },
    { key: "pii",       detail: "✓ 0 PII tokens redacted",            ms: 160  },
    { key: "cache",     detail: "MISS — computing fresh response",     ms: 120  },
    { key: "route",     detail: null, /* set dynamically */            ms: 200  },
    { key: "inference", detail: "Streaming from Vertex AI…",           ms: 1400 },
    { key: "rehydrate", detail: "✓ 0 tokens restored",                ms: 100  },
  ];

  // Model routing table (matches actual server config)
  const MODEL_ROUTES = {
    code_review:    { model: "opus-5-5",           region: "global",    label: "opus-5-5" },
    security_audit: { model: "opus-5-5",           region: "global",    label: "opus-5-5" },
    build_tests:    { model: "opus-5-5",           region: "global",    label: "opus-5-5" },
    second_opinion: { model: "opus-5-5",           region: "global",    label: "opus-5-5" },
    spec_critique:  { model: "opus-5-5",           region: "global",    label: "opus-5-5" },
  };

  const allSteps = document.querySelectorAll(".pipeline-step");
  const allConnectors = document.querySelectorAll(".pipeline-connector");
  const statsBar = document.getElementById("stats-bar");
  const statLatency = document.getElementById("stat-latency");
  const statModel = document.getElementById("stat-model");
  const statTokens = document.getElementById("stat-tokens");
  const statCache = document.getElementById("stat-cache");
  const runBtnLabel = document.getElementById("run-btn-label");

  function loadPreset(taskKey) {
    const preset = SIMULATION_PRESETS[taskKey];
    if (!preset) return;

    if (promptInput) promptInput.value = preset.prompt;
    if (contextInput) contextInput.value = preset.context;

    const route = MODEL_ROUTES[taskKey];
    if (resolvedModelBadge && route) {
      resolvedModelBadge.textContent = route.label;
    }
  }

  if (taskSelect) {
    taskSelect.addEventListener("change", (e) => {
      loadPreset(e.target.value);
      resetPipeline();
    });
    loadPreset(taskSelect.value);
  }

  function resetPipeline() {
    allSteps.forEach((s) => {
      s.classList.remove("active", "done");
      const timerEl = s.querySelector(".step-timer");
      if (timerEl) timerEl.textContent = "";
      const detailEl = s.querySelector(".step-detail");
      const stepKey = s.getAttribute("data-step");
      const stage = PIPELINE_STAGES.find((st) => st.key === stepKey);
      if (detailEl && stage) {
        // Restore original text
        const originals = {
          auth: "Verifying Bearer token…",
          injection: "Scanning for prompt injection…",
          pii: "Redacting secrets & PII…",
          cache: "SHA-256 cache check…",
          route: "Resolving model…",
          inference: "Calling model…",
          rehydrate: "Restoring redacted tokens…",
        };
        detailEl.textContent = originals[stepKey] || "";
      }
    });
    allConnectors.forEach((c) => c.classList.remove("lit"));
    if (outputWindow) {
      outputWindow.classList.remove("streaming");
      outputWindow.textContent = 'Select a task type and click "Execute Pipeline" to simulate a full MCP round-trip.';
    }
    if (statsBar) statsBar.classList.remove("visible");
  }

  async function runPipeline(taskKey) {
    const preset = SIMULATION_PRESETS[taskKey];
    const route = MODEL_ROUTES[taskKey];
    if (!preset || !route) return;

    // Disable button
    if (runBtn) runBtn.disabled = true;
    if (runBtnLabel) runBtnLabel.textContent = "Running…";

    // Reset state
    resetPipeline();
    if (outputWindow) outputWindow.textContent = "";

    let totalMs = 0;
    const connectorNodes = Array.from(allConnectors);
    let connectorIdx = 0;

    // Walk through each pipeline stage
    for (let i = 0; i < PIPELINE_STAGES.length; i++) {
      const stage = PIPELINE_STAGES[i];
      const stepEl = document.querySelector(`.pipeline-step[data-step="${stage.key}"]`);
      if (!stepEl) continue;

      // Activate step
      stepEl.classList.add("active");

      // Dynamic detail for route step
      if (stage.key === "route") {
        const routeDetail = stepEl.querySelector(".step-detail");
        if (routeDetail) routeDetail.textContent = `Routing → ${route.label} (${route.region})`;
      }

      // Dynamic detail for inference step
      if (stage.key === "inference") {
        const infDetail = stepEl.querySelector(".step-detail");
        if (infDetail) infDetail.textContent = `Streaming from ${route.label}…`;
      }

      // Wait simulated duration
      const jitter = Math.floor(Math.random() * 80) - 40;
      const duration = Math.max(60, stage.ms + jitter);
      await sleep(duration);
      totalMs += duration;

      // Mark done
      stepEl.classList.remove("active");
      stepEl.classList.add("done");

      // Update detail text
      const detailEl = stepEl.querySelector(".step-detail");
      if (detailEl) {
        if (stage.key === "route") {
          detailEl.textContent = `✓ Routed → ${route.label}`;
        } else if (stage.key === "inference") {
          detailEl.textContent = `✓ ${route.label} responded`;
        } else if (stage.detail) {
          detailEl.textContent = stage.detail;
        }
      }

      // Show timer
      const timerEl = stepEl.querySelector(".step-timer");
      if (timerEl) timerEl.textContent = `${duration}ms`;

      // Light up connector below (if any)
      if (connectorIdx < connectorNodes.length) {
        connectorNodes[connectorIdx].classList.add("lit");
        connectorIdx++;
      }
    }

    // Stream the response with typewriter
    if (outputWindow) {
      outputWindow.classList.add("streaming");
      await typeWriterAsync(outputWindow, preset.response, 5);
      outputWindow.classList.remove("streaming");
    }

    // Show stats
    const tokens = Math.floor(preset.response.length * 0.27);
    if (statLatency) statLatency.textContent = `${totalMs}ms`;
    if (statModel) statModel.textContent = route.label;
    if (statTokens) statTokens.textContent = `~${tokens}`;
    if (statCache) {
      statCache.textContent = "MISS";
      statCache.style.color = "var(--accent-orange, #d29922)";
    }
    if (statsBar) statsBar.classList.add("visible");

    // Re-enable button
    if (runBtn) runBtn.disabled = false;
    if (runBtnLabel) runBtnLabel.textContent = "Execute Pipeline";
  }

  if (runBtn) {
    runBtn.addEventListener("click", () => {
      const selectedTask = taskSelect ? taskSelect.value : "code_review";
      runPipeline(selectedTask);
    });
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Typewriter effect (async/await version).
   */
  function typeWriterAsync(element, text, speed = 8) {
    return new Promise((resolve) => {
      let i = 0;
      element.textContent = "";
      function tick() {
        if (i < text.length) {
          element.textContent += text[i++];
          setTimeout(tick, speed);
        } else {
          resolve();
        }
      }
      tick();
    });
  }

  // Setup Client Configuration Tabs
  const tabButtons = document.querySelectorAll(".tab-btn");
  const codeSnippet = document.getElementById("config-code");

  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");

      const clientKey = btn.getAttribute("data-client");
      if (codeSnippet && CLIENT_CONFIGS[clientKey]) {
        codeSnippet.textContent = CLIENT_CONFIGS[clientKey];
      }
    });
  });

  // Setup 1-Click Copy Buttons with Toast Notification
  const copyBtns = document.querySelectorAll(".copy-btn");
  const toast = document.getElementById("toast");

  function showToast(message = "Copied to clipboard!") {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => {
      toast.classList.remove("show");
    }, 2000);
  }

  copyBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-target");
      const targetEl = document.getElementById(targetId);
      if (targetEl) {
        navigator.clipboard.writeText(targetEl.textContent).then(() => {
          showToast();
        });
      }
    });
  });
});
