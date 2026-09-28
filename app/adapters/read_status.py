"""会话读取的显式状态模型。

界面必须区分"找到窗口"和"读到消息"。任何一步失败都要有确定的状态值，
不允许用空消息冒充读取成功。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import ChatMessage


class ReadStatus:
    """读取链路上的阶段状态；字符串常量便于落库和日志。"""

    WINDOW_NOT_FOUND = "window_not_found"
    WINDOW_FOUND = "window_found"
    CONTACT_DETECTED = "contact_detected"
    MESSAGE_REGION_NOT_FOUND = "message_region_not_found"
    MESSAGES_READ = "messages_read"
    NO_MESSAGES = "no_messages"
    UNSUPPORTED_CLIENT_VERSION = "unsupported_client_version"
    UIA_ACCESS_DENIED = "uia_access_denied"
    OCR_UNAVAILABLE = "ocr_unavailable"
    UNCERTAIN = "uncertain"


# 只有这个状态代表消息内容可信，可以进入模型上下文。
TRUSTED = {ReadStatus.MESSAGES_READ}

# 这些状态说明"连上了但没读到可信消息"，界面要如实说明，不能显示读取完成。
NOT_READABLE = {
    ReadStatus.WINDOW_NOT_FOUND,
    ReadStatus.WINDOW_FOUND,
    ReadStatus.CONTACT_DETECTED,
    ReadStatus.MESSAGE_REGION_NOT_FOUND,
    ReadStatus.NO_MESSAGES,
    ReadStatus.UNSUPPORTED_CLIENT_VERSION,
    ReadStatus.UIA_ACCESS_DENIED,
    ReadStatus.OCR_UNAVAILABLE,
    ReadStatus.UNCERTAIN,
}

_LABELS = {
    ReadStatus.WINDOW_NOT_FOUND: "未找到{platform}主窗口",
    ReadStatus.WINDOW_FOUND: "已找到{platform}窗口，但还未识别当前会话",
    ReadStatus.CONTACT_DETECTED: "已识别联系人，但未找到聊天消息区域",
    ReadStatus.MESSAGE_REGION_NOT_FOUND: "已找到{platform}窗口，但未找到聊天消息区域",
    ReadStatus.MESSAGES_READ: "已读取消息",
    ReadStatus.NO_MESSAGES: "已定位聊天区域，但当前没有可读取的消息",
    ReadStatus.UNSUPPORTED_CLIENT_VERSION: "当前{platform}版本暂不支持可靠读取",
    ReadStatus.UIA_ACCESS_DENIED: "无法访问{platform}的界面自动化接口，可能需要以相同权限运行",
    ReadStatus.OCR_UNAVAILABLE: "界面接口读不到消息，且本机没有可用的 OCR 引擎",
    ReadStatus.UNCERTAIN: "读取结果不稳定，已丢弃以避免误读界面文字",
}


def describe(status: str, platform: str = "该平台") -> str:
    """把状态翻译成给用户看的一句话。"""
    template = _LABELS.get(status, "未知状态：{status}")
    return template.format(platform=platform, status=status)


@dataclass(frozen=True)
class ReadResult:
    """一次只读读取的完整结果。

    messages 只有在 status 为 MESSAGES_READ 且 needs_review 为 False 时
    才允许进入模型上下文。needs_review 为 True 表示数据来自 OCR 等
    不确定来源，必须由人工核验。
    """

    status: str
    messages: tuple[ChatMessage, ...] = ()
    contact: str = ""
    needs_review: bool = False
    detail: str = ""
    region: tuple[int, int, int, int] | None = None
    diagnostics: dict = field(default_factory=dict)

    @property
    def trusted(self) -> bool:
        return self.status in TRUSTED and not self.needs_review and bool(self.messages)

    def label(self, platform: str = "该平台") -> str:
        if self.status == ReadStatus.MESSAGES_READ:
            if self.needs_review:
                return f"已识别联系人，但消息读取结果待核验（{len(self.messages)} 条）"
            return f"已读取 {len(self.messages)} 条消息"
        return describe(self.status, platform)
