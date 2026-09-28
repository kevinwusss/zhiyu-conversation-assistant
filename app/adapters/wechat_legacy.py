"""wxauto 3.9 消息解析的窄适配层；不调用其自动切会话/剪贴板发送。

上游源码及 Apache-2.0 许可证保留于 github/wxauto-main。
UIA 对象只在操作所在 COM 线程内创建、使用、销毁。
"""

import logging
import time
from contextlib import contextmanager
from typing import Callable
from app.core.models import ChatMessage, ChatSnapshot
from app.automation.uia import uia_thread
from .base import AdapterError
from .windows import wechat_windows


class WeChatAdapter:
    def __init__(self):
        self._seen: dict[str, set[str]] = {}

    def get_platform_name(self) -> str:
        return "微信"

    def detect_window(self) -> dict:
        windows = wechat_windows()
        supported = [w for w in windows if w["class_name"] == "WeChatMainWndForPC"]
        if len(supported) > 1:
            raise AdapterError("检测到多个微信窗口，请只保留一个目标窗口后重新读取。")
        if supported:
            return supported[0]
        if windows:
            raise AdapterError(
                "检测到新版微信。当前 wxauto 适配器面向 3.9.x，4.x 控件树尚未适配；可先手工输入生成建议。"
            )
        raise AdapterError("未找到微信主窗口。请启动并登录微信，打开目标聊天后重新读取。")

    @contextmanager
    def _session(self):
        window = self.detect_window()
        try:
            from wxauto import WeChat, uiautomation as uia

            logger = logging.getLogger("wxauto")
            logger.disabled = True
            with uia_thread(uia):
                uia.SetGlobalSearchTimeout(1)
                root = uia.ControlFromHandle(window["handle"])
                # 复用上游明确的三栏层级；不触发 _show、快捷键或滚动。
                main = next(c for c in root.GetChildren() if not c.ClassName)
                navigation, sessions, chat = main.GetFirstChildControl().GetChildren()
                backend = WeChat.__new__(WeChat)
                backend.language, backend.UiaAPI, backend.ChatBox = "cn", root, chat
                backend.C_MsgList = chat.ListControl(Name="消息", searchDepth=15)
                if not backend.C_MsgList.Exists(0.2):
                    raise AdapterError(
                        "微信消息控件不可读，请打开聊天。若仍失败，请导出控件树检查版本。"
                    )
                edit = chat.EditControl(searchDepth=10)
                if not edit.Exists(0.2) or not edit.Name.strip() or edit.Name in {"搜索", "Search"}:
                    raise AdapterError("无法可靠识别当前聊天输入框和联系人。")
                yield backend, edit, window
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                "微信 UIA 控件结构不匹配或已失效。请保持窗口可见，重新读取；不支持的版本可导出控件树。"
            ) from exc

    @staticmethod
    def _snapshot(backend, edit, window: dict) -> ChatSnapshot:
        contact = edit.Name.strip()
        messages = []
        for message in backend.GetAllMessage()[-50:]:
            kind = getattr(message, "type", "sys")
            sender = (
                "我"
                if kind == "self"
                else (str(getattr(message, "sender", contact)) if kind == "friend" else "系统")
            )
            content = str(message.content)
            message_type = "text" if kind in {"self", "friend"} else "system"
            if content.startswith(("[图片]", "[文件]", "[语音]", "[视频]")):
                message_type = "attachment"
            source_id = f"{window['process_epoch']}:{getattr(message, 'id', '')}"
            messages.append(ChatMessage(sender, content, source_id, message_type))
        return ChatSnapshot("微信", contact, window["handle"], tuple(messages), window["title"])

    def inspect(self) -> ChatSnapshot:
        with self._session() as (backend, edit, window):
            return self._snapshot(backend, edit, window)

    def get_current_contact(self) -> str:
        return self.inspect().contact_name

    def get_messages(self) -> tuple[ChatMessage, ...]:
        return self.inspect().messages

    def get_new_messages(self) -> tuple[ChatMessage, ...]:
        snapshot = self.inspect()
        key = f"{snapshot.window_handle}:{snapshot.contact_name}"
        previous = self._seen.get(key)
        self._seen[key] = {m.source_id for m in snapshot.messages}
        return tuple(
            m for m in snapshot.messages if previous is not None and m.source_id not in previous
        )

    def _validate(
        self, backend, edit, window, expected: ChatSnapshot, check: Callable[[], None]
    ) -> None:
        check()
        current = self._snapshot(backend, edit, window)
        if current.fingerprint != expected.fingerprint:
            raise AdapterError("联系人、窗口或聊天内容已变化。已取消操作，请重新读取并生成。")

    @staticmethod
    def _focus(edit, window: dict) -> None:
        import win32gui
        import win32con

        if win32gui.IsIconic(window["handle"]):
            win32gui.ShowWindow(window["handle"], win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(window["handle"])
        edit.SetFocus()
        if win32gui.GetForegroundWindow() != window["handle"] or not edit.HasKeyboardFocus:
            raise AdapterError("无法确认微信输入框焦点，已取消操作。")

    def focus_input(self, expected: ChatSnapshot, check: Callable[[], None]) -> None:
        with self._session() as (backend, edit, window):
            self._validate(backend, edit, window, expected, check)
            self._focus(edit, window)

    def _fill(self, backend, edit, window, text, expected, check):
        self._validate(backend, edit, window, expected, check)
        pattern = edit.GetValuePattern()
        existing = pattern.Value or ""
        if existing and existing != text:
            raise AdapterError("微信输入框已有草稿，已保留原内容。请先处理草稿再填入。")
        self._focus(edit, window)
        self._validate(backend, edit, window, expected, check)
        if existing != text:
            # 优先 UIA ValuePattern；失败时仅使用已验证焦点的文本粘贴。
            try:
                pattern.SetValue(text)
            except Exception:
                import win32clipboard
                import win32con

                win32clipboard.OpenClipboard()
                try:
                    # 非文本剪贴板无法无损恢复，直接让用户使用复制功能。
                    if (
                        win32clipboard.CountClipboardFormats()
                        and not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT)
                    ):
                        raise AdapterError(
                            "剪贴板含非文本内容，自动填入已取消。请使用复制按钮手工处理。"
                        )
                    formats, current_format = [], 0
                    while True:
                        current_format = win32clipboard.EnumClipboardFormats(current_format)
                        if not current_format:
                            break
                        formats.append(current_format)
                    if any(
                        f
                        not in {
                            win32con.CF_TEXT,
                            win32con.CF_UNICODETEXT,
                            win32con.CF_LOCALE,
                            win32con.CF_OEMTEXT,
                        }
                        for f in formats
                    ):
                        raise AdapterError(
                            "剪贴板含格式化内容，为保留原内容已取消自动粘贴。请使用复制按钮手动处理。"
                        )
                    old = (
                        win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT)
                        else ""
                    )
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                finally:
                    win32clipboard.CloseClipboard()
                try:
                    check()
                    if not edit.HasKeyboardFocus:
                        raise AdapterError("输入焦点已变化，取消粘贴。")
                    edit.SendKeys("{Ctrl}v", waitTime=0.15)
                finally:
                    win32clipboard.OpenClipboard()
                    try:
                        if (
                            win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT)
                            and win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) == text
                        ):
                            win32clipboard.EmptyClipboard()
                            if old:
                                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, old)
                    finally:
                        win32clipboard.CloseClipboard()
        if pattern.Value.replace("\r\n", "\n") != text.replace("\r\n", "\n"):
            raise AdapterError("无法验证输入框内容，已停止后续发送。")

    def fill_message(self, text: str, expected: ChatSnapshot, check: Callable[[], None]) -> str:
        with self._session() as (backend, edit, window):
            self._fill(backend, edit, window, text, expected, check)
            return "filled"

    def send_message(self, text: str, expected: ChatSnapshot, check: Callable[[], None]) -> str:
        with self._session() as (backend, edit, window):
            self._fill(backend, edit, window, text, expected, check)
            button = backend.ChatBox.ButtonControl(
                RegexName=r"^(发送|Send)(\(.*\)|（.*）)?$", searchDepth=15
            )
            if not button.Exists(0.2) or not button.IsEnabled:
                raise AdapterError("未找到可用的发送按钮，草稿已填入，请在微信手动发送。")
            self._validate(backend, edit, window, expected, check)
            check()
            try:
                invoke = button.GetInvokePattern()
            except Exception:
                invoke = None
            if invoke:
                invoke.Invoke()
            else:
                # 坐标来自实时控件边界，无固定屏幕坐标，也不假设 Enter 配置。
                button.Click(simulateMove=False)
            # 从 UIA 消息回显确认；不确定时不记录为已发送，也不重试。
            previous_ids = {m.source_id for m in expected.messages}
            for _ in range(6):
                if check:
                    check()
                time.sleep(0.25)
                current = self._snapshot(backend, edit, window)
                if current.contact_name != expected.contact_name:
                    return "unknown"
                if any(
                    m.sender == "我" and m.content == text and m.source_id not in previous_ids
                    for m in current.messages
                ):
                    return "sent"
            return "unknown"

    def control_tree(self) -> list[dict]:
        """本机诊断不含聊天文字，保留可定位控件的结构标记。"""
        from wxauto import uiautomation as uia

        windows = wechat_windows()
        if len(windows) != 1:
            raise AdapterError("请保留一个微信主窗口，再导出控件树。")
        window = windows[0]
        with uia_thread(uia):
            root = uia.ControlFromHandle(window["handle"])
            rows, pending = [], [(root, 0)]
            while pending and len(rows) < 800:
                control, depth = pending.pop()
                rows.append(
                    {
                        "depth": depth,
                        "type": control.ControlTypeName,
                        "class": control.ClassName,
                        "automation_id": control.AutomationId,
                        "name": control.Name
                        if control.Name in {"搜索", "消息", "发送", "聊天", "Send"}
                        else "[内容已隐藏]",
                    }
                )
                if depth < 16:
                    pending.extend((c, depth + 1) for c in reversed(control.GetChildren()))
            return rows
