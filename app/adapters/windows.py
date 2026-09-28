"""窗口句柄由 pywin32 处理，避免 64 位 ctypes 句柄截断。

QQ NT 和新版钉钉都是 Chromium/Electron 外壳，主窗口类名就是
``Chrome_WidgetWin_1``，同一个进程下还会有若干 0×0 的隐藏窗口和弹层。
所以这里的策略是：按进程名枚举全部可见顶层窗口，连同矩形一起返回，
再由 :func:`pick_main_window` 按尺寸挑出真正的主窗口，而不是一见到
多个窗口就报错。
"""

import sys


_LEGACY_MAIN_CLASSES = {"WeChatMainWndForPC", "WeixinMainWndForPC"}

# 主窗口的最小尺寸；低于这个尺寸的多半是托盘气泡、输入法候选或隐藏窗口。
MIN_MAIN_WIDTH = 480
MIN_MAIN_HEIGHT = 360

# 主窗口的可读状态。只有 READY 才有完整的控件树可以读取：
# 最小化时客户端只保留几个占位节点，托盘隐藏时窗口连 WS_VISIBLE 都没有。
WINDOW_READY = "ready"
WINDOW_MINIMIZED = "minimized"
WINDOW_HIDDEN = "hidden"
WINDOW_TOO_SMALL = "too_small"

_STATE_HINTS = {
    WINDOW_MINIMIZED: "{platform}主窗口当前是最小化状态，界面控件没有加载，读不到聊天内容。请先点击任务栏图标还原窗口，并打开目标聊天。",
    WINDOW_HIDDEN: "{platform}主窗口当前收在托盘里（窗口已隐藏），读不到聊天内容。请双击托盘图标打开{platform}主窗口，并打开目标聊天。",
    WINDOW_TOO_SMALL: "{platform}窗口过小（{width}×{height}），无法区分会话列表与聊天区域。请把窗口拉大后再读取。",
}


def ensure_dpi_awareness() -> str:
    """把本进程设为 Per-Monitor v2 DPI 感知；返回实际生效的方式。

    为什么必须做：在 150% 缩放的屏幕上，DPI 非感知进程从
    ``win32gui.GetWindowRect`` 拿到的是被系统缩小过的逻辑坐标，而
    ``uiautomation`` 的 ``BoundingRectangle`` 永远是物理像素。两套坐标混在
    一起比较，就会出现"焦点控件明明在输入框里却判定为不在输入带内"这类
    查不出原因的失败；``SetCursorPos`` 也会点偏。

    Qt 6 自己会在 ``QApplication`` 构造时设成 Per-Monitor v2，所以带界面运行
    时本来就是一致的；但脚本（如 ``scripts/probe_im.py``）没有 QApplication，
    必须显式设置，否则探测出来的几何和界面里看到的对不上。

    已经设置过时 Windows 会返回失败，这里当作正常情况忽略。
    """
    if sys.platform != "win32":
        return "non-windows"
    import ctypes

    # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2（Windows 10 1703+）
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "already-set-or-unavailable"


def window_state(row: dict) -> str:
    """主窗口当前处于哪种可读状态。"""
    if not row:
        return WINDOW_HIDDEN
    if row.get("iconic"):
        return WINDOW_MINIMIZED
    if not row.get("visible", True):
        return WINDOW_HIDDEN
    if not is_main_sized(row):
        return WINDOW_TOO_SMALL
    return WINDOW_READY


def state_hint(state: str, platform: str, row: dict | None = None) -> str:
    """把窗口状态翻译成一句可执行的提示。"""
    width, height = rect_size((row or {}).get("rect"))
    template = _STATE_HINTS.get(state, "")
    return template.format(platform=platform, width=width, height=height)


def restore_window(handle: int) -> bool:
    """把最小化或托盘隐藏的窗口重新显示出来；返回是否已还原。

    只做 ShowWindow，不抢前台焦点，也不发送任何按键。
    """
    if sys.platform != "win32":
        return False
    import win32con
    import win32gui

    try:
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        elif not win32gui.IsWindowVisible(handle):
            win32gui.ShowWindow(handle, win32con.SW_SHOW)
        else:
            return True
        return bool(win32gui.IsWindowVisible(handle)) and not win32gui.IsIconic(handle)
    except Exception:
        return False

QQ_PROCESS_NAMES = {"qq.exe", "tim.exe", "qqnt.exe"}
DINGTALK_PROCESS_NAMES = {"dingtalk.exe", "dingtalklite.exe"}
WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe"}


def _looks_like_wechat_main_class(class_name: str) -> bool:
    """微信 4.x 的标题可能乱码，主窗体类名才是稳定锚点。"""
    return class_name in _LEGACY_MAIN_CLASSES or (
        class_name.startswith("Qt") and "QWindowIcon" in class_name
    )


def rect_size(rect) -> tuple[int, int]:
    if not rect or len(rect) != 4:
        return (0, 0)
    return (max(0, rect[2] - rect[0]), max(0, rect[3] - rect[1]))


def is_main_sized(row: dict) -> bool:
    width, height = rect_size(row.get("rect"))
    return width >= MIN_MAIN_WIDTH and height >= MIN_MAIN_HEIGHT


def pick_main_window(rows: list[dict]) -> dict | None:
    """从同一进程的多个窗口里挑出主窗口。

    纯函数，便于离线测试。排序依据依次是：可直接读取的窗口优先，其次面积更大。
    最小化的窗口矩形没有意义（只有任务栏那一小条），所以它不靠尺寸入选，
    而是始终作为候选，让上层能如实报告"窗口在但被最小化了"。
    """
    if not rows:
        return None
    pool = [row for row in rows if row.get("iconic") or is_main_sized(row)] or rows

    def rank(row):
        width, height = rect_size(row.get("rect"))
        return (window_state(row) == WINDOW_READY, width * height)

    return max(pool, key=rank)


def foreground_window_title() -> str:
    if sys.platform != "win32":
        return ""
    import win32gui

    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def _enumerate(process_names: set[str], accept=None) -> list[dict]:
    """枚举属于指定进程的顶层窗口，连同可见性与最小化状态一起返回。

    这里刻意不按 ``IsWindowVisible`` 过滤：钉钉、QQ 收进托盘后主窗口的
    WS_VISIBLE 会被清掉，但窗口本身仍然存在且保留着正常尺寸。早先版本
    直接丢弃这类窗口，于是界面只能报告"未找到窗口，请启动并登录"，
    把用户引向完全错误的方向。现在把状态带上来，由上层如实说明。
    """
    if sys.platform != "win32":
        return []
    import psutil
    import win32gui
    import win32process

    rows: list[dict] = []

    def visit(hwnd, _):
        try:
            if win32gui.GetParent(hwnd):
                return
            class_name = win32gui.GetClassName(hwnd)
            if accept is not None and not accept(class_name):
                return
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            process = psutil.Process(pid)
            if process.name().lower() not in process_names:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            iconic = bool(win32gui.IsIconic(hwnd))
            if not iconic and (right - left <= 0 or bottom - top <= 0):
                return
            rows.append(
                {
                    "handle": hwnd,
                    "class_name": class_name,
                    "title": win32gui.GetWindowText(hwnd),
                    "rect": (left, top, right, bottom),
                    "visible": bool(win32gui.IsWindowVisible(hwnd)),
                    "iconic": iconic,
                    "process_epoch": str(process.create_time()),
                    "pid": pid,
                }
            )
        except (psutil.Error, OSError):
            return

    win32gui.EnumWindows(visit, None)
    return rows


def wechat_windows() -> list[dict]:
    # 不用标题白名单：当前微信 4.1 在部分 Windows 编码环境返回乱码，
    # 例如"微信"会变成不可匹配的替代字符，导致整个主窗口被过滤。
    return _enumerate(WECHAT_PROCESS_NAMES, accept=_looks_like_wechat_main_class)


def dingtalk_windows() -> list[dict]:
    """检测钉钉主窗口。

    钉钉新版本是 Electron，主窗体类名为 ``Chrome_WidgetWin_1``，
    旧实现把含 "Chrome" 的类名全部排除，等于把主窗口本身滤掉了。
    """
    # DingTalk creates many tooltip/image/dialog top-level windows. Keep the
    # actual client shells so a PDF preview or tooltip cannot be selected as
    # the chat window.
    main_classes = {"Qt51511QWindowIcon", "Chrome_WidgetWin_1", "Qt5QWindowIcon"}
    return _enumerate(DINGTALK_PROCESS_NAMES, accept=lambda cls: cls in main_classes)


def qq_windows() -> list[dict]:
    """检测 QQ 主窗口。

    兼容旧版 ``TXGuiFoundation`` 和 QQ NT 的 Chromium 外壳，
    不按类名过滤，交给 :func:`pick_main_window` 按尺寸判断。
    """
    return _enumerate(QQ_PROCESS_NAMES)
