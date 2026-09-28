"""基于真实已发送语料的可解释风格统计，不把猜测当事实。"""

import json
import re
from collections import Counter
from app.database import Database


def analyse(texts: list[str]) -> dict:
    if not texts:
        return {"samples": 0, "status": "样本不足，保持自然简洁，不擅自添加称呼"}
    phrases = Counter(
        p.strip()
        for t in texts
        for p in re.split(r"[，。！？!?\n]", t)
        if 2 <= len(p.strip()) <= 10
    )
    emoji = Counter(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "".join(texts)))
    titles = Counter(re.findall(r"[\u4e00-\u9fff]{1,3}(?:总|哥|姐|老师)", " ".join(texts)))
    average = round(sum(map(len, texts)) / len(texts), 1)
    return {
        "samples": len(texts),
        "average_length": average,
        "sentence_style": "短句" if average < 30 else "中长句",
        "common_phrases": [p for p, n in phrases.most_common(8) if n >= 2],
        "preferred_titles": [p for p, _ in titles.most_common(5)],
        "emoji": [p for p, _ in emoji.most_common(6)],
        "emoji_rate": round(
            sum(bool(re.search(r"[\U0001F300-\U0001FAFF]", t)) for t in texts) / len(texts), 2
        ),
        "formality": "偏正式" if sum("您" in t for t in texts) / len(texts) > 0.25 else "偏口语",
        "humor_rate": round(sum("哈哈" in t or "😂" in t for t in texts) / len(texts), 2),
        "line_break_rate": round(sum("\n" in t for t in texts) / len(texts), 2),
        "punctuation": dict(Counter(re.findall(r"[，。！？!?…]", "".join(texts))).most_common(5)),
        "examples": texts[-5:],
        "note": "描述性统计；不推断身份、性格或承诺",
    }


class PersonaService:
    def __init__(self, db: Database):
        self.db = db

    def profiles(self, contact_id: int) -> dict:
        recent = self.db.recent_messages(contact_id, 500, learning=True)
        global_rows = self.db.query("""SELECT content FROM messages m WHERE sender='我'
            AND m.id>COALESCE((SELECT memory_cutoff FROM conversations c WHERE c.contact_id=m.contact_id),0)
            ORDER BY id DESC LIMIT 1000""")
        profiles = {
            "mine_global": analyse([r["content"] for r in global_rows][::-1]),
            "mine_contact": analyse([r["content"] for r in recent if r["sender"] == "我"]),
            "other": analyse(
                [
                    r["content"]
                    for r in recent
                    if r["sender"] not in {"我", "系统"} and r["message_type"] == "text"
                ]
            ),
        }
        # 全局只能贡献风格统计，不能把其他联系人的原话/称呼带进当前回复。
        for key in ("examples", "preferred_titles", "common_phrases"):
            profiles["mine_global"].pop(key, None)
        for subject, profile in profiles.items():
            cid = 0 if subject == "mine_global" else contact_id
            self.db.execute(
                """INSERT INTO personas(contact_id,subject,profile) VALUES(?,?,?)
                ON CONFLICT(contact_id,subject) DO UPDATE SET profile=excluded.profile,updated_at=CURRENT_TIMESTAMP""",
                (cid, subject, json.dumps(profile, ensure_ascii=False)),
            )
        return profiles
