"""Action parsing and dispatch logic extracted from Agent.

Handles action extraction from Claude output, normalization, sanitization,
validation retries, and all per-action handlers (memory, documents, tasks,
conversations).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, TYPE_CHECKING

from core.actions.base import infer_doc_category
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.actions.registry import ActionRegistry
    from core.memory_manager import MemoryManager
    from core.knowledge_base import KnowledgeBase
    from core.task_board import TaskBoard
    from core.session_store import SessionStore
    from core.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class ActionHandler:
    """Parses and dispatches action blocks from Claude output.

    Extracted from the Agent god class.  All methods mirror their
    original Agent counterparts exactly.
    """

    # Maps wrong top-level keys to correct ones
    _ACTION_KEY_MAP = {"tool": "action"}

    # Maps wrong action names to correct ones
    _ACTION_NAME_MAP = {
        "save_to_memory": "update_memory",
        "save_memory": "update_memory",
        "memory": "update_memory",
        "respond": "respond_to_user",
        "reply": "respond_to_user",
        "send_message": "respond_to_user",
        "assign": "delegate",
        "assign_task": "delegate",
        "assign_ticket": "delegate",
        "create_ticket": "delegate",
        "create_task": "delegate",
        "resolve_comment": "resolve_comments",
        "conversation_summary": "end_conversation",
        "close_conversation": "end_conversation",
        "finish_conversation": "end_conversation",
        "summarize_conversation": "end_conversation",
    }

    # Maps wrong field names to correct ones, per action type
    _FIELD_MAP = {
        "delegate": {
            "message": "task_description",
            "description": "task_description",
            "content": "task_description",
            "target": "to",
            "agent": "to",
            "title": "task_title",
        },
        "respond_to_user": {
            "content": "message",
            "text": "message",
            "response": "message",
        },
        "update_memory": {
            "value": "content",
            "text": "content",
            "key": "memory_type",
        },
        "write_document": {
            "path": "title",
            "name": "title",
            "filename": "title",
            "type": "category",
        },
        "resolve_comments": {
            "comments": "resolutions",
            "results": "resolutions",
        },
        "assign_ticket": {
            "message": "task_description",
            "description": "task_description",
            "content": "task_description",
            "target": "to",
            "agent": "to",
            "title": "task_title",
        },
    }

    def __init__(
        self,
        *,
        agent_id: str,
        agent_name: str,
        action_registry: ActionRegistry | None,
        memory_manager: MemoryManager | None,
        knowledge_base: KnowledgeBase | None,
        task_board: TaskBoard | None,
        conversation_manager: Any | None,
        broker: Any | None,
        session_store: SessionStore | None,
        provider: BaseProvider,
        user_facing_agent: str,
        allowed_actions: set[str] | None,
        get_known_actions_fn: Callable[[], set[str]],
    ) -> None:
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._action_registry = action_registry
        self._memory_manager = memory_manager
        self._knowledge_base = knowledge_base
        self._task_board = task_board
        self._conversation_manager = conversation_manager
        self._broker = broker
        self._session_store = session_store
        self._provider = provider
        self._user_facing_agent = user_facing_agent
        self._allowed_actions = allowed_actions
        self._get_known_actions = get_known_actions_fn

        # Mutable state kept in sync by Agent
        self.current_task_id: str | None = None
        self.last_actions_count: int = 0  # actions dispatched in last _parse_response call

    # ------------------------------------------------------------------
    # Provider access (Agent swaps provider at runtime)
    # ------------------------------------------------------------------

    def _set_provider(self, provider: BaseProvider) -> None:
        self._provider = provider

    # ------------------------------------------------------------------
    # Central dispatcher
    # ------------------------------------------------------------------

    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        """Central response parser -- dispatches all actions via the registry.

        Replaces per-agent ``_parse_response`` overrides and the old
        ``_handle_common_actions`` method.  Every action (messaging,
        documents, memory, git, etc.) is handled through the registry.
        """
        actions = self._extract_actions(result_text)
        self.last_actions_count = len(actions)

        if not actions:
            # No action blocks found -- sanitize and forward raw text
            cleaned = self._sanitize_for_user(result_text)
            if not cleaned.strip():
                cleaned = result_text  # keep raw if sanitization removes everything
            if cleaned.strip():
                return [Message(
                    sender=self._agent_id,
                    recipient=original_msg.sender,
                    type=MessageType.RESPONSE,
                    content=cleaned,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                )]
            return []

        # Dispatch through the action registry
        if self._action_registry:
            # _parse_response receives an Agent instance from the caller
            # (the Agent itself), but execute_all expects the Agent object.
            # We store a back-reference so we can pass it through.
            messages = await self._action_registry.execute_all(
                self._agent_ref, actions, original_msg,
            )
        else:
            # Fallback: no registry -- just return sanitized text
            logger.warning(
                "Agent %s has no action registry -- cannot process actions",
                self._agent_id,
            )
            messages = []

        # Fallback: if actions were processed but no messages generated,
        # send sanitized text (if any human-readable content remains)
        if not messages:
            cleaned = self._sanitize_for_user(result_text)
            if cleaned.strip():
                messages.append(Message(
                    sender=self._agent_id,
                    recipient=original_msg.sender,
                    type=MessageType.RESPONSE,
                    content=cleaned,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

        return messages

    # ------------------------------------------------------------------
    # Action extraction & normalization
    # ------------------------------------------------------------------

    def _extract_actions(self, text: str) -> list[dict]:
        """Extract action blocks from Claude output.

        Primary: looks for ```action ... ``` or ```<action_name> ... ``` fenced blocks.
        Fallback: recovers bare JSON objects containing "action" or "tool" keys.
        All parsed actions are normalized via _normalize_action().
        """
        actions = []

        # Known action names (for flexible fence tag matching)
        known_actions = self._get_known_actions() if self._get_known_actions else set()

        # Primary: fenced action blocks — accept ```action or ```<known_action_name>
        pattern = r"```(\w+)\s*(.*?)\s*```"
        for match in re.finditer(pattern, text, re.DOTALL):
            tag = match.group(1)
            body = match.group(2).strip()

            if tag == "action":
                # Standard: body is the full action JSON dict
                try:
                    parsed = json.loads(body)
                    actions.append(self._normalize_action(parsed))
                except json.JSONDecodeError:
                    # Try extracting a JSON object from within the body
                    # (handles cases where extra text like "json\n" precedes the JSON)
                    extracted = self._extract_json_object(body)
                    if extracted is not None:
                        actions.append(self._normalize_action(extracted))
                        logger.info("Extracted JSON object from action block (%d chars)", len(body))
                    else:
                        repaired = self._try_repair_json(body)
                        if repaired is not None:
                            actions.append(self._normalize_action(repaired))
                            logger.info("Repaired malformed action block (%d chars)", len(body))
                        else:
                            logger.warning("Failed to parse action block: %s", body[:200])

            elif tag in known_actions:
                # Agent used the action name as fence tag (e.g. ```create_batch_tickets)
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    logger.warning(
                        "Failed to parse ```%s block: %s", tag, body[:100],
                    )
                    continue

                if isinstance(parsed, dict):
                    if "action" not in parsed:
                        parsed["action"] = tag
                    actions.append(self._normalize_action(parsed))
                elif isinstance(parsed, list):
                    # Agent put the payload array directly — wrap into action dict
                    array_field = self._infer_array_field(tag)
                    wrapped = {"action": tag, array_field: parsed}
                    actions.append(self._normalize_action(wrapped))
                    logger.info(
                        "Wrapped ```%s array payload into action dict (field='%s', %d items)",
                        tag, array_field, len(parsed),
                    )
                else:
                    logger.warning(
                        "Unexpected JSON type in ```%s block: %s",
                        tag, type(parsed).__name__,
                    )

        if actions:
            return actions

        # Fallback: bare JSON objects with "action" or "tool" key
        bare_pattern = r'(?<!`)\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        for match in re.findall(bare_pattern, text):
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict) and ("action" in parsed or "tool" in parsed):
                    actions.append(self._normalize_action(parsed))
            except (json.JSONDecodeError, ValueError):
                continue

        if actions:
            logger.warning(
                "Agent %s: recovered %d bare JSON action(s) (no fenced blocks found)",
                self._agent_id, len(actions),
            )

        return actions

    def _infer_array_field(self, action_name: str) -> str:
        """Given an action name, find its primary array field.

        Falls back to 'items' if no array field is found.
        """
        if self._action_registry:
            action = self._action_registry.get(action_name)
            if action:
                for field in action.fields:
                    if field.type == "array":
                        return field.name
        return "items"

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        """Try to extract a JSON object from text that may have extra content.

        Handles common LLM patterns like:
        - ``json\\n{...}`` (nested code fence tag leaks)
        - ``Here is the action:\\n{...}`` (preamble text)
        - Multiple JSON objects (returns the first valid one with an "action" key)
        """
        # Find all potential JSON object starts
        idx = 0
        while idx < len(text):
            start = text.find("{", idx)
            if start == -1:
                break
            # Try progressively longer substrings from this brace
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, dict) and ("action" in parsed or "tool" in parsed):
                                return parsed
                        except json.JSONDecodeError:
                            pass
                        break
            idx = start + 1
        return None

    @staticmethod
    def _try_repair_json(raw: str) -> dict | None:
        """Attempt to repair malformed JSON from LLM output.

        The most common failure is unescaped double-quotes inside long string
        values (e.g. the "content" field of write_document).  Strategy:
        find each string-valued key, extract its value as raw text between
        the opening quote and the correct closing quote, then re-escape it.
        """
        # Strategy 1: find "content": "..." which is the usual culprit.
        # The value starts after `"content": "` and ends at the last `"}`
        # (or `", "` before the next key).
        for field in ("content", "task_description", "description",
                      "completion_summary", "progress_note", "paused_summary"):
            # Match: "field": "  ... (greedy to end)
            field_pattern = rf'"{field}"\s*:\s*"'
            m = re.search(field_pattern, raw)
            if not m:
                continue

            prefix = raw[:m.start()]
            value_start = m.end()  # first char after opening quote

            # Find the closing pattern: either `"}` (last field) or `", "` (next field)
            # Walk backwards from the end to find the real closing quote.
            # The last `"` before a `}` or `, "next_key":` is our closer.
            # Simple approach: try removing the field value, re-escaping, and re-inserting.
            suffix_patterns = [
                ('"}\n', 2),   # end of object with newline
                ('"}', 2),     # end of object
            ]

            # Find the last `"}` or `", "` in the raw string after value_start
            best_end = -1
            suffix_text = ""
            # Check for trailing `"}`
            rstrip = raw.rstrip()
            if rstrip.endswith('"}'):
                best_end = len(raw) - len(raw) - len(rstrip) + rstrip.rfind('"}', value_start)
                # Simpler: rfind from the end
                best_end = raw.rfind('"}', value_start)
                suffix_text = raw[best_end + 1:]  # the `}` and anything after
                raw_value = raw[value_start:best_end]
            elif rstrip.endswith('"}'):
                continue
            else:
                continue

            if best_end <= value_start:
                continue

            # Re-escape the value: replace unescaped quotes
            # First undo any already-escaped quotes to avoid double-escaping
            clean = raw_value.replace('\\"', '"')
            # Now escape all quotes
            clean = clean.replace('"', '\\"')
            # Rebuild
            rebuilt = prefix + f'"{field}": "' + clean + '"' + suffix_text

            try:
                return json.loads(rebuilt)
            except json.JSONDecodeError:
                continue

        return None

    def _normalize_action(self, raw: dict) -> dict:
        """Normalize common wrong key/value patterns in a parsed action."""
        result = dict(raw)

        # Remap top-level keys (e.g. "tool" -> "action")
        for wrong, right in self._ACTION_KEY_MAP.items():
            if wrong in result and right not in result:
                result[right] = result.pop(wrong)

        # Remap action names (e.g. "save_to_memory" -> "update_memory")
        action_name = result.get("action", "")
        if action_name in self._ACTION_NAME_MAP:
            result["action"] = self._ACTION_NAME_MAP[action_name]

        # Remap field names per action type
        action_type = result.get("action", "")
        field_map = self._FIELD_MAP.get(action_type, {})
        for wrong, right in field_map.items():
            if wrong in result and right not in result:
                result[right] = result.pop(wrong)

        # Normalize agent references: lowercase "to" field
        if "to" in result and isinstance(result["to"], str):
            result["to"] = result["to"].lower().replace(" ", "_")

        return result

    @staticmethod
    def _sanitize_for_user(text: str) -> str:
        """Strip action blocks and bare JSON from text before showing to user."""
        # Remove fenced code blocks containing JSON
        cleaned = re.sub(r'```\w*\s*\{.*?\}\s*```', '', text, flags=re.DOTALL)
        # Remove bare JSON objects
        cleaned = re.sub(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', cleaned)
        # Remove [Saving to memory: ...] annotations
        cleaned = re.sub(r'\[.*?(?:memory|saving|delegat).*?\]', '', cleaned, flags=re.IGNORECASE)
        # Collapse excessive whitespace
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    # ------------------------------------------------------------------
    # Validation & retry
    # ------------------------------------------------------------------

    async def _validate_result_actions(
        self,
        result_text: str,
        *,
        model: str,
        allowed_tools: str,
        working_dir: Any | None,
        timeout: int,
        mcp_config_path: Any | None,
        get_session_reminder_fn: Callable[[], str],
    ) -> str:
        """Check for unknown action types in the result; retry once with correction.

        If the agent emitted action blocks with unrecognized names (after
        normalization), send a correction prompt asking it to re-emit with
        valid action names.  Returns corrected text, or original if no
        unknown actions or retry fails.
        """
        actions = self._extract_actions(result_text)
        if not actions:
            return result_text

        known = self._get_known_actions()
        unknown = [a.get("action") for a in actions if a.get("action") not in known]
        if not unknown:
            return result_text

        logger.warning(
            "Agent %s used unknown action(s): %s -- requesting correction",
            self._agent_id, unknown,
        )

        valid_list = ", ".join(sorted(known))
        correction = (
            f"Your previous response contained unrecognized action(s): {', '.join(unknown)}. "
            f"These are NOT valid actions and will be ignored.\n"
            f"Valid actions are: {valid_list}.\n"
            f"Please re-emit your response using only valid action names from the list above."
        )

        session_id = self._session_store.get(self._agent_id) if self._session_store else None
        retry = await self._provider.invoke(
            prompt=correction,
            system_prompt=get_session_reminder_fn(),
            model=model,
            allowed_tools=allowed_tools,
            session_id=session_id,
            working_dir=working_dir,
            timeout=timeout,
            mcp_config_path=mcp_config_path,
        )

        # Record retry stats
        if self._session_store:
            await self._session_store.record_request(
                self._agent_id,
                duration_ms=retry.duration_ms or 0,
                is_error=retry.is_error,
                cost_usd=retry.cost_usd or 0.0,
                input_tokens=retry.input_tokens or 0,
                output_tokens=retry.output_tokens or 0,
            )

        if retry.is_error:
            logger.warning(
                "Agent %s action validation retry failed: %s",
                self._agent_id, retry.result_text[:200],
            )
            return result_text  # Keep original if retry fails

        logger.info("Agent %s action validation retry succeeded", self._agent_id)
        return retry.result_text

    # ------------------------------------------------------------------
    # Legacy backward-compatibility handler
    # ------------------------------------------------------------------

    async def _handle_common_actions(self, actions: list[dict]) -> None:
        """Process actions that modify state without producing messages.

        .. deprecated::
            Kept for backward compatibility with any code that still
            calls it directly.  All action handling now goes through
            the :class:`ActionRegistry` in ``_parse_response``.
        """
        if self._action_registry:
            from core.actions.base import ActionContext
            ctx = ActionContext()
            for action in actions:
                if action.get("action") == "update_document" and action.get("doc_id"):
                    ctx.edited_doc_ids.add(action["doc_id"])
            for action in actions:
                await self._action_registry.execute(
                    self._agent_ref, action, Message(sender="system", recipient=self._agent_id,
                                         type=MessageType.SYSTEM, content=""), ctx,
                )
            if ctx.kb_changed and self._broker:
                await self._broker.broadcast_event({
                    "event_type": "knowledge_updated", "data": {},
                })
            return

        # Legacy fallback (no registry)
        kb_changed = False
        edited_doc_ids: set[str] = set()
        for action in actions:
            if action.get("action") == "update_document" and action.get("doc_id"):
                edited_doc_ids.add(action["doc_id"])

        for action in actions:
            action_type = action.get("action")

            if action_type == "update_memory":
                await self._handle_memory_update(action)
                if self._agent_ref:
                    self._agent_ref._memory_updated = True

            elif action_type == "write_document":
                await self._handle_write_document(action)
                kb_changed = True

            elif action_type == "update_document":
                await self._handle_update_document(action)
                kb_changed = True

            elif action_type == "resolve_comments":
                await self._handle_resolve_comments(action, edited_doc_ids)

            elif action_type == "update_task":
                await self._handle_update_task(action)

            elif action_type == "start_conversation":
                await self._handle_start_conversation(action)

            elif action_type == "end_conversation":
                await self._handle_end_conversation(action)

        # Broadcast KB update so frontend auto-refreshes
        if kb_changed and self._broker:
            await self._broker.broadcast_event({
                "event_type": "knowledge_updated",
                "data": {},
            })

    # ------------------------------------------------------------------
    # Individual action handlers (legacy fallback)
    # ------------------------------------------------------------------

    async def _handle_memory_update(self, action: dict):
        if not self._memory_manager:
            return
        memory_type = action.get("memory_type", "")
        content = action.get("content", "")
        if not content:
            return
        if memory_type == "personality":
            await self._memory_manager.update_personality_memory(self._agent_id, content)
        elif memory_type == "project":
            await self._memory_manager.update_project_memory(self._agent_id, content)
        else:
            logger.warning("Unknown memory_type '%s' from %s", memory_type, self._agent_id)

    async def _handle_write_document(self, action: dict):
        if not self._knowledge_base:
            return
        title = action.get("title", "")
        category = action.get("category", "") or infer_doc_category(title)
        content = action.get("content", "")
        if not title or not content:
            logger.warning(
                "Agent %s write_document missing fields: title=%s, category=%s, content_len=%d",
                self._agent_id, bool(title), bool(category), len(content),
            )
            return
        if not category:
            category = "specs"  # default for documents without explicit category
        try:
            await self._knowledge_base.add_document(
                title=title, category=category,
                content=content, created_by=self._agent_id,
            )
        except ValueError as e:
            logger.warning("KB write_document error from %s: %s", self._agent_id, e)

    async def _handle_update_document(self, action: dict):
        if not self._knowledge_base:
            return
        doc_id = action.get("doc_id", "")
        content = action.get("content", "")
        if not doc_id or not content:
            return
        await self._knowledge_base.update_document(
            doc_id=doc_id, content=content, updated_by=self._agent_id,
        )

    async def _handle_resolve_comments(
        self, action: dict, edited_doc_ids: set[str] | None = None,
    ):
        """Agent resolves one or more comments on a document."""
        if not self._knowledge_base:
            return
        doc_id = action.get("doc_id", "")
        resolutions = action.get("resolutions", [])
        if not doc_id or not resolutions:
            logger.warning("Agent %s resolve_comments missing fields", self._agent_id)
            return

        edit_verified = bool(edited_doc_ids and doc_id in edited_doc_ids)
        if not edit_verified:
            logger.warning(
                "Agent %s resolved comments on %s WITHOUT editing the document",
                self._agent_id, doc_id,
            )

        resolved = await self._knowledge_base.resolve_comments(
            doc_id, resolutions, edit_verified=edit_verified,
        )
        if resolved and self._broker:
            logger.info(
                "Agent %s resolved %d comment(s) on %s (edit_verified=%s)",
                self._agent_id, len(resolved), doc_id, edit_verified,
            )
            await self._broker.broadcast_event({
                "event_type": "comments_updated",
                "data": {"doc_id": doc_id},
            })

        # Auto-complete the current task if all assigned comments are resolved
        if resolved and self.current_task_id and self._task_board:
            all_comments = await self._knowledge_base.get_comments(doc_id)
            remaining = [
                c for c in all_comments
                if c["status"] == "open" and c.get("assigned_to") == self._agent_id
            ]
            if not remaining:
                verified_str = "with verified edits" if edit_verified else "WITHOUT document edits (unverified)"
                await self._task_board.update_task(
                    self.current_task_id,
                    status="done",
                    _agent_id=self._agent_id,
                    completion_summary=(
                        f"Resolved {len(resolved)} comment(s) on \"{doc_id}\" {verified_str}."
                    ),
                )
                logger.info(
                    "Auto-completed task %s after resolving all comments",
                    self.current_task_id,
                )

    async def _handle_update_task(self, action: dict):
        if not self._task_board:
            return
        task_id = action.get("task_id")
        if not task_id:
            return
        updates = {"_agent_id": self._agent_id}
        for key in ("status", "assignee", "role", "priority", "reviewer",
                     "progress_note", "completion_summary", "review_output",
                     "paused_summary", "labels", "outcome"):
            if key in action:
                updates[key] = action[key]
        await self._task_board.update_task(task_id, **updates)

    async def _handle_start_conversation(self, action: dict):
        """Agent requests a direct conversation with the user."""
        if not self._conversation_manager:
            logger.warning("No conversation_manager for %s", self._agent_id)
            return
        goals = action.get("goals", [])
        title = action.get("title", "Conversation")
        conv = await self._conversation_manager.start(self._agent_id, goals, title)

        # Send CONVERSATION message to self with the conversation context
        if self._broker:
            msg = Message(
                sender="system",
                recipient=self._agent_id,
                type=MessageType.CONVERSATION,
                content=f"Conversation started: {title}. Goals: {', '.join(goals)}",
                metadata={"conversation_id": conv["id"]},
            )
            await self._broker.deliver(msg)

    async def _handle_end_conversation(self, action: dict):
        """Agent ends a direct conversation with the user."""
        if not self._conversation_manager:
            return
        summary = action.get("summary", "")
        conv = await self._conversation_manager.close_by_agent(self._agent_id)
        if not conv:
            return

        # Save summary to knowledge base
        if summary and self._knowledge_base:
            try:
                await self._knowledge_base.add_document(
                    title=conv.get("title", "Conversation Summary"),
                    category="specs",
                    content=summary,
                    created_by=self._agent_id,
                )
                if self._broker:
                    await self._broker.broadcast_event({
                        "event_type": "knowledge_updated",
                        "data": {},
                    })
            except Exception:
                logger.exception("Failed to save conversation summary to KB")

        # Send summary to the user-facing agent so they know what was discussed
        if summary and self._broker:
            summary_msg = Message(
                sender=self._agent_id,
                recipient=self._user_facing_agent,
                type=MessageType.RESPONSE,
                content=(
                    f"Conversation completed: '{conv.get('title', 'Conversation')}'\n\n"
                    f"Summary:\n{summary}"
                ),
                metadata={"conversation_summary": True},
            )
            await self._broker.deliver(summary_msg)
