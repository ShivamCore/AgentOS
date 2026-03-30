<div align="center">

# ⚡ AgentOS

### Local-first autonomous agents that plan, execute, and learn — no cloud required.

[![CI](https://github.com/ShivamCore/AgentOS/actions/workflows/ci.yml/badge.svg)](https://github.com/ShivamCore/AgentOS/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-black)](https://ollama.com)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker)](docker-compose.yml)

<br/>

**You describe a task. AgentOS plans it, executes it, fixes its own errors, and saves results. Locally. Instantly.**

<br/>

</div>

---

## What it does

- 🧠 **Plans autonomously** — a Planner agent decomposes any task into a dependency-ordered DAG of atomic steps
- ⚙️ **Executes the graph** — each node runs in parallel where possible, sequentially where required
- 🔒 **Sandboxes everything** — Docker containers (subprocess fallback) with CPU/memory/time hard limits
- 🧬 **Remembers context** — ChromaDB semantic memory recalls relevant past tasks at inference time
- ⚡ **Runs fully offline** — Ollama on your machine, zero OpenAI, zero data leaving your device
- 🔁 **Self-corrects** — failed steps are retried with the debugger agent before raising

---

## ⚡ Live Terminal Demo
Test the AgentOS intelligent execution pipeline completely natively without Docker using our built-in Rich terminal dashboard.

```bash
agentos demo --watch
```

**Real-time Features:**
- 🟢 **Live DAG Execution**: Watch steps complete sequentially or in parallel.
- ⚡ **Instant ETAs**: Heuristic timing countdowns for each operation.
- 🧠 **Thought Streaming**: See the LLM's raw cognitive stream directly inside the protected sandbox.

---

## 🚀 Why AgentOS?

**Copilots suggest. AgentOS executes.**

Your AI IDE autocompletes a line. AgentOS takes a task, breaks it into 6 steps, runs each one in a sandbox, fixes what breaks, and delivers working output.

**The difference:**

| | Copilot / Cursor | AgentOS |
|---|---|---|
| Inputs | A line of code | A task description |
| Output | A suggestion | Working files |
| Execution | You press run | It presses run |
| Errors | You fix them | It fixes them |
| Memory | None | Semantic recall across tasks |
| Privacy | Cloud API | 100% local |
| Cost | $/month | Free forever |

This is not an IDE plugin. It's an autonomous execution system.

---

## 🧠 Autonomous Planning

The Planner agent reads your task description and outputs a validated DAG — a graph of atomic steps with declared dependencies, assigned agents, and required tools. No manual step definition.

```json
{
  "steps": [
    { "step_id": "1", "description": "Scaffold FastAPI app", "preferred_agent": "coder", "dependencies": [] },
    { "step_id": "2", "description": "Add Stripe webhook handler", "preferred_agent": "coder", "dependencies": ["1"] },
    { "step_id": "3", "description": "Write test suite", "preferred_agent": "coder", "dependencies": ["2"] },
    { "step_id": "4", "description": "Run tests and verify", "preferred_agent": "coder", "dependencies": ["3"] }
  ]
}
```

---

## ⚙️ Execution DAG

`DAGOrchestrator` resolves the dependency graph and runs steps in a `ThreadPoolExecutor` — parallel where safe, sequential where required. Each step reports status via a callback that updates the DB and broadcasts to the UI over WebSocket.

```
User Input
   │
   ▼
Planner (LLM) ──► DAG Graph
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
      [Step 1]    [Step 2]    [Step 3]   ← parallel
         │
         ▼
      [Step 4]                           ← waits for deps
```

---

## 🔒 Secure Sandbox

Every step executes inside a Docker container with hard limits. No agent can escape its workspace.

```
CPU:    limited per container
Memory: capped (configurable)
Time:   hard kill at TASK_TIMEOUT_SECONDS
FS:     isolated volume mount — no host access
Net:    internal network only
User:   non-root (UID 1000)
```

If Docker is unavailable, a `subprocess` fallback enforces limits via `ulimit`. The system degrades gracefully, never silently.

---

## 🧰 Tool System

Agents declare their tools in a Markdown manifest. The `ToolRegistry` validates all tool calls at load time — an agent referencing an unknown tool fails fast, not at runtime.

| Tool | What it can do |
|---|---|
| `file_read` | Read workspace files |
| `file_write` | Write or patch workspace files |
| `terminal` | Run shell commands inside the sandbox |
| `git` | Clone, diff, log within the sandbox |

---

## 🧬 Semantic Memory

ChromaDB stores every task's context, decisions, and outputs as vector embeddings. Before each LLM call, the memory engine retrieves the top-K semantically similar past tasks and injects them as context — giving agents genuine recall without fine-tuning.

```python
# Automatic — no configuration needed
memories = memory_engine.recall(task_description, top_k=3)
# → injects relevant past task summaries into the system prompt
```

---

## ⚡ TurboQuant Optimization

Benchmarked on Apple Silicon with real code-generation prompts.

| Model | Before (Q8_0) | After (Q4_0) | Speedup | Size |
|---|---|---|---|---|
| deepseek-coder 1.3b | 30.4 tok/s · 24.6s avg | **60.4 tok/s · 5.9s avg** | **2× faster** | 45% smaller |
| deepseek-coder 6.7b | 11.8 tok/s · 95.0s avg | **22.2 tok/s · 49.7s avg** | **1.88× faster** | 46% smaller |
| qwen2.5-coder 1.5b | 30.8 tok/s · 26.2s avg | **45.1 tok/s · 26.1s avg** | **1.46× faster** | 40% smaller |

**Real numbers. Real hardware. No cloud.**

The `fibonacci_dp` code generation task:

```
Before  →  47.74s   (Q8_0, cold load)
After   →   4.48s   (Q4_0, warm)
Savings →  43 seconds per task
```

Model routing picks the right quantization tier automatically:

```python
_TIER_SPEED    = ['deepseek-coder:1.3b']       # fast code generation
_TIER_ACCURACY = ['deepseek-coder:6.7b']        # complex planning
_TIER_BALANCED = ['qwen2.5:3b']                 # debug tasks
```

### 🦙 Bring Your Own Model
AgentOS isn't locked to our default stack. You can plug in **any** local LLM supported by Ollama. 

To apply **TurboQuant** (our high-speed quantization profile) to your own preferred models:

1. **Pull the optimized quantization:** Instead of pulling the massive default weights, specify the Q4 variant explicitly via Ollama:
   ```bash
   ollama pull llama3:8b-instruct-q4_0
   ```
2. **Auto-Discovery:** That's it! AgentOS will automatically detect the new local model. The internal `ModelRouter` profiles its parameter size and instantly assigns it to the optimal execution tier (Speed, Balanced, or Accuracy) on the fly without any manual registry updates.

## 🏗️ Architecture

```
┌─────────────────────────────────────────┐
│           Next.js Frontend              │
│  Task Builder · DAG View · Live Logs    │
└──────────────────┬──────────────────────┘
                   │ REST + WebSocket
┌──────────────────▼──────────────────────┐
│           FastAPI Backend               │
│  Rate Limiter · Validator · Backpressure│
└──────────────────┬──────────────────────┘
                   │ Celery queue (Redis)
┌──────────────────▼──────────────────────┐
│           Agent Pipeline                │
│                                         │
│  Planner ──► DAG ──► DAGOrchestrator   │
│                           │             │
│                    ┌──────▼──────┐      │
│                    │  Sandbox    │      │
│                    │  (Docker)   │      │
│                    └──────┬──────┘      │
│                           │             │
│         Memory Engine ◄───┘             │
│         (ChromaDB)   SQLite   Redis     │
└─────────────────────────────────────────┘
```

---

## 🛠️ Installation

**Prerequisites**

| Dependency | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Backend + agents |
| Node.js | ≥ 18 | Frontend only |
| Ollama | ≥ 0.1.29 | Local LLM inference |
| Redis | ≥ 7.0 | Task broker |
| Docker | ≥ 24.0 | Optional — sandbox + compose |

```bash
# 1. Clone
git clone https://github.com/ShivamCore/AgentOS
cd agentos/local-coder-agent

# 2. Configure
cp .env.example .env
# Edit OLLAMA_MODEL if needed — default: deepseek-coder:1.3b

# 3. Pull your model
ollama pull deepseek-coder:1.3b

# 4. Install everything
make install

# 5. Run
make dev
```

Open **http://localhost:3000** → Task Builder → Submit a task.

---

## ⚡ Quick Start

```bash
# Start Ollama (if not running as a service)
ollama serve

# Start Redis
brew services start redis   # macOS
# or: redis-server

# Start the full stack
./start.sh
# → API:      http://localhost:8000
# → Frontend: http://localhost:3000
# → Flower:   http://localhost:5555
```

Submit your first task via curl:

```bash
curl -X POST http://localhost:8000/tasks/create \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Hello World API",
    "description": "Create a FastAPI endpoint that returns {\"hello\": \"world\"}",
    "task_type": "create_api",
    "tech_stack": ["Python", "FastAPI"],
    "features": ["GET /hello endpoint", "GET /health check"],
    "constraints": { "max_time": 120, "max_steps": 3, "risk_level": "safe" }
  }'
```

Or use an example file:

```bash
curl -X POST http://localhost:8000/tasks/create \
  -H "Content-Type: application/json" \
  -d @examples/tasks/hello_world_api.json
```

---

## 📌 Example Task

**Input:**

```json
{
  "title": "Stripe Webhook Handler",
  "description": "FastAPI endpoint for payment_intent.succeeded with signature validation",
  "task_type": "create_api",
  "tech_stack": ["Python", "FastAPI", "Stripe"],
  "features": ["HMAC-SHA256 signature validation", "Replay attack prevention", "Idempotent payment logging"],
  "constraints": { "max_time": 300, "max_steps": 5, "risk_level": "safe" }
}
```

**What AgentOS does:**

```
Step 1  [coder]    Scaffold FastAPI app with project structure
Step 2  [coder]    Implement /webhook endpoint with Stripe signature validation
Step 3  [coder]    Add replay attack prevention (300s tolerance window)
Step 4  [coder]    Write idempotent payment event logger
Step 5  [coder]    Run test suite — verify all assertions pass
```

**Output:** Working `src/api/webhooks/stripe.py` — tested, validated, ready to deploy.

---

## ⚡ Performance

Benchmarked locally on Apple Silicon, real code generation prompts:

```
fibonacci_dp task (deepseek-coder 1.3b):

  Before TurboQuant:  47.74s  ████████████████████████ Q8_0  1.43 GB
  After TurboQuant:    4.48s  ███ Q4_0  0.78 GB

  2× faster inference.  45% smaller model.  Zero quality regression on code tasks.
```

```
binary_search_tree task:

  Before:  14.06s
  After:    7.06s

  async_retry task:

  Before:  11.89s
  After:    6.10s
```

Full benchmark report: [`docs/turboquant_results.md`](docs/turboquant_results.md)

---

## 🏗️ Project Structure

```
local-coder-agent/
├── agent/               ← agent loader, selector, planner, DAG executor, memory, sandbox
├── agents/              ← agent Markdown manifests (coder, debugger, planner)
├── backend/             ← FastAPI app, Celery workers, DB models, rate limiter
├── frontend/            ← Next.js UI (task builder, DAG view, live logs)
├── tests/               ← unit, integration, contract, regression, security, perf
├── scripts/             ← benchmarking + utility scripts
├── examples/            ← example task JSON + custom agent Markdown
├── docs/                ← architecture, API reference, agent format spec
├── Makefile             ← setup / run / test / lint in one command
├── docker-compose.yml   ← full stack: API + Celery + Redis + Flower
└── .env.example         ← all configuration documented
```

---

## 📚 Documentation

| Doc | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Full system design, component inventory, design decisions |
| [RUNBOOK.md](RUNBOOK.md) | Step-by-step local setup from zero |
| [DEPLOYMENT.md](DEPLOYMENT.md) | CI/CD, Docker, GitHub secrets, staging → production |
| [SECURITY.md](SECURITY.md) | Security model, responsible disclosure, automated scanning |
| [docs/api-reference.md](docs/api-reference.md) | REST + WebSocket API reference |
| [docs/agent-format.md](docs/agent-format.md) | How to write custom agents |

---

## 🤝 Contributing

```bash
# Fork → clone → branch
git checkout -b feat/your-feature

# Install dev dependencies + pre-commit hooks
make install

# Make changes, then:
make lint typecheck     # must pass
make test               # must pass (≥90% coverage)

# Commit (conventional commits enforced)
git commit -m "feat(agent): describe your change"
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide, PR checklist, and code style rules.

---

## 🌐 Roadmap

- [ ] Agent-to-agent communication (multi-agent collaboration)
- [ ] Browser tool (Playwright integration for web tasks)
- [ ] Voice input → task submission
- [ ] Plugin system for custom tools
- [ ] PostgreSQL + PgVector for scaled deployments
- [ ] Model fine-tuning pipeline on completed tasks

---

## 📜 License

MIT © 2024 AgentOS Contributors

---

<div align="center">

**Built for developers who want AI that actually does the work.**

⭐ Star this repo if it's useful — it helps others find it.

</div>
