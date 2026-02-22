from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    success: bool
    message: str
    conflicts: list[str]


class GitManager:
    def __init__(self, workspace: Path, main_branch: str = "main"):
        self.workspace = workspace
        self.main_branch = main_branch
        self._lock = asyncio.Lock()

    async def _run_git(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd or self.workspace),
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )

    async def init_or_validate(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        git_dir = self.workspace / ".git"
        if not git_dir.exists():
            await self._run_git("init")
            await self._run_git("checkout", "-b", self.main_branch)
            # Create initial commit so branches can be created
            readme = self.workspace / "README.md"
            readme.write_text("# Project\n\nInitialized by Polyagentic.\n")
            await self._run_git("add", "README.md")
            await self._run_git("commit", "-m", "Initial commit")
            logger.info("Initialized git repo at %s", self.workspace)
        else:
            logger.info("Git repo already exists at %s", self.workspace)

        # Ensure integration branch exists
        await self.create_branch("dev/integration", from_branch=self.main_branch)

    async def _ensure_branch(self, branch_name: str, from_branch: str | None = None) -> bool:
        """Create a branch if it doesn't exist. Must be called with _lock held."""
        rc, out, err = await self._run_git("branch", "--list", branch_name)
        if out.strip():
            return True
        base = from_branch or self.main_branch
        rc, out, err = await self._run_git("branch", branch_name, base)
        if rc != 0:
            logger.error("Failed to create branch %s: %s", branch_name, err)
            return False
        logger.info("Created branch %s from %s", branch_name, base)
        return True

    async def create_branch(self, branch_name: str, from_branch: str | None = None) -> bool:
        async with self._lock:
            return await self._ensure_branch(branch_name, from_branch)

    async def create_worktree(self, agent_id: str, branch: str, worktrees_dir: Path) -> Path:
        async with self._lock:
            worktree_path = worktrees_dir / agent_id
            if worktree_path.exists():
                return worktree_path

            await self._ensure_branch(branch)

            rc, out, err = await self._run_git(
                "worktree", "add", str(worktree_path), branch
            )
            if rc != 0:
                if "already checked out" in err or "already exists" in err:
                    logger.info("Worktree for %s already exists", agent_id)
                    return worktree_path
                logger.error("Failed to create worktree for %s: %s", agent_id, err)
                raise RuntimeError(f"Failed to create worktree: {err}")

            logger.info("Created worktree at %s for branch %s", worktree_path, branch)
            return worktree_path

    async def checkout(self, branch_name: str, cwd: Path | None = None):
        async with self._lock:
            rc, out, err = await self._run_git("checkout", branch_name, cwd=cwd)
            if rc != 0:
                logger.error("Failed to checkout %s: %s", branch_name, err)

    async def merge(self, source: str, target: str) -> MergeResult:
        async with self._lock:
            await self._run_git("checkout", target)
            rc, out, err = await self._run_git("merge", source, "--no-ff",
                                                "-m", f"Merge {source} into {target}")
            if rc == 0:
                return MergeResult(success=True, message=out, conflicts=[])

            # Check for conflicts
            rc2, status_out, _ = await self._run_git("diff", "--name-only", "--diff-filter=U")
            conflicts = [f.strip() for f in status_out.split("\n") if f.strip()]

            if conflicts:
                # Abort the merge so we don't leave dirty state
                await self._run_git("merge", "--abort")
                return MergeResult(
                    success=False,
                    message=f"Merge conflicts in {len(conflicts)} file(s)",
                    conflicts=conflicts,
                )

            return MergeResult(success=False, message=err, conflicts=[])

    async def get_branches(self) -> list[str]:
        rc, out, err = await self._run_git("branch", "--list")
        if rc != 0:
            return []
        return [b.strip().lstrip("*+ ") for b in out.split("\n") if b.strip()]

    async def get_log(self, branch: str | None = None, limit: int = 20) -> list[dict]:
        args = ["log", f"--max-count={limit}",
                "--format=%H|%h|%an|%s|%ci"]
        if branch:
            args.append(branch)

        rc, out, err = await self._run_git(*args)
        if rc != 0:
            return []

        entries = []
        for line in out.split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) == 5:
                entries.append({
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "author": parts[2],
                    "message": parts[3],
                    "date": parts[4],
                })
        return entries

    async def get_status(self) -> dict:
        rc, out, err = await self._run_git("status", "--porcelain")
        rc2, branch_out, _ = await self._run_git("branch", "--show-current")
        return {
            "current_branch": branch_out.strip(),
            "changes": out.split("\n") if out.strip() else [],
        }

