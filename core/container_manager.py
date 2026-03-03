from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_NAME = "polyagentic-agent:latest"
CONTAINER_PREFIX = "polyagentic-"


class ContainerManager:
    """Manages Docker containers for worker agents."""

    def __init__(self, workspace_dir: Path, worktrees_dir: Path, messages_dir: Path):
        self.workspace_dir = workspace_dir
        self.worktrees_dir = worktrees_dir
        self.messages_dir = messages_dir
        self._containers: dict[str, str] = {}  # agent_id -> container_name

    async def ensure_image(self) -> bool:
        """Build the agent Docker image if it doesn't exist. Returns True if ready."""
        # Check if image exists
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", IMAGE_NAME,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode == 0:
            logger.info("Docker image %s already exists", IMAGE_NAME)
            return True

        # Build image
        dockerfile = Path(__file__).parent.parent / "Dockerfile.agent"
        if not dockerfile.exists():
            logger.error("Dockerfile.agent not found at %s", dockerfile)
            return False

        logger.info("Building Docker image %s...", IMAGE_NAME)
        proc = await asyncio.create_subprocess_exec(
            "docker", "build", "-t", IMAGE_NAME, "-f", str(dockerfile), ".",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(dockerfile.parent),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

        if proc.returncode != 0:
            logger.error(
                "Failed to build Docker image: %s",
                stderr.decode(errors="replace")[:500],
            )
            return False

        logger.info("Docker image %s built successfully", IMAGE_NAME)
        return True

    async def create_container(
        self, agent_id: str, worktree_path: Path | None = None
    ) -> str:
        """Start a container for an agent. Returns the container name."""
        container_name = f"{CONTAINER_PREFIX}{agent_id}"

        # Check if container already exists
        proc = await asyncio.create_subprocess_exec(
            "docker", "container", "inspect", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode == 0:
            # Container exists — check if running
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{.State.Running}}", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if stdout.decode().strip() == "true":
                logger.info("Container %s already running", container_name)
                self._containers[agent_id] = container_name
                return container_name
            else:
                # Start stopped container
                await asyncio.create_subprocess_exec(
                    "docker", "start", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                logger.info("Started existing container %s", container_name)
                self._containers[agent_id] = container_name
                return container_name

        # Create new container
        work_mount = worktree_path or self.workspace_dir
        msg_dir = self.messages_dir / agent_id
        msg_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-v", f"{work_mount.resolve()}:/workspace",
            "-v", f"{msg_dir.resolve()}:/messages",
            "-e", f"ANTHROPIC_API_KEY={os.environ.get('ANTHROPIC_API_KEY', '')}",
            "-e", f"AGENT_ID={agent_id}",
        ]

        # Pass GitHub token if available
        gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if gh_token:
            cmd += ["-e", f"GH_TOKEN={gh_token}"]

        cmd.append(IMAGE_NAME)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"Failed to create container {container_name}: {err}")

        container_id = stdout.decode().strip()[:12]
        logger.info("Created container %s (%s) for agent %s", container_name, container_id, agent_id)
        self._containers[agent_id] = container_name
        return container_name

    async def stop_container(self, agent_id: str):
        """Stop and remove a container."""
        container_name = self._containers.pop(agent_id, f"{CONTAINER_PREFIX}{agent_id}")
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        logger.info("Stopped container %s", container_name)

    async def stop_all(self):
        """Stop all managed containers."""
        agent_ids = list(self._containers.keys())
        for agent_id in agent_ids:
            await self.stop_container(agent_id)
        logger.info("Stopped all %d containers", len(agent_ids))

    async def health_check(self, agent_id: str) -> bool:
        """Check if a container is running."""
        container_name = self._containers.get(agent_id, f"{CONTAINER_PREFIX}{agent_id}")
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Running}}", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() == "true"

    def get_container_name(self, agent_id: str) -> str | None:
        """Get the container name for an agent."""
        return self._containers.get(agent_id)
