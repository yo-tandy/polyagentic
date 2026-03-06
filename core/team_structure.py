"""Team structure loader with per-project override support.

Reads team_structure.yaml (global default) and optionally merges a
project-level override from projects/<project_id>/team_structure.yaml.

Agent instances are created from role definitions via the generic factory
in :mod:`agents.role_agent`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class AgentDefinition:
    agent_id: str
    name: str
    role: str               # display role label (e.g. "Manager")
    description: str
    role_id: str = ""       # references agent_roles.role_id (e.g. "manager")
    model: str = "sonnet"
    is_fixed: bool = False
    needs_worktree: bool = True
    routing_rules: list[dict] = field(default_factory=list)
    enabled: bool = True
    prompt_append: str = ""                  # project-specific prompt addition
    allowed_actions: list[str] | None = None  # override role defaults (None = use role)
    # Legacy fields — kept for backward compat during migration
    class_name: str = ""
    module_path: str = ""
    configure_extras: list[str] = field(default_factory=list)


@dataclass
class TeamStructure:
    user_facing_agent: str
    privileged_agents: list[str]
    checkpoint_agent: str
    agents: dict[str, AgentDefinition]

    def get_fixed_ids(self) -> set[str]:
        """Return IDs of all fixed (non-removable) agents."""
        return {aid for aid, a in self.agents.items() if a.is_fixed and a.enabled}

    def get_worktree_excluded_ids(self) -> set[str]:
        """Return IDs of agents that do NOT get their own worktree."""
        return {aid for aid, a in self.agents.items() if not a.needs_worktree and a.enabled}

    def get_enabled_agents(self) -> dict[str, AgentDefinition]:
        """Return only enabled agent definitions."""
        return {aid: a for aid, a in self.agents.items() if a.enabled}


# ── Loading & merging ────────────────────────────────────────────────

def load_team_structure(
    base_dir: Path,
    project_dir: Path | None = None,
) -> TeamStructure:
    """Load the global team structure, optionally merged with a project override.

    Args:
        base_dir: Root directory containing team_structure.yaml.
        project_dir: Project directory (e.g., projects/<id>/) that may contain
                     its own team_structure.yaml override. None = global only.
    """
    global_path = base_dir / "team_structure.yaml"
    if not global_path.exists():
        raise FileNotFoundError(f"Global team structure not found: {global_path}")

    with open(global_path) as f:
        global_data = yaml.safe_load(f) or {}

    # Merge with project-level override if it exists
    if project_dir:
        project_path = project_dir / "team_structure.yaml"
        if project_path.exists():
            with open(project_path) as f:
                project_data = yaml.safe_load(f) or {}
            global_data = _merge_structures(global_data, project_data)
            logger.info("Merged project team structure from %s", project_path)

    return _parse_structure(global_data)


def _merge_structures(base: dict, override: dict) -> dict:
    """Shallow merge: project override fields replace global fields.

    For top-level scalars (user_facing_agent, checkpoint_agent): override replaces.
    For privileged_agents: override replaces (not appended).
    For agents: per-agent shallow merge (project fields override base fields).
    """
    merged = dict(base)

    # Top-level scalars
    for key in ("user_facing_agent", "checkpoint_agent", "privileged_agents"):
        if key in override:
            merged[key] = override[key]

    # Agents: per-agent shallow merge
    if "agents" in override:
        base_agents = dict(merged.get("agents", {}))
        for agent_id, agent_override in override["agents"].items():
            if agent_override is None:
                continue
            if agent_id in base_agents:
                # Merge: project fields override base fields
                merged_agent = dict(base_agents[agent_id])
                merged_agent.update(agent_override)
                base_agents[agent_id] = merged_agent
            else:
                # New agent from project
                base_agents[agent_id] = agent_override
        merged["agents"] = base_agents

    return merged


def _parse_structure(data: dict) -> TeamStructure:
    """Parse raw YAML dict into a TeamStructure dataclass."""
    agents = {}
    for agent_id, agent_data in data.get("agents", {}).items():
        if not isinstance(agent_data, dict):
            continue
        agents[agent_id] = AgentDefinition(
            agent_id=agent_id,
            name=agent_data.get("name", agent_id.replace("_", " ").title()),
            role=agent_data.get("role", agent_id),
            description=agent_data.get("description", ""),
            role_id=agent_data.get("role_id", ""),
            model=agent_data.get("model", "sonnet"),
            is_fixed=agent_data.get("is_fixed", False),
            needs_worktree=agent_data.get("needs_worktree", True),
            routing_rules=agent_data.get("routing_rules", []) or [],
            enabled=agent_data.get("enabled", True),
            prompt_append=agent_data.get("prompt_append", ""),
            allowed_actions=agent_data.get("allowed_actions"),
            # Legacy
            class_name=agent_data.get("class", ""),
            module_path=agent_data.get("module", ""),
            configure_extras=agent_data.get("configure_extras", []) or [],
        )

    return TeamStructure(
        user_facing_agent=data.get("user_facing_agent", "manny"),
        privileged_agents=data.get("privileged_agents", []),
        checkpoint_agent=data.get("checkpoint_agent", "jerry"),
        agents=agents,
    )


# ── Prompt fragment generators ───────────────────────────────────────

def build_fixed_team_roles(structure: TeamStructure) -> str:
    """Generate the 'Fixed Team Roles' bullet list for the router agent's prompt.

    Excludes the user-facing agent itself (Manny doesn't list himself).
    """
    lines = []
    for aid, agent in structure.agents.items():
        if not agent.enabled:
            continue
        if not agent.is_fixed:
            continue
        if aid == structure.user_facing_agent:
            continue
        desc = agent.description.strip().rstrip(".")
        lines.append(f"- **{agent.name}** (`{aid}`): {agent.role} -- {desc}.")
    return "\n".join(lines)


def build_routing_guide(structure: TeamStructure) -> str:
    """Generate the markdown routing table from the user-facing agent's routing rules."""
    router = structure.agents.get(structure.user_facing_agent)
    if not router or not router.routing_rules:
        return ""
    lines = ["| Request type | Route to |", "|---|---|"]
    for rule in router.routing_rules:
        lines.append(f"| {rule.get('request', '')} | {rule.get('route_to', '')} |")
    return "\n".join(lines)
