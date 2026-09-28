"""增量迁移与短连接事务，兼容早期四表数据。"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from app.config import ROOT
from app.core.models import ChatMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, name TEXT NOT NULL, nickname TEXT, relationship TEXT, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER NOT NULL, sender TEXT NOT NULL, content TEXT NOT NULL, message_type TEXT DEFAULT 'text', timestamp TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(contact_id) REFERENCES contacts(id));
CREATE TABLE IF NOT EXISTS style_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER, overall_style TEXT, common_phrases TEXT, preferred_titles TEXT, emoji_usage TEXT, sentence_length TEXT, formality TEXT, examples TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(contact_id) REFERENCES contacts(id));
CREATE TABLE IF NOT EXISTS ai_replies (id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER NOT NULL, incoming_message TEXT, ai_reply TEXT, final_reply TEXT, was_modified INTEGER DEFAULT 0, was_sent INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(contact_id) REFERENCES contacts(id));
"""


class Database:
    def __init__(self, path: str | Path = ROOT / "data/chat.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
            for name, spec in {"source_id": "TEXT", "platform": "TEXT DEFAULT '微信'"}.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {spec}")
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS ix_messages_contact ON messages(contact_id,id);
                CREATE UNIQUE INDEX IF NOT EXISTS ix_source ON messages(contact_id,source_id) WHERE source_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS conversations (
                    contact_id INTEGER PRIMARY KEY REFERENCES contacts(id), summary TEXT DEFAULT '',
                    memory_cutoff INTEGER DEFAULT 0, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS personas (
                    contact_id INTEGER NOT NULL, subject TEXT NOT NULL, profile TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(contact_id,subject));
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY, contact_id INTEGER REFERENCES contacts(id),
                    kind TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS reply_feedback (
                    id INTEGER PRIMARY KEY, reply_id INTEGER UNIQUE REFERENCES ai_replies(id),
                    contact_id INTEGER REFERENCES contacts(id), ai_reply TEXT, final_reply TEXT,
                    accepted INTEGER, edited INTEGER, status TEXT NOT NULL,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS delivery_receipts (
                    fingerprint TEXT PRIMARY KEY, contact_id INTEGER REFERENCES contacts(id),
                    status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS contact_automation (contact_id INTEGER PRIMARY KEY REFERENCES contacts(id), enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.connect() as conn:
            return conn.execute(sql, params).lastrowid or 0

    def ensure_contact(self, name: str, platform: str = "微信") -> int:
        name = name.strip()
        if not name:
            raise ValueError("联系人不能为空")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM contacts WHERE name=? AND platform=?", (name, platform)
            ).fetchone()
            if row:
                return row["id"]
            return conn.execute(
                "INSERT INTO contacts(platform,name) VALUES(?,?)", (platform, name)
            ).lastrowid

    def contacts(self, platform: str | None = None) -> list[dict]:
        """按最近活跃排序的联系人；传 platform 只取该平台的。

        界面上微信、QQ、钉钉是三个互不相干的会话列表，混在一起既看不清
        也容易发错人，所以过滤放在 SQL 里做，而不是在界面上藏行。
        """
        if platform is None:
            return self.query("SELECT * FROM contacts ORDER BY updated_at DESC,id DESC")
        return self.query(
            "SELECT * FROM contacts WHERE platform=? ORDER BY updated_at DESC,id DESC",
            (platform,),
        )

    def contact(self, contact_id: int) -> dict:
        return self.query("SELECT * FROM contacts WHERE id=?", (contact_id,))[0]

    def auto_reply_enabled(self, contact_id: int) -> bool:
        rows = self.query("SELECT enabled FROM contact_automation WHERE contact_id=?", (contact_id,))
        return bool(rows and rows[0]["enabled"])

    def set_auto_reply(self, contact_id: int, enabled: bool) -> None:
        self.execute("INSERT INTO contact_automation(contact_id,enabled) VALUES(?,?) ON CONFLICT(contact_id) DO UPDATE SET enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP", (contact_id, int(enabled)))

    def recent_messages(
        self, contact_id: int, limit: int = 20, learning: bool = False
    ) -> list[dict]:
        cutoff = (
            "AND id > COALESCE((SELECT memory_cutoff FROM conversations WHERE contact_id=?),0)"
            if learning
            else ""
        )
        params = (contact_id, contact_id, limit) if learning else (contact_id, limit)
        return self.query(
            f"SELECT * FROM messages WHERE contact_id=? {cutoff} ORDER BY id DESC LIMIT ?", params
        )[::-1]

    def save_message(
        self,
        contact_id: int,
        sender: str,
        content: str,
        message_type: str = "text",
        source_id: str | None = None,
        timestamp: str = "",
    ) -> int:
        return self.execute(
            """INSERT OR IGNORE INTO messages(contact_id,sender,content,message_type,source_id,timestamp,platform)
            VALUES(?,?,?,?,?,?,(SELECT platform FROM contacts WHERE id=?))""",
            (contact_id, sender, content, message_type, source_id or None, timestamp, contact_id),
        )

    def ingest(self, contact_id: int, messages: tuple[ChatMessage, ...]) -> int:
        """用有序重叠消除重复读取；相同文字的不同消息仍可保留。"""
        old = self.recent_messages(contact_id, 200)
        old_keys = [(m["sender"], m["content"]) for m in old]
        new_keys = [(m.sender, m.content) for m in messages]
        overlap = 0
        for n in range(1, min(len(old_keys), len(new_keys)) + 1):
            if old_keys[-n:] == new_keys[:n]:
                overlap = n
        count = 0
        with self.connect() as conn:
            for m in messages[overlap:]:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO messages(contact_id,sender,content,message_type,source_id,timestamp,platform)
                    VALUES(?,?,?,?,?,?,(SELECT platform FROM contacts WHERE id=?))""",
                    (
                        contact_id,
                        m.sender,
                        m.content,
                        m.message_type,
                        m.source_id or None,
                        m.timestamp,
                        contact_id,
                    ),
                )
                count += cur.rowcount
        return count

    def save_reply(
        self,
        contact_id: int,
        incoming: str,
        ai_reply: str,
        final_reply: str = "",
        modified: bool = False,
        sent: bool = False,
    ) -> int:
        return self.execute(
            """INSERT INTO ai_replies(contact_id,incoming_message,ai_reply,final_reply,was_modified,was_sent)
            VALUES(?,?,?,?,?,?)""",
            (contact_id, incoming, ai_reply, final_reply, int(modified), int(sent)),
        )

    def feedback(self, reply_id: int, final: str, status: str) -> None:
        """发送成功才进入本人聊天语料；复制/填入/失败不会污染风格。"""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ai_replies WHERE id=?", (reply_id,)).fetchone()
            if not row:
                raise ValueError("找不到草稿记录")
            if row["was_sent"]:
                return
            edited = row["ai_reply"] != final
            sent = status == "sent"
            conn.execute(
                "UPDATE ai_replies SET final_reply=?,was_modified=?,was_sent=? WHERE id=?",
                (final, edited, sent, reply_id),
            )
            conn.execute(
                """INSERT INTO reply_feedback(reply_id,contact_id,ai_reply,final_reply,accepted,edited,status)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(reply_id) DO UPDATE SET final_reply=excluded.final_reply,
                accepted=excluded.accepted,edited=excluded.edited,status=excluded.status,timestamp=CURRENT_TIMESTAMP""",
                (reply_id, row["contact_id"], row["ai_reply"], final, sent, edited, status),
            )
            if sent:
                conn.execute(
                    """INSERT INTO messages(contact_id,sender,content,platform,timestamp)
                    VALUES(?,'我',?,(SELECT platform FROM contacts WHERE id=?),CURRENT_TIMESTAMP)""",
                    (row["contact_id"], final, row["contact_id"]),
                )

    def setting(self, key: str, default: str = "") -> str:
        rows = self.query("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def clear_memory(self, contact_id: int) -> None:
        """保留原始历史，但设置学习截止点，旧聊天不再进入 Prompt 或 Persona。"""
        with self.connect() as conn:
            for table in ("personas", "style_profiles", "memories", "reply_feedback", "ai_replies"):
                conn.execute(f"DELETE FROM {table} WHERE contact_id=?", (contact_id,))
            conn.execute("DELETE FROM personas WHERE contact_id=0")
            conn.execute(
                """INSERT INTO conversations(contact_id,summary,memory_cutoff)
                VALUES(?,'',(SELECT COALESCE(MAX(id),0) FROM messages WHERE contact_id=?))
                ON CONFLICT(contact_id) DO UPDATE SET summary='',memory_cutoff=excluded.memory_cutoff""",
                (contact_id, contact_id),
            )

    def claim_delivery(self, fingerprint: str, contact_id: int) -> bool:
        """持久化一次性发送意图；未知结果也不会被其他候选绕过。"""
        with self.connect() as conn:
            result = conn.execute(
                "INSERT OR IGNORE INTO delivery_receipts(fingerprint,contact_id,status) VALUES(?,?,'sending')",
                (fingerprint, contact_id),
            )
            return result.rowcount == 1
