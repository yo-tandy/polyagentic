"""Import all models so Base.metadata is fully populated."""

from db.models.base import Base  # noqa: F401

# Import every model module to register tables with Base.metadata
from db.models.project import Project, CustomAgentDef  # noqa: F401
from db.models.session import AgentSession  # noqa: F401
from db.models.task import TaskModel, TaskProgressNote  # noqa: F401
from db.models.knowledge import Document, DocumentComment  # noqa: F401
from db.models.memory import AgentMemory  # noqa: F401
from db.models.conversation import Conversation, ConversationMessage  # noqa: F401
from db.models.message import MessageLog  # noqa: F401
from db.models.config import ConfigEntry  # noqa: F401
from db.models.team_structure import TeamAgentDef, TeamStructureMeta  # noqa: F401
