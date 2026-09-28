"""QQ 适配器。

QQ 主窗口标题通常只有 "QQ" / "TIM" 或为空，不能当会话名用，联系人主要
依靠聊天区域上方的标题栏识别。读写链路都在 DesktopIMAdapter 里。
"""

from __future__ import annotations

from .desktop_base import DesktopIMAdapter
from .windows import qq_windows


class QQAdapter(DesktopIMAdapter):
    platform = "QQ"
    generic_titles = frozenset({"QQ", "TIM", "腾讯QQ", "QQ NT", "消息", "聊天"})
    title_suffixes = (" - QQ", " - TIM")

    def _windows(self) -> list[dict]:
        return qq_windows()
