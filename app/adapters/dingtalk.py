"""钉钉适配器。

钉钉主窗口标题可能是 "会话名 - 钉钉"，也可能是乱码或纯 "钉钉"。乱码标题
一律丢弃，改由聊天区域上方的标题栏识别联系人。读写链路都在
DesktopIMAdapter 里。
"""

from __future__ import annotations

from .desktop_base import DesktopIMAdapter
from .windows import dingtalk_windows


class DingTalkAdapter(DesktopIMAdapter):
    platform = "钉钉"
    generic_titles = frozenset({"钉钉", "DingTalk", "消息", "工作台", "通讯录"})
    title_suffixes = (" - 钉钉", " - DingTalk")

    def _windows(self) -> list[dict]:
        return dingtalk_windows()
