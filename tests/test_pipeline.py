"""离线端到端：模拟模型和平台，不向真实联系人发送。"""

import json
import os
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from app.database import Database, SCHEMA
from app.core.engine import ReplyEngine
from app.core.models import ChatMessage, ChatSnapshot
from app.core.risk import classify, combined_risk
from app.automation.safety import SafetyGate, SafetyError
from app.config import Settings
from app.llm import DeepSeekProvider, ProviderError
from app.persona import PersonaService
from app.memory import MemoryService
from app.adapters.base import AdapterError
from app.adapters.wechat import WeChatAdapter
from app.utils.logging import redact


class FakeProvider:
    def complete(self, messages):
        self.messages = messages
        return {"replies": ["可以的", "可以啊，到时候聊", "好"]}


class FakeAdapter:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0
        self.result = "sent"

    def send_message(self, text, expected, check):
        check()
        if expected != self.snapshot:
            raise AdapterError("联系人已切换")
        self.calls += 1
        return self.result

    def fill_message(self, text, expected, check):
        check()
        return "filled"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "chat.db")
        self.cid = self.db.ensure_contact("测试甲", "微信")
        self.gate = SafetyGate()
        self.provider = FakeProvider()
        self.engine = ReplyEngine(self.db, self.provider, self.gate)
        self.snapshot = ChatSnapshot("微信", "测试甲", 1, (ChatMessage("对方", "在吗", "id1"),))
        self.engine.conversation.ingest(self.snapshot)

    def tearDown(self):
        self.temp.cleanup()

    def test_three_candidates_context_and_unmodified_history(self):
        batch = self.engine.generate(self.cid, "在吗", self.snapshot)
        self.assertEqual(len(batch.candidates), 3)
        self.assertEqual(len(batch.draft_ids), 3)
        self.assertEqual(len(self.db.recent_messages(self.cid)), 1)
        payload = json.loads(self.provider.messages[1]["content"])
        self.assertIn("personas", payload)
        self.assertIn("recent", payload["memory"])
        self.assertEqual(payload["contact"]["name"], "测试甲")

    def test_fill_and_copy_do_not_train_but_sent_edit_does(self):
        batch = self.engine.generate(self.cid, "在吗", self.snapshot)
        self.engine.deliver(batch, 0, "可以的哥😂", FakeAdapter(self.snapshot), "fill")
        self.assertEqual(PersonaService(self.db).profiles(self.cid)["mine_contact"]["samples"], 0)
        self.engine.deliver(
            batch, 0, "可以的哥😂", FakeAdapter(self.snapshot), "send", confirmed=True
        )
        self.assertEqual(PersonaService(self.db).profiles(self.cid)["mine_contact"]["samples"], 1)
        feedback = self.db.query("SELECT * FROM reply_feedback")[0]
        self.assertEqual(feedback["edited"], 1)
        self.assertEqual(feedback["final_reply"], "可以的哥😂")

    def test_double_send_is_blocked(self):
        batch = self.engine.generate(self.cid, "在吗", self.snapshot)
        adapter = FakeAdapter(self.snapshot)
        self.engine.deliver(batch, 0, "可以的", adapter, "send", confirmed=True)
        with self.assertRaises(SafetyError):
            self.engine.deliver(batch, 0, "可以的", adapter, "send", confirmed=True)
        self.assertEqual(adapter.calls, 1)

    def test_unknown_result_never_trains_or_retries(self):
        batch = self.engine.generate(self.cid, "在吗", self.snapshot)
        adapter = FakeAdapter(self.snapshot)
        adapter.result = "unknown"
        self.engine.deliver(batch, 0, "可以的", adapter, "send", confirmed=True)
        with self.assertRaises(SafetyError):
            self.engine.deliver(batch, 0, "可以的", adapter, "send", confirmed=True)
        self.assertEqual(len(self.db.recent_messages(self.cid)), 1)
        with self.assertRaises(SafetyError):
            self.engine.deliver(batch, 1, "另外一条候选", adapter, "send", confirmed=True)

    def test_contact_switch_blocks_external_call(self):
        batch = self.engine.generate(self.cid, "在吗", self.snapshot)
        adapter = FakeAdapter(ChatSnapshot("微信", "测试乙", 2, ()))
        with self.assertRaises(AdapterError):
            self.engine.deliver(batch, 0, "可以的", adapter, "send", confirmed=True)
        self.assertEqual(adapter.calls, 0)
        self.assertFalse(
            self.db.query("SELECT was_sent FROM ai_replies WHERE id=?", (batch.draft_ids[0],))[0][
                "was_sent"
            ]
        )

    def test_no_send_without_confirmation(self):
        batch = self.engine.generate(self.cid, "在吗", self.snapshot)
        with self.assertRaises(SafetyError):
            self.engine.deliver(batch, 0, "可以的", FakeAdapter(self.snapshot), "send")

    def test_pause_cancels_pending_model_result(self):
        def respond(_):
            self.gate.stop()
            return {"replies": ["在", "好", "嗯"]}

        self.provider.complete = respond
        with self.assertRaises(SafetyError):
            self.engine.generate(self.cid, "在吗", self.snapshot)
        self.assertEqual(self.db.query("SELECT * FROM ai_replies"), [])

    def test_memory_isolation_and_forget_cutoff(self):
        other = self.db.ensure_contact("测试乙")
        self.db.save_message(other, "我", "乙的专属约定")
        self.db.save_message(self.cid, "我", "甲的专属约定")
        MemoryService(self.db).remember(self.cid, "甲的重要事件")
        self.db.clear_memory(self.cid)
        context = MemoryService(self.db).context(self.cid, "约定")
        self.assertEqual(context["recent"], [])
        self.assertEqual(context["long_term"], [])
        self.assertEqual(PersonaService(self.db).profiles(self.cid)["mine_contact"]["samples"], 0)
        self.assertEqual(len(self.db.recent_messages(other)), 1)
        self.assertEqual(len(self.db.recent_messages(self.cid)), 2)
        global_profile = PersonaService(self.db).profiles(self.cid)["mine_global"]
        self.assertNotIn("乙的专属约定", json.dumps(global_profile, ensure_ascii=False))

    def test_auto_never_sends_historical_snapshot(self):
        self.gate.auto_enabled = True
        batch = self.engine.generate(self.cid, "在吗", self.snapshot)
        with self.assertRaises(SafetyError):
            self.engine.deliver(
                batch, 0, "可以的", FakeAdapter(self.snapshot), "send", automatic=True, fresh=False
            )

    def test_high_risk_auto_never_calls_adapter(self):
        self.gate.auto_enabled = True
        batch = self.engine.generate(self.cid, "价格100元", self.snapshot)
        adapter = FakeAdapter(self.snapshot)
        with self.assertRaises(SafetyError):
            self.engine.deliver(batch, 0, "可以的", adapter, "send", automatic=True, fresh=True)
        self.assertEqual(adapter.calls, 0)

    def test_uia_validation_before_focus_or_write(self):
        adapter = WeChatAdapter()
        changed = ChatSnapshot("微信", "测试乙", 1, self.snapshot.messages)
        with patch.object(adapter, "_snapshot", return_value=changed):
            with self.assertRaises(AdapterError):
                adapter._validate(None, None, {}, self.snapshot, self.gate.check)

    def test_repeated_reads_and_repeated_words(self):
        messages = (ChatMessage("对方", "在吗", "id1"), ChatMessage("对方", "在吗", "id2"))
        self.db.ingest(self.cid, messages)
        self.db.ingest(self.cid, messages)
        self.assertEqual(len(self.db.recent_messages(self.cid)), 2)
        restarted = tuple(
            ChatMessage(m.sender, m.content, "new" + str(i)) for i, m in enumerate(messages)
        )
        self.db.ingest(self.cid, restarted)
        self.assertEqual(len(self.db.recent_messages(self.cid)), 2)

    def test_old_database_migration_keeps_data(self):
        path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO contacts(platform,name) VALUES('微信','旧联系人')")
            conn.execute("INSERT INTO messages(contact_id,sender,content) VALUES(1,'我','旧记录')")
        conn.close()
        new = Database(path)
        self.assertEqual(new.recent_messages(1)[0]["content"], "旧记录")
        self.assertEqual(new.query("PRAGMA user_version")[0]["user_version"], 2)

    def test_self_message_does_not_call_provider(self):
        self.provider.complete = Mock()
        snapshot = ChatSnapshot("微信", "测试甲", 1, (ChatMessage("我", "收到"),))
        self.assertFalse(self.engine.generate(self.cid, "收到", snapshot).should_reply)
        self.provider.complete.assert_not_called()

    def test_auto_requires_low_risk_and_limits(self):
        self.gate.auto_enabled = True
        for text in ["验证码123456", "价格100元", "我保证退款", "明天开会", "我有个问题"]:
            with self.assertRaises(SafetyError):
                self.gate.reserve_auto(self.cid, text, classify(text)[0], True)
        self.gate.reserve_auto(self.cid, "a", "LOW", True, 100)
        with self.assertRaises(SafetyError):
            self.gate.reserve_auto(self.cid, "b", "LOW", True, 120)
        self.gate.reserve_auto(self.cid, "b", "LOW", True, 170)
        self.gate.reserve_auto(self.cid, "c", "LOW", True, 240)
        with self.assertRaises(SafetyError):
            self.gate.reserve_auto(self.cid, "d", "LOW", True, 310)
        self.assertEqual(combined_risk("好", "可以的", [{"content": "合同退款"}]), "HIGH")


class ProviderTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ZHIYU_LIVE_UIA_TEST") == "1", "Optional legacy wxauto desktop integration test; requires interactive Windows")
    def test_vendor_uia_thread_context_reads_desktop(self):
        from wxauto import uiautomation as uia
        from app.automation.uia import uia_thread

        with uia_thread(uia):
            self.assertEqual(uia.GetRootControl().ControlTypeName, "PaneControl")

    def test_key_missing(self):
        with self.assertRaises(ProviderError):
            DeepSeekProvider(Settings()).complete([])

    @patch("app.llm.provider.requests.post")
    def test_validated_json_and_http_error(self, post):
        provider = DeepSeekProvider(Settings(api_key="test-only"))
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"replies":["在","你好","嗯"]}'}}]
        }
        self.assertEqual(len(provider.complete([])["replies"]), 3)
        post.return_value.status_code = 401
        with self.assertRaisesRegex(ProviderError, "API Key 无效"):
            provider.complete([])
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"replies":["一样","一样","一样"]}'}}]
        }
        with self.assertRaises(ProviderError):
            provider.complete([])

    def test_sensitive_log_redaction(self):
        self.assertNotIn("13812345678", redact("电话13812345678"))
        self.assertNotIn("123456", redact("验证码：123456"))
        self.assertNotIn("secret", redact("Bearer secret"))

    @patch("app.adapters.wechat.wechat_windows", return_value=[])
    def test_no_wechat_action_when_unavailable(self, _):
        with self.assertRaisesRegex(AdapterError, "未找到微信"):
            WeChatAdapter().inspect()

    def test_wechat_4_window_title_is_not_used_as_contact(self):
        adapter = WeChatAdapter()
        window = {
            "handle": 123,
            "title": "微信",
            "class_name": "Qt51514QWindowIcon",
        }
        with patch.object(adapter, "_current_contact", return_value="真实联系人") as current:
            backend = Mock()
            backend.get_messages.return_value = []
            backend.search_contact.return_value = [{"username": "wxid_real"}]
            backend.get_sessions.return_value = []
            with patch.object(adapter, "detect_window", return_value=window), patch.object(
                adapter, "_ensure_backend", return_value=backend
            ):
                snapshot = adapter.inspect()
        current.assert_called_once_with(window)
        self.assertEqual(snapshot.contact_name, "真实联系人")
        self.assertEqual(adapter._generic_window_title("微信"), True)
        self.assertEqual(adapter._generic_window_title("Weixin"), True)
        self.assertEqual(adapter._generic_window_title("真实联系人"), False)

    def test_generic_wechat_title_without_uia_is_explicit_error(self):
        adapter = WeChatAdapter()
        driver = Mock()
        driver.WeChatUIA.return_value.ensure_window.return_value = False
        with patch.dict(sys.modules, {"wechatauto.uia_driver": driver}):
            with self.assertRaisesRegex(AdapterError, "无法识别当前微信会话"):
                adapter._current_contact({"title": "微信"})


if __name__ == "__main__":
    unittest.main()
