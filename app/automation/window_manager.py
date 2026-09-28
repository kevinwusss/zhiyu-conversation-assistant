"""Windows窗口管理：防止应用窗口在后台操作时跳出。"""

import sys
from typing import Optional


class WindowFocusManager:
    """管理窗口焦点，防止在操作其他应用时本应用窗口跳到前台。"""

    def __init__(self):
        self._saved_hwnd: Optional[int] = None

    def save_foreground(self) -> None:
        """保存当前前台窗口句柄。"""
        if sys.platform != "win32":
            return
        try:
            import win32gui
            self._saved_hwnd = win32gui.GetForegroundWindow()
        except Exception:
            self._saved_hwnd = None

    def restore_foreground(self) -> None:
        """尝试恢复之前的前台窗口。"""
        if sys.platform != "win32" or self._saved_hwnd is None:
            return
        try:
            import win32gui
            # 仅当保存的窗口仍然存在且可见时才恢复
            if win32gui.IsWindow(self._saved_hwnd) and win32gui.IsWindowVisible(self._saved_hwnd):
                win32gui.SetForegroundWindow(self._saved_hwnd)
        except Exception:
            pass
        finally:
            self._saved_hwnd = None

    def prevent_activation(self, hwnd: int) -> None:
        """设置窗口为不自动激活。"""
        if sys.platform != "win32":
            return
        try:
            import win32gui
            import win32con
            # 移除WS_EX_APPWINDOW样式，添加WS_EX_NOACTIVATE
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            ex_style |= win32con.WS_EX_NOACTIVATE
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
        except Exception:
            pass
