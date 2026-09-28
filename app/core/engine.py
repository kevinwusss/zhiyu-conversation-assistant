"""回复与反馈流程；完全不依赖微信或 Qt。"""

import logging
from app.automation.safety import SafetyGate, SafetyError
from app.conversation import ConversationManager
from app.core.models import ChatSnapshot, ReplyBatch
from app.core.prompt import build_prompt
from app.core.ranker import rank_replies
from app.core.risk import classify, combined_risk
from app.database import Database
from app.llm import LLMProvider
from app.memory import MemoryService
from app.persona import PersonaService

log = logging.getLogger("assistant")


class ReplyEngine:
    def __init__(self, db: Database, provider: LLMProvider, gate: SafetyGate):
        self.db, self.provider, self.gate = db, provider, gate
        self.memory, self.persona = MemoryService(db), PersonaService(db)
        self.conversation = ConversationManager(db)

    def generate(
        self, contact_id: int, incoming: str, snapshot: ChatSnapshot | None = None
    ) -> ReplyBatch:
        self.gate.check()
        if not incoming.strip():
            raise ValueError("请先读取或输入当前消息。")
        risk, reason, scene = classify(incoming)
        should_reply = not (
            snapshot
            and snapshot.messages
            and (
                snapshot.messages[-1].sender in {"我", "系统"}
                or snapshot.messages[-1].message_type != "text"
            )
        )
        if not should_reply:
            return ReplyBatch(
                contact_id,
                incoming,
                [],
                risk,
                "最新消息来自本人、系统或未识别附件，不自动生成回复",
                scene,
                False,
                snapshot,
            )
        memory = self.memory.context(contact_id, incoming)
        personas = self.persona.profiles(contact_id)
        messages = build_prompt(
            self.conversation.context_contact(contact_id),
            memory,
            personas,
            incoming,
            scene,
            self.db.setting("prohibitions", "不承诺价格、付款或合同；不泄露私人资料"),
        )
        log.info("开始生成 联系人ID=%s 风险=%s", contact_id, risk)
        response = self.provider.complete(messages)
        self.gate.check()
        profile = (
            personas["mine_contact"]
            if personas["mine_contact"]["samples"]
            else personas["mine_global"]
        )
        ranked = rank_replies(response["replies"], incoming, profile, memory["recent"])
        result = ReplyBatch(contact_id, incoming, ranked, risk, reason, scene, True, snapshot)
        for candidate in ranked:
            candidate["risk"] = combined_risk(incoming, candidate["text"], memory["recent"])
            result.draft_ids.append(self.db.save_reply(contact_id, incoming, candidate["text"]))
        return result

    def manual_draft(self, contact_id: int, text: str, snapshot: ChatSnapshot) -> ReplyBatch:
        """把用户手打的一句话包装成一次性草稿。

        手工发送和选候选发送走完全相同的 deliver 闸门——确认、送达回执、
        重复发送拦截一个都不能少，所以这里只造草稿，不另开发送通道。
        """
        self.gate.check()
        if not text.strip():
            raise SafetyError("回复内容为空。")
        candidate = {
            "text": text,
            "variant": "手工输入",
            "score": 0,
            "note": "用户手工输入，未经模型生成。",
            "risk": classify(text)[0],
        }
        batch = ReplyBatch(
            contact_id, "", [candidate], candidate["risk"], "手工输入", "手工", True, snapshot
        )
        batch.draft_ids.append(self.db.save_reply(contact_id, "", text))
        return batch

    def deliver(
        self,
        batch: ReplyBatch,
        index: int,
        text: str,
        adapter,
        action: str,
        confirmed: bool = False,
        automatic: bool = False,
        fresh: bool = False,
    ) -> str:
        self.gate.check()
        if action not in {"fill", "send"} or not batch.snapshot:
            raise SafetyError("请先读取真实聊天，并针对这次读取生成回复。")
        if not text.strip() or len(text) > 1500:
            raise SafetyError("回复内容为空或超过 1500 字。")
        risk = combined_risk(
            batch.incoming, text, self.db.recent_messages(batch.contact_id, 20, learning=True)
        )
        reply_id = batch.draft_ids[index]
        existing = self.db.query("SELECT status FROM reply_feedback WHERE reply_id=?", (reply_id,))
        if existing and existing[0]["status"] in {"sent", "unknown", "sending"}:
            raise SafetyError("该草稿已发送或结果不确定，请先检查微信，禁止重复发送。")
        if action == "send":
            if automatic:
                self.gate.reserve_auto(batch.contact_id, batch.snapshot.fingerprint, risk, fresh)
            elif not confirmed:
                raise SafetyError("发送需要用户确认目标和最终内容。")
            if not self.db.claim_delivery(batch.snapshot.fingerprint, batch.contact_id):
                raise SafetyError("这一轮聊天已尝试发送。请检查微信，系统不会切换候选重复发送。")
        # 在触发外部操作前保存意图；崩溃时保持 sending，不自动重试。
        self.db.feedback(reply_id, text, "sending" if action == "send" else "filling")
        try:
            result = (
                adapter.send_message(text, batch.snapshot, self.gate.check)
                if action == "send"
                else adapter.fill_message(text, batch.snapshot, self.gate.check)
            )
        except Exception:
            self.db.feedback(reply_id, text, "unknown" if action == "send" else "fill_failed")
            if action == "send":
                self.db.execute(
                    "UPDATE delivery_receipts SET status='unknown' WHERE fingerprint=?",
                    (batch.snapshot.fingerprint,),
                )
            raise
        if result not in {"sent", "filled", "unknown"}:
            result = "unknown"
        self.db.feedback(reply_id, text, result)
        if action == "send":
            self.db.execute(
                "UPDATE delivery_receipts SET status=? WHERE fingerprint=?",
                (result, batch.snapshot.fingerprint),
            )
        if result == "sent":
            self.persona.profiles(batch.contact_id)
        log.info("操作完成 联系人ID=%s 结果=%s", batch.contact_id, result)
        return result
