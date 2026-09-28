"""耗时任务串行运行在 Qt 工作线程，结果通过信号回到主线程。"""

import logging
from PySide6.QtCore import QObject, QRunnable, Signal, Slot
from app.adapters.base import AdapterError
from app.automation.safety import SafetyError
from app.llm import ProviderError


class Signals(QObject):
    done = Signal(object, object)


class Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = Signals()

    @Slot()
    def run(self):
        try:
            result = self.fn()
            self.signals.done.emit(result, None)
        except Exception as exc:
            logging.getLogger("assistant").warning("任务失败 类型=%s", type(exc).__name__)
            safe = (
                str(exc)
                if isinstance(exc, (AdapterError, SafetyError, ProviderError, ValueError))
                else "操作失败，请检查依赖、数据目录权限和运行日志后重试。"
            )
            self.signals.done.emit(None, safe)
