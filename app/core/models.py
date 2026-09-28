"""跨模块只传递普通数据，不传递 UIA/Qt 控件。"""

from dataclasses import dataclass, field
from hashlib import sha256


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    content: str
    source_id: str = ""
    message_type: str = "text"
    timestamp: str = ""


@dataclass(frozen=True)
class ChatSnapshot:
    platform: str
    contact_name: str
    window_handle: int
    messages: tuple[ChatMessage, ...]
    window_title: str = ""
    # 读取阶段状态；默认值保证微信等已验证链路不受影响。
    status: str = "messages_read"
    # True 表示消息来自 OCR 等不确定来源，必须人工核验，不得自动回复。
    needs_review: bool = False
    # 给界面显示的补充说明，例如失败发生在哪一步。
    detail: str = ""
    # 用户在界面上逐条核对过待核验消息后置为 True；只影响人工链路。
    reviewed: bool = False
    # 会话名是不是从客户端窗口里读出来的。False 表示它来自界面左栏的选择，
    # 系统并没有核实客户端此刻真的停在这个聊天上——QQ NT 的标题恒为"QQ"，
    # 无障碍树里也没有会话标题，只能这样。发送确认弹窗必须把这点说清楚。
    contact_verified: bool = True

    @property
    def readable(self) -> bool:
        """消息是否可以进入模型上下文。

        结构化读取（needs_review=False）天然可信；OCR 等不确定来源必须由
        用户在界面上确认过（reviewed=True）才放行，且永远不进自动链路。
        """
        if self.status != "messages_read" or not self.messages:
            return False
        return not self.needs_review or self.reviewed

    @property
    def auto_safe(self) -> bool:
        """是否允许进入自动回复链路。待核验来源一律不允许，即便人工确认过。"""
        return self.readable and not self.needs_review

    @property
    def fingerprint(self) -> str:
        # 联系人、窗口和可见消息共同标识一次读取。
        value = repr(
            (
                self.platform,
                self.contact_name,
                self.window_handle,
                [(m.source_id, m.sender, m.content) for m in self.messages],
            )
        )
        return sha256(value.encode()).hexdigest()


@dataclass
class ReplyBatch:
    contact_id: int
    incoming: str
    candidates: list[dict]
    risk: str
    reason: str
    scene: str
    should_reply: bool = True
    snapshot: ChatSnapshot | None = None
    draft_ids: list[int] = field(default_factory=list)
