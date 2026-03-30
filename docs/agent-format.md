# Agent Markdown Format Reference

Agents in AgentOS are defined as plain Markdown files in the `agents/` directory.

The `agent/loader.py` module parses these files into a validated `AgentManifest` Pydantic model before any agent is selected or executed.

---

## File location

```
agents/
├── coder.md       ← writes code files
├── debugger.md    ← diagnoses and fixes bugs
├── planner.md     ← decomposes tasks into DAGs
└── <your-agent>.md
```

---

## Full Format

```markdown
# Agent: <name>

## Role
<One or two sentences describing what this agent does.>

## Model
Auto | deepseek-coder:1.3b | deepseek-coder:6.7b | <any Ollama model tag>

## Tools
- file_read
- file_write
- terminal
- git

## System Prompt
<The full system prompt injected before every LLM call for this agent.>

<Include the expected JSON output format here.>

## Constraints
- <constraint 1>
- <constraint 2>

## Memory
- persistent: true | false
- scope: task | session | global
```

---

## Field Reference

| Field | Required | Description |
|---|---|---|
| `# Agent: <name>` | ✅ | Unique agent identifier. Used by `AgentSelector` for routing. |
| `## Role` | ✅ | Human-readable description. Also used in confidence scoring. |
| `## Model` | ✅ | Ollama model tag, or `Auto` to let `AgentSelector` pick. |
| `## Tools` | ✅ | List of tool names the agent may call. Validated against `ToolRegistry`. |
| `## System Prompt` | ✅ | The full, literal system prompt for LLM calls. |
| `## Constraints` | ❌ | Optional. Execution constraints passed to the sandbox. |
| `## Memory` | ❌ | Optional. Persistence settings for the memory engine. |

---

## Tools Available

| Tool | What it does |
|---|---|
| `file_read` | Read an existing file from the workspace |
| `file_write` | Write or overwrite a file in the workspace |
| `terminal` | Run a shell command in the sandbox |
| `git` | Run git commands (clone, diff, log) in the sandbox |

> Tools are validated at load time. An agent referencing an unknown tool will fail to load.

---

## Model Selection

Set `## Model` to:
- **`Auto`** — `AgentSelector` picks the best available Ollama model based on confidence scoring
- **A specific tag** — forces a particular model (e.g. `deepseek-coder:6.7b` for complex tasks)

The model must be available via `ollama list` on the host. If the specified model is not found, `AgentSelector` falls back to `OLLAMA_MODEL` from `.env`.

---

## Memory Scopes

| Scope | Behavior |
|---|---|
| `task` | Memory is cleared after each task completes |
| `session` | Memory persists for the life of the Celery worker process |
| `global` | Memory persists in ChromaDB across restarts |

`persistent: false` disables ChromaDB recall for this agent entirely.

---

## Security Notes

All `.md` files in `agents/` are scanned by `agent/loader.py` before loading:

- URLs in system prompts are rejected (potential SSRF)
- Shell directives (`exec`, `eval`, `__import__`) are blocked
- Common prompt injection phrases are flagged and rejected

If an agent file fails validation, the system falls back to the last known-good manifest.

---

## Examples

See [`examples/agents/`](../examples/agents/) for complete working examples:
- `reviewer.md` — code review agent
- `tester.md` — pytest test generation agent
