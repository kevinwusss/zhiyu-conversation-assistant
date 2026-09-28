"""写入路径里纯几何部分的离线测试。

只测"输入框在哪"这一段——它是整条写入链路里唯一会算错位置的地方，
而且不需要真实窗口就能验证。真正的按键、剪贴板、焦点确认都不在这里跑。
"""

from __future__ import annotations

import unittest

from app.adapters.desktop_send import (
    estimate_input_rect,
    find_input_control,
    in_input_band,
    rect_center,
)
from app.adapters.desktop_uia import UiaNode

WINDOW = (0, 0, 1000, 700)


def node(index, parent, depth, ctype, name, rect):
    return UiaNode(
        index=index,
        parent=parent,
        depth=depth,
        control_type=ctype,
        name=name,
        automation_id="",
        class_name="",
        rect=rect,
    )


def build_qq_tree() -> list[UiaNode]:
    """QQ NT 形状：整棵树里唯一的 EditControl 是左边的搜索框。

    聊天输入框是 Chromium 的 contenteditable，报成 GroupControl；底部还有一条
    带发送按钮的工具行——真机上估算矩形一度正好落在那条按钮行上，
    点下去就是直接触发发送，所以这两块必须能分清。
    """
    return [
        node(0, -1, 0, "WindowControl", "", WINDOW),
        # 左侧：搜索框（唯一的标准可编辑控件）+ 会话列表
        node(1, 0, 1, "EditControl", "搜索", (10, 20, 270, 50)),
        node(2, 0, 1, "ListControl", "", (0, 60, 280, 700)),
        node(3, 2, 2, "TextControl", "李四", (10, 80, 270, 110)),
        # 右侧消息区
        node(4, 0, 1, "PaneControl", "", (280, 60, 1000, 560)),
        node(5, 4, 2, "TextControl", "明天上午的评审改到十点", (300, 100, 700, 140)),
        node(6, 4, 2, "TextControl", "收到，我改一下日程", (600, 160, 980, 200)),
        # 输入区：可编辑区本体（内部只有占位提示，没有任何按钮）
        node(7, 0, 1, "GroupControl", "", (280, 580, 1000, 660)),
        node(8, 7, 2, "TextControl", "按Enter发送", (290, 590, 500, 610)),
        # 输入区下方的按钮行
        node(9, 0, 1, "PaneControl", "", (280, 660, 1000, 700)),
        node(10, 9, 2, "ButtonControl", "发送", (900, 665, 980, 695)),
    ]


class TestFindInputControl(unittest.TestCase):
    def setUp(self):
        self.nodes = build_qq_tree()

    def test_structural_fallback_picks_editable_area(self):
        found = find_input_control(self.nodes, WINDOW)
        self.assertIsNotNone(found)
        self.assertEqual(found.index, 7)

    def test_button_row_is_rejected(self):
        # 按钮行比编辑区更靠下，只按"越靠下越像输入框"就会选错。
        found = find_input_control(self.nodes, WINDOW)
        self.assertNotEqual(found.index, 9)

    def test_sidebar_search_box_is_never_picked(self):
        found = find_input_control(self.nodes, WINDOW)
        self.assertNotEqual(found.index, 1)

    def test_strict_editable_wins_when_present(self):
        # 微信 / 旧版 QQ 的形状：输入带里有真正的 EditControl，优先用它。
        nodes = self.nodes + [
            node(11, 0, 1, "EditControl", "", (280, 580, 1000, 655)),
        ]
        found = find_input_control(nodes, WINDOW)
        self.assertEqual(found.index, 11)

    def test_no_candidate_returns_none(self):
        nodes = [n for n in self.nodes if n.index not in {1, 7}]
        self.assertIsNone(find_input_control(nodes, WINDOW))

    def test_control_outside_the_window_is_never_picked(self):
        """窗口外的可编辑控件一律不要——照它点下去点的是别的程序。

        钉钉真机上就有这么一个：控件树里挂着一个 EditControl，整块位于
        主窗口下边缘之下，中心点落在另一个应用的窗口里。它比窗口内的任何
        候选都"更靠下"，不排除就一定被选中。
        """
        stray = node(12, 0, 1, "EditControl", "", (400, 760, 980, 820))
        found = find_input_control(self.nodes + [stray], WINDOW)
        self.assertEqual(found.index, 7)
        # 结构兜底那一轮也要拦住同样的东西。
        container = node(13, 0, 1, "GroupControl", "", (400, 760, 980, 830))
        pruned = [n for n in self.nodes if n.index != 7] + [container]
        self.assertIsNone(find_input_control(pruned, WINDOW))


class TestEstimateInputRect(unittest.TestCase):
    def test_estimate_center_lands_in_editor_not_send_button(self):
        nodes = build_qq_tree()
        rect = estimate_input_rect(WINDOW)
        self.assertIsNotNone(rect)
        cx, cy = rect_center(rect)
        editor = next(n for n in nodes if n.index == 7)
        button_row = next(n for n in nodes if n.index == 9)
        self.assertTrue(editor.rect[1] <= cy <= editor.rect[3], (cx, cy))
        self.assertFalse(button_row.rect[1] <= cy <= button_row.rect[3], (cx, cy))

    def test_estimate_excludes_sidebar(self):
        rect = estimate_input_rect(WINDOW)
        self.assertGreaterEqual(rect[0], 280)

    def test_estimate_rejects_tiny_window(self):
        self.assertIsNone(estimate_input_rect((0, 0, 320, 240)))

    def test_in_input_band(self):
        # 第二个参数是**窗口**矩形：写入时用它复核焦点控件落在窗口下半部。
        self.assertTrue(in_input_band((300, 590, 900, 640), WINDOW))
        # 消息区里的控件不算输入带
        self.assertFalse(in_input_band((300, 100, 900, 140), WINDOW))
        # 左侧搜索框虽然靠上，但即使挪到下方也因为在侧栏里被排除
        self.assertFalse(in_input_band((10, 590, 270, 620), WINDOW))


if __name__ == "__main__":
    unittest.main()
