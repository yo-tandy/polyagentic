"""Create a new team member agent (DevManager variant of recruit_agent)."""

from core.actions.recruit_agent import RecruitAgent


class CreateAgent(RecruitAgent):
    """Same logic as RecruitAgent, but with different name and permissions."""

    name = "create_agent"
    description = "Create a new team member agent."
