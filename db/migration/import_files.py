"""One-shot file→DB importer.

Reads existing JSON/YAML/Markdown files from the polyagentic project
directory structure and inserts them into the database tables.

Usage:
    python -m db.migration.import_files [--base-dir /path/to/polyagentic]

Or via the server:
    python main.py --migrate-from-files
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from db import init_db, get_session_factory
from db.models.project import Project, CustomAgentDef
from db.models.session import AgentSession
from db.models.task import TaskModel, TaskProgressNote
from db.models.knowledge import Document, DocumentComment
from db.models.memory import AgentMemory
from db.models.team_structure import TeamAgentDef, TeamStructureMeta
from db.models.config import ConfigEntry
from db.config_provider import DEFAULT_CONFIG_SEEDS

from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("migrate")


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string into a datetime, or return None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _parse_dt_required(value: str | None) -> datetime:
    """Parse ISO-8601 string, fallback to now() if missing."""
    dt = _parse_dt(value)
    return dt or datetime.now(timezone.utc)


# ── Project Import ─────────────────────────────────────────────────────

async def import_projects(sf, base_dir: Path) -> int:
    """Import projects from projects.json + per-project project.json files."""
    projects_json = base_dir / "projects.json"
    if not projects_json.exists():
        logger.warning("No projects.json found at %s", projects_json)
        return 0

    with open(projects_json) as f:
        data = json.load(f)

    active_id = data.get("active_project_id")
    projects = data.get("projects", [])
    count = 0

    async with sf() as session:
        for p in projects:
            pid = p["id"]
            # Check if already exists
            existing = await session.get(Project, pid)
            if existing:
                logger.info("  Project '%s' already exists, skipping", pid)
                continue

            # Try to read per-project project.json for richer data
            project_json = base_dir / "projects" / pid / "project.json"
            if project_json.exists():
                with open(project_json) as f:
                    pdata = json.load(f)
                p.update(pdata)  # per-project file has more detail

            project = Project(
                id=pid,
                name=p.get("name", pid),
                description=p.get("description", ""),
                status=p.get("status", "active"),
                main_branch=p.get("main_branch", "main"),
                github_url=p.get("github_url"),
                is_active=(pid == active_id),
                tenant_id="default",
            )
            session.add(project)
            count += 1
            logger.info("  Imported project: %s", pid)

        await session.commit()

    # Import custom agents per project
    agent_count = 0
    for p in projects:
        pid = p["id"]
        agents_json = base_dir / "projects" / pid / "agents.json"
        if not agents_json.exists():
            continue
        with open(agents_json) as f:
            agents_data = json.load(f)

        agents_list = agents_data if isinstance(agents_data, list) else agents_data.get("agents", [])

        async with sf() as session:
            for a in agents_list:
                if not isinstance(a, dict) or not a.get("name"):
                    continue
                agent_def = CustomAgentDef(
                    project_id=pid,
                    name=a["name"],
                    role=a.get("role", ""),
                    system_prompt=a.get("system_prompt", ""),
                    model=a.get("model", "sonnet"),
                    allowed_tools=a.get("allowed_tools", "Bash,Edit,Write,Read,Glob,Grep"),
                    tenant_id="default",
                )
                session.add(agent_def)
                agent_count += 1
            await session.commit()

    if agent_count:
        logger.info("  Imported %d custom agent definitions", agent_count)

    return count


# ── Session Import ─────────────────────────────────────────────────────

async def import_sessions(sf, base_dir: Path) -> int:
    """Import sessions.json from each project directory."""
    count = 0
    projects_dir = base_dir / "projects"
    if not projects_dir.exists():
        return 0

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        sessions_json = project_dir / "sessions.json"
        if not sessions_json.exists():
            continue

        project_id = project_dir.name
        with open(sessions_json) as f:
            data = json.load(f)

        sessions = data.get("sessions", data)  # handle both formats

        async with sf() as session:
            for agent_id, sdata in sessions.items():
                if not isinstance(sdata, dict):
                    continue

                # Check if already imported
                stmt = select(AgentSession).where(
                    AgentSession.project_id == project_id,
                    AgentSession.agent_id == agent_id,
                )
                result = await session.execute(stmt)
                if result.scalar_one_or_none():
                    continue

                agent_session = AgentSession(
                    project_id=project_id,
                    agent_id=agent_id,
                    session_id=sdata.get("session_id", ""),
                    state=sdata.get("state", "active"),
                    model=sdata.get("model"),
                    prompt_hash=sdata.get("prompt_hash"),
                    request_count=sdata.get("request_count", 0),
                    error_count=sdata.get("error_count", 0),
                    consecutive_errors=sdata.get("consecutive_errors", 0),
                    total_duration_ms=sdata.get("total_duration_ms", 0),
                    total_cost_usd=sdata.get("total_cost_usd", 0.0),
                    total_input_tokens=sdata.get("total_input_tokens", 0),
                    total_output_tokens=sdata.get("total_output_tokens", 0),
                    last_used_at=_parse_dt(sdata.get("last_used_at")),
                    paused_at=_parse_dt(sdata.get("paused_at")),
                    killed_at=_parse_dt(sdata.get("killed_at")),
                    tenant_id="default",
                )
                session.add(agent_session)
                count += 1

            await session.commit()

    return count


# ── Task Import ────────────────────────────────────────────────────────

async def import_tasks(sf, base_dir: Path) -> int:
    """Import tasks.json from each project directory."""
    count = 0
    note_count = 0
    projects_dir = base_dir / "projects"
    if not projects_dir.exists():
        return 0

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        tasks_json = project_dir / "tasks.json"
        if not tasks_json.exists():
            continue

        project_id = project_dir.name
        with open(tasks_json) as f:
            data = json.load(f)

        tasks = data.get("tasks", data)
        if not isinstance(tasks, dict):
            continue

        async with sf() as session:
            for task_id, tdata in tasks.items():
                if not isinstance(tdata, dict):
                    continue

                # Check if already imported
                existing = await session.get(TaskModel, task_id)
                if existing:
                    continue

                task = TaskModel(
                    id=task_id,
                    project_id=project_id,
                    title=tdata.get("title", ""),
                    description=tdata.get("description", ""),
                    status=tdata.get("status", "pending"),
                    created_by=tdata.get("created_by", "unknown"),
                    assignee=tdata.get("assignee"),
                    role=tdata.get("role"),
                    reviewer=tdata.get("reviewer"),
                    priority=tdata.get("priority", 3),
                    labels=tdata.get("labels", []),
                    branch=tdata.get("branch"),
                    parent_task_id=tdata.get("parent_task_id"),
                    subtasks=tdata.get("subtasks", []),
                    messages=tdata.get("messages", []),
                    paused_summary=tdata.get("paused_summary"),
                    outcome=tdata.get("outcome"),
                    completion_summary=tdata.get("completion_summary"),
                    review_output=tdata.get("review_output"),
                    tenant_id="default",
                )
                session.add(task)
                count += 1

                # Import progress notes
                for note in tdata.get("progress_notes", []):
                    pn = TaskProgressNote(
                        task_id=task_id,
                        agent_id=note.get("agent", "unknown"),
                        note=note.get("note", ""),
                        created_at=note.get("timestamp", ""),
                    )
                    session.add(pn)
                    note_count += 1

            await session.commit()

    if note_count:
        logger.info("  (%d progress notes)", note_count)
    return count


# ── Knowledge Base Import ──────────────────────────────────────────────

async def import_knowledge_base(sf, base_dir: Path) -> int:
    """Import KB documents and comments from each project."""
    doc_count = 0
    comment_count = 0
    projects_dir = base_dir / "projects"
    if not projects_dir.exists():
        return 0

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        docs_dir = project_dir / "docs"
        index_json = docs_dir / "_index.json"
        if not index_json.exists():
            continue

        project_id = project_dir.name
        with open(index_json) as f:
            data = json.load(f)

        documents = data.get("documents", [])

        async with sf() as session:
            for doc in documents:
                doc_id = doc.get("id")
                if not doc_id:
                    continue

                # Check if already imported
                existing = await session.get(Document, doc_id)
                if existing:
                    continue

                category = doc.get("category", "specs")
                filename = doc.get("filename", "")

                # Try to read document content from file
                content = ""
                doc_file = docs_dir / category / filename
                if doc_file.exists():
                    try:
                        content = doc_file.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(
                            "  Could not read doc file %s: %s", doc_file, e,
                        )

                document = Document(
                    id=doc_id,
                    project_id=project_id,
                    title=doc.get("title", "Untitled"),
                    category=category,
                    filename=filename,
                    content=content,
                    source=doc.get("source"),
                    source_path=doc.get("source_path"),
                    created_by=doc.get("created_by", "unknown"),
                    tenant_id="default",
                )
                session.add(document)
                doc_count += 1

                # Import comments for this document
                comments_file = docs_dir / category / f"{filename}.comments.json"
                if comments_file.exists():
                    try:
                        with open(comments_file) as f:
                            cdata = json.load(f)
                        for c in cdata.get("comments", []):
                            comment = DocumentComment(
                                id=c.get("id", ""),
                                doc_id=doc_id,
                                highlighted_text=c.get("highlighted_text", ""),
                                element_index=c.get("element_index", 0),
                                comment_text=c.get("comment_text", ""),
                                assigned_to=c.get("assigned_to", ""),
                                created_by=c.get("created_by", "user"),
                                status=c.get("status", "open"),
                                resolution=c.get("resolution"),
                                edit_verified=c.get("edit_verified", False),
                                resolved_at=_parse_dt(c.get("resolved_at")),
                                created_at=_parse_dt_required(c.get("created_at")),
                            )
                            session.add(comment)
                            comment_count += 1
                    except Exception as e:
                        logger.warning(
                            "  Could not import comments from %s: %s",
                            comments_file, e,
                        )

            await session.commit()

    if comment_count:
        logger.info("  (%d comments)", comment_count)
    return doc_count


# ── Memory Import ──────────────────────────────────────────────────────

async def import_memories(sf, base_dir: Path) -> int:
    """Import memory markdown files (global personality + per-project)."""
    count = 0

    async with sf() as session:
        # 1. Global personality memories (memory/<agent_id>/personality.md)
        global_memory_dir = base_dir / "memory"
        if global_memory_dir.exists():
            for agent_dir in sorted(global_memory_dir.iterdir()):
                if not agent_dir.is_dir():
                    continue
                agent_id = agent_dir.name
                personality_file = agent_dir / "personality.md"
                if not personality_file.exists():
                    continue

                # Check if already imported
                stmt = select(AgentMemory).where(
                    AgentMemory.agent_id == agent_id,
                    AgentMemory.memory_type == "personality",
                    AgentMemory.project_id.is_(None),
                )
                result = await session.execute(stmt)
                if result.scalar_one_or_none():
                    continue

                content = personality_file.read_text(encoding="utf-8").strip()
                if not content:
                    continue

                memory = AgentMemory(
                    agent_id=agent_id,
                    memory_type="personality",
                    project_id=None,
                    content=content,
                    tenant_id="default",
                )
                session.add(memory)
                count += 1

        # 2. Per-project memories (projects/<project_id>/memory/<agent_id>/project.md)
        projects_dir = base_dir / "projects"
        if projects_dir.exists():
            for project_dir in sorted(projects_dir.iterdir()):
                if not project_dir.is_dir():
                    continue
                project_id = project_dir.name
                memory_dir = project_dir / "memory"
                if not memory_dir.exists():
                    continue

                for agent_dir in sorted(memory_dir.iterdir()):
                    if not agent_dir.is_dir():
                        continue
                    agent_id = agent_dir.name
                    project_file = agent_dir / "project.md"
                    if not project_file.exists():
                        continue

                    # Check if already imported
                    stmt = select(AgentMemory).where(
                        AgentMemory.agent_id == agent_id,
                        AgentMemory.memory_type == "project",
                        AgentMemory.project_id == project_id,
                    )
                    result = await session.execute(stmt)
                    if result.scalar_one_or_none():
                        continue

                    content = project_file.read_text(encoding="utf-8").strip()
                    if not content:
                        continue

                    memory = AgentMemory(
                        agent_id=agent_id,
                        memory_type="project",
                        project_id=project_id,
                        content=content,
                        tenant_id="default",
                    )
                    session.add(memory)
                    count += 1

        await session.commit()

    return count


# ── Team Structure Import ──────────────────────────────────────────────

async def import_team_structure(sf, base_dir: Path) -> int:
    """Import team_structure.yaml into DB."""
    yaml_path = base_dir / "team_structure.yaml"
    if not yaml_path.exists():
        logger.warning("No team_structure.yaml found at %s", yaml_path)
        return 0

    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    count = 0
    async with sf() as session:
        # Check if already imported
        stmt = select(TeamStructureMeta).where(
            TeamStructureMeta.tenant_id == "default",
            TeamStructureMeta.project_id.is_(None),
        )
        result = await session.execute(stmt)
        if result.scalar_one_or_none():
            logger.info("  Team structure already imported, skipping")
            return 0

        # Import meta
        meta = TeamStructureMeta(
            tenant_id="default",
            project_id=None,
            user_facing_agent=data.get("user_facing_agent", "manny"),
            privileged_agents=data.get("privileged_agents", ["manny", "jerry"]),
            checkpoint_agent=data.get("checkpoint_agent", "jerry"),
        )
        session.add(meta)

        # Import agent definitions
        agents = data.get("agents", {})
        for agent_id, agent_data in agents.items():
            if not isinstance(agent_data, dict):
                continue
            agent_def = TeamAgentDef(
                tenant_id="default",
                project_id=None,
                agent_id=agent_id,
                class_name=agent_data.get("class", "CustomAgent"),
                module_path=agent_data.get("module", "agents.custom_agent"),
                name=agent_data.get("name", agent_id.replace("_", " ").title()),
                role=agent_data.get("role", ""),
                description=agent_data.get("description", ""),
                model=agent_data.get("model", "sonnet"),
                is_fixed=agent_data.get("is_fixed", False),
                needs_worktree=agent_data.get("needs_worktree", True),
                configure_extras=agent_data.get("configure_extras", []) or [],
                routing_rules=agent_data.get("routing_rules", []) or [],
                enabled=agent_data.get("enabled", True),
            )
            session.add(agent_def)
            count += 1

        await session.commit()

    return count


# ── Config Seeds ───────────────────────────────────────────────────────

async def import_config_seeds(sf) -> int:
    """Seed default config entries if the table is empty."""
    async with sf() as session:
        stmt = select(ConfigEntry.id).limit(1)
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            logger.info("  Config table already seeded, skipping")
            return 0

        for d in DEFAULT_CONFIG_SEEDS:
            session.add(ConfigEntry(
                tenant_id=d.get("tenant_id", "default"),
                scope=d["scope"],
                scope_id=d.get("scope_id"),
                key=d["key"],
                value=d["value"],
                value_type=d.get("value_type", "string"),
                description=d.get("description"),
            ))

        await session.commit()
        return len(DEFAULT_CONFIG_SEEDS)


# ── Main ───────────────────────────────────────────────────────────────

async def run_migration(base_dir: Path | None = None) -> dict[str, int]:
    """Run the full file→DB migration.

    Returns a dict of {store_name: imported_count}.
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent.parent

    logger.info("=" * 60)
    logger.info("  FILE → DB MIGRATION")
    logger.info("  Base directory: %s", base_dir)
    logger.info("=" * 60)

    await init_db()
    sf = get_session_factory()

    results: dict[str, int] = {}

    # 1. Config seeds (needed before other tables reference config)
    logger.info("\n[1/6] Seeding config defaults...")
    results["config"] = await import_config_seeds(sf)
    logger.info("  → %d config entries seeded", results["config"])

    # 2. Team structure (needed before agent creation)
    logger.info("\n[2/6] Importing team structure...")
    results["team_structure"] = await import_team_structure(sf, base_dir)
    logger.info("  → %d agent definitions imported", results["team_structure"])

    # 3. Projects (must come before project-scoped data)
    logger.info("\n[3/6] Importing projects...")
    results["projects"] = await import_projects(sf, base_dir)
    logger.info("  → %d projects imported", results["projects"])

    # 4. Sessions
    logger.info("\n[4/6] Importing sessions...")
    results["sessions"] = await import_sessions(sf, base_dir)
    logger.info("  → %d sessions imported", results["sessions"])

    # 5. Tasks
    logger.info("\n[5/6] Importing tasks...")
    results["tasks"] = await import_tasks(sf, base_dir)
    logger.info("  → %d tasks imported", results["tasks"])

    # 6. Knowledge base (documents + comments)
    logger.info("\n[6/6] Importing knowledge base...")
    results["documents"] = await import_knowledge_base(sf, base_dir)
    logger.info("  → %d documents imported", results["documents"])

    # 7. Agent memories
    logger.info("\n[7/7] Importing agent memories...")
    results["memories"] = await import_memories(sf, base_dir)
    logger.info("  → %d memories imported", results["memories"])

    # Summary
    total = sum(results.values())
    logger.info("\n" + "=" * 60)
    logger.info("  MIGRATION COMPLETE")
    logger.info("  Total records imported: %d", total)
    for name, cnt in results.items():
        logger.info("    %-20s %d", name, cnt)
    logger.info("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Import file-based data into the database",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Project root directory (defaults to polyagentic repo root)",
    )
    args = parser.parse_args()

    asyncio.run(run_migration(args.base_dir))


if __name__ == "__main__":
    main()
