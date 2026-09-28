"""配置以项目根目录为基准，不依赖启动位置。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout: int = 40
    db_path: Path = ROOT / "data/chat.db"

    @classmethod
    def load(cls) -> "Settings":
        values = {**dotenv_values(ROOT / ".env"), **os.environ}
        return cls(
            api_key=values.get("DEEPSEEK_API_KEY") or "",
            base_url=values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            model=values.get("DEEPSEEK_MODEL") or "deepseek-chat",
        )


# 旧模块导入兼容；新代码按需读取 Settings。
DEEPSEEK_API_KEY = Settings.load().api_key
DEEPSEEK_BASE_URL = Settings.load().base_url
DEEPSEEK_MODEL = Settings.load().model
