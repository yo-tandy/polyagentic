# Polyagentic

A multi-agent development system where specialized AI agents collaborate on software projects. Users talk to a Manager agent through a web dashboard; the manager delegates to a team of agents that work in parallel across isolated git worktrees.

## How It Works

```
User  ──▶  Web Dashboard  ──▶  Manny (manager/router)
                                    │
                  ┌────────┬────────┼────────┬────────┐
                  ▼        ▼        ▼        ▼        ▼
               Perry    Jerry     Rory     Innes   Custom
              (product) (project) (recruit) (git)   Agents
                                                      │
              ◀─── all agents work in parallel ───────┘
```

Each agent is a **Claude Code subprocess** invoked via `claude -p "..." --output-format json`. Agents communicate through **file-based JSON messages** polled by a central message broker. Tasks flow through a state machine (pending → in_progress → review → done) with review loops and parallel work tracking.

Agents can also run on alternative AI backends (Claude API, OpenAI, Google Gemini) with per-agent model switching at runtime.

## Quick Start

### Prerequisites

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- An Anthropic API key with available credits

### Install & Run

```bash
pip install -r requirements.txt
python main.py --config team_config.yaml --port 8000
```

Open `http://localhost:8000` in your browser.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_CLI` | `claude` | Path to the Claude Code binary |
| `POLYAGENTIC_MODEL` | `sonnet` | Default model for agents |
| `OPENAI_API_KEY` | *(none)* | OpenAI API key (optional, for OpenAI provider) |
| `GOOGLE_API_KEY` | *(none)* | Google API key (optional, for Gemini provider) |
| `JWT_SECRET` | *(auto-generated)* | Secret for signing JWT auth tokens |
| `GOOGLE_CLIENT_ID` | *(none)* | Google OAuth client ID (optional, for multi-user auth) |
| `GOOGLE_CLIENT_SECRET` | *(none)* | Google OAuth client secret (optional, for multi-user auth) |

## Architecture

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| **Agent** | `core/agent.py` | Base class with message loop, session management, action dispatch |
| **PromptBuilder** | `core/prompt_builder.py` | Template rendering, system prompts, task context assembly |
| **ActionHandler** | `core/action_handler.py` | Action extraction, normalization, validation, and dispatch |
| **Message Broker** | `core/message_broker.py` | Polls agent inboxes (1s interval), delivers messages, broadcasts WebSocket events |
| **Task Board** | `core/task_board.py` | DB-backed task state machine with transition validation |
| **Phase Board** | `core/phase_board.py` | Sprint/milestone tracking with velocity metrics |
| **Git Manager** | `core/git_manager.py` | Async git operations with lock protection; creates per-agent worktrees |
| **Session Store** | `core/session_store.py` | Persists session IDs, tracks errors, auto-pauses after 3 consecutive failures |
| **Memory Manager** | `core/memory_manager.py` | Two-tier agent memory: global personality + project-scoped |
| **Knowledge Base** | `core/knowledge_base.py` | Shared project documents with comments and review workflows |
| **MCP Manager** | `core/mcp_manager.py` | Deploy and manage MCP tool servers per agent |
| **MCP Registry** | `core/mcp_registry.py` | Search and discover MCP skill packages |
| **Project Store** | `core/project_store.py` | Multi-project management with isolated directory structures |
| **Constants** | `core/constants.py` | Centralized security defaults, validation patterns, ID generation |

### AI Providers

| Provider | File | Backend |
|----------|------|---------|
| **Claude CLI** | `core/providers/claude_cli_provider.py` | Claude Code subprocess with `--resume` sessions |
| **Claude API** | `core/providers/claude_api_provider.py` | Direct Anthropic API with tool use |
| **OpenAI** | `core/providers/openai_provider.py` | GPT-4o / GPT-4 via OpenAI API |
| **Gemini** | `core/providers/gemini_provider.py` | Google Gemini via Google GenAI SDK |
| **Base** | `core/providers/api_provider_base.py` | Abstract base with shared tool-loop logic |

All API providers share a common base class that handles the tool-use loop, cost estimation, and result building. Providers can be switched per-agent at runtime via the session management UI.

### Database Layer

SQLAlchemy async with aiosqlite. 22 ORM models in `db/models/`, accessed through repository classes in `db/repositories/` that extend a `BaseRepository` with generic CRUD helpers.

### Fixed Agents

| Agent | ID | Role | Session | Tools |
|-------|-----|------|---------|-------|
| **Manny** | `manny` | User's primary contact; thin router that immediately delegates | Stateless | None (pure reasoning) |
| **Jerry** | `jerry` | Project manager — assigns tickets, monitors progress, manages sprints | Stateful | Read-only |
| **Perry** | `perry` | Product manager — clarifies requirements, writes specs through conversations | Stateful | Read-only |
| **Innes** | `innes` | Integrator — manages repos, PRs, merges, code quality | Stateful | Full dev |
| **Rory** | `rory` | Robot resources — recruits and configures custom agents, deploys MCP skills | Stateful | Full dev |

Custom agents are defined in `team_config.yaml` and can also be recruited dynamically at runtime by Rory.

### Communication Protocol

Agents respond with structured action blocks that the system parses:

````
```action
{"action": "delegate", "to": "backend_developer", "task_title": "Implement auth", "priority": 2}
```
````

**Core actions**: `delegate`, `respond_to_user`, `update_task`, `update_memory`, `write_document`, `update_document`, `resolve_comments`, `start_conversation`, `end_conversation`

**Management actions**: `assign_ticket`, `create_batch_tickets`, `create_phase`, `update_phase`, `pause_task`, `start_task`

**Infrastructure actions**: `recruit_agent`, `search_mcp_registry`, `deploy_mcp`, `request_capability`

Action names are normalized automatically — common mistakes like `save_to_memory` or `send_message` are mapped to their correct equivalents. Unknown actions trigger a one-shot correction retry.

### Message Flow

1. User sends chat via dashboard
2. Message written to `manny/inbox/` as JSON file
3. Broker polls inbox, queues message to agent
4. Agent invokes Claude CLI (or API provider), parses action blocks from output
5. Actions create new messages routed to other agents
6. All events broadcast to dashboard via WebSocket

## Multi-User Authentication

When Google OAuth credentials are configured, the system supports multi-user access:

- **Google OAuth 2.0** login flow with JWT cookie sessions
- **Organizations** — users create or join orgs via invite links
- **Role-based access control** — admin-only config endpoints protected by `require_admin` dependency
- **Secure cookies** — HTTP-only, SameSite=Lax, Secure flag auto-set for non-localhost

Without OAuth credentials, the system runs in anonymous single-user mode.

## MCP Integration

Agents can acquire new capabilities at runtime through the Model Context Protocol:

- **Registry search** — agents search for MCP skill packages matching their needs
- **Skill deployment** — MCP servers are installed and configured per-agent
- **Per-agent config** — each agent gets its own `mcp_servers.json` with connected tools

## Sprint & Velocity Tracking

- **Phase board** — project milestones with status tracking (planning → active → completed)
- **Story point estimation** — tasks carry point estimates (1, 2, 3, 5, 8, 13)
- **Velocity metrics** — per-agent throughput calculated from completed tasks (points/time)
- **Sprint context** — management agents see velocity data in their prompt context

## Project Structure

```
polyagentic/
├── main.py                          # Entry point with lifecycle management
├── config.py                        # Path defaults and model settings
├── team_config.yaml                 # Team definition
├── requirements.txt
├── core/
│   ├── agent.py                     # Base Agent class
│   ├── prompt_builder.py            # Prompt construction (extracted from Agent)
│   ├── action_handler.py            # Action parsing/dispatch (extracted from Agent)
│   ├── constants.py                 # Security defaults, validation, ID generation
│   ├── agent_registry.py            # Agent lookup
│   ├── message.py                   # Message dataclass
│   ├── message_broker.py            # Inbox polling & event broadcast
│   ├── task.py                      # Task dataclass & status enum
│   ├── task_board.py                # Task state machine
│   ├── phase_board.py               # Sprint/milestone tracking
│   ├── git_manager.py               # Git worktree management
│   ├── session_store.py             # Session persistence & auto-pause
│   ├── memory_manager.py            # Personality + project memory
│   ├── knowledge_base.py            # Shared document store
│   ├── project_store.py             # Multi-project management
│   ├── subprocess_manager.py        # Claude CLI wrapper
│   ├── prompt_loader.py             # Prompt template inheritance chain
│   ├── mcp_manager.py               # MCP server deployment
│   ├── mcp_registry.py              # MCP skill discovery
│   ├── container_manager.py         # Docker container lifecycle
│   ├── team_structure.py            # Team roles and routing guide
│   ├── providers/
│   │   ├── base.py                  # Provider interface
│   │   ├── api_provider_base.py     # Shared API tool-loop logic
│   │   ├── claude_cli_provider.py   # Claude Code subprocess
│   │   ├── claude_api_provider.py   # Anthropic API direct
│   │   ├── openai_provider.py       # OpenAI GPT
│   │   └── gemini_provider.py       # Google Gemini
│   └── actions/                     # Action handler plugins (~30 types)
│       ├── base.py
│       ├── registry.py
│       ├── delegate.py
│       ├── respond.py
│       ├── recruit_agent.py
│       └── ...
├── agents/
│   ├── custom_agent.py              # Configurable agent template
│   └── prompts/                     # System prompt templates
│       ├── manny.md
│       ├── jerry.md
│       ├── perry.md
│       ├── innes.md
│       └── rory.md
├── db/
│   ├── engine.py                    # Async engine + migrations
│   ├── models/                      # 22 SQLAlchemy ORM models
│   └── repositories/                # Data access layer with BaseRepository CRUD
├── web/
│   ├── app.py                       # FastAPI app factory
│   ├── auth.py                      # Google OAuth + JWT management
│   ├── middleware.py                # Auth middleware with token refresh
│   ├── services/
│   │   └── agent_service.py         # Agent lifecycle business logic
│   ├── routes/
│   │   ├── chat.py                  # POST /api/chat
│   │   ├── agents.py                # GET/POST /api/agents
│   │   ├── tasks.py                 # GET/PATCH /api/tasks
│   │   ├── phases.py                # GET/POST /api/phases
│   │   ├── sessions.py              # GET/POST /api/sessions
│   │   ├── knowledge.py             # GET/POST /api/knowledge
│   │   ├── memory.py                # GET/PATCH /api/memory
│   │   ├── projects.py              # GET/POST /api/projects
│   │   ├── config.py                # GET/POST /api/config
│   │   ├── git.py                   # GET/POST /api/git
│   │   ├── uploads.py               # POST /api/uploads
│   │   ├── orgs.py                  # GET/POST /api/orgs
│   │   ├── mcp.py                   # GET/POST /api/mcp
│   │   ├── conversations.py         # GET/POST /api/conversations
│   │   ├── github.py                # GitHub integration
│   │   ├── activity.py              # GET /api/activity
│   │   └── ws.py                    # WS /ws
│   └── static/
│       ├── index.html
│       ├── settings.html
│       ├── css/
│       └── js/
│           ├── app.js
│           └── components/
│               ├── agentPanel.js
│               ├── taskBoard.js
│               ├── chatView.js
│               ├── activityLog.js
│               ├── gitPanel.js
│               ├── knowledgePanel.js
│               ├── projectSelector.js
│               ├── teamConfig.js
│               ├── sessionStatus.js
│               ├── conversationBar.js
│               ├── conversationWindow.js
│               └── projectInfo.js
└── projects/                        # Per-project isolated data
    └── {project_id}/
        ├── workspace/               # Main git repo
        ├── worktrees/               # Agent git worktrees
        ├── messages/                # Agent inbox/outbox
        ├── docs/                    # Knowledge base documents
        └── memory/                  # Project-scoped agent memory
```

## Web Dashboard

The dashboard provides a real-time view of the entire system:

- **Agents Panel** — Status of each agent (idle/working/error/paused), with direct messaging
- **Task Board** — Kanban view across statuses (pending, in progress, review, blocked, paused, done) with story point estimates
- **Chat** — Conversation with Manny (the manager agent)
- **Activity Log** — Live feed of all inter-agent messages
- **Knowledge Base** — Shared documents organized by category, with comments and review workflows
- **Session Status** — Per-agent session control (pause/resume/kill/reset), model and provider switching
- **Conversations** — Direct agent-user conversation windows for clarification
- **Project Info** — Project metadata, phase tracking, and velocity dashboard

## Configuration

### team_config.yaml

```yaml
project:
  name: my-project
  workspace_path: workspace
  main_branch: main

agents:
  fixed:
    manny:
      model: sonnet
      description: Manager — primary user contact, thin router, delegates to team.
    rory:
      model: sonnet
      description: Robot Resources — recruits and configures worker agents.
    innes:
      model: sonnet
      description: Integrator — manages repos, PRs, merges, code quality.
    perry:
      model: sonnet
      description: Product Manager — builds specs through user conversations.
    jerry:
      model: sonnet
      description: Project Manager — assigns tickets, monitors progress.
  custom:
    - name: backend_developer
      role: Senior Back-End Developer
      system_prompt: You are an expert backend developer...
      model: sonnet
      allowed_tools: Bash,Edit,Write,Read,Glob,Grep
```

New agents can be added to the config, created through the dashboard's Team Config modal, or recruited dynamically by Rory at runtime.

### Tool Presets

| Preset | Tools | Use Case |
|--------|-------|----------|
| Full Dev | `Bash,Edit,Write,Read,Glob,Grep` | Agents that write code |
| Read-only | `Read,Glob,Grep` | Managers and reviewers |
| None | *(empty)* | Pure reasoning (Manny) |

## Key Design Decisions

**File-based message passing** — Agents exchange JSON files in inbox/outbox directories. Simple, reliable, and avoids distributed consensus complexity.

**Git worktrees** — Each coding agent gets its own worktree on a dedicated branch (`dev/{agent_id}`). This allows true parallel development without lock contention on the working tree.

**Stateless router** — Manny receives a fresh system prompt on every call with the current team roster and memory. It never accumulates stale context.

**Session persistence** — Worker agents use Claude Code's `--resume` flag to maintain conversation context across requests. Session IDs are stored per project in the database.

**Auto-pause circuit breaker** — After 3 consecutive errors, an agent's session is automatically paused to prevent runaway cost.

**Two-tier memory** — Global personality memory survives project switches; project memory is scoped. Both are injected into prompts.

**Role-based architecture** — Agent behavior is defined by database-stored roles (prompt, tools, actions, budget, timeout). Roles can be customized without code changes.

**Action registry** — All agent actions are pluggable handlers registered in `core/actions/`. Adding a new action type is one file and one registry entry.

**Provider abstraction** — All API providers extend a common base class with shared tool-loop logic. Switching providers per-agent requires no code changes.

**Repository pattern** — Data access goes through repository classes with generic CRUD helpers, keeping SQL out of business logic.

## API Reference

### Chat
- `POST /api/chat` — Send message to Manny
- `GET /api/chat/history` — Get chat history

### Agents
- `GET /api/agents` — List all agents with status
- `POST /api/agents/{agent_id}/message` — Send message to specific agent
- `POST /api/agents/{agent_id}/status-request` — Request status report

### Tasks
- `GET /api/tasks` — List all tasks
- `GET /api/tasks/{task_id}` — Get task detail
- `PATCH /api/tasks/{task_id}` — Update task fields

### Phases
- `GET /api/phases` — List project phases
- `POST /api/phases` — Create phase
- `PATCH /api/phases/{phase_id}` — Update phase

### Sessions
- `GET /api/sessions` — Session metadata for all agents
- `POST /api/sessions/{agent_id}/pause` — Pause agent session
- `POST /api/sessions/{agent_id}/resume` — Resume agent session
- `POST /api/sessions/{agent_id}/kill` — Kill agent session
- `POST /api/sessions/{agent_id}/reset` — Reset session stats
- `POST /api/sessions/{agent_id}/model` — Change agent model
- `POST /api/sessions/{agent_id}/provider` — Change agent AI provider
- `POST /api/sessions/pause-all` — Pause all session-based agents
- `POST /api/sessions/resume-all` — Resume all paused agents

### Knowledge Base
- `GET /api/knowledge` — List all documents
- `GET /api/knowledge/{doc_id}` — Get document content
- `POST /api/knowledge` — Create document
- `POST /api/knowledge/{doc_id}/comments` — Add comment

### Projects
- `GET /api/projects` — List projects
- `POST /api/projects` — Create new project
- `PATCH /api/projects/{project_id}` — Activate project

### Organizations
- `GET /api/orgs` — Get current org info
- `GET /api/orgs/members` — List org members
- `POST /api/orgs/invites` — Create invite link

### Uploads
- `POST /api/uploads` — Upload file to knowledge base
- `GET /api/uploads/{doc_id}/download` — Download uploaded file

### MCP
- `GET /api/mcp/servers` — List deployed MCP servers
- `POST /api/mcp/deploy` — Deploy MCP skill

### Auth
- `GET /auth/login` — Login page
- `GET /auth/google` — Start Google OAuth flow
- `GET /auth/google/callback` — OAuth callback
- `GET /auth/me` — Current user info
- `POST /auth/logout` — Clear session

### Config
- `GET /api/config/agents` — List agent configs
- `POST /api/config/agents` — Add custom agent
- `DELETE /api/config/agents/{agent_id}` — Remove custom agent
- `GET /api/config/roles` — List agent roles
- `POST /api/config/roles` — Create/update role

### WebSocket
- `WS /ws` — Real-time event stream (agent status, task updates, chat, KB changes, phase updates)

## Task Workflow

```
PENDING ──▶ IN_PROGRESS ──▶ REVIEW ──▶ DONE
   │              │            │
   ▼              ▼            ▼
BLOCKED        PAUSED     IN_PROGRESS (revision requested)
```

- Tasks carry story point estimates and track start/completion times for velocity calculation
- Tasks auto-transition to `in_progress` when an agent starts working
- Agents outline a numbered plan before starting each task
- Moving to `review` auto-notifies the assigned reviewer
- Review feedback loops back to the original assignee
- Completed tasks contribute to per-agent velocity metrics

## Security

- **CORS** — Restricted origins, methods, and headers (not wildcards)
- **Security headers** — X-Frame-Options, X-Content-Type-Options, Referrer-Policy, HSTS
- **JWT authentication** — HTTP-only secure cookies with role claims
- **Admin RBAC** — All state-changing config endpoints require admin role
- **Subprocess isolation** — Sensitive env vars (API keys, secrets) filtered from agent subprocess environments
- **Path traversal protection** — File downloads validated against project upload directory
- **Input validation** — MCP package names validated with regex before installation

## Dependencies

```
fastapi>=0.115.0
uvicorn>=0.34.0
websockets>=13.0
pydantic>=2.0
pyyaml>=6.0
anthropic>=0.40.0
openai>=1.50.0
google-genai>=1.0.0
sqlalchemy[asyncio]>=2.0
aiosqlite>=0.20
pymupdf>=1.24.0
python-docx>=1.1.0
Pillow>=10.0
python-multipart>=0.0.9
pyjwt>=2.8
httpx>=0.27
```
