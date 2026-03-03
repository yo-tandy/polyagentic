from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_DEV, DEFAULT_MODEL

logger = logging.getLogger(__name__)


class RoryAgent(Agent):
    """Robot Resources agent — recruits and configures worker agents.

    Session-based (stateful). Handles `recruit_agent` actions by calling
    create_and_register_agent() to spin up new agents dynamically.
    """

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("rory")
        self._prompt_template = prompt_template
        super().__init__(
            agent_id="rory",
            name="Rory",
            role="Robot Resources",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_DEV,
            messages_dir=messages_dir,
            working_dir=working_dir,
            use_session=True,
        )
        self._registry = None
        self._git_manager = None
        self._extra_session_store = None
        self._workspace_path = None
        self._messages_dir = None
        self._worktrees_dir = None
        self._container_manager = None
        self._project_store = None

    def configure_extras(self, registry, git_manager, session_store, workspace_path,
                         messages_dir=None, worktrees_dir=None, container_manager=None,
                         project_store=None, team_structure=None):
        """Provide dependencies needed for dynamic agent creation."""
        self._registry = registry
        self._git_manager = git_manager
        self._extra_session_store = session_store
        self._workspace_path = workspace_path
        self._messages_dir = messages_dir
        self._worktrees_dir = worktrees_dir
        self._container_manager = container_manager
        self._project_store = project_store
        self._team_structure = team_structure

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template, roster_text, team_roles=team_roles, routing_guide=routing_guide,
        )

    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        messages = []
        actions = self._extract_actions(result_text)

        # Handle recruit_agent actions first (modifies registry)
        recruit_actions = [a for a in actions if a.get("action") == "recruit_agent"]
        if recruit_actions and self._registry:
            from web.routes.config import create_and_register_agent
            for ra in recruit_actions:
                name = ra.get("name", "")
                role = ra.get("role", "")
                sys_prompt = ra.get("system_prompt", f"You are a {role}.")
                if not name:
                    continue
                if self._registry.get(name):
                    logger.info("Agent %s already exists, skipping recruitment", name)
                    continue
                try:
                    await create_and_register_agent(
                        name=name,
                        role=role,
                        system_prompt=sys_prompt,
                        model=ra.get("model", DEFAULT_MODEL),
                        allowed_tools=ra.get("allowed_tools", CLAUDE_ALLOWED_TOOLS_DEV),
                        registry=self._registry,
                        broker=self._broker,
                        session_store=self._extra_session_store,
                        task_board=self._task_board,
                        git_manager=self._git_manager,
                        workspace_path=self._workspace_path,
                        messages_dir=self._messages_dir,
                        worktrees_dir=self._worktrees_dir,
                        memory_manager=self._memory_manager,
                        knowledge_base=self._knowledge_base,
                        container_manager=self._container_manager,
                        project_store=self._project_store,
                        team_structure=getattr(self, "_team_structure", None),
                    )
                    logger.info("Rory recruited new agent: %s (%s)", name, role)
                except Exception:
                    logger.exception("Failed to recruit agent %s", name)

        # Handle common actions (memory, KB, update_task)
        await self._handle_common_actions(actions)

        if not actions:
            messages.append(Message(
                sender=self.agent_id,
                recipient=original_msg.sender,
                type=MessageType.RESPONSE,
                content=result_text,
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            ))
            return messages

        for action in actions:
            action_type = action.get("action")

            if action_type == "delegate":
                to = action.get("to", "")
                title = action.get("task_title", "Task")
                desc = action.get("task_description", "")
                labels = action.get("labels", [])

                if self._task_board:
                    task = self._task_board.create_task(
                        title=title, description=desc,
                        created_by=self.agent_id, assignee=to,
                        labels=labels,
                    )
                    task_id = task.id
                else:
                    task_id = None

                messages.append(Message(
                    sender=self.agent_id,
                    recipient=to,
                    type=MessageType.TASK,
                    content=desc,
                    task_id=task_id,
                    parent_message_id=original_msg.id,
                    metadata={"task_title": title},
                ))

            elif action_type == "respond_to_user":
                suggested = action.get("suggested_answers", [])
                meta = {}
                if suggested:
                    meta["suggested_answers"] = suggested[:3]
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=action.get("message", result_text),
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                    metadata=meta if meta else None,
                ))

            # recruit_agent, update_task, update_memory handled above

        if not messages:
            messages.append(Message(
                sender=self.agent_id,
                recipient=original_msg.sender,
                type=MessageType.RESPONSE,
                content=result_text,
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            ))

        return messages
