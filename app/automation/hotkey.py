"""Windows 全局 Ctrl+Alt+Q，不限于应用有焦点时。"""

import ctypes
import sys
from ctypes import wintypes
from PySide6.QtCore import QAbstractNativeEventFilter


class EmergencyHotkey(QAbstractNativeEventFilter):
    ID = 0x4CA1

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.registered = bool(
            sys.platform == "win32"
            and ctypes.windll.user32.RegisterHotKey(None, self.ID, 0x4003, ord("Q"))
        )

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.ID:
                self.callback()
                return True, 0
        return False, 0

    def close(self) -> None:
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.ID)
            self.registered = False
