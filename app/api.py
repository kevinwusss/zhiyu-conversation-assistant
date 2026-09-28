"""进程内组合入口，不启动网络服务。"""

from app.config import Settings
from app.database import Database
from app.automation.safety import SafetyGate
from app.core.engine import ReplyEngine
from app.llm import DeepSeekProvider


def create_engine(settings: Settings, gate: SafetyGate | None = None) -> ReplyEngine:
    return ReplyEngine(Database(settings.db_path), DeepSeekProvider(settings), gate or SafetyGate())
