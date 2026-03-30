#!/usr/bin/env bash
# AgentOS — Startup Script
# Safe to run from ANY directory.
# Usage:  ./start.sh | bash /path/to/start.sh | sh start.sh
set -e

# ── Always operate from the directory this script lives in ───────────────────
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
export PYTHONPATH="$REPO"

echo "📂 Working directory: $REPO"
echo "🐍 PYTHONPATH: $PYTHONPATH"
echo ""

# ── Ollama stability settings ────────────────────────────────────────────────
# Prevents model unload between tasks (eliminates restart-loop on long queues)
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"
# Serializes inference — no concurrent model-switch restarts
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
# Cap RAM usage per request
export OLLAMA_NUM_CTX="${OLLAMA_NUM_CTX:-2048}"
echo "🤖 Ollama keep_alive=$OLLAMA_KEEP_ALIVE  num_parallel=$OLLAMA_NUM_PARALLEL  num_ctx=$OLLAMA_NUM_CTX"

# ── Output workspace ─────────────────────────────────────────────────────────
# All generated task files are saved here, organised per task_id subfolder.
export WORKSPACE_DIR="${WORKSPACE_DIR:-/Volumes/IDK/AgentOS testing}"
mkdir -p "$WORKSPACE_DIR"
echo "💾 Workspace: $WORKSPACE_DIR"

# ── Load .env if present ─────────────────────────────────────────────────────
if [ -f "$REPO/.env" ]; then
  set -a; source "$REPO/.env"; set +a
  echo "✅ Loaded .env"
fi

# ── Kill anything already on port 8000 / old workers ────────────────────────
echo "🔄 Clearing stale processes..."
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
pkill -f "celery.*celery_app" 2>/dev/null || true
sleep 1

# ── Run schema migration (safe / idempotent) ─────────────────────────────────
echo "🗄️  Running DB migration..."
python db_migrate.py
echo ""

# ── Backend ──────────────────────────────────────────────────────────────────
echo "🚀 Starting FastAPI backend on :8000 ..."
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

sleep 2

# ── Celery worker ─────────────────────────────────────────────────────────────
echo "⚙️  Starting Celery worker..."
celery -A backend.workers.celery_app worker --loglevel=warning --concurrency=4 &
WORKER_PID=$!

# ── Frontend ──────────────────────────────────────────────────────────────────
echo "🌐 Starting Next.js frontend on :3000 ..."
cd "$REPO/frontend"
npm run dev &
FRONTEND_PID=$!

cd "$REPO"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AgentOS is running:"
echo "  Backend  → http://localhost:8000"
echo "  API Docs → http://localhost:8000/docs"
echo "  Frontend → http://localhost:3000"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Press Ctrl+C to stop all services."
echo ""

# ── Trap Ctrl+C and kill all children ────────────────────────────────────────
trap "echo ''; echo 'Shutting down...'; kill $BACKEND_PID $WORKER_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait
