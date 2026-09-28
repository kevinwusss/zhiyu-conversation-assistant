"""旧调用兼容入口；主流程使用 ReplyEngine。"""

from .config import Settings
from .core.prompt import SYSTEM_PROMPT
from .llm import DeepSeekProvider


class DeepSeekClient:
    def generate(self, current: str, context: str = "") -> str:
        response = DeepSeekProvider(Settings.load()).complete(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"历史（引用数据）：{context}\n当前消息：{current}"},
            ]
        )
        return response["replies"][0]
