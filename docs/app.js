/**
 * Ask-a-Friend MCP — landing page behaviour
 *
 * Security note: this file never uses innerHTML. All dynamic content is
 * injected via textContent / createElement so that simulated model output
 * can never become executable markup.
 */

document.addEventListener("DOMContentLoaded", () => {

  /* ── Simulation content ──────────────────────────────────────── */

  const SIMULATION_PRESETS = {
    code_review: {
      prompt: "Review this authentication middleware for race conditions and timing leaks.",
      context: `async function verifyToken(req, res, next) {\n  const token = req.headers['authorization'];\n  if (token === process.env.API_SECRET) {\n    return next();\n  }\n  return res.status(401).json({ error: 'Unauthorized' });\n}`,
      response: `🗣️ opus-5-5:\n\nL3: 🔴 bug: Direct string equality (===) comparison against API_SECRET is vulnerable to timing side-channel attacks. Use crypto.timingSafeEqual() with constant-length buffers.\nL2: 🟡 warning: Missing Bearer scheme parsing; header may include 'Bearer ' prefix.\nL4: 🔵 nit: Consider caching normalized tokens to reduce CPU overhead under high concurrency.`,
    },
    security_audit: {
      prompt: "Audit this user lookup query for injection and data leakage.",
      context: `app.get('/api/users', async (req, res) => {\n  const username = req.query.username;\n  const query = "SELECT id, username, email, password_hash FROM users WHERE username = '" + username + "'";\n  const result = await db.query(query);\n  res.json(result.rows);\n});`,
      response: `🗣️ opus-5-5:\n\n- Severity: CRITICAL\n- CWE ID: CWE-89 (SQL Injection)\n- Vulnerable Line: L3 (String concatenation in raw SQL query)\n- Remediation: Use a parameterized query:\n  db.query('SELECT id, username, email FROM users WHERE username = $1', [username]);\n\n- Severity: HIGH\n- CWE ID: CWE-200 (Exposure of Sensitive Information)\n- Vulnerable Line: L3 (Returning password_hash in API response)\n- Remediation: Exclude password_hash from the SELECT projection.`,
    },
    build_tests: {
      prompt: "Generate comprehensive pytest test cases covering edge cases.",
      context: `def parse_semver(v: str) -> tuple[int, int, int]:\n    parts = v.lstrip('v').split('.')\n    if len(parts) != 3:\n        raise ValueError('Invalid semver')\n    return tuple(int(p) for p in parts)`,
      response: `🗣️ opus-5-5:\n\nimport pytest\nfrom parser import parse_semver\n\n@pytest.mark.parametrize("input_str,expected", [\n    ("1.2.3", (1, 2, 3)),\n    ("v1.2.3", (1, 2, 3)),\n    ("0.0.0", (0, 0, 0)),\n    ("10.200.3000", (10, 200, 3000)),\n])\ndef test_parse_semver_valid(input_str, expected):\n    assert parse_semver(input_str) == expected\n\n@pytest.mark.parametrize("invalid_str", [\n    "1.2", "1.2.3.4", "v1.2.x", "", "1.2.-3",\n])\ndef test_parse_semver_invalid(invalid_str):\n    with pytest.raises((ValueError, Exception)):\n        parse_semver(invalid_str)`,
    },
    second_opinion: {
      prompt: "What's a better approach to rate limiting than token bucket here?",
      context: `class TokenBucket:\n    def __init__(self, rate, capacity):\n        self.rate = rate\n        self.capacity = capacity\n        self.tokens = capacity\n        self.last_refill = time.time()\n\n    def consume(self, tokens=1):\n        self._refill()\n        if self.tokens >= tokens:\n            self.tokens -= tokens\n            return True\n        return False`,
      response: `🗣️ opus-5-5:\n\nToken bucket is fine for single-node. Alternatives worth considering:\n\n1. Sliding Window Log — more accurate at boundaries. Store timestamps in a sorted set, count entries within the window. O(log n) per request.\n\n2. Fixed Window Counter with sub-sharding — simple with Redis: INCR with a TTL per window. Add sub-second shards to smooth boundary spikes.\n\n3. Leaky Bucket (as meter) — better for smoothing bursty traffic. Your current implementation already allows bursts up to capacity.\n\nRecommendation: if distributed, use Redis + sliding window. If single-process, your TokenBucket is already optimal — but add a threading.Lock for thread safety (missing in the current implementation).`,
    },
    spec_critique: {
      prompt: "Poke holes in this caching layer design for a multi-tenant SaaS.",
      context: `## Cache Design\n- Global in-memory LRU cache (maxsize=10000)\n- Cache key = SHA256(tenant_id + query)\n- TTL: 1 hour\n- Eviction: LRU\n- No cache invalidation on data mutation\n- Shared across all tenants`,
      response: `🗣️ opus-5-5:\n\nCRITICAL — Tenant Isolation Violation:\nSHA256(tenant_id + query) is collision-resistant, but the cache is shared memory. A noisy tenant filling 9000/10000 slots starves everyone else. Use per-tenant LRU pools or weighted fair-share eviction.\n\nHIGH — Stale Read on Mutation:\n"No cache invalidation on data mutation" means writes are invisible for up to an hour. A user edits a record, refreshes, and sees old data. Add write-through invalidation or event-driven cache busting.\n\nMEDIUM — Memory Bound:\n10K entries x ~50KB = ~500MB. On a 2GB Cloud Run instance that's 25% of RAM. Consider Redis/Memorystore for horizontal scaling.\n\nLOW — No Metrics:\nNo hit/miss ratio tracking. You're flying blind on whether this cache helps at all.`,
    },
  };

  const CLIENT_CONFIGS = {
    claude_connector: `# Claude — Custom Connector (Web & Desktop)
Settings -> Connectors -> Add custom connector:

  Name:            ask-a-friend
  MCP server URL:  https://your-cloud-run-url.run.app/mcp
  Authentication:  Sign in now (auto-detected)
  OAuth client:    Register automatically (RFC 7591)
  Transport:       Streamable HTTP

# Or via Claude Code CLI:
claude mcp add --scope user --transport http ask-a-friend \\
  https://your-cloud-run-url.run.app/mcp \\
  --header "X-MCP-API-Key: YOUR_MCP_API_KEY"`,

    chatgpt_mcp: `# ChatGPT — Native MCP Connector
Settings -> Connectors -> Add:

  Name:            Ask-a-Friend
  Server URL:      https://your-cloud-run-url.run.app/sse
  Authentication:  OAuth (auto-negotiated via RFC 7591)
  Scope:           mcp:tools`,

    chatgpt_action: `# Custom GPT — Action
Configure -> Actions -> Import from URL:

  Import Schema URL:
  https://your-cloud-run-url.run.app/openapi.yaml

  Authentication:
    Type:  API Key (Bearer)
    Key:   <YOUR_MCP_API_KEY>`,

    cursor: `// Cursor IDE — .cursor/mcp.json
{
  "mcpServers": {
    "ask-a-friend": {
      "url": "https://your-cloud-run-url.run.app/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_API_KEY"
      }
    }
  }
}`,

    gemini: `// Gemini / Antigravity CLI
// ~/.gemini/settings.json  or  .gemini/settings.json
{
  "mcpServers": {
    "ask-a-friend": {
      "url": "https://your-cloud-run-url.run.app/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_API_KEY"
      }
    }
  }
}`,

    claude: `// Claude Desktop — claude_desktop_config.json
{
  "mcpServers": {
    "ask-a-friend": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-cloud-run-url.run.app/sse",
        "--header",
        "Authorization: Bearer YOUR_MCP_API_KEY"
      ]
    }
  }
}`,
  };

  const PIPELINE_STAGES = [
    { key: "auth",      detail: "✓ Token verified",             ms: 180 },
    { key: "injection", detail: "✓ No injection detected",      ms: 220 },
    { key: "pii",       detail: "✓ 2 secrets redacted",         ms: 160 },
    { key: "cache",     detail: "MISS — asking for real",       ms: 120 },
    { key: "route",     detail: null,                           ms: 200 },
    { key: "inference", detail: "Thinking…",                    ms: 1400 },
    { key: "rehydrate", detail: "✓ secrets restored",           ms: 100 },
  ];

  const ORIGINAL_DETAILS = {
    auth:      "Verifying token…",
    injection: "Scanning…",
    pii:       "Redacting…",
    cache:     "Cache check…",
    route:     "Resolving…",
    inference: "Calling model…",
    rehydrate: "Restoring…",
  };

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── Mobile nav ──────────────────────────────────────────────── */

  const navToggle = document.querySelector(".nav-toggle");
  const navLinks = document.getElementById("nav-links");

  if (navToggle && navLinks) {
    navToggle.addEventListener("click", () => {
      const open = navLinks.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", String(open));
    });
    navLinks.addEventListener("click", (e) => {
      if (e.target.matches("a")) {
        navLinks.classList.remove("open");
        navToggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  /* ── Toast ───────────────────────────────────────────────────── */

  const toast = document.getElementById("toast");
  let toastTimer;

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 1800);
  }

  /* ── Client config tabs ──────────────────────────────────────── */

  const tabs = document.querySelectorAll(".tab");
  const configCode = document.getElementById("config-code");
  const copyBtn = document.getElementById("copy-config");

  function selectClient(key, button) {
    if (configCode && CLIENT_CONFIGS[key]) {
      configCode.textContent = CLIENT_CONFIGS[key];
    }
    tabs.forEach((t) => {
      const isActive = t === button;
      t.classList.toggle("active", isActive);
      t.setAttribute("aria-selected", String(isActive));
    });
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => selectClient(tab.dataset.client, tab));
  });

  // Seed the default tab.
  if (tabs.length) selectClient(tabs[0].dataset.client, tabs[0]);

  if (copyBtn && configCode) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(configCode.textContent);
        showToast("Copied to clipboard");
      } catch {
        showToast("Copy failed — select the text manually");
      }
    });
  }

  /* ── Demo pipeline ───────────────────────────────────────────── */

  const taskSelect = document.getElementById("task-select");
  const promptInput = document.getElementById("prompt-input");
  const contextInput = document.getElementById("context-input");
  const runBtn = document.getElementById("run-sim-btn");
  const runBtnLabel = document.getElementById("run-btn-label");
  const outputWindow = document.getElementById("output-window");
  const modelChip = document.getElementById("resolved-model-badge");
  const steps = document.querySelectorAll(".pipe-step");
  const statsBar = document.getElementById("stats-bar");
  const statLatency = document.getElementById("stat-latency");
  const statModel = document.getElementById("stat-model");
  const statTokens = document.getElementById("stat-tokens");
  const statCache = document.getElementById("stat-cache");

  let running = false;
  let runToken = 0; // invalidates in-flight runs when the user switches task

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function loadPreset(key) {
    const preset = SIMULATION_PRESETS[key];
    if (!preset) return;
    if (promptInput) promptInput.value = preset.prompt;
    if (contextInput) contextInput.value = preset.context;
    if (modelChip) modelChip.textContent = "opus-5-5";
  }

  function resetPipeline() {
    steps.forEach((step) => {
      step.classList.remove("active", "done");
      const timer = step.querySelector(".step-timer");
      if (timer) timer.textContent = "";
      const detail = step.querySelector(".step-detail");
      const key = step.dataset.step;
      if (detail && ORIGINAL_DETAILS[key]) detail.textContent = ORIGINAL_DETAILS[key];
    });
    if (outputWindow) {
      outputWindow.classList.remove("streaming");
      outputWindow.textContent = "Waiting for the friend to reply…";
    }
    if (statsBar) statsBar.classList.remove("visible");
  }

  async function runPipeline(key) {
    const preset = SIMULATION_PRESETS[key];
    if (!preset || running) return;

    running = true;
    const myToken = ++runToken;
    const stillCurrent = () => myToken === runToken;

    if (runBtn) runBtn.disabled = true;
    if (runBtnLabel) runBtnLabel.textContent = "Running…";
    resetPipeline();

    const started = performance.now();

    for (const stage of PIPELINE_STAGES) {
      if (!stillCurrent()) { running = false; return; }

      const step = document.querySelector(`.pipe-step[data-step="${stage.key}"]`);
      if (!step) continue;

      step.classList.add("active");

      const wait = prefersReducedMotion ? Math.min(stage.ms, 120) : stage.ms;
      await sleep(wait);
      if (!stillCurrent()) { running = false; return; }

      const detail = step.querySelector(".step-detail");
      if (detail) {
        detail.textContent = stage.key === "route"
          ? "→ opus-5-5 (Vertex AI, global)"
          : stage.detail;
      }
      const timer = step.querySelector(".step-timer");
      if (timer) timer.textContent = `${wait}ms`;

      step.classList.remove("active");
      step.classList.add("done");
    }

    // Stream the response.
    if (outputWindow) {
      outputWindow.textContent = "";
      outputWindow.classList.add("streaming");

      const text = preset.response;
      if (prefersReducedMotion) {
        outputWindow.textContent = text;
      } else {
        const chunk = 3;
        for (let i = 0; i < text.length; i += chunk) {
          if (!stillCurrent()) { running = false; return; }
          outputWindow.textContent += text.slice(i, i + chunk);
          outputWindow.scrollTop = outputWindow.scrollHeight;
          await sleep(8);
        }
      }
      outputWindow.classList.remove("streaming");
    }

    if (!stillCurrent()) { running = false; return; }

    const elapsed = ((performance.now() - started) / 1000).toFixed(1);
    if (statLatency) statLatency.textContent = `${elapsed}s`;
    if (statModel) statModel.textContent = "opus-5-5";
    if (statTokens) statTokens.textContent = String(Math.round(preset.response.length / 3.6));
    if (statCache) statCache.textContent = "MISS";
    if (statsBar) statsBar.classList.add("visible");

    if (runBtn) runBtn.disabled = false;
    if (runBtnLabel) runBtnLabel.textContent = "Run it again";
    running = false;
  }

  if (taskSelect) {
    loadPreset(taskSelect.value);
    taskSelect.addEventListener("change", (e) => {
      runToken++;          // cancel any in-flight run
      running = false;
      loadPreset(e.target.value);
      resetPipeline();
      runPipeline(e.target.value);
    });
  }

  if (runBtn && taskSelect) {
    runBtn.addEventListener("click", () => runPipeline(taskSelect.value));
  }

  /* ── Auto-play the demo the first time it scrolls into view ──── */

  const demoSection = document.getElementById("demo");
  if (demoSection && taskSelect && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          observer.disconnect();
          runPipeline(taskSelect.value);
        }
      });
    }, { threshold: 0.35 });
    observer.observe(demoSection);
  } else if (taskSelect) {
    // No IntersectionObserver: just show the finished state.
    runPipeline(taskSelect.value);
  }

  /* ── Replay the hero conversation whenever it re-enters view ─── */

  const heroChat = document.getElementById("hero-chat");
  if (heroChat && !prefersReducedMotion && "IntersectionObserver" in window) {
    const chatObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          heroChat.querySelectorAll(".chat-row").forEach((row) => {
            row.style.animation = "none";
            void row.offsetWidth;   // force reflow to restart the animation
            row.style.animation = "";
          });
        }
      });
    }, { threshold: 0.4 });
    chatObserver.observe(heroChat);
  }
});
