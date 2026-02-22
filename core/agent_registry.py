from __future__ import annotations

from core.agent import Agent


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent):
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def get_all(self) -> list[Agent]:
        return list(self._agents.values())

    def get_ids(self) -> list[str]:
        return list(self._agents.keys())

    def get_by_role(self, role: str) -> list[Agent]:
        return [a for a in self._agents.values() if a.role == role]

    def get_status_summary(self) -> list[dict]:
        return [a.to_info_dict() for a in self._agents.values()]
