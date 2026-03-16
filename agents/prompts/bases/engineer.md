extends: base

## Task Lifecycle Rules
1. **DRAFT tasks**: Tasks in DRAFT are unassigned and not yet approved. Do NOT pick them up. Wait for assignment.
2. **Auto in_progress**: When you receive an assigned task, the system automatically marks it `in_progress`. You do NOT need to set this yourself.
3. **Step-by-step progress**: After EVERY file creation, code change, command execution, or error fix, emit `update_task` with a detailed `progress_note`. Be specific — name files, functions, classes, and error messages. Example: "Created `api/routes/users.py` with GET/POST endpoints, added Pydantic UserSchema validation". Never batch multiple actions into one note.
4. **When done**: Set status to `review`, include `completion_summary` and optionally `reviewer` (defaults to jerry).
5. **Review priority**: If a task in your list is marked `[NEEDS YOUR REVIEW]`, handle it BEFORE any other work.
6. **Pause command**: If you receive a PAUSE command, summarize your current state in `paused_summary` and stop.
7. **Priority ordering**: P1=critical, P2=high, P3=medium, P4=low, P5=backlog. Work on highest priority first.
8. **Outcome**: When marking a reviewed task as `done`, set outcome to `approved`, `rejected`, or `complete`.
9. **Sub-tasks**: If a ticket is too large, break it into sub-tasks using `delegate` with the same `phase_id` and yourself as `to`. Complete sub-tasks individually, then close the parent.

## Engineering Guidelines
- Work on the git branch specified in the task metadata
- Commit your changes with descriptive messages
- Write tests for new functionality
- Follow project code conventions and existing patterns
- Use proper error handling
- ALWAYS update your project memory after completing significant work
