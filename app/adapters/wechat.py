"""微信适配器：优先支持微信 4.x 的 wechatauto-replica，兼容旧版 wxauto。"""

from __future__ import annotations
from typing import Callable
from app.core.models import ChatMessage, ChatSnapshot
from .base import AdapterError
from .windows import (
    WINDOW_READY,
    pick_main_window,
    restore_window,
    state_hint,
    wechat_windows,
    window_state,
)

try:
    from wechatauto import WeChatDB, WeChatGUI
except Exception:  # 依赖未安装时仍可启动手工模式
    WeChatDB = WeChatGUI = None


class WeChatAdapter:
    def __init__(self, auto_restore: bool = False):
        self._seen: dict[str, set[str]] = {}
        self._last_seq: dict[str, int] = {}
        self._db = None
        self._gui = None
        # 仅手工点击"读取当前会话"时为 True；后台轮询不允许自动弹窗口。
        self.auto_restore = auto_restore

    def get_platform_name(self) -> str:
        return "微信"

    def detect_window(self) -> dict:
        """定位主窗口，并区分"没启动"和"启动了但被最小化 / 收进托盘"。

        微信同一进程会开出多个顶层窗口（主窗口、独立聊天窗、小程序容器），
        见到多个就报错会把正常情况也堵死，这里交给 pick_main_window 选。
        """
        windows = wechat_windows()
        window = pick_main_window(windows)
        if window is None:
            raise AdapterError("未找到微信主窗口，请启动并登录微信。")
        state = window_state(window)
        if state != WINDOW_READY and self.auto_restore:
            if restore_window(int(window.get("handle") or 0)):
                window = pick_main_window(wechat_windows()) or window
                state = window_state(window)
        if state != WINDOW_READY:
            raise AdapterError(state_hint(state, "微信", window))
        return window

    def _ensure_backend(self):
        if WeChatDB is None:
            raise AdapterError(
                "未安装 wechatauto-replica，请运行 pip install -r requirements-windows-integrations.txt。"
            )
        if self._db is None:
            try:
                self._db = WeChatDB()
            except Exception as exc:
                raise AdapterError(f"无法读取微信4.x本地数据库：{exc}") from exc
        return self._db

    @staticmethod
    def _session_name(db, username: str) -> str:
        for s in db.get_sessions(limit=200):
            if s.get("username") == username:
                return s.get("last_sender") or username
        try:
            found = db.search_contact(username)
            if found:
                return found[0].get("nickname") or found[0].get("remark") or username
        except Exception:
            pass
        return username

    def _resolve_user(self, contact: str) -> str:
        db = self._ensure_backend()
        if any(s.get("username") == contact for s in db.get_sessions(limit=300)):
            return contact
        try:
            hits = db.search_contact(contact)
            if hits:
                return hits[0].get("username") or hits[0].get("wxid") or contact
        except Exception:
            pass
        return contact

    @staticmethod
    def _generic_window_title(title: str) -> bool:
        """判断窗口标题是否只是微信主窗口标题，而不是聊天对象。"""
        normalized = (title or "").strip()
        # 当前机器的 Win32 返回值曾出现过乱码“΢��”，不能把它当联系人查库。
        return not normalized or normalized in {"微信", "Weixin", "WeChat", "΢��", "��"}

    def _current_contact(self, window: dict) -> str:
        """从微信4.x当前输入框读取会话名，不把主窗口标题误当联系人。"""
        try:
            from wechatauto.uia_driver import WeChatUIA

            uia = WeChatUIA(timeout=6)
            if uia.ensure_window(wake=True, timeout=6):
                contact = (uia.current_chat() or "").strip()
                if contact:
                    return contact
        except Exception:
            # UIA 热激活/控件读取失败时保留标题回退；后面会验证标题是否可查库。
            pass

        title = str(window.get("title") or "").strip()
        if not self._generic_window_title(title):
            return title
        raise AdapterError(
            "无法识别当前微信会话。请恢复微信主窗口、打开目标聊天后再点击“读取当前微信”；"
            "若仍失败，请在“日志 / 诊断”导出脱敏控件树。"
        )

    def _snapshot_from(self, contact: str, rows: list[dict], window: dict) -> ChatSnapshot:
        msgs = []
        for row in reversed(rows[-50:]):
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            sender_id = str(row.get("sender_id") or "")
            sender_name = row.get("sender_username") or contact
            # 微信4.x real_sender_id=2 表示自己，其余为对方/群成员
            sender = "我" if sender_id == "2" else sender_name
            typ = str(row.get("type") or "文本")
            mtype = "text" if typ == "文本" else ("system" if typ == "系统消息" else "attachment")
            source = f"{row.get('sort_seq', '')}:{row.get('local_id', '')}"
            msgs.append(
                ChatMessage(sender, content, source, mtype, str(row.get("create_time") or ""))
            )
        return ChatSnapshot("微信", contact, window["handle"], tuple(msgs), window.get("title", ""))

    def inspect(self) -> ChatSnapshot:
        window = self.detect_window()
        db = self._ensure_backend()
        # 数据库负责读消息，UIA 只负责确认"当前会话是谁"；不能再把主窗口
        # 标题当成联系人，微信4.x主窗口标题通常只是"微信"。
        contact = self._current_contact(window)
        user = self._resolve_user(contact)
        rows = db.get_messages(user, limit=50)
        if not rows:
            hits = db.search_contact(contact)
            if not hits and not any(
                s.get("username") == contact for s in db.get_sessions(limit=300)
            ):
                raise AdapterError(
                    f"已识别会话“{contact}”，但本地微信数据库没有对应消息。请确认当前账号和会话。"
                )
        return self._snapshot_from(contact, rows, window)

    def get_current_contact(self) -> str:
        return self.inspect().contact_name

    def get_messages(self) -> tuple[ChatMessage, ...]:
        return self.inspect().messages

    def get_new_messages(self) -> tuple[ChatMessage, ...]:
        snap = self.inspect()
        key = f"{snap.window_handle}:{snap.contact_name}"
        old = self._seen.get(key)
        self._seen[key] = {m.source_id for m in snap.messages}
        return tuple(m for m in snap.messages if old is not None and m.source_id not in old)

    def _snapshot(self, backend=None, edit=None, window=None) -> ChatSnapshot:
        return self.inspect()

    def _validate(self, backend, edit, window, expected: ChatSnapshot, check: Callable[[], None]):
        check()
        current = self._snapshot(backend, edit, window)
        if current.fingerprint != expected.fingerprint:
            raise AdapterError("联系人或聊天内容已变化，已取消操作，请重新读取并生成。")

    def _check(self, expected: ChatSnapshot, check: Callable[[], None]):
        self._validate(None, None, None, expected, check)

    def focus_input(self, expected, check):
        self._check(expected, check)
        if WeChatGUI is None:
            raise AdapterError("发送组件未安装。")

    def fill_message(self, text, expected, check):
        self._check(expected, check)
        # replica 负责 UIA/OCR 混合输入；发送前仅填入不点击发送无法由其 API保证，交由受控发送
        raise AdapterError("微信4.x暂不支持独立填入草稿，请使用发送按钮完成受控发送。")

    def send_message(self, text: str, expected: ChatSnapshot, check: Callable[[], None]) -> str:
        self._check(expected, check)
        if WeChatGUI is None:
            raise AdapterError("发送组件未安装。")
        contact = expected.contact_name
        user = self._resolve_user(contact)
        try:
            gui = self._gui or WeChatGUI()
            self._gui = gui
            check()
            result = gui.send_msg(text, who=user, verify=True)
            check()
            ok = bool(getattr(result, "success", False))
            if not ok and isinstance(result, dict):
                ok = bool(result.get("success"))
            return "sent" if ok else "unknown"
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"微信4.x发送失败，结果未知：{exc}") from exc

    def control_tree(self):
        return [
            {"type": "Window", "class": w["class_name"], "name": "[内容已隐藏]"}
            for w in wechat_windows()
        ]

