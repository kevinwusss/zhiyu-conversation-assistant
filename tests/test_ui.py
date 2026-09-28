"""Qt 状态与后台任务测试；不连接真实微信和模型。"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from app.config import Settings
from app.core.models import ChatMessage, ChatSnapshot
from app.ui.candidates import candidate_title
from app.ui.theme import colors
from app.ui.widgets import ChatView, bubble_width
from app.ui.window import MainWindow
from tests.test_pipeline import FakeAdapter, FakeProvider


class ReadableAdapter(FakeAdapter):
    def inspect(self):
        return self.snapshot


class UiTestCase(unittest.TestCase):
    """所有界面测试共用的窗口装配；不连接真实客户端和模型。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(Settings(db_path=Path(self.temp.name) / "test.db"))
        self.window.engine.provider = FakeProvider()
        self.window.contact_id = self.window.db.ensure_contact("测试联系人", "手工")

    def wait_idle(self):
        deadline = time.monotonic() + 5
        while self.window.busy and time.monotonic() < deadline:
            loop = QEventLoop()
            QTimer.singleShot(20, loop.quit)
            loop.exec()
        self.assertFalse(self.window.busy)

    def tearDown(self):
        self.wait_idle()
        self.window.close()
        self.temp.cleanup()


class UiTests(UiTestCase):
    def test_pending_response_is_discarded_after_stop(self):
        started, release = threading.Event(), threading.Event()

        class SlowProvider:
            def complete(self, messages):
                started.set()
                release.wait(3)
                return {"replies": ["在", "你好", "嗯"]}

        self.window.engine.provider = SlowProvider()
        self.window.incoming.setPlainText("在吗")
        self.window.generate()
        self.assertTrue(started.wait(2))
        self.assertFalse(self.window.generate_btn.isEnabled())
        self.window.stop()
        release.set()
        self.wait_idle()
        self.assertIsNone(self.window.batch)
        self.assertEqual(self.window.candidates.count(), 0)
        self.assertFalse(self.window.gate.auto_enabled)

    def test_auto_baseline_then_new_message_only(self):
        class GreetingProvider:
            def complete(self, messages):
                return {"replies": ["你好", "你好呀", "嗨"]}

        self.window.engine.provider = GreetingProvider()
        first = ChatSnapshot("微信", "模拟微信", 123, (ChatMessage("对方", "在吗", "1"),))
        adapter = ReadableAdapter(first)
        self.window.adapter = adapter
        self.window.mode.setCurrentText("自动回复")
        self.window.timer.stop()
        self.window.read_chat(True)
        self.wait_idle()
        self.assertEqual(adapter.calls, 0)
        # The contact-level opt-in is required in addition to the global mode.
        self.window.db.set_auto_reply(self.window.contact_id, True)
        adapter.snapshot = ChatSnapshot(
            "微信", "模拟微信", 123, first.messages + (ChatMessage("对方", "你好", "2"),)
        )
        self.window.read_chat(True)
        self.wait_idle()
        self.assertEqual(adapter.calls, 1)
        self.window.read_chat(True)
        self.wait_idle()
        self.assertEqual(adapter.calls, 1)

    def test_revisited_contact_does_not_auto_reply_to_old_messages(self):
        a = ChatSnapshot("微信", "模拟甲", 123, (ChatMessage("对方", "在吗", "1"),))
        adapter = ReadableAdapter(a)
        self.window.adapter = adapter
        self.window.mode.setCurrentText("自动回复")
        self.window.timer.stop()
        for snapshot in [
            a,
            ChatSnapshot("微信", "模拟乙", 123, (ChatMessage("对方", "在吗", "2"),)),
            ChatSnapshot("微信", "模拟甲", 123, a.messages + (ChatMessage("对方", "你好", "3"),)),
        ]:
            adapter.snapshot = snapshot
            self.window.read_chat(True)
            self.wait_idle()
        self.assertEqual(adapter.calls, 0)

    def test_manual_message_invalidates_bound_wechat_target(self):
        first = ChatSnapshot("微信", "模拟微信", 123, (ChatMessage("对方", "在吗", "1"),))
        self.window.adapter = ReadableAdapter(first)
        self.window.read_chat(False)
        self.wait_idle()
        self.assertIsNotNone(self.window.snapshot)
        self.window.incoming.setPlainText("手工更改的消息")
        self.assertIsNone(self.window.snapshot)
        self.window.generate()
        self.wait_idle()
        self.assertFalse(self.window.fill_btn.isEnabled())
        self.assertFalse(self.window.send_btn.isEnabled())

    def test_unreadable_snapshot_never_reports_success(self):
        """只找到窗口没读到消息时，界面必须说明失败阶段，不能显示读取完成。"""
        snapshot = ChatSnapshot(
            "QQ",
            "小李",
            123,
            (),
            "QQ",
            status="message_region_not_found",
            detail="控件树里没有聊天区域。",
        )
        self.window.adapter = ReadableAdapter(snapshot)
        self.window.read_chat(False)
        self.wait_idle()
        notice = self.window.notice.text()
        self.assertNotIn("读取完成", notice)
        self.assertIn("message_region_not_found", notice)
        self.assertEqual(self.window.incoming.toPlainText(), "")
        self.assertFalse(self.window.generate_btn.isEnabled())
        self.assertFalse(self.window.fill_btn.isEnabled())
        self.assertFalse(self.window.send_btn.isEnabled())

    def test_unreadable_snapshot_blocks_generation(self):
        snapshot = ChatSnapshot(
            "QQ", "小李", 123, (ChatMessage("待确认", "疑似界面文字", "x1"),),
            status="messages_read", needs_review=True, detail="来自 OCR，待核验。",
        )
        self.window.adapter = ReadableAdapter(snapshot)
        self.window.read_chat(False)
        self.wait_idle()
        self.window.incoming.blockSignals(True)
        self.window.incoming.setPlainText("疑似界面文字")
        self.window.incoming.blockSignals(False)
        self.window.generate()
        self.wait_idle()
        self.assertIsNone(self.window.batch)
        self.assertIn("阻止生成", self.window.notice.text())

    def test_unreadable_snapshot_is_not_stored(self):
        snapshot = ChatSnapshot(
            "QQ", "小李", 123, (ChatMessage("对方", "通讯录", "x1"),),
            status="uncertain", detail="两次读取不一致。",
        )
        self.window.adapter = ReadableAdapter(snapshot)
        self.window.read_chat(False)
        self.wait_idle()
        stored = self.window.db.recent_messages(self.window.contact_id, 50)
        self.assertEqual([m["content"] for m in stored], [])

    def test_pause_preserves_edited_draft(self):
        self.window.incoming.setPlainText("在吗")
        self.window.generate()
        self.wait_idle()
        self.window.editor.setPlainText("我修改过的草稿")
        batch = self.window.batch
        self.window.toggle_pause()
        self.assertTrue(self.window.gate.stopped.is_set())
        self.assertEqual(self.window.editor.toPlainText(), "我修改过的草稿")
        self.assertIs(self.window.batch, batch)
        self.assertFalse(self.window.fill_btn.isEnabled())
        self.window.toggle_pause()
        self.assertIs(self.window.batch, batch)
        self.assertEqual(self.window.editor.toPlainText(), "我修改过的草稿")


class ShellTests(UiTestCase):
    """新版三栏界面：平台滑块、按平台隔离的联系人、气泡与折叠候选。"""

    def contact_names(self):
        """列表里真正可选的联系人名字；分组标题不算。"""
        names = []
        for i in range(self.window.contacts.count()):
            item = self.window.contacts.item(i)
            if item.data(Qt.UserRole) is None or item.isHidden():
                continue
            names.append(item.text().split("\n")[0])
        return names

    def test_platform_switch_isolates_contacts(self):
        self.window.db.ensure_contact("微信甲", "微信")
        self.window.db.ensure_contact("QQ乙", "QQ")
        self.window.switch_platform("微信")
        names = self.contact_names()
        self.assertIn("微信甲", names)
        self.assertNotIn("QQ乙", names)
        self.window.switch_platform("QQ")
        names = self.contact_names()
        self.assertIn("QQ乙", names)
        self.assertNotIn("微信甲", names)
        # 手工会话不属于任何平台，切到哪个平台都要留在列表里。
        self.assertIn("测试联系人", names)
        self.assertIn("QQ", self.window.contacts_label.text())

    def test_platform_switch_syncs_the_slider_and_settings_page(self):
        """代码侧切平台时滑块和设置说明也要跟着走，不能显示成别的平台。"""
        self.window.switch_platform("钉钉")
        self.assertEqual(self.window.platform_switch.current(), "钉钉")
        self.assertIn("钉钉", self.window.settings_page.platform_note.text())
        self.window.switch_platform("微信")
        self.assertEqual(self.window.platform_switch.current(), "微信")
        self.assertIn("微信", self.window.settings_page.platform_note.text())

    def test_platform_switch_stops_listening(self):
        """监听盯的是上一个平台的窗口，换平台必须停掉再重新建立基线。"""
        self.window.listen.setChecked(True)
        self.assertTrue(self.window.timer.isActive())
        self.window.switch_platform("QQ")
        self.assertFalse(self.window.listen.isChecked())
        self.assertFalse(self.window.timer.isActive())

    def test_candidate_title_drops_the_decimal_point(self):
        self.assertEqual(candidate_title({"variant": "惯常表达", "score": 75.0}), "惯常表达 · 75 分")
        # 分数缺失或不是数字时至少要显示风格名，不能崩在绘制里。
        self.assertEqual(candidate_title({"variant": "惯常表达"}), "惯常表达")
        self.assertEqual(candidate_title({"variant": "惯常表达", "score": None}), "惯常表达")

    def test_platform_switch_drops_the_previous_snapshot(self):
        snapshot = ChatSnapshot("微信", "模拟微信", 123, (ChatMessage("对方", "在吗", "1"),))
        self.window.adapter = ReadableAdapter(snapshot)
        self.window.read_chat(False)
        self.wait_idle()
        self.assertIsNotNone(self.window.snapshot)
        self.window.switch_platform("QQ")
        # 快照来自微信窗口，切到 QQ 后绝不能拿它当发送目标。
        self.assertIsNone(self.window.snapshot)
        self.assertIsNone(self.window.batch)
        self.assertFalse(self.window.send_btn.isEnabled())

    def test_bubbles_put_me_on_the_right_and_escape_html(self):
        palette = colors("浅色")
        view = ChatView()
        mine = view._bubble("我", "在的", "12:00", palette, 20)
        theirs = view._bubble("对方", "<b>在吗</b>", "11:59", palette, 20)
        self.assertIn('align="right"', mine)
        self.assertIn(palette["accent"], mine)
        self.assertIn('align="left"', theirs)
        self.assertIn(palette["bubble"], theirs)
        self.assertIn("&lt;b&gt;在吗&lt;/b&gt;", theirs)
        self.assertNotIn("<b>", theirs)

    def test_bubble_width_follows_measured_text(self):
        """气泡宽度按实测文字宽度走：短消息不许铺满，长消息封顶不超屏。"""
        metrics = ChatView().fontMetrics()
        short = bubble_width("在", metrics, 900)
        medium = bubble_width("这次价格还能便宜吗？", metrics, 900)
        long_text = bubble_width("很长的一段话" * 30, metrics, 900)
        self.assertLess(short, medium)
        self.assertLess(medium, 60)
        self.assertLessEqual(long_text, 74)
        # 窗口变宽时同一句话占的比例应该变小。
        self.assertLess(bubble_width("这次价格还能便宜吗？", metrics, 1800), medium)

    def test_bubbles_follow_the_current_theme(self):
        """气泡颜色写死在 HTML 里，换主题必须重绘，不能停在旧配色。"""
        cid = self.window.db.ensure_contact("换肤测试", "手工")
        self.window.db.ingest(cid, (ChatMessage("我", "在的", "t1"),))
        self.window.contact_id = cid
        self.window.change_theme("浅色")
        self.window.refresh_details()
        self.assertIn(colors("浅色")["accent"].lower(), self.window.chat.toHtml().lower())
        self.window.change_theme("深色")
        self.assertIn(colors("深色")["accent"].lower(), self.window.chat.toHtml().lower())

    def test_chat_view_renders_stored_history(self):
        cid = self.window.db.ensure_contact("气泡测试", "手工")
        self.window.db.ingest(
            cid, (ChatMessage("对方", "在吗", "b1"), ChatMessage("我", "在的", "b2"))
        )
        self.window.contact_id = cid
        self.window.refresh_details()
        text = self.window.chat.toPlainText()
        self.assertIn("在吗", text)
        self.assertIn("在的", text)

    def test_candidate_panel_folds_until_generation(self):
        self.assertFalse(self.window.suggestions.is_expanded())
        self.window.incoming.setPlainText("在吗")
        # 折起来的时候标题也要说明这一轮在回复什么。
        self.assertIn("在吗", self.window.suggestions.header.text())
        self.window.generate()
        self.wait_idle()
        self.assertTrue(self.window.suggestions.is_expanded())
        self.assertEqual(self.window.candidates.count(), 3)
        self.assertIn("3", self.window.suggestions.header.text())
        self.window.ignore()
        self.assertFalse(self.window.suggestions.is_expanded())
        self.assertEqual(self.window.candidates.count(), 0)

    def test_typed_message_sends_without_generating_candidates(self):
        snapshot = ChatSnapshot("微信", "模拟微信", 123, (ChatMessage("对方", "在吗", "1"),))
        adapter = ReadableAdapter(snapshot)
        self.window.adapter = adapter
        self.window.read_chat(False)
        self.wait_idle()
        self.window.editor.setPlainText("我自己打的回复")
        self.assertIsNone(self.window.batch)
        self.assertTrue(self.window.send_btn.isEnabled())
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.Yes):
            self.window.deliver("send")
        self.wait_idle()
        self.assertEqual(adapter.calls, 1)
        replies = self.window.db.query("SELECT * FROM ai_replies")
        self.assertEqual([r["final_reply"] for r in replies], ["我自己打的回复"])
        self.assertEqual([r["was_sent"] for r in replies], [1])

    def test_typed_message_is_not_sendable_without_a_snapshot(self):
        self.window.editor.setPlainText("没有读取就想发")
        self.assertFalse(self.window.send_btn.isEnabled())
        self.assertFalse(self.window.fill_btn.isEnabled())
        self.window.deliver("send")
        self.wait_idle()
        self.assertIsNone(self.window.batch)
        self.assertEqual(self.window.db.query("SELECT * FROM ai_replies"), [])

    def unreadable_snapshot(self):
        """钉钉的真实处境：窗口找得到、输入框点得进，但聊天内容读不出来。"""
        return ChatSnapshot(
            "钉钉",
            "王经理",
            456,
            (),
            status="message_region_not_found",
            detail="聊天区是 CEF WebView，无障碍树里没有文本。",
            contact_verified=False,
        )

    def test_typed_message_sends_even_when_chat_is_unreadable(self):
        # 读不到内容就连手打一句话都发不出去的话，钉钉这类客户端等于完全不可用；
        # 而这句话本来就是用户看着屏幕自己写的，不依赖系统读到任何东西。
        adapter = ReadableAdapter(self.unreadable_snapshot())
        self.window.adapter = adapter
        self.window.read_chat(False)
        self.wait_idle()
        self.assertFalse(self.window.snapshot.readable)
        self.window.editor.setPlainText("好的，我十分钟后到")
        self.assertTrue(self.window.send_btn.isEnabled())
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.Yes):
            self.window.deliver("send")
        self.wait_idle()
        self.assertEqual(adapter.calls, 1)

    def test_ai_candidates_stay_blocked_when_chat_is_unreadable(self):
        adapter = ReadableAdapter(self.unreadable_snapshot())
        self.window.adapter = adapter
        self.window.read_chat(False)
        self.wait_idle()
        snapshot = self.window.snapshot
        self.assertFalse(snapshot.readable)
        # 读不到内容时待回复框不会被回填，这里用 blockSignals 手动塞进去，
        # 走的是和回填完全一样的路径（不经过 input_changed，快照仍然绑着）。
        self.window.incoming.blockSignals(True)
        self.window.incoming.setPlainText("对方说了什么")
        self.window.incoming.blockSignals(False)
        self.window.update_actions()
        self.assertFalse(self.window.generate_btn.isEnabled())
        self.window.generate()
        self.wait_idle()
        self.assertIsNone(self.window.batch)
        self.assertIn("阻止生成", self.window.notice.text())
        # 再退一步：就算候选已经在手上（快照是生成之后才失效的），发送闸门也得拦住。
        self.window.batch = self.window.engine.generate(
            self.window.contact_id, "对方说了什么", snapshot
        )
        self.window.editor.setPlainText(self.window.batch.candidates[0]["text"])
        self.assertFalse(self.window.send_btn.isEnabled())
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.Yes):
            self.window.deliver("send")
        self.wait_idle()
        self.assertEqual(adapter.calls, 0)
        self.assertIn("AI 候选不可发送", self.window.notice.text())

    def test_unverified_contact_is_called_out_before_sending(self):
        snapshot = ChatSnapshot(
            "QQ",
            "王经理",
            789,
            (ChatMessage("对方", "在吗", "1"),),
            contact_verified=False,
        )
        adapter = ReadableAdapter(snapshot)
        self.window.adapter = adapter
        self.window.read_chat(False)
        self.wait_idle()
        self.assertIn("读不出会话名", self.window.notice.text())
        self.window.editor.setPlainText("在的")
        seen = {}

        def decline(dialog):
            seen["text"] = dialog.text()
            return QMessageBox.No

        with patch.object(QMessageBox, "exec", new=decline):
            self.window.deliver("send")
        self.wait_idle()
        self.assertIn("未核实", seen.get("text", ""))
        self.assertEqual(adapter.calls, 0)

    def test_send_confirmation_can_be_declined(self):
        snapshot = ChatSnapshot("微信", "模拟微信", 123, (ChatMessage("对方", "在吗", "1"),))
        adapter = ReadableAdapter(snapshot)
        self.window.adapter = adapter
        self.window.read_chat(False)
        self.wait_idle()
        self.window.editor.setPlainText("点错了不想发")
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.No):
            self.window.deliver("send")
        self.wait_idle()
        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
