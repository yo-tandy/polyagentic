"""Request history repository — insert + time-windowed aggregation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import func, select

from db.models.request_history import RequestHistory
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class RequestHistoryRepository(BaseRepository):

    async def record(
        self,
        project_id: str,
        agent_id: str,
        duration_ms: int = 0,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        is_error: bool = False,
    ) -> None:
        """Insert a single request history row."""
        async with self._session() as session:
            entry = RequestHistory(
                project_id=project_id,
                agent_id=agent_id,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                is_error=is_error,
            )
            session.add(entry)
            await session.commit()

    async def get_project_stats(
        self,
        project_id: str,
        since: datetime | None = None,
    ) -> dict:
        """Aggregate stats for a project, optionally since a cutoff time.

        Returns dict with: requests, cost_usd, errors, input_tokens,
        output_tokens, last_activity.
        """
        async with self._session() as session:
            filters = [RequestHistory.project_id == project_id]
            if since:
                filters.append(RequestHistory.timestamp >= since)

            # SQLite stores booleans as 0/1, so sum(is_error) works
            stmt = select(
                func.count(RequestHistory.id).label("requests"),
                func.coalesce(func.sum(RequestHistory.cost_usd), 0.0).label("cost_usd"),
                func.coalesce(func.sum(RequestHistory.is_error), 0).label("errors"),
                func.coalesce(func.sum(RequestHistory.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestHistory.output_tokens), 0).label("output_tokens"),
                func.max(RequestHistory.timestamp).label("last_activity"),
            ).where(*filters)

            result = await session.execute(stmt)
            row = result.one()
            return {
                "requests": row.requests or 0,
                "cost_usd": round(float(row.cost_usd or 0), 4),
                "errors": row.errors or 0,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "last_activity": row.last_activity.isoformat() if row.last_activity else None,
            }

    async def get_all_projects_stats(
        self,
        project_ids: list[str] | None = None,
    ) -> dict[str, dict]:
        """Get time-windowed stats for multiple projects.

        Returns: {project_id: {"hour": {...}, "day": {...}, "overall": {...}}}
        """
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        results: dict[str, dict] = {}

        # Get unique project IDs
        async with self._session() as session:
            if project_ids:
                ids = project_ids
            else:
                stmt = select(RequestHistory.project_id).distinct()
                result = await session.execute(stmt)
                ids = [r[0] for r in result.all()]

        for pid in ids:
            hour_stats = await self.get_project_stats(pid, since=one_hour_ago)
            day_stats = await self.get_project_stats(pid, since=one_day_ago)
            overall_stats = await self.get_project_stats(pid)
            results[pid] = {
                "hour": hour_stats,
                "day": day_stats,
                "overall": overall_stats,
            }

        return results

    async def get_agent_count(self, project_id: str) -> int:
        """Get number of distinct agents that have made requests for a project."""
        async with self._session() as session:
            stmt = select(
                func.count(func.distinct(RequestHistory.agent_id))
            ).where(RequestHistory.project_id == project_id)
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def cleanup_old(self, days: int = 30) -> int:
        """Delete entries older than N days. Returns count deleted."""
        from sqlalchemy import delete
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with self._session() as session:
            stmt = delete(RequestHistory).where(
                RequestHistory.timestamp < cutoff,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0
