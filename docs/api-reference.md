# Task API Reference

## Endpoint: `POST /tasks/create`

Creates and queues a new agent task. The task is validated, written to the database with status `CREATED`, and dispatched to the Celery worker queue.

### Request Body

```json
{
  "title": "string",
  "description": "string",
  "task_type": "string",
  "tech_stack": ["string"],
  "features": ["string"],
  "constraints": {
    "max_time": 300,
    "max_steps": 5,
    "risk_level": "safe",
    "file_scope": ["src/"]
  }
}
```

### Field Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | `str` | ✅ | Short name shown in the DAG visualiser |
| `description` | `str` | ✅ | Full task description passed to the Planner agent |
| `task_type` | `str` | ✅ | Hint for agent selection (e.g. `create_api`, `refactor`, `debug`, `add_tests`) |
| `tech_stack` | `list[str]` | ✅ | Technologies involved — helps confidence scoring |
| `features` | `list[str]` | ✅ | Specific features to implement — injected into planner prompt |
| `constraints.max_time` | `int` | ❌ | Hard kill timeout in seconds (default: `TASK_TIMEOUT_SECONDS` from env) |
| `constraints.max_steps` | `int` | ❌ | Maximum DAG nodes the planner may generate |
| `constraints.risk_level` | `str` | ❌ | `safe` (read-only FS) or `unrestricted` (may install packages) |
| `constraints.file_scope` | `list[str]` | ❌ | If set, agent writes are restricted to these path prefixes |

### Response

**202 Accepted**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "CREATED"
}
```

**429 Too Many Requests** — rate limit exceeded (configurable via `RATE_LIMIT_RPM`)

**422 Unprocessable Entity** — request body validation failed (Pydantic error details in body)

**503 Service Unavailable** — backpressure: too many tasks in `CREATED`/`RUNNING` state

---

## Endpoint: `GET /tasks/{task_id}`

Returns full task detail including all DAG node statuses.

### Response

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Stripe Webhook Handler",
  "status": "COMPLETED",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:32:45Z",
  "nodes": [
    {
      "id": "node-1",
      "label": "Create webhook endpoint",
      "status": "COMPLETED",
      "agent": "coder",
      "started_at": "2024-01-15T10:30:05Z",
      "completed_at": "2024-01-15T10:31:20Z"
    }
  ]
}
```

**Task status values:** `CREATED` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED`

---

## Endpoint: `GET /tasks/{task_id}/explain`

Returns the full reasoning trace: planner output, agent selection confidence scores, tool calls made.

Useful for debugging unexpected agent behaviour.

---

## Endpoint: `GET /tasks/{task_id}/steps`

Returns per-step execution logs in chronological order.

```json
[
  {
    "node_id": "node-1",
    "log_type": "stdout",
    "content": "Writing src/api/webhooks/stripe.py...",
    "timestamp": "2024-01-15T10:30:10Z"
  }
]
```

---

## Endpoint: `POST /retry/{task_id}`

Re-queues a task that is in `FAILED` state. Has no effect on tasks in any other state.

**Response:** `202 Accepted` → `{ "task_id": "...", "status": "CREATED" }`

---

## Endpoint: `GET /health`

Liveness probe. Returns `200 OK` if the process is running.

```json
{"status": "ok"}
```

---

## Endpoint: `GET /ready`

Readiness probe. Returns `200 OK` only if the database and Redis connections are healthy.

```json
{"status": "ready", "db": "ok", "redis": "ok"}
```

Returns `503` if either dependency is unavailable.

---

## Rate Limiting

All `POST /tasks/create` calls are rate-limited per client IP address.

Default: 10 requests per minute (configurable via `RATE_LIMIT_RPM` in `.env`).

When the limit is exceeded, the API returns:

```
HTTP 429 Too Many Requests
Retry-After: 60
```

---

## WebSocket: Real-time Logs

Connect to `ws://localhost:8000/ws/{task_id}` to receive real-time log events as the task executes.

Each message is a JSON object:

```json
{
  "type": "log",
  "node_id": "node-1",
  "log_type": "stdout",
  "content": "Writing file...",
  "timestamp": "2024-01-15T10:30:10Z"
}
```

Status change events:

```json
{
  "type": "status",
  "node_id": "node-1",
  "status": "COMPLETED"
}
```
