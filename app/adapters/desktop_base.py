"""QQ / 钉钉共用的桌面 IM 适配器基类。

两个平台的差异只有进程名、通用标题和标题后缀，读取与写入逻辑完全一致，
放在这里避免两份代码各自漂移。

写入（填入 / 发送）的前置条件很严格，顺序不能变：
窗口状态就绪 → 快照平台与句柄一致 → 定位到输入框 → 焦点确认在输入框内。
任何一步不通过都会在按下按键之前抛错，绝不盲发。
"""

from __future__ import annotations

from typing import Callable

from app.core.models import ChatMessage, ChatSnapshot
from .base import AdapterError
from .desktop_uia import control_tree_summary, read_conversation, snapshot_tree
from .read_status import ReadStatus, describe
from .windows import (
    WINDOW_READY,
    pick_main_window,
    restore_window,
    state_hint,
    window_state,
)

# Windows 在编码不匹配时会把中文标题变成 U+FFFD 替换字符，不能当联系人用。
_REPLACEMENT = "�"

# 可识别字符范围：日文假名、CJK 汉字、韩文谚文、兼容汉字。
_CJK_RANGES = (
    (0x3040, 0x30FF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
)


def _readable_char(ch: str) -> bool:
    if ch.isascii() and ch.isalnum():
        return True
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def is_garbled(title: str) -> bool:
    """标题是否乱码到不可用。

    两种情况判为乱码：出现 U+FFFD 替换字符；或整串没有任何可识别的
    ASCII 字母数字和东亚文字（典型的 GBK / UTF-8 互相误解码结果）。
    """
    value = (title or "").strip()
    if not value:
        return True
    if _REPLACEMENT in value:
        return True
    return not any(_readable_char(ch) for ch in value)


class DesktopIMAdapter:
    """只读桌面 IM 适配器；子类只需声明平台常量并提供窗口枚举函数。"""

    platform = ""
    generic_titles: frozenset[str] = frozenset()
    title_suffixes: tuple[str, ...] = ()
    # 写入（填入 / 发送）能力总开关，见 _require_write()。
    write_supported = True

    def __init__(self, allow_ocr: bool = False, auto_restore: bool = False):
        self._seen: dict[str, set[str]] = {}
        self._allow_ocr = allow_ocr
        # 手工点击"读取当前会话"时允许把托盘/最小化的窗口还原出来；
        # 后台监听轮询不允许，否则窗口会每隔几秒自己弹出来打断用户。
        self.auto_restore = auto_restore
        self._last_result = None
        # 界面左栏当前选中的联系人名。只在客户端窗口里读不出会话名时才用，
        # 且用了就会把快照标成 contact_verified=False。
        self.contact_hint = ""

    def set_contact_hint(self, name: str) -> None:
        self.contact_hint = (name or "").strip()

    # ---- 子类提供 -------------------------------------------------

    def _windows(self) -> list[dict]:
        raise NotImplementedError

    # ---- 窗口与联系人 ---------------------------------------------

    def get_platform_name(self) -> str:
        return self.platform

    def detect_window(self) -> dict:
        """定位主窗口，并保证它确实处于可读状态。

        窗口"存在但不可读"（最小化、收在托盘里、被拉得过小）与"没有启动"
        是完全不同的两件事，必须分别报告：前者让用户还原窗口，后者才是让
        用户去启动客户端。
        """
        windows = self._windows()
        window = pick_main_window(windows)
        if window is None:
            raise AdapterError(
                f"{describe(ReadStatus.WINDOW_NOT_FOUND, self.platform)}，"
                f"请启动并登录{self.platform}。"
            )

        state = window_state(window)
        if state != WINDOW_READY and self.auto_restore:
            if restore_window(int(window.get("handle") or 0)):
                window = pick_main_window(self._windows()) or window
                state = window_state(window)
        if state != WINDOW_READY:
            raise AdapterError(state_hint(state, self.platform, window))
        return window

    def _contact_from_title(self, window: dict) -> str:
        """标题里能直接读出会话名时才用标题，否则返回空交给 UIA。"""
        title = str(window.get("title") or "").strip()
        if is_garbled(title):
            return ""
        for suffix in self.title_suffixes:
            if suffix in title:
                head = title.split(suffix)[0].strip()
                if head and head not in self.generic_titles:
                    return head
        if title and title not in self.generic_titles:
            return title
        return ""

    def _current_contact(self, window: dict) -> tuple[str, bool]:
        """识别当前会话名，返回 (会话名, 是否从客户端窗口里核实过)。

        顺序：窗口标题 → UIA 聊天区标题栏 → 界面左栏选中的联系人。

        前两条是客户端自己给出的，可信；第三条只是用户的选择，系统并不知道
        客户端此刻是不是真的停在这个聊天上，所以标成未核实，由界面在发送确认
        时明确提示。QQ NT 必须走到第三条：它的窗口标题恒为"QQ"，无障碍树里
        也根本没有会话标题这个控件。

        三条都没有才报错——那时连"往哪发"都无从谈起。
        """
        from_title = self._contact_from_title(window)
        if from_title:
            return from_title, True
        result = read_conversation(
            int(window.get("handle") or 0), self.platform, verify=False
        )
        if result.contact:
            return result.contact, True
        if self.contact_hint:
            return self.contact_hint, False
        if result.messages:
            raise AdapterError(
                f"已读取到 {len(result.messages)} 条{self.platform}消息，但无法确认联系人。"
                "为防止把消息记到错误的人名下，请先在左栏选中对应联系人，"
                f"再确认{self.platform}窗口正停在这个聊天后重新读取。"
                f"（阶段：{result.status}）"
            )
        raise AdapterError(
            f"无法识别当前{self.platform}会话。请先在左栏选中要对话的联系人，"
            f"并在{self.platform}里打开对应聊天；若仍失败，请在日志 / 诊断页"
            f"导出脱敏控件树。（阶段：{result.status}）"
        )

    # ---- 读取 -----------------------------------------------------

    def inspect(self) -> ChatSnapshot:
        window = self.detect_window()
        contact, verified = self._current_contact(window)
        result = read_conversation(
            int(window.get("handle") or 0), self.platform, contact_hint=contact
        )

        if not result.messages and self._allow_ocr and result.region:
            from .ocr import read_region

            ocr_result = read_region(result.region, contact)
            if ocr_result.messages:
                result = ocr_result

        self._last_result = result
        detail = result.detail or result.label(self.platform)
        return ChatSnapshot(
            self.platform,
            contact,
            int(window.get("handle") or 0),
            tuple(result.messages),
            str(window.get("title") or ""),
            status=result.status,
            needs_review=result.needs_review,
            detail=detail,
            contact_verified=verified,
        )

    def get_current_contact(self) -> str:
        return self.inspect().contact_name

    def get_messages(self) -> tuple[ChatMessage, ...]:
        return self.inspect().messages

    def get_new_messages(self) -> tuple[ChatMessage, ...]:
        snap = self.inspect()
        if not snap.readable:
            return ()
        key = f"{snap.window_handle}:{snap.contact_name}"
        old = self._seen.get(key)
        self._seen[key] = {m.source_id for m in snap.messages}
        if old is None:
            return ()
        return tuple(m for m in snap.messages if m.source_id not in old)

    # ---- 写入 -----------------------------------------------------

    def _reverify(self, expected: ChatSnapshot, check: Callable[[], None]) -> None:
        """写入前的一致性复核：平台、窗口句柄必须和快照完全对得上。"""
        check()
        if expected is None:
            raise AdapterError("没有可用的会话快照，请先读取当前会话。")
        platform = getattr(expected, "platform", None)
        if platform is not None and platform != self.platform:
            raise AdapterError("会话快照与当前平台不一致，已取消操作。")
        window = self.detect_window()
        handle = getattr(expected, "window_handle", None)
        if handle is not None and int(window.get("handle") or 0) != int(handle):
            raise AdapterError("窗口已变化，已取消操作，请重新读取。")
        check()

    def _require_write(self, action: str) -> None:
        if not self.write_supported:
            raise AdapterError(
                f"{self.platform}的{action}功能当前已关闭，请使用复制后手动粘贴。"
            )

    def _input_rect(self, handle: int) -> tuple[int, int, int, int]:
        """定位输入框矩形：先找可编辑控件，找不到再按几何估算输入带。"""
        from .desktop_send import estimate_input_rect, find_input_control

        nodes, error = snapshot_tree(handle)
        if error or not nodes:
            raise AdapterError(f"无法读取{self.platform}控件树，已取消，未做任何输入。{error}")
        window_rect = nodes[0].rect
        control = find_input_control(nodes, window_rect)
        if control is not None:
            return control.rect
        estimated = estimate_input_rect(window_rect)
        if estimated is None:
            raise AdapterError(
                f"没有在{self.platform}窗口里定位到聊天输入框，已取消，未做任何输入。"
            )
        return estimated

    def _write(self, text: str, expected: ChatSnapshot, check: Callable[[], None], submit: bool):
        """填入或发送；任一前置校验不通过都不会按下任何按键。"""
        from .desktop_send import WriteError, deliver_text

        self._require_write("发送" if submit else "填入")
        self._reverify(expected, check)
        handle = int(getattr(expected, "window_handle", 0) or 0)
        input_rect = self._input_rect(handle)
        # 真正动键盘之前的最后一道闸门：此刻若已暂停/紧急停止就不再继续。
        check()
        try:
            deliver_text(handle, text, input_rect, submit)
        except WriteError as exc:
            raise AdapterError(str(exc)) from exc
        return "sent" if submit else "filled"

    def focus_input(self, expected: ChatSnapshot, check: Callable[[], None]) -> None:
        from .desktop_send import activate_window

        self._require_write("定位输入框")
        self._reverify(expected, check)
        handle = int(getattr(expected, "window_handle", 0) or 0)
        if not activate_window(handle):
            raise AdapterError(f"未能把{self.platform}窗口切到前台。")

    def fill_message(self, text: str, expected: ChatSnapshot, check: Callable[[], None]) -> str:
        return self._write(text, expected, check, submit=False)

    def send_message(self, text: str, expected: ChatSnapshot, check: Callable[[], None]) -> str:
        self._write(text, expected, check, submit=True)
        # 回车按下去了不等于对方收到了，所以回读一次确认；确认不了就如实说"不确定"。
        return "sent" if self._echo_confirmed(expected, text) else "unknown"

    def _echo_confirmed(self, expected: ChatSnapshot, text: str) -> bool:
        """回读聊天区，确认自己刚发出的这条消息真的出现在窗口里。

        确认不了只返回 False（界面会显示"结果不确定"），绝不自动重发。
        """
        handle = int(getattr(expected, "window_handle", 0) or 0)
        wanted = " ".join(text.split())
        if not wanted:
            return False
        try:
            result = read_conversation(handle, self.platform, verify=False)
        except Exception:
            return False
        for message in reversed(result.messages):
            if message.sender == "我" and " ".join(message.content.split()) == wanted:
                return True
        return False

    # ---- 诊断 -----------------------------------------------------

    def control_tree(self):
        rows = []
        for window in self._windows():
            rows.append(
                {
                    "platform": self.platform,
                    "type": "Window",
                    "class": window.get("class_name", ""),
                    "rect": window.get("rect"),
                    "name": "[内容已隐藏]",
                }
            )
            rows.extend(control_tree_summary(int(window.get("handle") or 0)))
        return rows
