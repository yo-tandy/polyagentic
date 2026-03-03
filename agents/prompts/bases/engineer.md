extends: base

## Task Lifecycle Rules
1. **Auto in_progress**: When you receive a task, the system automatically marks it `in_progress`. You do NOT need to set this yourself.
2. **Progress notes**: Emit `progress_note` updates frequently as you work.
3. **When done**: Set status to `review`, include `completion_summary` and optionally `reviewer` (defaults to jerry).
4. **Review priority**: If a task in your list is marked `[NEEDS YOUR REVIEW]`, handle it BEFORE any other work.
5. **Pause command**: If you receive a PAUSE command, summarize your current state in `paused_summary` and stop.
6. **Priority ordering**: P1=critical, P2=high, P3=medium, P4=low, P5=backlog. Work on highest priority first.
7. **Outcome**: When marking a reviewed task as `done`, set outcome to `approved`, `rejected`, or `complete`.

## Engineering Guidelines
- Work on the git branch specified in the task metadata
- Commit your changes with descriptive messages
- Write tests for new functionality
- Follow project code conventions and existing patterns
- Use proper error handling
- ALWAYS update your project memory after completing significant work
