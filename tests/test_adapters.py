"""QQ / 钉钉适配器测试。

不连接真实客户端：窗口枚举、控件读取和写入原语都被替换成可控桩，
验证的是两件事——"读不到就如实报状态、绝不把界面文字当消息"，
以及"写入的每一道前置校验不通过时都不会按下任何按键"。
"""

import unittest
from unittest.mock import Mock, patch

from app.adapters.base import AdapterError
from app.adapters.desktop_base import is_garbled
from app.adapters.desktop_uia import UiaNode
from app.adapters.dingtalk import DingTalkAdapter
from app.adapters.qq import QQAdapter
from app.adapters.read_status import ReadResult, ReadStatus
from app.core.models import ChatMessage, ChatSnapshot
from app.conversation.manager import ConversationManager

READ = "app.adapters.desktop_base.read_conversation"
TREE = "app.adapters.desktop_base.snapshot_tree"
DELIVER = "app.adapters.desktop_send.deliver_text"

# 一个尺寸正常、可见、未最小化的主窗口。
MAIN_RECT = (0, 0, 1280, 800)


def win(handle=54321, title="QQ", class_name="TXGuiFoundation", rect=MAIN_RECT, **extra):
    """构造窗口枚举返回的那种字典；默认就是"就绪"状态。"""
    row = {
        "handle": handle,
        "title": title,
        "class_name": class_name,
        "rect": rect,
        "visible": True,
        "iconic": False,
    }
    row.update(extra)
    return row


def ok_result(contact="张三"):
    return ReadResult(
        status=ReadStatus.MESSAGES_READ,
        messages=(ChatMessage("张三", "晚点一起看下方案", "a1"),),
        contact=contact,
        region=(280, 60, 1000, 600),
    )


def blank_result(status=ReadStatus.MESSAGE_REGION_NOT_FOUND, contact=""):
    return ReadResult(status=status, contact=contact, detail="控件树里没有聊天区域。")


_BAD_SAMPLE = "�"  # Windows 解码失败时的替换字符


class TestGarbledTitle(unittest.TestCase):
    def test_sample_constant_is_garbled(self):
        self.assertTrue(is_garbled(_BAD_SAMPLE))

    def test_replacement_chars_are_garbled(self):
        self.assertTrue(is_garbled("΢��"))
        self.assertTrue(is_garbled("���"))

    def test_empty_is_garbled(self):
        self.assertTrue(is_garbled(""))
        self.assertTrue(is_garbled("   "))

    def test_normal_title_is_not_garbled(self):
        self.assertFalse(is_garbled("张三 - 钉钉"))
        self.assertFalse(is_garbled("QQ"))


class TestDingTalkAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = DingTalkAdapter()

    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_detect_window_success(self, windows):
        windows.return_value = [win(12345, "测试会话 - 钉钉", "DingTalk")]
        self.assertEqual(self.adapter.detect_window()["handle"], 12345)

    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_detect_window_not_found(self, windows):
        windows.return_value = []
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.detect_window()
        self.assertIn("未找到钉钉主窗口", str(ctx.exception))

    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_multiple_windows_picks_the_main_one(self, windows):
        """同进程的小弹层不应该让整个读取失败，主窗口是最大的那个。"""
        windows.return_value = [
            win(1, "提示", rect=(0, 0, 300, 200)),
            win(2, "会话 - 钉钉", rect=(0, 0, 1400, 900)),
        ]
        self.assertEqual(self.adapter.detect_window()["handle"], 2)

    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_minimized_window_reports_restore_hint(self, windows):
        windows.return_value = [win(2, "钉钉", rect=(-32000, -32000, -31840, -31972), iconic=True)]
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.detect_window()
        message = str(ctx.exception)
        self.assertIn("最小化", message)
        self.assertNotIn("请启动", message)

    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_tray_hidden_window_reports_tray_hint(self, windows):
        """收进托盘的窗口仍然存在，不能报成"没启动"。"""
        windows.return_value = [win(2, "钉钉", visible=False)]
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.detect_window()
        message = str(ctx.exception)
        self.assertIn("托盘", message)
        self.assertNotIn("请启动", message)

    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_too_small_window_reports_size_hint(self, windows):
        windows.return_value = [win(2, "钉钉", rect=(0, 0, 300, 200))]
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.detect_window()
        self.assertIn("过小", str(ctx.exception))

    @patch("app.adapters.desktop_base.restore_window")
    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_auto_restore_reopens_tray_window(self, windows, restore):
        """手工读取允许把托盘窗口还原出来，还原后应当直接可读。"""
        hidden, ready = win(2, "钉钉", visible=False), win(2, "钉钉")
        windows.side_effect = [[hidden], [ready]]
        restore.return_value = True
        adapter = DingTalkAdapter()
        adapter.auto_restore = True
        self.assertEqual(adapter.detect_window()["handle"], 2)
        restore.assert_called_once_with(2)

    @patch("app.adapters.desktop_base.restore_window")
    @patch("app.adapters.dingtalk.dingtalk_windows")
    def test_monitoring_never_restores_window(self, windows, restore):
        """后台轮询绝不能让客户端自己弹到屏幕上打断用户。"""
        windows.return_value = [win(2, "钉钉", visible=False)]
        with self.assertRaises(AdapterError):
            self.adapter.detect_window()
        restore.assert_not_called()

    def test_contact_from_suffix_title(self):
        self.assertEqual(
            self.adapter._current_contact({"title": "张三 - 钉钉", "handle": 1}),
            ("张三", True),
        )

    @patch(READ)
    def test_generic_title_falls_back_to_uia(self, read):
        read.return_value = ok_result("项目讨论群")
        contact = self.adapter._current_contact({"title": "钉钉", "handle": 1})
        self.assertEqual(contact, ("项目讨论群", True))
        read.assert_called_once()

    @patch(READ)
    def test_garbled_title_is_not_used_as_contact(self, read):
        read.return_value = ok_result("项目讨论群")
        contact = self.adapter._current_contact({"title": "������ - 钉钉", "handle": 1})
        self.assertEqual(contact, ("项目讨论群", True))

    @patch(READ)
    def test_unknown_contact_raises_with_stage(self, read):
        read.return_value = blank_result()
        with self.assertRaises(AdapterError) as ctx:
            self.adapter._current_contact({"title": "钉钉", "handle": 1})
        message = str(ctx.exception)
        self.assertIn("无法识别当前钉钉会话", message)
        self.assertIn(ReadStatus.MESSAGE_REGION_NOT_FOUND, message)

    @patch(READ)
    def test_contact_hint_is_last_resort_and_unverified(self, read):
        """窗口里读不出会话名时才用界面选择，且必须标成未核实。"""
        read.return_value = blank_result()
        self.adapter.set_contact_hint("王经理")
        self.assertEqual(
            self.adapter._current_contact({"title": "钉钉", "handle": 1}),
            ("王经理", False),
        )

    @patch(READ)
    def test_window_title_beats_contact_hint(self, read):
        """客户端自己说得出会话名时，绝不能被界面选择覆盖。"""
        read.return_value = blank_result()
        self.adapter.set_contact_hint("王经理")
        self.assertEqual(
            self.adapter._current_contact({"title": "张三 - 钉钉", "handle": 1}),
            ("张三", True),
        )

    def test_platform_name(self):
        self.assertEqual(self.adapter.get_platform_name(), "钉钉")


class TestQQAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = QQAdapter()

    @patch("app.adapters.qq.qq_windows")
    def test_detect_window_success(self, windows):
        windows.return_value = [win()]
        self.assertEqual(self.adapter.detect_window()["handle"], 54321)

    @patch("app.adapters.qq.qq_windows")
    def test_detect_window_not_found(self, windows):
        windows.return_value = []
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.detect_window()
        self.assertIn("未找到QQ主窗口", str(ctx.exception))

    @patch(READ)
    def test_generic_qq_title_uses_chat_header(self, read):
        read.return_value = ok_result("小李")
        for title in ("QQ", "TIM", "腾讯QQ", ""):
            with self.subTest(title=title):
                read.reset_mock()
                self.assertEqual(
                    self.adapter._current_contact({"title": title, "handle": 1}),
                    ("小李", True),
                )
                read.assert_called_once()

    @patch("app.adapters.qq.qq_windows")
    @patch(READ)
    def test_inspect_success(self, read, windows):
        read.return_value = ok_result("小李")
        windows.return_value = [win()]
        snapshot = self.adapter.inspect()
        self.assertEqual(snapshot.platform, "QQ")
        self.assertEqual(snapshot.contact_name, "小李")
        self.assertEqual(snapshot.status, ReadStatus.MESSAGES_READ)
        self.assertTrue(snapshot.readable)
        self.assertEqual(len(snapshot.messages), 1)

    @patch("app.adapters.qq.qq_windows")
    @patch(READ)
    def test_inspect_region_missing_is_not_success(self, read, windows):
        read.return_value = ReadResult(
            status=ReadStatus.MESSAGE_REGION_NOT_FOUND,
            contact="小李",
            detail="没找到聊天区域。",
        )
        windows.return_value = [win()]
        snapshot = self.adapter.inspect()
        self.assertEqual(snapshot.status, ReadStatus.MESSAGE_REGION_NOT_FOUND)
        self.assertEqual(snapshot.messages, ())
        self.assertFalse(snapshot.readable)
        self.assertTrue(snapshot.detail)

    @patch("app.adapters.qq.qq_windows")
    @patch(READ)
    def test_ocr_result_needs_review(self, read, windows):
        read.return_value = ReadResult(
            status=ReadStatus.MESSAGES_READ,
            messages=(ChatMessage("待确认", "看起来像一句话", "o1"),),
            contact="小李",
            needs_review=True,
            detail="结果来自 OCR，必须人工核验。",
        )
        windows.return_value = [win()]
        snapshot = self.adapter.inspect()
        self.assertTrue(snapshot.needs_review)
        self.assertFalse(snapshot.readable)

    @patch("app.adapters.qq.qq_windows")
    @patch(READ)
    def test_new_messages_empty_when_unreadable(self, read, windows):
        read.return_value = ReadResult(
            status=ReadStatus.NO_MESSAGES, contact="小李", detail="没有可读消息。"
        )
        windows.return_value = [win()]
        self.assertEqual(self.adapter.get_new_messages(), ())
        self.assertEqual(self.adapter.get_new_messages(), ())

    def test_platform_name(self):
        self.assertEqual(self.adapter.get_platform_name(), "QQ")


def input_tree():
    """一棵最小控件树：根窗口 + 一个位于下方输入带里的可编辑控件。"""
    return (
        [
            UiaNode(0, -1, 0, "WindowControl", "QQ", rect=MAIN_RECT),
            UiaNode(1, 0, 1, "EditControl", "", rect=(360, 640, 1240, 760)),
        ],
        "",
    )


class TestQQSend(unittest.TestCase):
    """发送链路。每条用例都要确认"拒绝时一次按键都没发出去"。"""

    def setUp(self):
        self.adapter = QQAdapter()
        self.snapshot = ChatSnapshot("QQ", "小李", 54321, ())

    @patch(DELIVER)
    @patch(TREE, return_value=input_tree())
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_fill_does_not_press_enter(self, windows, tree, deliver):
        status = self.adapter.fill_message("测试", self.snapshot, Mock())
        self.assertEqual(status, "filled")
        self.assertIs(deliver.call_args.args[3], False)

    @patch(READ)
    @patch(DELIVER)
    @patch(TREE, return_value=input_tree())
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_send_confirms_by_reading_the_echo(self, windows, tree, deliver, read):
        read.return_value = ReadResult(
            status=ReadStatus.MESSAGES_READ,
            messages=(ChatMessage("我", "测试", "m1"),),
            contact="小李",
        )
        status = self.adapter.send_message("测试", self.snapshot, Mock())
        self.assertEqual(status, "sent")
        self.assertIs(deliver.call_args.args[3], True)

    @patch(READ)
    @patch(DELIVER)
    @patch(TREE, return_value=input_tree())
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_send_without_echo_is_unknown_not_sent(self, windows, tree, deliver, read):
        """回车按下去了不代表发出去了，看不到回显就必须说"不确定"。"""
        read.return_value = ReadResult(status=ReadStatus.NO_MESSAGES, contact="小李")
        self.assertEqual(self.adapter.send_message("测试", self.snapshot, Mock()), "unknown")

    @patch(READ, side_effect=RuntimeError("UIA 掉线"))
    @patch(DELIVER)
    @patch(TREE, return_value=input_tree())
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_echo_failure_never_resends(self, windows, tree, deliver, read):
        self.assertEqual(self.adapter.send_message("测试", self.snapshot, Mock()), "unknown")
        self.assertEqual(deliver.call_count, 1)

    @patch(DELIVER)
    @patch(TREE, return_value=([], "控件树为空"))
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_send_aborts_when_input_not_located(self, windows, tree, deliver):
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.send_message("测试", self.snapshot, Mock())
        self.assertIn("未做任何输入", str(ctx.exception))
        deliver.assert_not_called()

    @patch(DELIVER)
    @patch("app.adapters.qq.qq_windows", return_value=[win(99999)])
    def test_send_rejects_changed_window(self, windows, deliver):
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.send_message("测试", self.snapshot, Mock())
        self.assertIn("窗口已变化", str(ctx.exception))
        deliver.assert_not_called()

    @patch(DELIVER)
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_send_rejects_other_platform_snapshot(self, windows, deliver):
        snapshot = ChatSnapshot("微信", "小李", 54321, ())
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.send_message("测试", snapshot, Mock())
        self.assertIn("平台不一致", str(ctx.exception))
        deliver.assert_not_called()

    @patch(DELIVER)
    @patch("app.adapters.qq.qq_windows", return_value=[win(visible=False)])
    def test_send_aborts_when_window_in_tray(self, windows, deliver):
        with self.assertRaises(AdapterError):
            self.adapter.send_message("测试", self.snapshot, Mock())
        deliver.assert_not_called()

    @patch(DELIVER)
    @patch(TREE, return_value=input_tree())
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_paused_gate_blocks_send(self, windows, tree, deliver):
        """闸门（暂停 / 紧急停止）抛错时，绝不能已经把消息发出去。"""
        check = Mock(side_effect=AdapterError("已暂停"))
        with self.assertRaises(AdapterError):
            self.adapter.send_message("测试", self.snapshot, check)
        deliver.assert_not_called()

    @patch(DELIVER)
    @patch(TREE, return_value=input_tree())
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_gate_is_rechecked_right_before_keystrokes(self, windows, tree, deliver):
        """定位输入框要花时间，这期间用户可能已经按下紧急停止。"""
        check = Mock()
        with patch(READ, return_value=ReadResult(status=ReadStatus.NO_MESSAGES)):
            self.adapter.send_message("测试", self.snapshot, check)
        self.assertGreaterEqual(check.call_count, 3)

    @patch(DELIVER)
    @patch("app.adapters.qq.qq_windows", return_value=[win()])
    def test_write_switch_off_blocks_send(self, windows, deliver):
        self.adapter.write_supported = False
        with self.assertRaises(AdapterError) as ctx:
            self.adapter.send_message("测试", self.snapshot, Mock())
        self.assertIn("手动粘贴", str(ctx.exception))
        deliver.assert_not_called()


class _StubDb:
    def __init__(self):
        self.ingested = []

    def ensure_contact(self, name, platform):
        return 7

    def ingest(self, cid, messages):
        self.ingested.append((cid, tuple(messages)))
        return len(messages)


class TestIngestGate(unittest.TestCase):
    """入库闸门：不可信的读取结果只登记联系人，不写消息。"""

    def setUp(self):
        self.db = _StubDb()
        self.manager = ConversationManager(self.db)

    def test_trusted_snapshot_is_ingested(self):
        snapshot = ChatSnapshot("QQ", "小李", 1, (ChatMessage("小李", "在吗", "s1"),))
        cid, count = self.manager.ingest(snapshot)
        self.assertEqual((cid, count), (7, 1))
        self.assertEqual(len(self.db.ingested), 1)

    def test_unreadable_snapshot_is_not_ingested(self):
        snapshot = ChatSnapshot(
            "QQ",
            "小李",
            1,
            (ChatMessage("对方", "通讯录", "s1"),),
            status=ReadStatus.UNCERTAIN,
        )
        cid, count = self.manager.ingest(snapshot)
        self.assertEqual((cid, count), (7, 0))
        self.assertEqual(self.db.ingested, [])

    def test_needs_review_snapshot_is_not_ingested(self):
        snapshot = ChatSnapshot(
            "QQ", "小李", 1, (ChatMessage("待确认", "疑似文本", "s1"),), needs_review=True
        )
        cid, count = self.manager.ingest(snapshot)
        self.assertEqual(count, 0)
        self.assertEqual(self.db.ingested, [])


if __name__ == "__main__":
    unittest.main()
