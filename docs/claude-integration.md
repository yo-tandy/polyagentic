# Claude CLI Integration

This document describes how polyagentic connects to AI models via the Claude CLI subprocess.

## Architecture Overview

```
Agent.process_message(msg)
  |
  +-- _build_prompt(msg)        # Compose user-facing prompt with context
  +-- _get_system_prompt_if_first_call()  # System prompt or session reminder
  |
  +-- SubprocessManager.invoke(prompt, system_prompt, model, ...)
  |     |
  |     +-- _build_claude_args()   # Build CLI argument list
  |     +-- _execute()             # asyncio.create_subprocess_exec
  |     +-- _parse_output()        # Parse JSON response
  |     +-- Auth retry logic       # Detect 401s, refresh OAuth token
  |     |
  |     +-- Returns SubprocessResult
  |
  +-- _extract_actions(result_text)  # Parse ```action blocks from response
  +-- _parse_response()              # Dispatch actions via ActionRegistry
```

Every agent communicates with Claude by spawning a `claude` CLI process. The CLI handles tool execution internally (Bash, Read, Write, Edit, Glob, Grep) and returns the final text result.

## Key Files

| File | Purpose |
|------|---------|
| `core/subprocess_manager.py` | Spawns Claude CLI, parses JSON output, handles auth errors |
| `core/agent.py` | Agent base class: message loop, prompt building, session management |
| `core/session_store.py` | DB-backed session state (active/paused/killed) with in-memory cache |
| `db/models/session.py` | ORM model for `agent_sessions` table |
| `db/repositories/session_repo.py` | CRUD for session records (stats, state, model overrides) |
| `db/config_provider.py` | Configuration seeds and cached config reader |
| `web/routes/sessions.py` | REST API for session management (pause/resume/kill/model) |
| `config.py` | Static defaults (model names, tool permissions, paths) |

## SubprocessResult

The unified return type from every Claude CLI invocation:

```python
@dataclass
class SubprocessResult:
    result_text: str          # Claude's text response
    session_id: str | None    # Session ID for --resume
    cost_usd: float | None    # Total cost of this call
    duration_ms: int | None   # Wall-clock time
    input_tokens: int | None  # Tokens consumed
    output_tokens: int | None # Tokens generated
    is_error: bool            # True if invocation failed
```

All downstream code (Agent, ActionRegistry, TaskBoard) works exclusively with this dataclass. This is the contract point where a new provider must match.

## Claude CLI Invocation

`SubprocessManager.invoke()` in `core/subprocess_manager.py` (line 156) builds and executes the CLI command:

```
claude -p "<prompt>" --output-format json --model <model> \
       [--resume <session_id>] \
       [--tools <allowed_tools>] \
       [--system-prompt <text> | --append-system-prompt <text>] \
       [--max-budget-usd <budget>] \
       --dangerously-skip-permissions
```

### Argument Details

| Argument | Source | Notes |
|----------|--------|-------|
| `-p <prompt>` | `Agent._build_prompt()` | Includes memory, KB index, task board, message content |
| `--model <model>` | Agent constructor / session store override | One of: `sonnet`, `opus`, `haiku` |
| `--resume <session_id>` | `SessionStore.get(agent_id)` | Continues existing conversation |
| `--tools <tools>` | Agent `allowed_tools` field | Comma-separated: `Bash,Edit,Write,Read,Glob,Grep` |
| `--system-prompt` | `Agent._build_full_system_prompt()` | Used on first call (new session) |
| `--append-system-prompt` | `Agent._get_session_reminder()` | Used on resumed sessions |
| `--max-budget-usd` | Agent `max_budget_usd` field | Per-call cost cap |
| `--dangerously-skip-permissions` | Always set | Skips interactive permission prompts |

### Execution Environment

- `asyncio.create_subprocess_exec` with `stdin=DEVNULL`, piped stdout/stderr
- `CLAUDECODE` env var removed to prevent nested session detection
- Working directory set to agent's worktree or project workspace
- Default timeout: 300 seconds (configurable per agent)

### Output Parsing

Claude CLI returns JSON on stdout:

```json
{
  "result": "The agent's text response...",
  "session_id": "abc123-...",
  "total_cost_usd": 0.0142,
  "duration_ms": 3200,
  "is_error": false,
  "usage": {
    "input_tokens": 1500,
    "output_tokens": 450
  }
}
```

`_parse_output()` (line 275) extracts these fields into `SubprocessResult`. Special handling:
- Non-JSON output treated as plain text (not an error)
- `subtype: "error_max_budget_usd"` generates a budget-exceeded error
- Return code != 0 with valid JSON still parsed (rate limits, context overflow)

## Docker Execution Mode

`DockerSubprocessManager` (line 327) extends `SubprocessManager` by wrapping the CLI command in `docker exec`:

```
docker exec -w /workspace <container_name> claude -p ...
```

This allows agents to run inside isolated Docker containers with their own filesystem.

## Session Management

### Session Lifecycle

Each agent has a persistent session tracked in the `agent_sessions` DB table:

```
New agent (no session_id)
  |
  +-- First invoke: Claude CLI returns session_id
  |     +-- Stored in SessionStore (DB + cache)
  |
  +-- Subsequent invokes: --resume <session_id>
  |     +-- Claude continues the conversation
  |     +-- --append-system-prompt sends compact reminder
  |
  +-- Prompt change detected (hash mismatch)
  |     +-- Session invalidated (ID cleared, stats kept)
  |     +-- Next invoke starts fresh with new system prompt
  |
  +-- Stale session ("No conversation found")
  |     +-- Session cleared, retry with fresh invocation
  |
  +-- Consecutive errors >= 3
  |     +-- Auto-paused: agent blocks in message loop
  |     +-- Requires manual resume or server restart
  |
  +-- Manual kill via API
        +-- Session invalidated on next message loop iteration
        +-- Fresh session created on next invoke
```

### Session States

| State | Meaning | Agent Behavior |
|-------|---------|----------------|
| `active` | Normal operation | Processes messages normally |
| `paused` | Holding messages | Message loop blocks; no invocations |
| `killed` | Marked for reset | Session ID cleared on next iteration, then becomes active |

### Prompt Hash Tracking

To detect system prompt changes across server restarts:

1. After each successful invoke, the full system prompt is hashed (MD5, 12 chars)
2. Hash stored in `session_store.set_prompt_hash(agent_id, hash)`
3. On next invoke with a resumed session:
   - Current prompt hash compared to stored hash
   - If match: send compact `--append-system-prompt` reminder
   - If mismatch: invalidate session, send full `--system-prompt` on fresh session

### Stateless Agents

Agents with `stateless=True` (e.g., Manny, dev_manager):
- Never use `--resume` (every call is a fresh session)
- Always send the full system prompt
- No session_id persisted
- Lower timeout and budget limits

## Model Configuration

Models are resolved in priority order:

1. **Session store override** -- `session_store.get_model(agent_id)` (set via API, survives restarts)
2. **Team config YAML** -- `team_config.yaml` agent-specific model
3. **Team structure definition** -- `TeamAgentDef.model` from team structure files
4. **Config provider default** -- `DEFAULT_MODEL` from DB config_entries table
5. **Static fallback** -- `config.py: DEFAULT_MODEL = "sonnet"`

### Allowed Models

Currently restricted to Claude model tiers:

```python
ALLOWED_MODELS = {"sonnet", "opus", "haiku"}
```

These are validated in the `/sessions/{agent_id}/model` API endpoint.

### Switching Models at Runtime

```
POST /api/sessions/{agent_id}/model
Body: {"model": "opus"}
```

This updates both the in-memory agent and the DB-persisted override. Takes effect on the next invocation.

## Tool Permissions

Three permission levels, configured per-role:

| Level | Tools | Used By |
|-------|-------|---------|
| `dev` | `Bash,Edit,Write,Read,Glob,Grep` | Engineers (Rory, Innes, custom agents) |
| `readonly` | `Read,Glob,Grep` | QA/Review agents (Perry) |
| `none` | (empty string) | Management agents (Manny, Jerry) for text-only calls |

The planning phase (pre-execution outline) always uses `allowed_tools=""` since it's text-only reasoning.

## Auth Error Recovery

`SubprocessManager` detects authentication failures and auto-retries once:

1. Combined stdout+stderr checked against patterns: `401`, `oauth token has expired`, `unauthorized`, etc.
2. On match: `_refresh_auth()` runs `claude auth status --output json`
3. If `loggedIn: true`: token was auto-refreshed by CLI, retry the invocation
4. If not logged in: attempt `claude auth login` (interactive OAuth flow)
5. Retry flag (`_auth_retry=True`) prevents infinite retry loops

## Error Resilience

### Auto-Pause

After `CONSECUTIVE_ERROR_THRESHOLD` (default: 3) consecutive errors:
- `SessionRepository.record_request()` sets state to `paused`
- Agent message loop detects pause and blocks
- WebSocket event broadcast to dashboard
- Requires manual resume via API or automatic reset on server restart

### Server Restart Recovery

On startup, `main.py` resets all paused sessions to active:

```python
for agent_id, info in session_store.get_all_info().items():
    if info.get("state") == "paused":
        await session_store.set_state(agent_id, SessionState.ACTIVE)
```

### Stale Session Retry

If Claude CLI returns "No conversation found" for a resumed session:
- Session cleared in store
- Fresh invocation (no `--resume`) with full system prompt

## Request Statistics

Every invocation records stats via `SessionStore.record_request()`:

- `request_count` / `error_count` / `consecutive_errors`
- `total_duration_ms` / `total_cost_usd`
- `total_input_tokens` / `total_output_tokens`
- `last_error` text
- `last_used_at` timestamp

These are exposed through `GET /api/sessions` for the dashboard session status panel.

## Agent Response Protocol

Claude's text response is expected to contain fenced action blocks:

````
```action
{
  "action": "delegate",
  "recipient": "rory",
  "task_title": "Implement login form",
  "description": "Build the login form component"
}
```
````

The agent's `_extract_actions()` method parses these blocks, and `_parse_response()` dispatches them through the `ActionRegistry`. Actions that fail validation trigger a retry: the agent receives a correction prompt listing valid action names and re-emits its response.

## Call Sites in Agent

`self._subprocess.invoke()` is called in four places in `core/agent.py`:

1. **Main invocation** (line 444) -- `process_message()` primary call
2. **Stale session retry** (line 463) -- Fresh call after "No conversation found"
3. **Planning phase** (line 401) -- `_run_planning_phase()` text-only outline
4. **Action validation retry** (line 557) -- `_validate_result_actions()` correction call
