"""启发式评分只用于排序，不宣称能验证事实正确性。"""

import re
from app.memory.service import keywords


def rank_replies(
    replies: list[str], incoming: str, profile: dict, recent: list[dict]
) -> list[dict]:
    average = profile.get("average_length", 25)
    prior = [r["content"] for r in recent if r["sender"] == "我"]
    output = []
    for index, reply in enumerate(replies):
        length = max(0, 20 - abs(len(reply) - average) / max(average, 10) * 10)
        ai_tone = any(
            p in reply for p in ("作为AI", "希望能够帮助", "非常理解", "您好，", "首先，")
        )
        relevance = min(15, 5 * len(keywords(incoming) & keywords(reply)))
        emoji_match = bool(re.search(r"[\U0001F300-\U0001FAFF]", reply)) == (
            profile.get("emoji_rate", 0) > 0.3
        )
        score = round(
            max(
                0,
                min(
                    100,
                    50
                    + length
                    + relevance
                    + (5 if emoji_match else 0)
                    - (25 if ai_tone else 0)
                    - (15 if reply in prior[-3:] else 0),
                ),
            ),
            1,
        )
        output.append(
            {
                "text": reply,
                "score": score,
                "variant": ("惯常表达", "自然随意", "更加简洁")[index],
                "source_index": index,
                "note": "启发式：句长、关键词、Emoji、重复及客服措辞；不代表事实可信度",
            }
        )
    return sorted(output, key=lambda item: item["score"], reverse=True)
