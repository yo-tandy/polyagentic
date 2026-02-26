# Polyagentic

A multi-agent development system where specialized AI agents collaborate on software projects. Users talk to a Development Manager through a web dashboard; the manager delegates to a team of Claude Code agents that work in parallel across isolated git worktrees.

## How It Works

```
User  ──▶  Web Dashboard  ──▶  Dev Manager (router)
                                    │
                  ┌─────────────────┼─────────────────┐
                  ▼                 ▼                  ▼
           Backend Dev       Frontend Dev        QA Engineer
           (worktree)        (worktree)          (worktree)
                  │                 │                  │
                  └────────▶  Integrator  ◀────────────┘
                                    │
                                  main
```

Each agent is a **Claude Code subprocess** invoked via `claude -p "..." --output-format json`. Agents communicate through **file-based JSON messages** polled by a central message broker. Tasks flow through a state machine (pending → in_progress → review → done) with review loops and parallel work tracking.

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

## Architecture

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| **Agent** | `core/agent.py` | Base class with message loop, session management, action parsing |
| **Message Broker** | `core/message_broker.py` | Polls agent inboxes (1s interval), delivers messages, broadcasts WebSocket events |
| **Task Board** | `core/task_board.py` | Persistent JSON-backed task state machine with transition validation |
| **Git Manager** | `core/git_manager.py` | Async git operations with lock protection; creates per-agent worktrees |
| **Session Store** | `core/session_store.py` | Persists Claude session IDs, tracks errors, auto-pauses after 3 consecutive failures |
| **Memory Manager** | `core/memory_manager.py` | Two-tier agent memory: global personality + project-scoped |
| **Knowledge Base** | `core/knowledge_base.py` | Shared project documents indexed by category |
| **Subprocess Manager** | `core/subprocess_manager.py` | Async wrapper around `claude` CLI invocation |
| **Project Store** | `core/project_store.py` | Multi-project management with isolated directory structures |

### Fixed Agents

| Agent | Role | Session | Tools |
|-------|------|---------|-------|
| **dev_manager** | User's primary contact; thin router that immediately delegates | Stateless | None (pure reasoning) |
| **project_manager** | Coordinates team, manages priorities, reviews tasks | Stateful | Read-only |
| **product_manager** | Clarifies requirements, writes user stories | Stateful | Read-only |
| **integrator** | Merges branches, resolves conflicts | Stateful | Full dev |
| **cicd_engineer** | Runs tests, validates builds | Stateful | Full dev |

Custom agents are defined in `team_config.yaml` and can also be created dynamically at runtime.

### Communication Protocol

Agents respond with structured action blocks that the system parses:

````
```action
{"action": "delegate", "to": "backend_developer", "task_title": "Implement auth", "priority": 2}
```
````

**Supported actions**: `delegate`, `respond_to_user`, `update_task`, `update_memory`, `write_document`, `update_document`, `create_agent`

### Message Flow

1. User sends chat via dashboard
2. Message written to `dev_manager/inbox/` as JSON file
3. Broker polls inbox, queues message to agent
4. Agent invokes Claude CLI, parses action blocks from output
5. Actions create new messages routed to other agents
6. All events broadcast to dashboard via WebSocket

## Project Structure

```
polyagentic/
├── main.py                        # Entry point
├── config.py                      # Global settings
├── team_config.yaml               # Team definition
├── requirements.txt
├── core/
│   ├── agent.py                   # Base Agent class
│   ├── agent_registry.py          # Agent lookup
│   ├── message.py                 # Message dataclass
│   ├── message_broker.py          # Inbox polling & event broadcast
│   ├── task.py                    # Task dataclass & status enum
│   ├── task_board.py              # Task state machine
│   ├── git_manager.py             # Git worktree management
│   ├── session_store.py           # Session persistence & auto-pause
│   ├── memory_manager.py          # Personality + project memory
│   ├── knowledge_base.py          # Shared document store
│   ├── project_store.py           # Multi-project management
│   └── subprocess_manager.py      # Claude CLI wrapper
├── agents/
│   ├── dev_manager.py             # Stateless router agent
│   ├── project_manager.py         # Coordination agent
│   ├── product_manager.py         # Requirements agent
│   ├── integrator.py              # Git merge agent
│   ├── cicd_engineer.py           # Build/test agent
│   ├── custom_agent.py            # Configurable agent template
│   └── prompts/                   # System prompt markdown files
│       ├── dev_manager.md
│       ├── project_manager.md
│       ├── product_manager.md
│       ├── integrator.md
│       └── cicd_engineer.md
├── web/
│   ├── app.py                     # FastAPI app factory
│   ├── routes/
│   │   ├── chat.py                # POST /api/chat
│   │   ├── agents.py              # GET/POST /api/agents
│   │   ├── tasks.py               # GET/PATCH /api/tasks
│   │   ├── git.py                 # GET/POST /api/git
│   │   ├── knowledge.py           # GET/POST /api/knowledge
│   │   ├── memory.py              # GET/PATCH /api/memory
│   │   ├── sessions.py            # GET/POST /api/sessions
│   │   ├── projects.py            # GET/POST /api/projects
│   │   ├── config.py              # POST /api/config/agents
│   │   ├── activity.py            # GET /api/activity
│   │   └── ws.py                  # WS /ws
│   └── static/
│       ├── index.html
│       ├── css/
│       │   ├── main.css
│       │   └── dashboard.css
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
│               └── sessionStatus.js
└── projects/                      # Per-project isolated data
    └── {project_id}/
        ├── workspace/             # Main git repo
        ├── worktrees/             # Agent git worktrees
        ├── messages/              # Agent inbox/outbox
        ├── docs/                  # Knowledge base
        ├── memory/                # Project-scoped agent memory
        ├── tasks.json
        ├── sessions.json
        └── project.json
```

## Web Dashboard

The dashboard provides a real-time view of the entire system:

- **Agents Panel** -- Status of each agent (idle/working/error/paused), with direct messaging
- **Task Board** -- Kanban view across statuses (pending, in progress, review, blocked, paused, done)
- **Chat** -- Conversation with the Dev Manager
- **Activity Log** -- Live feed of all inter-agent messages
- **Knowledge Base** -- Shared documents organized by category
- **Session Status** -- Per-agent session control (pause/resume/kill/reset), model switching, bulk pause/resume

## Configuration

### team_config.yaml

```yaml
project:
  name: my-project
  workspace_path: workspace
  main_branch: main

agents:
  fixed:
    dev_manager:
      model: sonnet
    project_manager:
      model: sonnet
    integrator:
      model: sonnet
    cicd_engineer:
      model: sonnet
  custom:
    - name: backend_developer
      role: Senior Back-End Developer
      system_prompt: You are an expert backend developer...
      model: sonnet
      allowed_tools: Bash,Edit,Write,Read,Glob,Grep
    - name: frontend_dev
      role: Frontend Developer
      system_prompt: You are an expert frontend developer...
      model: sonnet
      allowed_tools: Bash,Edit,Write,Read,Glob,Grep
```

New agents can be added to the config or created at runtime through the dashboard's Team Config modal.

### Tool Presets

| Preset | Tools | Use Case |
|--------|-------|----------|
| Full Dev | `Bash,Edit,Write,Read,Glob,Grep` | Agents that write code |
| Read-only | `Read,Glob,Grep` | Managers and reviewers |
| None | *(empty)* | Pure reasoning (dev_manager) |

## Key Design Decisions

**File-based message passing** -- Agents exchange JSON files in inbox/outbox directories. Simple, reliable, and avoids distributed consensus complexity.

**Git worktrees** -- Each coding agent gets its own worktree on a dedicated branch (`dev/{agent_id}`). This allows true parallel development without lock contention on the working tree.

**Stateless router** -- The Dev Manager receives a fresh system prompt on every call with the current team roster and memory. It never accumulates stale context.

**Session persistence** -- Worker agents use Claude Code's `--resume` flag to maintain conversation context across requests. Session IDs are stored in `sessions.json` per project.

**Auto-pause circuit breaker** -- After 3 consecutive errors, an agent's session is automatically paused to prevent runaway cost.

**Two-tier memory** -- Global personality memory survives project switches; project memory is scoped. Both are injected into prompts with a 2000-char cap.

## API Reference

### Chat
- `POST /api/chat` -- Send message to dev_manager
- `GET /api/chat/history` -- Get chat history

### Agents
- `GET /api/agents` -- List all agents with status
- `POST /api/agents/{agent_id}/message` -- Send message to specific agent
- `POST /api/agents/{agent_id}/status-request` -- Request status report

### Tasks
- `GET /api/tasks` -- List all tasks
- `GET /api/tasks/{task_id}` -- Get task detail
- `PATCH /api/tasks/{task_id}` -- Update task fields

### Sessions
- `GET /api/sessions` -- Session metadata for all agents
- `POST /api/sessions/{agent_id}/pause` -- Pause agent session
- `POST /api/sessions/{agent_id}/resume` -- Resume agent session
- `POST /api/sessions/{agent_id}/kill` -- Kill agent session
- `POST /api/sessions/{agent_id}/reset` -- Reset session stats
- `POST /api/sessions/{agent_id}/model` -- Change agent model (sonnet/opus/haiku)
- `POST /api/sessions/pause-all` -- Pause all session-based agents
- `POST /api/sessions/resume-all` -- Resume all paused agents

### Knowledge Base
- `GET /api/knowledge` -- List all documents
- `GET /api/knowledge/{doc_id}` -- Get document content
- `POST /api/knowledge` -- Create document

### Projects
- `GET /api/projects` -- List projects
- `POST /api/projects` -- Create new project
- `PATCH /api/projects/{project_id}` -- Activate project

### WebSocket
- `WS /ws` -- Real-time event stream (agent status, task updates, chat, KB changes)

## Task Workflow

```
PENDING ──▶ IN_PROGRESS ──▶ REVIEW ──▶ DONE
   │              │            │
   ▼              ▼            ▼
BLOCKED        PAUSED     IN_PROGRESS (revision requested)
```

- Privileged agents (user, dev_manager, project_manager) can override transition rules
- Tasks auto-transition to `in_progress` when an agent starts working
- Moving to `review` auto-assigns the project_manager as reviewer
- Review feedback loops back to the original assignee

## Dependencies

```
fastapi>=0.115.0
uvicorn>=0.34.0
websockets>=13.0
pydantic>=2.0
pyyaml>=6.0
anthropic>=0.40.0
```
