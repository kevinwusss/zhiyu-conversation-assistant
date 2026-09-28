"""QQ / 钉钉的只读会话读取：严格限定在右侧聊天消息区域。

设计约束（来自真实误读事故）：

* 绝不整窗抓取文本。旧实现把整棵控件树里所有 TextControl 的 Name 当成聊天
  消息，结果左侧联系人列表、导航栏、按钮文字全部被当成"对方说的话"送进模型。
* 绝不使用"复制整个窗口"（Ctrl+A / Ctrl+C）作为默认方案。
* 读不到就如实返回状态，不允许用空消息冒充读取成功。

读取策略：先用 UIA 快照整棵树（只取只读属性，不做任何点击/输入），再用窗口
几何关系定位右侧聊天区域，只解析落在该区域矩形内的控件，并用左侧栏文本做
交叉排除。
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, replace
from hashlib import sha1

from app.core.models import ChatMessage
from .read_status import ReadResult, ReadStatus

# 遍历上限：防止异常界面导致卡死。真实 QQ/钉钉主窗口通常在 1000 节点以内。
MAX_NODES = 3000
MAX_DEPTH = 45

# 聊天区域至少要占窗口的这个比例，避免把小面板误判成消息区。
_MIN_REGION_WIDTH_RATIO = 0.30
_MIN_REGION_HEIGHT_RATIO = 0.25
# 聊天区域左边界必须在窗口这个比例之后，用于排除左侧会话列表。
_SIDEBAR_RATIO = 0.28

_CONTAINER_TYPES = {
    "ListControl",
    "DocumentControl",
    "PaneControl",
    "GroupControl",
    "TableControl",
    "ScrollViewerControl",
    "CustomControl",
}
_TEXT_TYPES = {"TextControl", "DocumentControl", "HyperlinkControl", "ImageControl"}

# 没有几何信息的控件统一用它表示，避免在各处写 (0, 0, 0, 0) 字面量。
_EMPTY_RECT = (0, 0, 0, 0)

# 明确属于界面框架的文字，任何情况下都不能当成聊天消息。
_CHROME_EXACT = {
    "发送", "发送(S)", "关闭", "最小化", "最大化", "还原", "设置", "搜索", "更多",
    "表情", "截图", "文件", "图片", "语音", "视频", "音视频通话", "发起群聊",
    "消息", "联系人", "通讯录", "动态", "工作台", "文档", "日历", "待办", "会议",
    "邮箱", "云盘", "收藏", "群聊", "好友", "我的", "首页", "全部", "未读",
    "输入消息", "请输入", "按Enter发送", "按 Enter 发送", "点击输入", "说点什么",
    "QQ", "TIM", "腾讯QQ", "钉钉", "DingTalk", "置顶", "免打扰", "标记已读",
    "撤回", "回复", "转发", "复制", "删除", "多选", "引用",
}
# 出现这些片段的文本几乎一定是界面提示而不是聊天内容。
_CHROME_SUBSTR = (
    "按Enter", "按 Enter", "点击输入", "输入消息", "条未读", "未读消息",
    "正在输入", "对方正在", "网络连接", "登录中", "加载中", "版本更新",
)

# 时间戳：12:30 / 昨天 12:30 / 2026/09/11 / 2026-09-11 12:30 / 上午10:05
_TIME_RE = re.compile(
    r"^(?:(?:昨天|今天|前天|星期[一二三四五六日天]|周[一二三四五六日天])\s*)?"
    r"(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*)?"
    r"(?:上午|下午|凌晨|晚上)?\s*"
    r"(?:\d{1,2}[:：]\d{2}(?:[:：]\d{2})?)?$"
)
# 纯符号 / 纯数字徽标（未读计数）
_BADGE_RE = re.compile(r"^[\d\s\+·•…\-—_|/\\]*$")


@dataclass(frozen=True)
class UiaNode:
    """控件树的只读快照；不持有 COM 对象，可安全跨线程传递和做单元测试。"""

    index: int
    parent: int
    depth: int
    control_type: str
    name: str
    automation_id: str = ""
    class_name: str = ""
    # (left, top, right, bottom)
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return (self.rect[0] + self.rect[2]) / 2


def _rect_of(control) -> tuple[int, int, int, int]:
    try:
        r = control.BoundingRectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return (0, 0, 0, 0)


def _attr(control, name: str) -> str:
    try:
        return str(getattr(control, name, "") or "")
    except Exception:
        return ""


def _snapshot_once(handle: int, max_nodes: int = MAX_NODES) -> tuple[list[UiaNode], str]:
    """抓一次控件树快照。

    返回 (nodes, error)。error 非空表示 UIA 不可用或被拒绝访问。
    不依赖 GetDescendants：部分 uiautomation 版本和部分控件没有该方法，
    这里统一用 GetChildren 做广度优先遍历。
    """
    if sys.platform != "win32":
        return [], "当前系统不是 Windows，无法使用 UI Automation。"
    try:
        import uiautomation as uia
    except Exception as exc:
        return [], f"未安装 uiautomation：{exc}"

    try:
        root = uia.ControlFromHandle(handle)
    except Exception as exc:
        return [], f"无法通过句柄获取控件：{exc}"
    if root is None:
        return [], "无法通过句柄获取控件，窗口可能已关闭。"

    nodes: list[UiaNode] = [
        UiaNode(
            index=0,
            parent=-1,
            depth=0,
            control_type=_attr(root, "ControlTypeName"),
            name=_attr(root, "Name"),
            automation_id=_attr(root, "AutomationId"),
            class_name=_attr(root, "ClassName"),
            rect=_rect_of(root),
        )
    ]
    queue: list[tuple[int, object, int]] = [(0, root, 0)]
    while queue and len(nodes) < max_nodes:
        parent_index, control, depth = queue.pop(0)
        if depth >= MAX_DEPTH:
            continue
        try:
            children = control.GetChildren()
        except Exception:
            children = []
        for child in children:
            if len(nodes) >= max_nodes:
                break
            node = UiaNode(
                index=len(nodes),
                parent=parent_index,
                depth=depth + 1,
                control_type=_attr(child, "ControlTypeName"),
                name=_attr(child, "Name"),
                automation_id=_attr(child, "AutomationId"),
                class_name=_attr(child, "ClassName"),
                rect=_rect_of(child),
            )
            nodes.append(node)
            queue.append((node.index, child, depth + 1))
    return nodes, ""


# Chromium/Electron 外壳（QQ NT、新版钉钉）的无障碍树是**按需**构建的：没有
# 无障碍客户端在访问时，Chromium 会把渲染进程的无障碍树整个关掉，连
# ``Chrome_RenderWidgetHostHWND`` 这个子窗口都一起销毁。此时从外面看，QQ 主
# 窗口只剩 9 个空 PaneControl，一条文字都没有——真机实测就是这个数字。
#
# 唤醒方式就是持续访问它（``ControlFromHandle`` + 遍历本身就是访问），
# Chromium 收到请求后会重新建树，但不是立刻，实测要几秒。所以这里的重试
# 既是"等"，也是"催"。
WARMUP_WAIT_SECONDS = 0.6
WARMUP_MAX_TRIES = 5


def _is_dormant(nodes: list[UiaNode]) -> bool:
    """这棵树是不是"壳还在、内容没建"的状态。

    判据只有一条：全树没有任何文字。真实 IM 主窗口哪怕一条消息都没有，
    导航栏、按钮、输入提示也一定带文字；全树零文本只可能是无障碍树没建起来。

    刻意**不**按节点数判定。节点少不等于休眠——一棵只有三个节点但带文字的树
    是读得到内容的，按数量拦下来就会把"没找到聊天区"误报成"接口休眠"。
    根矩形为空是另一回事（客户端根本没暴露几何），催也没用，不在这里揽。
    """
    if not nodes or nodes[0].rect == _EMPTY_RECT:
        return False
    return not any(n.control_type in _TEXT_TYPES and n.name.strip() for n in nodes)


def snapshot_tree(handle: int, max_nodes: int = MAX_NODES) -> tuple[list[UiaNode], str]:
    """抓控件树；树还没建起来时等待并重试（重试同时也在催客户端建树）。"""
    nodes, error = _snapshot_once(handle, max_nodes)
    tries = 1
    while not error and _is_dormant(nodes) and tries < WARMUP_MAX_TRIES:
        time.sleep(WARMUP_WAIT_SECONDS)
        nodes, error = _snapshot_once(handle, max_nodes)
        tries += 1
    return nodes, error


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    """inner 的中心点是否落在 outer 内；比严格包含更能容忍 1px 误差。"""
    if inner == (0, 0, 0, 0) or outer == (0, 0, 0, 0):
        return False
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def is_ui_chrome(text: str) -> bool:
    """判断一段文字是否属于界面框架而非聊天内容。"""
    value = (text or "").strip()
    if not value or len(value) > 2000:
        return True
    if value in _CHROME_EXACT:
        return True
    if _BADGE_RE.match(value):
        return True
    for fragment in _CHROME_SUBSTR:
        if fragment in value:
            return True
    return False


def looks_like_time(text: str) -> bool:
    value = (text or "").strip()
    if not value or len(value) > 24:
        return False
    if not any(ch.isdigit() for ch in value):
        return False
    return bool(_TIME_RE.match(value))


def intersect_rect(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """两个矩形的交集；不相交或有一个是空矩形时返回空矩形。"""
    if a == _EMPTY_RECT or b == _EMPTY_RECT:
        return _EMPTY_RECT
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return _EMPTY_RECT
    return (left, top, right, bottom)


def visible_rect(
    by_index: dict[int, UiaNode],
    node: UiaNode,
    window_rect: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """节点在屏幕上真正可见的那块矩形：自身矩形与所有祖先矩形求交。

    为什么不能直接用 ``node.rect``：Chromium 外壳（QQ NT）的滚动画布把**整个
    内容高度**写进 BoundingRectangle。真机上 QQ 的消息画布是
    ``[1401, -3296, 2382, 1298]``——顶部在屏幕外 3296 像素处，因为上面还有
    几十屏已经滚过去的历史消息。拿这个矩形当聊天区会连着两个后果：
    区域顶部跑到窗口外面，"标题在区域上方"的判断永远不成立，联系人识别整个
    失效；滚出可视范围的历史消息也会被当成"屏幕上看得见的内容"读进来。

    与所有祖先求交之后，得到的就是被各级裁剪框切出来的可视口，
    上面那个画布会被收敛回 ``[1401, 391, 2382, 849]``。
    """
    rect = node.rect
    index = node.parent
    seen = {node.index}
    while index in by_index and index not in seen:
        seen.add(index)
        parent = by_index[index]
        if parent.rect != _EMPTY_RECT:
            rect = intersect_rect(rect, parent.rect)
            if rect == _EMPTY_RECT:
                return _EMPTY_RECT
        index = parent.parent
    return intersect_rect(rect, window_rect)


def find_message_region(nodes: list[UiaNode], window_rect: tuple[int, int, int, int]):
    """定位右侧聊天消息区域。

    判据：位于窗口右侧、足够大、内部含有多条文本。优先选择文本条数最多的
    容器；条数相同时选**层级更深**的那个——同样包住全部消息的容器一定是嵌套
    关系，越深的越贴近消息列表本身，而不是整个右栏。

    返回的节点保留原容器的 ``index``（下游按 index 找子节点分行要用），但
    ``rect`` 换成 :func:`visible_rect` 裁剪后的可视矩形。
    """
    win_w = max(1, window_rect[2] - window_rect[0])
    win_h = max(1, window_rect[3] - window_rect[1])
    sidebar_edge = window_rect[0] + win_w * _SIDEBAR_RATIO
    by_index = {n.index: n for n in nodes}

    best = None
    best_rect = _EMPTY_RECT
    best_key = None
    for node in nodes:
        # DingTalk's native Qt shell exposes the chat pane as a WindowControl
        # whose automation id is stable, while its message rows are custom
        # painted. Treat that pane as a candidate even though WindowControl is
        # intentionally excluded from the generic container list.
        qt_chat_pane = (
            node.control_type == "WindowControl"
            and "qt_chat_navigable_content_widget" in node.automation_id
        )
        if node.control_type not in _CONTAINER_TYPES and not qt_chat_pane:
            continue
        if node.rect == _EMPTY_RECT:
            continue
        vis = visible_rect(by_index, node, window_rect)
        if vis == _EMPTY_RECT:
            continue
        # 几何判据一律针对可视矩形，滚动画布那种超出屏幕的尺寸不算数。
        if vis[0] < sidebar_edge:
            continue
        if vis[2] - vis[0] < win_w * _MIN_REGION_WIDTH_RATIO:
            continue
        if vis[3] - vis[1] < win_h * _MIN_REGION_HEIGHT_RATIO:
            continue
        # 文本归属仍按原始矩形算：这是"谁包含谁"的结构关系，
        # 换成裁剪矩形会把贴着可视口边缘的半截消息判成不属于本容器。
        texts = [
            other
            for other in nodes
            if other.index != node.index
            and other.control_type in _TEXT_TYPES
            and other.name.strip()
            and not is_ui_chrome(other.name)
            and _contains(node.rect, other.rect)
        ]
        if qt_chat_pane and len(texts) < 2:
            # Message text is commonly absent from UIA in this pane; the
            # bounded pane itself is still suitable for the OCR fallback.
            key = (0, node.depth)
            if best_key is None or key > best_key:
                best_key = key
                best = node
                best_rect = vis
            continue
        if len(texts) < 2:
            continue
        key = (len(texts), node.depth)
        if best_key is None or key > best_key:
            best_key = key
            best = node
            best_rect = vis
    if best is not None:
        return best if best.rect == best_rect else replace(best, rect=best_rect)
    # 兜底：Electron / 自绘外壳常常没有可识别的容器控件，但文本节点本身仍带
    # 正确坐标。这里用"右侧非界面文本的包围盒"当聊天区域，几何约束一点没放松：
    # 左侧栏、顶部标题带、底部输入区的文本都不参与包围盒。
    return text_bbox_region(nodes, window_rect)


# 包围盒兜底至少需要这么多条右侧正文，避免拿一两条标题凑数。
MIN_BBOX_TEXTS = 3
# 顶部标题带与底部输入区的比例，这两段里的文本不是聊天内容。
HEADER_BAND_RATIO = 0.10
INPUT_BAND_RATIO = 0.86


def text_bbox_region(
    nodes: list[UiaNode], window_rect: tuple[int, int, int, int]
) -> UiaNode | None:
    """没有容器控件时，用右侧正文文本的包围盒充当聊天区域。"""
    win_w = max(1, window_rect[2] - window_rect[0])
    win_h = max(1, window_rect[3] - window_rect[1])
    sidebar_edge = window_rect[0] + win_w * _SIDEBAR_RATIO
    header_edge = window_rect[1] + win_h * HEADER_BAND_RATIO
    input_edge = window_rect[1] + win_h * INPUT_BAND_RATIO

    picked = [
        n
        for n in nodes
        if n.control_type in _TEXT_TYPES
        and n.name.strip()
        and not is_ui_chrome(n.name)
        and n.rect != (0, 0, 0, 0)
        and n.rect[0] >= sidebar_edge
        and n.rect[1] >= header_edge
        and n.rect[3] <= input_edge
    ]
    if len(picked) < MIN_BBOX_TEXTS:
        return None
    left = min(n.rect[0] for n in picked)
    top = min(n.rect[1] for n in picked)
    right = max(n.rect[2] for n in picked)
    bottom = max(n.rect[3] for n in picked)
    if right - left < win_w * _MIN_REGION_WIDTH_RATIO:
        return None
    return UiaNode(
        index=-1,
        parent=-1,
        depth=0,
        control_type="SyntheticRegion",
        name="",
        rect=(left, top, right, bottom),
    )


def estimate_region(
    window_rect: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """按窗口几何估算右侧聊天区矩形，只用于限定 OCR 范围。

    这不是"整窗 OCR"：左侧会话列表、顶部标题栏和底部输入区都被排除在外。
    估算结果只能作为待核验来源，绝不会被当成结构化读取成功。
    """
    if not window_rect or len(window_rect) != 4:
        return None
    win_w = window_rect[2] - window_rect[0]
    win_h = window_rect[3] - window_rect[1]
    if win_w < 400 or win_h < 300:
        return None
    left = int(window_rect[0] + win_w * _SIDEBAR_RATIO)
    top = int(window_rect[1] + win_h * HEADER_BAND_RATIO)
    right = int(window_rect[2])
    bottom = int(window_rect[1] + win_h * INPUT_BAND_RATIO)
    if right - left <= 0 or bottom - top <= 0:
        return None
    return (left, top, right, bottom)


def _sidebar_texts(nodes: list[UiaNode], window_rect: tuple[int, int, int, int]) -> set[str]:
    """左侧会话列表里的文字；聊天区若出现同样文字，多半是误读。"""
    win_w = max(1, window_rect[2] - window_rect[0])
    edge = window_rect[0] + win_w * _SIDEBAR_RATIO
    out = set()
    for node in nodes:
        if node.control_type not in _TEXT_TYPES:
            continue
        text = node.name.strip()
        if not text:
            continue
        if node.rect != (0, 0, 0, 0) and node.rect[2] <= edge:
            out.add(text)
    return out


# 会话标题不会长在按钮里。QQ NT 标题栏上那些图标按钮带单字符 Name（真机上
# 是 node 52，一个落在 ButtonControl 里的字形），几何上完全符合"在聊天区正
# 上方、靠右"，不排掉就会被当成联系人名。
_NON_TITLE_ANCESTORS = {
    "ButtonControl",
    "ToolBarControl",
    "MenuControl",
    "MenuItemControl",
    "MenuBarControl",
    "TabControl",
    "TabItemControl",
    "ListItemControl",
}
# 一两个字符的"标题"要么是图标字形，要么是徽标，不足以当联系人名。
MIN_CONTACT_LENGTH = 2


def _has_ancestor_type(by_index: dict[int, UiaNode], node: UiaNode, types: set[str]) -> bool:
    index = node.parent
    seen = {node.index}
    while index in by_index and index not in seen:
        seen.add(index)
        parent = by_index[index]
        if parent.control_type in types:
            return True
        index = parent.parent
    return False


def contact_from_region(
    nodes: list[UiaNode],
    region: UiaNode | None,
    window_rect: tuple[int, int, int, int],
) -> str:
    """聊天区正上方的标题栏通常就是当前联系人/群名。"""
    if region is None:
        return ""
    win_w = max(1, window_rect[2] - window_rect[0])
    edge = window_rect[0] + win_w * _SIDEBAR_RATIO
    by_index = {n.index: n for n in nodes}
    candidates = []
    for node in nodes:
        if node.control_type not in {"TextControl", "HeaderControl", "DocumentControl"}:
            continue
        text = node.name.strip()
        if not text or is_ui_chrome(text) or looks_like_time(text):
            continue
        if len(text) < MIN_CONTACT_LENGTH or len(text) > 60:
            continue
        if node.rect == (0, 0, 0, 0):
            continue
        # 必须在右侧、且在消息区域上方
        if node.rect[0] < edge:
            continue
        if node.rect[3] > region.rect[1]:
            continue
        if _has_ancestor_type(by_index, node, _NON_TITLE_ANCESTORS):
            continue
        candidates.append(node)
    if not candidates:
        return ""
    # 取最靠近消息区顶部的那一个
    candidates.sort(key=lambda n: (-n.rect[3], n.rect[0]))
    return candidates[0].name.strip()


def _row_containers(nodes: list[UiaNode], region: UiaNode) -> list[UiaNode]:
    """区域容器下真正的"消息行"。

    直接子节点通常就是一行一条消息。但中间常常夹着只有一个孩子的包装容器
    （QQ NT 的可视口 → 滚动画布之间就隔了两层），所以这里逐层往下穿，
    直到遇到有多个孩子的那一层为止。
    """
    if region.index < 0:
        return []
    children_of: dict[int, list[UiaNode]] = {}
    for n in nodes:
        if n.index != n.parent:
            children_of.setdefault(n.parent, []).append(n)
    rows = children_of.get(region.index, [])
    guard = 0
    while len(rows) == 1 and guard < MAX_DEPTH:
        deeper = children_of.get(rows[0].index, [])
        if not deeper:
            break
        rows = deeper
        guard += 1
    return rows


def _by_position(node: UiaNode) -> tuple[int, int]:
    return (node.rect[1], node.rect[0])


def _bucket_texts(
    containers: list[UiaNode], texts: list[UiaNode]
) -> list[tuple[UiaNode | None, list[UiaNode]]]:
    """把文本装进各自所属的行容器；装不进去的每条自成一组。"""
    groups: list[tuple[UiaNode | None, list[UiaNode]]] = []
    used: set[int] = set()
    for container in sorted(containers, key=_by_position):
        bucket = [
            t for t in texts if t.index not in used and _contains(container.rect, t.rect)
        ]
        for t in bucket:
            used.add(t.index)
        if bucket:
            groups.append((container, sorted(bucket, key=_by_position)))
    leftover = [t for t in texts if t.index not in used]
    for t in sorted(leftover, key=_by_position):
        groups.append((None, [t]))
    return groups


def _group_rows(
    nodes: list[UiaNode], region: UiaNode
) -> list[tuple[UiaNode | None, list[UiaNode]]]:
    """把聊天区里的文本按消息行分组，返回 (行容器, 该行的文本)。

    行容器可能为 None（Electron 自绘外壳常常没有可识别的行结构），
    此时发送方只能退回到文字对齐来判断。
    """
    texts = [
        n
        for n in nodes
        if n.control_type in _TEXT_TYPES
        and n.name.strip()
        and n.index != region.index
        and _contains(region.rect, n.rect)
    ]
    if not texts:
        return []

    rows = [n for n in _row_containers(nodes, region) if n.rect != _EMPTY_RECT]
    if rows:
        return _bucket_texts(rows, texts)

    items = [
        n
        for n in nodes
        if n.control_type in {"ListItemControl", "DataItemControl"}
        and n.index != region.index
        and _contains(region.rect, n.rect)
    ]
    if items:
        return _bucket_texts(items, texts)
    return [(None, [t]) for t in sorted(texts, key=_by_position)]


# 头像的几何特征：贴着行一侧的小方块。上下限覆盖 QQ(48×49)、微信、钉钉常见尺寸。
_AVATAR_MIN_SIDE = 20
_AVATAR_MAX_SIDE = 140
# 长宽比下限：低于这个值就是长条，不是头像。
_AVATAR_ASPECT = 0.65
# 头像中心偏离行中心的最小比例；居中的小方块（表情、状态图标）不算头像。
_AVATAR_EDGE_RATIO = 0.5


def _row_avatar(nodes: list[UiaNode], row: UiaNode) -> UiaNode | None:
    """行内最靠边的小方块控件——就是头像。

    为什么不靠文字左右对齐判断发送方：QQ NT 的气泡文本无论谁发的，
    BoundingRectangle 左边缘都一样（真机上统一是 x=1506），左右对齐这个信息
    在无障碍树里根本不存在。只有头像还留在真实位置上——自己发的贴右
    （x≈2304），对方发的贴左（x≈1431）。
    """
    half = max(1.0, (row.rect[2] - row.rect[0]) / 2)
    row_center = (row.rect[0] + row.rect[2]) / 2
    best = None
    best_offset = 0.0
    for n in nodes:
        if n.index == row.index or n.rect == _EMPTY_RECT:
            continue
        if n.control_type in _TEXT_TYPES:
            continue
        w, h = n.width, n.height
        if not (_AVATAR_MIN_SIDE <= w <= _AVATAR_MAX_SIDE):
            continue
        if not (_AVATAR_MIN_SIDE <= h <= _AVATAR_MAX_SIDE):
            continue
        if min(w, h) / max(w, h) < _AVATAR_ASPECT:
            continue
        if not _contains(row.rect, n.rect):
            continue
        offset = abs(n.center_x - row_center)
        if offset > best_offset:
            best_offset = offset
            best = n
    if best is None or best_offset / half < _AVATAR_EDGE_RATIO:
        return None
    return best


def _row_text_hints(nodes: list[UiaNode], row: UiaNode, used: set[int]) -> list[str]:
    """同一行里没能进入正文的文本（多半是被裁到可视区外的昵称和时间）。

    这些文字**不会**变成消息内容——它们在屏幕上看不见，读进来就是伪造。
    唯一的用途是把整行汇总文本前面粘着的昵称和时间切掉。
    """
    return [
        n.name.strip()
        for n in nodes
        if n.index not in used
        and n.index != row.index
        and n.control_type in _TEXT_TYPES
        and n.name.strip()
        and _contains(row.rect, n.rect)
    ]


def _dedupe_texts(
    body_nodes: list[UiaNode], hints: list[str]
) -> tuple[str, str, list[str]]:
    """整理同一行的文本，返回 (发送者名, 时间, 正文各段)。

    QQ NT 对一条消息会同时挂出整行汇总（"昵称 时间 正文"）和拆开的各段，
    三条都在控件树里。原样拼起来内容会重复一遍；只留汇总又会把昵称和时间
    混进正文。所以：

    1. 被同行更长文本完整包含的段落丢掉，只留汇总；
    2. 再用同行各段（包括看不见、只能当线索用的那些）把汇总的前缀一层层剥掉，
       剥下来的是时间就当时间戳，像名字就当发送者名。
    """
    values = [n.name.strip() for n in body_nodes if n.name.strip()]
    if not values:
        return "", "", []
    kept = [v for v in values if not any(o != v and len(o) > len(v) and v in o for o in values)]
    if not kept:
        kept = list(values)

    prefixes = sorted({*values, *hints}, key=len, reverse=True)
    sender = ""
    timestamp = ""
    out: list[str] = []
    for value in kept:
        rest = value
        stripped = True
        while stripped:
            stripped = False
            for cand in prefixes:
                # cand != rest 保证每轮都在变短，循环一定终止。
                if cand and cand != rest and rest.startswith(cand):
                    rest = rest[len(cand):].strip()
                    if looks_like_time(cand):
                        timestamp = timestamp or cand
                    elif not sender and _looks_like_sender(cand):
                        sender = cand
                    stripped = True
                    break
        out.append(rest or value)
    return sender, timestamp, out


def _looks_like_sender(text: str) -> bool:
    value = text.strip()
    if not value or len(value) > 24:
        return False
    if looks_like_time(value):
        return False
    # 句末标点说明这是一句话而不是名字
    return not any(value.endswith(p) for p in "。！？，、；：.!?,;")


def extract_messages(
    nodes: list[UiaNode],
    region: UiaNode,
    window_rect: tuple[int, int, int, int],
    contact: str,
    limit: int = 50,
) -> list[ChatMessage]:
    """只解析聊天区域内的控件，生成消息列表。"""
    sidebar = _sidebar_texts(nodes, window_rect)
    region_center = (region.rect[0] + region.rect[2]) / 2
    groups = _group_rows(nodes, region)

    messages: list[ChatMessage] = []
    counter: dict[str, int] = {}
    for row, group in groups:
        timestamp = ""
        body_nodes = []
        # 从本行剔掉的文字（时间、界面文案、会话标题）。它们不是正文，但仍是
        # 整行汇总文本前面粘着的那一截，留着当剥前缀的依据——不留的话，
        # "张三 11:14 明天开会"里的昵称和时间就没有任何办法切下来。
        dropped: list[str] = []
        for node in group:
            text = node.name.strip()
            if not text:
                continue
            if looks_like_time(text):
                timestamp = timestamp or text
                dropped.append(text)
                continue
            # 会话标题（当前联系人/群名）有时也落在区域里，它不是聊天内容。
            if is_ui_chrome(text) or text in sidebar or text == contact.strip():
                dropped.append(text)
                continue
            body_nodes.append(node)
        if not body_nodes:
            continue

        hints = (
            _row_text_hints(nodes, row, {n.index for n in group}) if row is not None else []
        )
        name_hint, hinted_time, values = _dedupe_texts(body_nodes, hints + dropped)
        timestamp = timestamp or hinted_time
        if not name_hint and len(values) >= 2 and _looks_like_sender(values[0]):
            name_hint = values[0]
            values = values[1:]
        content = "\n".join(v for v in values if v)
        if not content:
            continue

        message_type = "text"
        if all(n.control_type == "ImageControl" for n in body_nodes):
            message_type = "attachment"

        # 发送方判定按可靠性排序：头像位置 > 昵称 > 文字对齐。
        avatar = _row_avatar(nodes, row) if row is not None else None
        if avatar is not None:
            mine = avatar.center_x > region_center
        else:
            mine = body_nodes[0].center_x > region_center
        if mine:
            sender = "我"
        else:
            sender = name_hint or contact or "对方"

        key = f"{sender}|{content}"
        counter[key] = counter.get(key, 0) + 1
        source = sha1(f"{key}|{timestamp}|{counter[key]}".encode("utf-8")).hexdigest()[:16]
        messages.append(ChatMessage(sender, content, source, message_type, timestamp))

    return messages[-limit:]


def read_conversation(
    handle: int,
    platform: str,
    contact_hint: str = "",
    limit: int = 50,
    verify: bool = True,
) -> ReadResult:
    """读取当前会话；任何一步失败都返回明确状态而不是空成功。"""
    nodes, error = snapshot_tree(handle)
    if error:
        return ReadResult(status=ReadStatus.UIA_ACCESS_DENIED, detail=error)
    if not nodes:
        return ReadResult(
            status=ReadStatus.UIA_ACCESS_DENIED,
            detail="控件树为空，可能被权限或渲染方式屏蔽。",
        )
    window_rect = nodes[0].rect
    if window_rect == _EMPTY_RECT:
        return ReadResult(
            status=ReadStatus.UNSUPPORTED_CLIENT_VERSION,
            detail="无法获取窗口矩形，无法区分聊天区域与侧栏。",
        )

    if _is_dormant(nodes):
        # 这不是"读不到聊天区"，而是客户端的无障碍树整个还没建起来。
        # 说清楚区别，用户才知道该再点一次而不是去改设置。
        return ReadResult(
            status=ReadStatus.UIA_ACCESS_DENIED,
            detail=(
                f"{platform}的无障碍接口处于休眠状态（控件树只有 {len(nodes)} 个空节点，"
                "没有任何文字）。QQ / 钉钉这类 Chromium 客户端在无人访问时会关闭无障碍树，"
                "重新建好需要几秒。请把窗口切到目标聊天后，过几秒再点一次读取。"
            ),
            diagnostics={"nodes": len(nodes), "dormant": True},
        )

    region = find_message_region(nodes, window_rect)
    contact = contact_hint or contact_from_region(nodes, region, window_rect)
    diagnostics = {
        "nodes": len(nodes),
        "window_rect": window_rect,
        "region_found": region is not None,
    }

    if region is None:
        # Qt/CEF versions of DingTalk often expose the chat pane but omit all
        # message text from UIA. Keep the bounded right-side pane as an OCR
        # region so the adapter can use the restricted OCR fallback.
        estimated = estimate_region(window_rect)
        return ReadResult(
            status=(
                ReadStatus.CONTACT_DETECTED if contact else ReadStatus.MESSAGE_REGION_NOT_FOUND
            ),
            contact=contact,
            detail="控件树里没有符合右侧聊天区域几何特征的容器，可能使用了 WebView 或自绘渲染。",
            region=estimated,
            diagnostics=diagnostics,
        )

    messages = extract_messages(nodes, region, window_rect, contact, limit)
    if not messages:
        return ReadResult(
            status=ReadStatus.NO_MESSAGES,
            contact=contact,
            detail="已定位聊天区域，但区域内没有可识别为消息的文本。",
            region=region.rect,
            diagnostics=diagnostics,
        )

    if verify:
        again_nodes, again_error = snapshot_tree(handle)
        if again_error or not again_nodes:
            return ReadResult(
                status=ReadStatus.UNCERTAIN,
                contact=contact,
                detail="二次校验读取失败，本次结果已丢弃。",
                region=region.rect,
                diagnostics=diagnostics,
            )
        again_region = find_message_region(again_nodes, again_nodes[0].rect)
        again_messages = (
            extract_messages(again_nodes, again_region, again_nodes[0].rect, contact, limit)
            if again_region is not None
            else []
        )
        first = [(m.sender, m.content) for m in messages]
        second = [(m.sender, m.content) for m in again_messages]
        if first != second:
            return ReadResult(
                status=ReadStatus.UNCERTAIN,
                contact=contact,
                detail="连续两次读取结果不一致，可能正在滚动或渲染，已丢弃以避免误读。",
                region=region.rect,
                diagnostics=diagnostics,
            )

    return ReadResult(
        status=ReadStatus.MESSAGES_READ,
        messages=tuple(messages),
        contact=contact,
        detail="",
        region=region.rect,
        diagnostics=diagnostics,
    )


def control_tree_summary(handle: int, max_nodes: int = 400) -> list[dict]:
    """导出脱敏控件结构用于诊断：只保留类型和几何，不含任何文本内容。"""
    nodes, error = snapshot_tree(handle, max_nodes=max_nodes)
    if error:
        return [{"error": error}]
    return [
        {
            # index / parent 是还原树形结构用的，排查"区域选错了"必须有它们：
            # 只看 depth 无法判断两个同深度节点是不是同一条分支。
            "index": n.index,
            "parent": n.parent,
            "depth": n.depth,
            "type": n.control_type,
            "class": n.class_name,
            "automation_id": n.automation_id,
            "rect": list(n.rect),
            "has_name": bool(n.name.strip()),
            "name_length": len(n.name.strip()),
            "name": "[内容已隐藏]" if n.name.strip() else "",
        }
        for n in nodes
    ]
