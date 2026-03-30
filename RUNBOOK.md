# AgentOS — Local Dev Runbook

> **Goal**: From zero to a running local environment in under 10 minutes.

---

## Prerequisites

Install these once. Skip anything you already have.

```bash
# macOS — install Homebrew first if missing
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install python@3.11 node redis git
brew install --cask ollama

# Verify
python3.11 --version   # must be ≥ 3.11
node --version         # must be ≥ 18
redis-server --version
ollama --version
```

---

## Step 1 — Clone the repo (30 seconds)

```bash
git clone https://github.com/youruser/agentos
cd agentos/local-coder-agent
```

---

## Step 2 — Create your `.env` (1 minute)

```bash
cp .env.example .env
```

The defaults work out of the box. The only line you might want to change is the workspace path:

```bash
# .env
WORKSPACE_DIR=./workspace        # where generated files are saved
OLLAMA_MODEL=deepseek-coder:1.3b # change to a model you have pulled
```

Validate the environment (optional but recommended):

```bash
python scripts/check_secrets.py
```

---

## Step 3 — Install Python dependencies (2 minutes)

```bash
python3.11 -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 4 — Install frontend dependencies (1 minute)

```bash
cd frontend
npm install
cd ..
```

---

## Step 5 — Start Ollama and pull a model (2 minutes)

```bash
# Terminal 1 — start Ollama server
ollama serve

# Terminal 2 — pull the default model (1.3B = ~800 MB)
ollama pull deepseek-coder:1.3b
```

> **Tip**: `deepseek-coder:1.3b` is the smallest and fastest model. For better results use `deepseek-coder:6.7b` (~4 GB).

---

## Step 6 — Start Redis (30 seconds)

```bash
# macOS — run as a background service
brew services start redis

# Or run in a terminal
redis-server
```

Verify it's up:

```bash
redis-cli ping   # should return: PONG
```

---

## Step 7 — Start the app (1 minute)

```bash
# Make sure your venv is active
source venv/bin/activate

# One command starts everything: FastAPI + Celery + Next.js
./start.sh
```

You should see:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AgentOS is running:
  Backend  → http://localhost:8000
  API Docs → http://localhost:8000/docs
  Frontend → http://localhost:3000
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Open **http://localhost:3000** in your browser.

---

## Step 8 — Submit your first task (30 seconds)

1. Go to **http://localhost:3000**
2. Click **New Task**
3. Fill in:
   - **Title**: `Hello World API`
   - **Type**: `create_api`
   - **Description**: `Create a FastAPI endpoint that returns {"hello": "world"}`
4. Click **Submit**

Watch the DAG visualisation update in real time.

---

## Verify everything is working

```bash
# Backend health check
curl http://localhost:8000/health

# Ready check (DB + Redis)
curl http://localhost:8000/ready

# API docs
open http://localhost:8000/docs
```

Expected responses:

```json
{"status": "ok"}
{"status": "ready", "db": "ok", "redis": "ok"}
```

---

## Run the test suite

```bash
source venv/bin/activate
pytest tests/unit/ -v -q   # fast unit tests only (~15s)
```

---

## Stop everything

Press `Ctrl+C` in the terminal running `./start.sh`. All three processes (API, Celery, Next.js) shut down cleanly.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `redis.exceptions.ConnectionError` | Run `redis-server` or `brew services start redis` |
| `ollama: command not found` | Install Ollama from [ollama.com](https://ollama.com) |
| `Error: model not found` | Run `ollama pull deepseek-coder:1.3b` |
| Port 8000 already in use | `lsof -ti :8000 \| xargs kill -9` |
| Port 3000 already in use | `lsof -ti :3000 \| xargs kill -9` |
| `ModuleNotFoundError` | Activate venv: `source venv/bin/activate` |
| Frontend build errors | `cd frontend && npm install && cd ..` |

---

## Useful shortcuts

```bash
make test-unit    # run unit tests only
make lint         # lint + type check
make clean        # remove all caches and .pyc files
make dev          # alternative: run everything via Docker Compose
```

> **Docker alternative**: If you have Docker installed, `make dev` starts the entire stack (API + Celery + Redis) in containers with hot-reload — no manual steps needed.
