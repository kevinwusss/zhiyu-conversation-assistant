"""所有发送统一经过这里；自动化开关只保存在本次运行内。"""

import threading
import time
from collections import deque


class SafetyError(RuntimeError):
    pass


class SafetyGate:
    def __init__(self):
        self.stopped = threading.Event()
        self.auto_enabled = False
        self._sent: deque[float] = deque()
        self._contact_count: dict[int, int] = {}
        self._attempted: set[str] = set()

    def stop(self) -> None:
        self.stopped.set()
        self.auto_enabled = False

    def check(self) -> None:
        if self.stopped.is_set():
            raise SafetyError("操作已暂停，请先恢复 AI。")

    def reserve_auto(
        self, contact_id: int, fingerprint: str, risk: str, fresh: bool, now: float | None = None
    ) -> None:
        self.check()
        now = time.monotonic() if now is None else now
        if not self.auto_enabled or not fresh or risk != "LOW":
            raise SafetyError("此消息不满足自动发送条件，已保留建议。")
        if fingerprint in self._attempted:
            raise SafetyError("该消息已处理，不重复自动发送。")
        while self._sent and now - self._sent[0] >= 3600:
            self._sent.popleft()
        if self._sent and now - self._sent[-1] < 60:
            raise SafetyError("自动回复冷却中（至少间隔 60 秒）。")
        if len(self._sent) >= 10 or self._contact_count.get(contact_id, 0) >= 3:
            raise SafetyError("已达到自动回复上限：每小时 10 次、每联系人每次运行 3 次。")
        # 不确定发送结果也计入额度，避免失败重试造成重复。
        self._attempted.add(fingerprint)
        self._sent.append(now)
        self._contact_count[contact_id] = self._contact_count.get(contact_id, 0) + 1
