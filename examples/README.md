# Examples

This directory contains ready-to-use examples for AgentOS.

---

## `tasks/` — Example task JSON files

These files can be submitted directly to `POST /tasks/create`:

```bash
curl -X POST http://localhost:8000/tasks/create \
  -H "Content-Type: application/json" \
  -d @examples/tasks/hello_world_api.json
```

| File | Description |
|---|---|
| `hello_world_api.json` | Minimal FastAPI endpoint — good first task to verify the system works |
| `stripe_webhook.json` | Real-world API task with signature validation and replay prevention |
| `redis_cache_layer.json` | Refactoring task that adds a caching decorator |

---

## `agents/` — Example agent Markdown files

These show how to write new agents beyond the built-in `coder`, `debugger`, and `planner`.

To use an example agent, copy it to `agents/`:

```bash
cp examples/agents/reviewer.md agents/reviewer.md
```

The agent is discovered automatically at startup — no code changes needed.

| File | Description |
|---|---|
| `reviewer.md` | Code review agent — analyses diffs for bugs and security issues |
| `tester.md` | Test generation agent — writes pytest suites for existing code |

See [`docs/agent-format.md`](../docs/agent-format.md) for the full agent specification.
