"""QQ / 钉钉的受控写入：激活窗口 → 定位输入框 → 确认焦点 → 粘贴 → 回车。

为什么不用 UIA 直接 SetValue：QQ NT 和新版钉钉的输入框是 Chromium 自绘的
可编辑区域，多数情况下不暴露 ValuePattern，SetValue 会静默失败。所以这里
走"点击输入框 + 剪贴板粘贴 + 模拟回车"这条通用路径。

安全约束（写入永远比读取危险，这些检查一条都不能省）：

* 粘贴前必须确认焦点控件是可编辑控件，且位于窗口下方的输入带内；
  确认不了就直接放弃，不做任何按键，绝不盲发。
* 全程不使用 Ctrl+A 之外的选择操作，且 Ctrl+A 只在焦点确认之后执行。
* 用完恢复用户原来的剪贴板内容和鼠标位置。
* 回车只在调用方明确要求发送时才按；"填入"不会按回车。
"""

from __future__ import annotations

import sys
import time

from .desktop_uia import (
    UiaNode,
    _CONTAINER_TYPES,
    _SIDEBAR_RATIO,
    find_message_region,
)
from .windows import ensure_dpi_awareness

# 输入框判定：必须落在窗口下方这个比例之后。
INPUT_TOP_RATIO = 0.58
_STRICT_EDITABLE = {"EditControl", "DocumentControl", "ComboBoxControl"}

# 焦点落到这些控件上就一定不是输入框——点歪了必须立刻放弃，绝不盲按。
# 用黑名单而不是白名单：QQ NT 的输入框是 Chromium 的 contenteditable，
# 报出来的类型随版本变（GroupControl / CustomControl / DocumentControl 都见过），
# 白名单会把能用的情况一起挡掉，等于永远发不出去。
_NON_EDITABLE_FOCUS = {
    "ButtonControl",
    "SplitButtonControl",
    "MenuItemControl",
    "MenuBarControl",
    "ListItemControl",
    "TreeItemControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "TabItemControl",
    "HyperlinkControl",
    "ScrollBarControl",
    "SliderControl",
    "ImageControl",
    "TitleBarControl",
}

# 输入框容器里不该出现的东西。工具栏、发送按钮那一排都带这些控件，
# 真正的编辑区是输入带里**唯一一块没有按钮**的大容器。
_INPUT_BLOCKERS = {
    "ButtonControl",
    "SplitButtonControl",
    "ToolBarControl",
    "MenuItemControl",
    "ListItemControl",
    "TabItemControl",
}
# 输入框最小尺寸：宽度占窗口这个比例以上，高度不少于这么多像素。
_INPUT_MIN_WIDTH_RATIO = 0.20
_INPUT_MIN_HEIGHT = 24

# 几何兜底用的输入带上下沿。下沿刻意留出窗口底部 7%：那里是发送按钮那一排，
# 点下去就是直接触发发送，比点不中还危险。
_ESTIMATE_TOP_RATIO = 0.80
_ESTIMATE_BOTTOM_RATIO = 0.93

# 各步骤之间的等待，给客户端渲染和输入法留时间。
STEP_DELAY = 0.12
ACTIVATE_DELAY = 0.35
PASTE_DELAY = 0.25


class WriteError(RuntimeError):
    """写入链路的失败；调用方负责翻译成用户可读的错误。"""


def _input_band_top(
    nodes: list[UiaNode], window_rect: tuple[int, int, int, int]
) -> float:
    """输入带的上沿：消息区下边缘和固定比例取更靠下的那个。

    消息区下边缘是更准的锚点——输入框一定在消息区下面。拿不到时才退回比例。
    """
    win_h = max(1, window_rect[3] - window_rect[1])
    by_ratio = window_rect[1] + win_h * INPUT_TOP_RATIO
    region = find_message_region(nodes, window_rect)
    if region is None:
        return by_ratio
    return max(by_ratio, float(region.rect[3]))


def _descendant_types(nodes: list[UiaNode], root_index: int) -> set[str]:
    children: dict[int, list[UiaNode]] = {}
    for n in nodes:
        if n.index != n.parent:
            children.setdefault(n.parent, []).append(n)
    out: set[str] = set()
    stack = list(children.get(root_index, []))
    while stack:
        node = stack.pop()
        out.add(node.control_type)
        stack.extend(children.get(node.index, []))
    return out


def find_input_control(
    nodes: list[UiaNode], window_rect: tuple[int, int, int, int]
) -> UiaNode | None:
    """定位右下方的输入框控件。

    两轮：先找标准可编辑控件（微信、旧版 QQ 走这条）；找不到再按结构找——
    输入带里最大的、内部一个按钮都没有的容器。QQ NT 只能靠后者，它整棵树里
    唯一的 EditControl 是左边的搜索框，聊天输入框是 Chromium 的
    contenteditable，报成 GroupControl。

    两轮都先要求控件中心落在窗口矩形内。客户端的控件树里会混进不属于这个
    窗口的东西（钉钉真机上就出现过一个整体位于窗口下方的 EditControl），
    照着它的中心点下去点的是别的程序的窗口——这正是"内容粘进别人聊天框"
    那类事故的开头。窗口外的候选一律不要。
    """
    win_w = max(1, window_rect[2] - window_rect[0])
    sidebar_edge = window_rect[0] + win_w * _SIDEBAR_RATIO
    top_edge = _input_band_top(nodes, window_rect)
    min_width = win_w * _INPUT_MIN_WIDTH_RATIO

    best = None
    for node in nodes:
        if node.control_type not in _STRICT_EDITABLE:
            continue
        if node.rect == (0, 0, 0, 0):
            continue
        if not _inside(node.rect, window_rect):
            continue
        if node.rect[0] < sidebar_edge:
            continue
        if node.rect[3] < top_edge:
            continue
        if node.width < min_width or node.height < 12:
            continue
        # 越靠下越像输入框；同高度取更宽的那个
        key = (node.rect[3], node.width)
        if best is None or key > (best.rect[3], best.width):
            best = node
    if best is not None:
        return best

    fallback = None
    fallback_key = None
    for node in nodes:
        if node.control_type not in _CONTAINER_TYPES:
            continue
        if node.rect == (0, 0, 0, 0):
            continue
        if not _inside(node.rect, window_rect):
            continue
        if node.rect[0] < sidebar_edge:
            continue
        # 必须整块落在输入带里，跨在消息区和输入区之间的父容器不算。
        if node.rect[1] < top_edge:
            continue
        if node.width < min_width or node.height < _INPUT_MIN_HEIGHT:
            continue
        if _descendant_types(nodes, node.index) & _INPUT_BLOCKERS:
            continue
        # 面积最大的那块就是编辑区；面积相同取层级最浅的，保证结果稳定。
        key = (node.area, -node.depth)
        if fallback_key is None or key > fallback_key:
            fallback_key = key
            fallback = node
    return fallback


def estimate_input_rect(
    window_rect: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """没有可识别输入控件时，按几何估算输入带矩形。"""
    if not window_rect or len(window_rect) != 4:
        return None
    win_w = window_rect[2] - window_rect[0]
    win_h = window_rect[3] - window_rect[1]
    if win_w < 400 or win_h < 300:
        return None
    left = int(window_rect[0] + win_w * _SIDEBAR_RATIO)
    top = int(window_rect[1] + win_h * _ESTIMATE_TOP_RATIO)
    right = int(window_rect[2] - win_w * 0.04)
    bottom = int(window_rect[1] + win_h * _ESTIMATE_BOTTOM_RATIO)
    if right - left <= 0 or bottom - top <= 0:
        return None
    return (left, top, right, bottom)


def rect_center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def _inside(rect, outer) -> bool:
    """焦点控件的中心必须落在目标窗口内。

    不加这条，前台切换失败时焦点可能还在别的窗口上，而那个窗口恰好也有
    "右下方的可编辑区域"——于是校验通过，内容粘到了别人的聊天框里。
    """
    if not rect or rect == (0, 0, 0, 0) or not outer:
        return False
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _still_foreground(handle: int) -> bool:
    try:
        import win32gui

        return win32gui.GetForegroundWindow() == handle
    except Exception:
        return False


def in_input_band(rect, window_rect) -> bool:
    """焦点控件是否落在窗口右下方的输入带内。"""
    if not rect or rect == (0, 0, 0, 0):
        return False
    win_w = max(1, window_rect[2] - window_rect[0])
    win_h = max(1, window_rect[3] - window_rect[1])
    center_x = (rect[0] + rect[2]) / 2
    center_y = (rect[1] + rect[3]) / 2
    if center_x < window_rect[0] + win_w * _SIDEBAR_RATIO:
        return False
    return center_y >= window_rect[1] + win_h * INPUT_TOP_RATIO


# ---- Win32 原语 -----------------------------------------------------------


def _require_win32():
    if sys.platform != "win32":
        raise WriteError("当前系统不是 Windows，无法执行写入。")


def activate_window(handle: int) -> bool:
    """把目标窗口切到前台；返回是否确实切过去了。"""
    _require_win32()
    import win32con
    import win32gui

    try:
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
    except Exception:
        pass
    for _ in range(3):
        try:
            # 先发一次 ALT，绕开 Windows 的前台窗口锁定策略。
            import win32api

            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(handle)
        except Exception:
            pass
        time.sleep(ACTIVATE_DELAY)
        try:
            if win32gui.GetForegroundWindow() == handle:
                return True
        except Exception:
            return False
    return False


def window_rect(handle: int) -> tuple[int, int, int, int]:
    _require_win32()
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(handle)
    return (left, top, right, bottom)


def get_clipboard_text():
    """读取剪贴板文本；没有文本内容时返回 None。"""
    _require_win32()
    import win32clipboard
    import win32con

    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.08)
            continue
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    return None


def set_clipboard_text(text: str) -> bool:
    _require_win32()
    import win32clipboard
    import win32con

    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.08)
            continue
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            return True
        except Exception:
            return False
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    return False


def _tap(vk: int) -> None:
    import win32api
    import win32con

    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def _combo(modifier: int, vk: int) -> None:
    import win32api
    import win32con

    win32api.keybd_event(modifier, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(modifier, 0, win32con.KEYEVENTF_KEYUP, 0)


def click(point: tuple[int, int], restore_cursor: bool = True) -> None:
    """点击屏幕坐标，并把鼠标放回原位。"""
    _require_win32()
    import win32api
    import win32con

    origin = None
    try:
        origin = win32api.GetCursorPos()
    except Exception:
        origin = None
    win32api.SetCursorPos(point)
    time.sleep(STEP_DELAY)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(STEP_DELAY)
    if restore_cursor and origin:
        try:
            win32api.SetCursorPos(origin)
        except Exception:
            pass


def focused_control_info() -> tuple[str, tuple[int, int, int, int]]:
    """当前焦点控件的类型和矩形；拿不到时返回空类型。"""
    if sys.platform != "win32":
        return "", (0, 0, 0, 0)
    try:
        import uiautomation as uia

        control = uia.GetFocusedControl()
        if control is None:
            return "", (0, 0, 0, 0)
        rect = control.BoundingRectangle
        return (
            str(getattr(control, "ControlTypeName", "") or ""),
            (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
        )
    except Exception:
        return "", (0, 0, 0, 0)


# ---- 组合动作 -------------------------------------------------------------


def deliver_text(
    handle: int,
    text: str,
    input_rect: tuple[int, int, int, int],
    submit: bool,
) -> None:
    """把文本送进输入框；submit 为 True 时再按回车发送。

    任一前置校验不通过就抛 WriteError，且保证此前没有按下任何按键。
    """
    _require_win32()
    if not text.strip():
        raise WriteError("回复内容为空，已取消。")
    if not input_rect:
        raise WriteError("没有定位到输入框矩形，已取消，未做任何输入。")
    # win32 的窗口矩形和 UIA 的控件矩形必须在同一套坐标系里才能比较，
    # 否则在缩放屏上"焦点明明在输入框里"也会被判成不在。
    ensure_dpi_awareness()

    if not activate_window(handle):
        raise WriteError("未能把目标窗口切到前台，已取消，未做任何输入。")

    rect = window_rect(handle)
    click(rect_center(input_rect))
    time.sleep(STEP_DELAY)

    if not _still_foreground(handle):
        raise WriteError("点击后目标窗口已不在前台，已取消，未做任何输入。")

    control_type, focus_rect = focused_control_info()
    if not control_type:
        raise WriteError("无法确认焦点控件，已取消，未做任何输入。")
    if control_type in _NON_EDITABLE_FOCUS:
        raise WriteError(
            f"点击后焦点落在{control_type}上而不是输入框，已取消，未做任何输入。"
        )
    if not _inside(focus_rect, rect) or not in_input_band(focus_rect, rect):
        raise WriteError(
            "点击后焦点不在聊天输入框所在区域内（焦点控件："
            f"{control_type}），已取消，未做任何输入。"
        )

    original = get_clipboard_text()
    if not set_clipboard_text(text):
        raise WriteError("无法写入剪贴板，已取消，未做任何输入。")
    try:
        import win32con

        # 先全选再粘贴，避免和输入框里的残留草稿拼在一起。
        _combo(win32con.VK_CONTROL, ord("A"))
        time.sleep(STEP_DELAY)
        _combo(win32con.VK_CONTROL, ord("V"))
        time.sleep(PASTE_DELAY)
        if submit:
            _tap(win32con.VK_RETURN)
            time.sleep(PASTE_DELAY)
    finally:
        if original is not None:
            set_clipboard_text(original)
