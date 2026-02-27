from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from config import CLAUDE_ALLOWED_TOOLS_DEV

logger = logging.getLogger(__name__)

CUSTOM_PROMPT_TEMPLATE = """# {role}

{system_prompt}

## Communication Protocol
When you need to delegate work to a peer or request help:

```action
{{"action": "delegate", "to": "<agent_id>", "task_title": "<title>", "task_description": "<description>", "labels": ["<optional-label>"]}}
```

When reporting results back to whoever assigned you a task:

```action
{{"action": "respond_to_user", "message": "<your detailed response>", "suggested_answers": ["<option1>", "<option2>"]}}
```
Use `suggested_answers` (1-3 short options) when asking the user a question or requesting a decision.

### Update your task progress
As you work and when you finish:

```action
{{"action": "update_task", "task_id": "<task_id>", "status": "<review|done|paused>", "progress_note": "<brief update>", "completion_summary": "<when done: summary of all changes>", "reviewer": "<agent_id to review, default: jerry>", "paused_summary": "<when pausing: snapshot of current state>", "labels": ["<optional>"], "outcome": "<approved|rejected|complete>"}}
```

### Task Lifecycle Rules
1. **Auto in_progress**: When you receive a task, the system automatically marks it `in_progress`. You do NOT need to set this yourself.
2. **Progress notes**: Emit `progress_note` updates frequently as you work.
3. **When done**: Set status to `review`, include `completion_summary` and optionally `reviewer` (defaults to jerry).
4. **Review priority**: If a task in your list is marked `[NEEDS YOUR REVIEW]`, handle it BEFORE any other work.
5. **Pause command**: If you receive a PAUSE command, summarize your current state in `paused_summary` and stop.
6. **Priority**: P1=critical, P2=high, P3=medium, P4=low, P5=backlog. Work on highest priority first.
7. **Outcome**: When marking a reviewed task as `done`, set outcome to `approved`, `rejected`, or `complete`.

### MANDATORY: Update project memory after EVERY task
Before setting status to `review`, you MUST emit an `update_memory` action.
Do NOT just append — re-summarize and restructure to stay concise.
Include: what you worked on, key decisions, current state, blockers.

```action
{{"action": "update_memory", "memory_type": "project", "content": "<updated project notes in markdown>"}}
```

```action
{{"action": "update_memory", "memory_type": "personality", "content": "<updated skills/preferences notes in markdown>"}}
```

### Review Feedback
When you receive REVIEW FEEDBACK from a reviewer, update your personality memory with lessons learned.

### Write a project document
To contribute to the shared knowledge base:

```action
{{"action": "write_document", "title": "<document title>", "category": "<specs|design|architecture|planning|history>", "content": "<document content in markdown>"}}
```

## Your Team
{team_roster}

## Your Memory
{memory}

## Guidelines
- Focus on your area of expertise
- If a task falls outside your expertise, redirect it to an appropriate team member
- Provide clear, actionable results
- Work on the git branch specified in the task metadata
- Commit your changes with descriptive messages
- ALWAYS update your project memory after completing significant work
- Write design or architecture documents when making important technical decisions
"""


def create_custom_agent(
    name: str,
    role: str,
    system_prompt: str,
    model: str,
    allowed_tools: str,
    messages_dir: Path,
    working_dir: Path,
    team_roster: str = "",
) -> CustomAgent:
    full_prompt = CUSTOM_PROMPT_TEMPLATE.format(
        role=role,
        system_prompt=system_prompt,
        team_roster=team_roster,
        memory="No memory recorded yet.",
    )
    return CustomAgent(
        agent_id=name,
        name=name.replace("_", " ").title(),
        role=role,
        system_prompt=full_prompt,
        model=model,
        allowed_tools=allowed_tools,
        messages_dir=messages_dir,
        working_dir=working_dir,
    )


class CustomAgent(Agent):
    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        messages = []
        actions = self._extract_actions(result_text)

        # Handle common actions (memory, KB)
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
                messages.append(Message(
                    sender=self.agent_id,
                    recipient=action.get("to", ""),
                    type=MessageType.REDIRECT,
                    content=action.get("task_description", ""),
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                    metadata={"task_title": action.get("task_title", "")},
                ))

            elif action_type == "respond_to_user":
                suggested = action.get("suggested_answers", [])
                meta = {}
                if suggested:
                    meta["suggested_answers"] = suggested[:3]
                messages.append(Message(
                    sender=self.agent_id,
                    recipient=original_msg.sender,
                    type=MessageType.RESPONSE,
                    content=action.get("message", result_text),
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                    metadata=meta if meta else None,
                ))

            # update_memory, write_document handled by _handle_common_actions

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
