"""SQLite 三层记忆入口；检索按联系人隔离，摘要保留原文来源。"""

import re
from app.database import Database


def keywords(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return set(
        w
        for chunk in words
        for w in ([chunk] if chunk.isascii() else [chunk[i : i + 2] for i in range(len(chunk) - 1)])
    )


class MemoryService:
    def __init__(self, db: Database):
        self.db = db

    def remember(self, contact_id: int, content: str, kind: str = "fact") -> None:
        if not content.strip():
            raise ValueError("记忆内容不能为空")
        self.db.execute(
            "INSERT INTO memories(contact_id,kind,content,source) VALUES(?,?,?,'user')",
            (contact_id, kind, content.strip()),
        )

    def context(self, contact_id: int, query: str) -> dict:
        rows = self.db.recent_messages(contact_id, 500, learning=True)
        terms = keywords(query)
        ranked = sorted(rows[:-20], key=lambda r: len(terms & keywords(r["content"])), reverse=True)
        relevant = [r for r in ranked if terms & keywords(r["content"])][:8]
        # 抽取式摘要，不生成未经确认的事实；将旧历史的最近话题按原文列出。
        older = rows[:-20]
        summary = "\n".join(
            f"[消息 {r['id']}] {r['sender']}：{r['content'][:160]}" for r in older[-8:]
        )
        self.db.execute(
            """INSERT INTO conversations(contact_id,summary) VALUES(?,?)
            ON CONFLICT(contact_id) DO UPDATE SET summary=excluded.summary,updated_at=CURRENT_TIMESTAMP""",
            (contact_id, summary),
        )
        feedback = self.db.query(
            """SELECT ai_reply,final_reply FROM reply_feedback
            WHERE contact_id=? AND status='sent' ORDER BY id DESC LIMIT 30""",
            (contact_id,),
        )
        return {
            "recent": rows[-20:],
            "summary_extract": summary,
            "relevant_history": relevant,
            "similar_replies": [
                r for r in rows if r["sender"] == "我" and terms & keywords(r["content"])
            ][-5:],
            "corrections": feedback[:8],
            "long_term": self.db.query(
                "SELECT kind,content,source FROM memories WHERE contact_id=? ORDER BY id DESC LIMIT 30",
                (contact_id,),
            ),
        }
