"""平台适配器注册表。新增平台只需实现 BaseChatAdapter 并在这里登记。"""

from contextlib import contextmanager

from .base import BaseChatAdapter
from .wechat import WeChatAdapter
from .dingtalk import DingTalkAdapter
from .qq import QQAdapter

# 顺序即界面上滑块的顺序：微信 / QQ / 钉钉。
ADAPTERS = {"微信": WeChatAdapter, "QQ": QQAdapter, "钉钉": DingTalkAdapter}

PLATFORMS = tuple(ADAPTERS)


def create_adapter(name: str) -> BaseChatAdapter:
    if name not in ADAPTERS:
        raise ValueError(f"平台 {name} 尚未接入")
    # QQ NT / DingTalk Qt or CEF builds may paint message text outside UIA.
    # Enable the bounded chat-region OCR fallback for desktop IM adapters.
    if name in {"QQ", "钉钉"}:
        return ADAPTERS[name](allow_ocr=True)
    return ADAPTERS[name]()


@contextmanager
def allow_restore(adapter):
    """在这个上下文里允许把最小化 / 托盘隐藏的窗口还原出来。

    只给"用户刚点了按钮"的路径用。后台监听轮询若也能还原窗口，客户端会每
    隔几秒自己弹到屏幕上，属于用户完全没预期的骚扰行为。
    """
    previous = getattr(adapter, "auto_restore", False)
    try:
        adapter.auto_restore = True
    except Exception:
        yield adapter
        return
    try:
        yield adapter
    finally:
        adapter.auto_restore = previous
