from app.core.models import ChatSnapshot
from app.database import Database


class ConversationManager:
    """平台消息归一化后的唯一入库入口。"""

    def __init__(self, db: Database):
        self.db = db

    def ingest(self, snapshot: ChatSnapshot) -> tuple[int, int]:
        """入库前的最后一道闸门：不可信的读取结果只登记联系人，不写消息。

        needs_review（OCR 等）或非 messages_read 状态的内容有可能是界面文字，
        一旦入库就会被当成真实聊天记录学习，因此在这里直接拦掉。
        """
        cid = self.db.ensure_contact(snapshot.contact_name, snapshot.platform)
        if not getattr(snapshot, "readable", True):
            return cid, 0
        count = self.db.ingest(cid, snapshot.messages)
        return cid, count

    def context_contact(self, contact_id: int) -> dict:
        contact = self.db.contact(contact_id)
        return {
            key: contact.get(key) or "未设置"
            for key in ("name", "nickname", "relationship", "notes", "platform")
        }
