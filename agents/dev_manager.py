from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_NONE, DEFAULT_MODEL, CLAUDE_ALLOWED_TOOLS_DEV

logger = logging.getLogger(__name__)


class DevManagerAgent(Agent):
    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("dev_manager")
        self._prompt_template = prompt_template  # keep raw template for re-rendering
        self._team_roster: str = ""
        super().__init__(
            agent_id="dev_manager",
            name="Development Manager",
            role="Development Manager",
            system_prompt=prompt_template,  # team roster injected via update_team_roster
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_NONE,
            messages_dir=messages_dir,
            working_dir=working_dir,
            timeout=120,
            use_session=False,  # Stateless — each call gets fresh system prompt
            max_budget_usd=0.25,  # Cap spending to prevent infinite generation with --tools ""
        )
        # Extra deps for create_agent flow (set via configure_extras)
        self._registry = None
        self._git_manager = None
        self._extra_session_store = None
        self._workspace_path = None
        self._messages_dir = None
        self._worktrees_dir = None

    def configure_extras(self, registry, git_manager, session_store, workspace_path,
                         messages_dir=None, worktrees_dir=None):
        """Provide extra dependencies needed for dynamic agent creation."""
        self._registry = registry
        self._git_manager = git_manager
        self._extra_session_store = session_store
        self._workspace_path = workspace_path
        self._messages_dir = messages_dir
        self._worktrees_dir = worktrees_dir

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with the current team roster.

        Can be called at startup and whenever agents are added/removed
        at runtime.  Since use_session=False, the next CLI call will
        automatically pick up the new prompt.
        """
        self._team_roster = roster_text
        self._team_roles = team_roles
        self._routing_guide = routing_guide
        self._render_system_prompt()

    def _render_system_prompt(self):
        """Re-build system prompt from template + roster + roles + routing + memory."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template,
            roster=self._team_roster or "",
            team_roles=getattr(self, "_team_roles", ""),
            routing_guide=getattr(self, "_routing_guide", ""),
        )

    def _get_system_prompt_if_first_call(self) -> str | None:
        # Dev manager is stateless — always re-render and send system prompt
        self._render_system_prompt()
        return self.system_prompt

    async def process_message(self, msg: Message) -> list[Message]:
        """Override to handle create_agent actions before normal parse."""
        # Call Claude CLI (same as base class)
        prompt = self._build_prompt(msg)
        session_id = None
        if self.use_session and self._session_store:
            session_id = self._session_store.get(self.agent_id)

        result = await self._subprocess.invoke(
            prompt=prompt,
            system_prompt=self._get_system_prompt_if_first_call(),
            model=self.model,
            allowed_tools=self.allowed_tools,
            session_id=session_id,
            working_dir=self.working_dir,
            timeout=self.timeout,
            max_budget_usd=self.max_budget_usd,
        )

        # Retry on stale session: clear the session and invoke fresh
        if result.is_error and session_id and "No conversation found" in result.result_text:
            logger.warning(
                "Agent %s stale session %s, clearing and retrying fresh",
                self.agent_id, session_id,
            )
            if self._session_store:
                self._session_store.set(self.agent_id, "")
            result = await self._subprocess.invoke(
                prompt=prompt,
                system_prompt=self._get_system_prompt_if_first_call(),
                model=self.model,
                allowed_tools=self.allowed_tools,
                session_id=None,
                working_dir=self.working_dir,
                timeout=self.timeout,
                max_budget_usd=self.max_budget_usd,
            )

        if self.use_session and result.session_id and self._session_store:
            self._session_store.set(self.agent_id, result.session_id)

        # Record subprocess stats (stateless — no auto-pause)
        if self._session_store:
            self._session_store.record_request(
                self.agent_id,
                duration_ms=result.duration_ms or 0,
                is_error=result.is_error,
            )

        if result.is_error:
            logger.error("Agent %s Claude error: %s", self.agent_id, result.result_text)
            return [Message(
                sender=self.agent_id,
                recipient="user",
                type=MessageType.CHAT,
                content=f"⚠️ {self.name} error: {result.result_text}",
                task_id=msg.task_id,
                parent_message_id=msg.id,
            )]

        result_text = result.result_text

        # Handle create_agent actions (async) before normal parse
        actions = self._extract_actions(result_text)
        create_actions = [a for a in actions if a.get("action") == "create_agent"]

        if create_actions and self._registry:
            from web.routes.config import create_and_register_agent
            for ca in create_actions:
                name = ca.get("name", "")
                role = ca.get("role", "")
                sys_prompt = ca.get("system_prompt", f"You are a {role}.")
                if not name:
                    continue
                if self._registry.get(name):
                    logger.info("Agent %s already exists, skipping creation", name)
                    continue
                try:
                    await create_and_register_agent(
                        name=name,
                        role=role,
                        system_prompt=sys_prompt,
                        model=ca.get("model", DEFAULT_MODEL),
                        allowed_tools=ca.get("allowed_tools", CLAUDE_ALLOWED_TOOLS_DEV),
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
                    )
                    logger.info("Dev manager created new agent: %s (%s)", name, role)
                except Exception:
                    logger.exception("Failed to create agent %s", name)

        # Handle common actions (memory, KB)
        await self._handle_common_actions(actions)

        return self._parse_response(result_text, msg)

    # Dev manager _parse_response stays sync — no KB writes happen here
    def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        messages = []
        actions = self._extract_actions(result_text)

        if not actions:
            logger.warning(
                "Dev Manager produced no action blocks (prompt from %s, %d chars). "
                "Forwarding raw text to user.",
                original_msg.sender, len(result_text),
            )
            messages.append(Message(
                sender=self.agent_id,
                recipient="user",
                type=MessageType.CHAT,
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
                priority = action.get("priority", 3)
                labels = action.get("labels", [])
                role = action.get("role", None)

                # Determine if 'to' is a known agent or a role name
                is_known_agent = self._registry and self._registry.get(to)

                # Create task on the board
                if self._task_board:
                    task = self._task_board.create_task(
                        title=title,
                        description=desc,
                        created_by=self.agent_id,
                        assignee=to if is_known_agent else None,
                        role=role or (to if not is_known_agent else None),
                        priority=priority,
                        labels=labels,
                    )
                    task_id = task.id
                else:
                    task_id = None

                # Only send message if the agent exists
                if is_known_agent:
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
                user_msg = action.get("message", result_text)
                suggested = action.get("suggested_answers", [])
                meta = {}
                if suggested:
                    meta["suggested_answers"] = suggested[:3]
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=user_msg,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                    metadata=meta if meta else None,
                ))

            elif action_type == "pause_task":
                target_agent = action.get("agent_id", "")
                target_task_id = action.get("task_id", "")
                if target_agent and target_task_id:
                    messages.append(Message(
                        sender=self.agent_id,
                        recipient=target_agent,
                        type=MessageType.SYSTEM,
                        content=(
                            f"PAUSE TASK {target_task_id}: Summarize your current progress on this task, "
                            f"save your state using update_task with a paused_summary, and stop working on it."
                        ),
                        task_id=target_task_id,
                        metadata={"command": "pause_task"},
                    ))

            elif action_type == "start_task":
                target_agent = action.get("agent_id", "")
                target_task_id = action.get("task_id", "")
                task = self._task_board.get_task(target_task_id) if self._task_board else None
                content = ""
                if task:
                    content = task.description
                    if task.paused_summary:
                        content += f"\n\n--- RESUMED TASK ---\nPrevious state when paused:\n{task.paused_summary}"
                    # Reassign if needed
                    self._task_board.update_task(
                        target_task_id, assignee=target_agent, _agent_id=self.agent_id,
                    )
                messages.append(Message(
                    sender=self.agent_id,
                    recipient=target_agent,
                    type=MessageType.TASK,
                    content=content or f"Start working on task {target_task_id}",
                    task_id=target_task_id,
                    parent_message_id=original_msg.id,
                    metadata={"task_title": task.title if task else "Task"},
                ))

            # create_agent, update_task, update_memory, write_document handled by common actions above

        # If no user response was generated, add a summary
        has_user_response = any(m.recipient == "user" for m in messages)
        if not has_user_response and original_msg.sender == "user":
            delegations = [a for a in actions if a.get("action") == "delegate"]
            if delegations:
                summary = f"I've delegated your request to {len(delegations)} team member(s). I'll report back when they're done."
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=summary,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

        return messages
