from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.db.database import engine, Base
from backend.api.routers import task, system, ws, health, agents, metrics
import logging
import os

logger = logging.getLogger(__name__)

try:
    from agent.llm import warmup_model, check_ollama
    from rich.console import Console
    _console = Console()
    _llm_available = True
except ImportError:
    _llm_available = False

# Auto-create tables on startup
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler — replaces deprecated @app.on_event('startup')."""
    # ── Startup ───────────────────────────────────────────────────────
    if _llm_available:
        try:
            _console.print("[bold blue]AgentOS: Checking Ollama LLM...[/bold blue]")
            if not check_ollama():
                _console.print("[bold yellow]WARNING: Ollama unreachable. LLM calls will fail until Ollama starts.[/bold yellow]")
            else:
                import threading
                threading.Thread(target=warmup_model, daemon=True).start()
        except Exception as ex:
            logger.exception(f"[lifespan] LLM warmup failed: {ex}")
    else:
        logger.warning("[lifespan] agent.llm not available — LLM warmup skipped.")

    yield  # ← application runs here

    # ── Shutdown (nothing to tear down currently) ─────────────────────


app = FastAPI(
    title="AgentOS — AI Developer Operating System",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(task.router)
app.include_router(system.router)
app.include_router(ws.router)
app.include_router(health.router)
app.include_router(agents.router, prefix="/agents", tags=["Agents"])
app.include_router(metrics.router)


@app.get("/")
def root():
    return {"status": "ok", "message": "AgentOS Backend Running"}
