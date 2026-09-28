"""Offline, synthetic demonstration: no API, IM client, or external sending."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.automation.safety import SafetyGate
from app.core.engine import ReplyEngine
from app.database import Database


class DemoProvider:
    def complete(self, messages):
        return {"replies": ["在的，你说。", "在，有什么想讨论的吗？", "在，我们聊聊项目吧。"]}


def main():
    source = Path(__file__).resolve().parents[1] / "examples/synthetic-chat.json"
    fixture = json.loads(source.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "demo.db")
        cid = db.ensure_contact(fixture["contact"], "手工")
        for message in fixture["messages"]:
            db.save_message(cid, message["sender"], message["content"])
        engine = ReplyEngine(db, DemoProvider(), SafetyGate())
        batch = engine.generate(cid, fixture["messages"][-1]["content"])
        assert len(batch.candidates) == 3
        print(json.dumps({"data": "synthetic", "provider": "deterministic mock",
                          "external_messages_sent": 0, "risk": batch.risk,
                          "candidates": batch.candidates}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
