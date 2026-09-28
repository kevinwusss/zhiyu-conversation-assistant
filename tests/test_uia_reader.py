"""区域感知只读读取器的离线测试。

全部用构造的控件树，不依赖真实 QQ / 钉钉，也不包含任何真实聊天内容。
重点验证：左侧联系人列表、导航栏文字绝不会变成 ChatMessage。
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from app.adapters import desktop_uia as du
from app.adapters.desktop_uia import (
    UiaNode,
    contact_from_region,
    control_tree_summary,
    extract_messages,
    find_message_region,
    is_ui_chrome,
    looks_like_time,
    read_conversation,
    snapshot_tree,
)
from app.adapters.read_status import ReadStatus

WINDOW = (0, 0, 1000, 700)


def node(index, parent, depth, ctype, name, rect, cls="", aid=""):
    return UiaNode(
        index=index,
        parent=parent,
        depth=depth,
        control_type=ctype,
        name=name,
        automation_id=aid,
        class_name=cls,
        rect=rect,
    )


def build_tree() -> list[UiaNode]:
    """典型双栏 IM 布局：左侧会话列表 + 右侧聊天区。"""
    return [
        node(0, -1, 0, "WindowControl", "", WINDOW),
        # 左侧会话列表
        node(1, 0, 1, "ListControl", "", (0, 60, 280, 700)),
        node(2, 1, 2, "TextControl", "李四", (10, 80, 270, 110)),
        node(3, 1, 2, "TextControl", "王五", (10, 120, 270, 150)),
        # 右侧聊天区标题栏
        node(4, 0, 1, "TextControl", "张三", (300, 10, 500, 50)),
        # 右侧消息区域
        node(5, 0, 1, "ListControl", "", (280, 60, 1000, 600)),
        node(6, 5, 2, "ListItemControl", "", (300, 100, 700, 140)),
        node(7, 6, 3, "TextControl", "晚点一起看下方案", (310, 105, 500, 135)),
        node(8, 5, 2, "ListItemControl", "", (600, 160, 980, 210)),
        node(9, 8, 3, "TextControl", "10:05", (610, 165, 660, 180)),
        node(10, 8, 3, "TextControl", "好的我稍后回复", (700, 185, 970, 205)),
        # 区域内的界面文字与侧栏重复文字，都必须被过滤
        node(11, 5, 2, "TextControl", "对方正在输入…", (300, 520, 500, 550)),
        node(12, 5, 2, "TextControl", "李四", (300, 560, 500, 590)),
        # 输入框与按钮在区域之外
        node(13, 0, 1, "EditControl", "", (280, 610, 1000, 700)),
        node(14, 0, 1, "ButtonControl", "发送", (900, 650, 980, 690)),
    ]


class TestFilters(unittest.TestCase):
    def test_ui_chrome(self):
        for text in ("发送", "设置", "通讯录", "工作台", "3", "···", "按Enter发送", "对方正在输入…"):
            self.assertTrue(is_ui_chrome(text), text)

    def test_not_ui_chrome(self):
        for text in ("晚点一起看下方案", "好的我稍后回复"):
            self.assertFalse(is_ui_chrome(text), text)

    def test_looks_like_time(self):
        for text in ("10:05", "昨天 12:30", "2026/09/11", "上午10:05", "2026-09-11 08:00"):
            self.assertTrue(looks_like_time(text), text)

    def test_not_time(self):
        for text in ("好的我稍后回复", "张三", ""):
            self.assertFalse(looks_like_time(text), text)


class TestRegionAndContact(unittest.TestCase):
    def setUp(self):
        self.nodes = build_tree()

    def test_region_is_right_side_list(self):
        region = find_message_region(self.nodes, WINDOW)
        self.assertIsNotNone(region)
        self.assertEqual(region.index, 5)

    def test_region_never_sidebar(self):
        region = find_message_region(self.nodes, WINDOW)
        self.assertGreaterEqual(region.rect[0], WINDOW[0] + (WINDOW[2] - WINDOW[0]) * 0.28)

    def test_contact_from_header(self):
        region = find_message_region(self.nodes, WINDOW)
        self.assertEqual(contact_from_region(self.nodes, region, WINDOW), "张三")

    def test_no_region_when_only_sidebar(self):
        nodes = [
            node(0, -1, 0, "WindowControl", "", WINDOW),
            node(1, 0, 1, "ListControl", "", (0, 60, 280, 700)),
            node(2, 1, 2, "TextControl", "李四", (10, 80, 270, 110)),
            node(3, 1, 2, "TextControl", "王五", (10, 120, 270, 150)),
        ]
        self.assertIsNone(find_message_region(nodes, WINDOW))


class TestExtractMessages(unittest.TestCase):
    def setUp(self):
        self.nodes = build_tree()
        self.region = find_message_region(self.nodes, WINDOW)
        self.messages = extract_messages(self.nodes, self.region, WINDOW, "张三")

    def test_message_count(self):
        self.assertEqual(len(self.messages), 2)

    def test_sender_by_bubble_alignment(self):
        self.assertEqual(self.messages[0].sender, "张三")
        self.assertEqual(self.messages[1].sender, "我")

    def test_content(self):
        self.assertEqual(self.messages[0].content, "晚点一起看下方案")
        self.assertEqual(self.messages[1].content, "好的我稍后回复")

    def test_timestamp_extracted(self):
        self.assertEqual(self.messages[1].timestamp, "10:05")

    def test_sidebar_contact_never_becomes_message(self):
        contents = [m.content for m in self.messages]
        self.assertNotIn("李四", contents)
        self.assertNotIn("王五", contents)

    def test_ui_chrome_never_becomes_message(self):
        contents = [m.content for m in self.messages]
        self.assertNotIn("发送", contents)
        self.assertNotIn("对方正在输入…", contents)

    def test_source_ids_unique(self):
        ids = [m.source_id for m in self.messages]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(ids))

    def test_empty_region_returns_no_messages(self):
        nodes = [
            node(0, -1, 0, "WindowControl", "", WINDOW),
            node(1, 0, 1, "ListControl", "", (280, 60, 1000, 600)),
            node(2, 1, 2, "TextControl", "发送", (300, 100, 400, 130)),
            node(3, 1, 2, "TextControl", "设置", (300, 140, 400, 170)),
        ]
        region = nodes[1]
        self.assertEqual(extract_messages(nodes, region, WINDOW, "张三"), [])


def build_qq_tree() -> list[UiaNode]:
    """QQ NT 的真实形状（按真机 data/probe-im.json 的结构等比缩到测试坐标系）。

    三个真机上踩过的坑都在这棵树里：

    1. 滚动画布的 BoundingRectangle 是**整段内容**的高度（这里 -3000..1200），
       远超屏幕；可视范围要靠祖先可视口去裁。
    2. 气泡文本的左边缘不分收发一律相同，判断发送方只能靠头像位置。
    3. 同一条消息同时挂着整行汇总（"昵称 时间 正文"）和拆开的各段。
    """
    return [
        node(0, -1, 0, "WindowControl", "", WINDOW),
        node(1, 0, 1, "TextControl", "张三", (300, 10, 500, 50)),  # 顶部标题栏
        node(2, 0, 1, "PaneControl", "", (280, 60, 1000, 560)),  # 可视口
        node(3, 2, 2, "PaneControl", "", (280, -3000, 1000, 1200)),  # 滚动画布
        # 第一行：对方发的，头像贴左
        node(4, 3, 3, "PaneControl", "", (280, 100, 1000, 200)),
        node(5, 4, 4, "ButtonControl", "", (290, 105, 330, 145)),
        node(6, 4, 4, "TextControl", "张三 11:14 明天上午的评审改到十点", (400, 105, 900, 190)),
        node(7, 4, 4, "TextControl", "张三", (400, 105, 460, 125)),
        node(8, 4, 4, "TextControl", "11:14", (860, 105, 900, 125)),
        node(9, 4, 4, "TextControl", "明天上午的评审改到十点", (420, 150, 880, 185)),
        # 第二行：自己发的，头像贴右
        node(10, 3, 3, "PaneControl", "", (280, 220, 1000, 320)),
        node(11, 10, 4, "ButtonControl", "", (950, 225, 990, 265)),
        node(12, 10, 4, "TextControl", "阿墨 11:15 收到，我改一下日程", (400, 225, 900, 310)),
        node(13, 10, 4, "TextControl", "阿墨", (400, 225, 460, 245)),
        node(14, 10, 4, "TextControl", "11:15", (860, 225, 900, 245)),
        node(15, 10, 4, "TextControl", "收到，我改一下日程", (420, 270, 880, 305)),
    ]


class TestQqShape(unittest.TestCase):
    """真机 QQ NT 回归：滚动画布裁剪、头像判发送方、汇总与分段去重。"""

    def setUp(self):
        self.nodes = build_qq_tree()
        self.region = find_message_region(self.nodes, WINDOW)
        self.messages = extract_messages(self.nodes, self.region, WINDOW, "张三")

    def test_region_is_the_scroll_canvas(self):
        # 选中的必须是画布本身，下游要按它的 index 找子节点分行。
        self.assertEqual(self.region.index, 3)

    def test_region_rect_is_clipped_to_viewport(self):
        # 这条是本轮的核心修复：不裁的话区域高 4200px，几何判据全部失效，
        # 界面会显示"读取成功"，实际读到的是整个滚动内容而非当前屏。
        self.assertEqual(self.region.rect, (280, 60, 1000, 560))

    def test_sender_from_avatar_side(self):
        self.assertEqual([m.sender for m in self.messages], ["张三", "我"])

    def test_content_deduplicated(self):
        self.assertEqual(
            [m.content for m in self.messages],
            ["明天上午的评审改到十点", "收到，我改一下日程"],
        )

    def test_timestamp_split_off(self):
        self.assertEqual([m.timestamp for m in self.messages], ["11:14", "11:15"])

    def test_nickname_never_becomes_content(self):
        contents = " ".join(m.content for m in self.messages)
        self.assertNotIn("张三", contents)
        self.assertNotIn("阿墨", contents)

    def test_own_nickname_normalised_to_self(self):
        # 自己发的那条汇总里写着昵称"阿墨"，但入库必须统一是"我"，
        # 否则历史里同一个人会出现两种身份。
        self.assertEqual(self.messages[1].sender, "我")


# ---- 假 UIA：只提供 GetChildren，验证不依赖 GetDescendants ----------------


class FakeRect:
    def __init__(self, rect):
        self.left, self.top, self.right, self.bottom = rect


class FakeControl:
    """刻意不提供 GetDescendants，模拟部分 uiautomation 版本。"""

    def __init__(self, ctype, name, rect, children=None, cls="", aid=""):
        self.ControlTypeName = ctype
        self.Name = name
        self.ClassName = cls
        self.AutomationId = aid
        self.BoundingRectangle = FakeRect(rect)
        self._children = children or []

    def GetChildren(self):
        return list(self._children)


def build_fake_root() -> FakeControl:
    sidebar = FakeControl(
        "ListControl",
        "",
        (0, 60, 280, 700),
        [
            FakeControl("TextControl", "李四", (10, 80, 270, 110)),
            FakeControl("TextControl", "王五", (10, 120, 270, 150)),
        ],
    )
    region = FakeControl(
        "ListControl",
        "",
        (280, 60, 1000, 600),
        [
            FakeControl(
                "ListItemControl",
                "",
                (300, 100, 700, 140),
                [FakeControl("TextControl", "晚点一起看下方案", (310, 105, 500, 135))],
            ),
            FakeControl(
                "ListItemControl",
                "",
                (600, 160, 980, 210),
                [
                    FakeControl("TextControl", "10:05", (610, 165, 660, 180)),
                    FakeControl("TextControl", "好的我稍后回复", (700, 185, 970, 205)),
                ],
            ),
        ],
    )
    return FakeControl(
        "WindowControl",
        "",
        WINDOW,
        [
            sidebar,
            FakeControl("TextControl", "张三", (300, 10, 500, 50)),
            region,
            FakeControl("ButtonControl", "发送", (900, 650, 980, 690)),
        ],
    )


class FakeUia:
    def __init__(self, root):
        self._root = root

    def ControlFromHandle(self, handle):
        return self._root


class UiaHarness:
    """把 desktop_uia 临时接到假的 uiautomation 上。"""

    def __init__(self, root):
        self.root = root
        self._patches = []

    def __enter__(self):
        self._patches = [
            patch.object(du.sys, "platform", "win32"),
            patch.dict(sys.modules, {"uiautomation": FakeUia(self.root)}),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestSnapshotTree(unittest.TestCase):
    def test_traverses_without_get_descendants(self):
        with UiaHarness(build_fake_root()):
            nodes, error = snapshot_tree(1234)
        self.assertEqual(error, "")
        self.assertEqual(nodes[0].control_type, "WindowControl")
        self.assertEqual(nodes[0].rect, WINDOW)
        names = {n.name for n in nodes}
        self.assertIn("好的我稍后回复", names)
        self.assertIn("李四", names)

    def test_max_nodes_respected(self):
        with UiaHarness(build_fake_root()):
            nodes, error = snapshot_tree(1234, max_nodes=3)
        self.assertEqual(error, "")
        self.assertLessEqual(len(nodes), 3)

    def test_control_tree_summary_hides_text(self):
        with UiaHarness(build_fake_root()):
            rows = control_tree_summary(1234)
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(row["name"], {"", "[内容已隐藏]"})
        dumped = repr(rows)
        self.assertNotIn("好的我稍后回复", dumped)
        self.assertNotIn("张三", dumped)


class TestReadConversation(unittest.TestCase):
    def test_reads_only_chat_region(self):
        with UiaHarness(build_fake_root()):
            result = read_conversation(1234, "QQ")
        self.assertEqual(result.status, ReadStatus.MESSAGES_READ)
        self.assertTrue(result.trusted)
        self.assertEqual(result.contact, "张三")
        self.assertEqual([m.content for m in result.messages],
                         ["晚点一起看下方案", "好的我稍后回复"])
        self.assertNotIn("李四", [m.content for m in result.messages])

    def test_contact_hint_wins(self):
        with UiaHarness(build_fake_root()):
            result = read_conversation(1234, "QQ", contact_hint="项目群")
        self.assertEqual(result.contact, "项目群")

    def test_no_region_reports_stage(self):
        root = FakeControl(
            "WindowControl",
            "",
            WINDOW,
            [
                FakeControl(
                    "ListControl",
                    "",
                    (0, 60, 280, 700),
                    [FakeControl("TextControl", "李四", (10, 80, 270, 110))],
                )
            ],
        )
        with UiaHarness(root):
            result = read_conversation(1234, "钉钉")
        self.assertEqual(result.status, ReadStatus.MESSAGE_REGION_NOT_FOUND)
        self.assertEqual(result.messages, ())
        self.assertFalse(result.trusted)

    def test_region_without_messages(self):
        root = FakeControl(
            "WindowControl",
            "",
            WINDOW,
            [
                FakeControl("TextControl", "张三", (300, 10, 500, 50)),
                FakeControl(
                    "ListControl",
                    "",
                    (280, 60, 1000, 600),
                    [
                        FakeControl("TextControl", "发送", (300, 100, 500, 130)),
                        FakeControl("TextControl", "设置", (300, 140, 500, 170)),
                    ],
                ),
            ],
        )
        with UiaHarness(root):
            result = read_conversation(1234, "QQ")
        self.assertIn(result.status, {ReadStatus.NO_MESSAGES, ReadStatus.MESSAGE_REGION_NOT_FOUND})
        self.assertEqual(result.messages, ())

    def test_zero_rect_window_is_unsupported(self):
        root = FakeControl("WindowControl", "", (0, 0, 0, 0), [])
        with UiaHarness(root):
            result = read_conversation(1234, "QQ")
        self.assertEqual(result.status, ReadStatus.UNSUPPORTED_CLIENT_VERSION)

    def test_uia_unavailable_is_explicit(self):
        # sys.modules 里放 None 会让 import 抛 ImportError，模拟缺少 uiautomation。
        with patch.object(du.sys, "platform", "win32"), patch.dict(
            sys.modules, {"uiautomation": None}
        ):
            result = read_conversation(1234, "QQ")
        self.assertEqual(result.status, ReadStatus.UIA_ACCESS_DENIED)
        self.assertEqual(result.messages, ())
        self.assertTrue(result.detail)

    def test_non_windows_is_explicit(self):
        with patch.object(du.sys, "platform", "linux"):
            result = read_conversation(1234, "QQ")
        self.assertEqual(result.status, ReadStatus.UIA_ACCESS_DENIED)
        self.assertEqual(result.messages, ())

    def test_unstable_read_is_discarded(self):
        """两次读取不一致时必须返回 uncertain，而不是给出可能误读的结果。"""
        trees = [build_fake_root(), build_fake_root()]
        trees[1].GetChildren()[2].GetChildren()[0].GetChildren()[0].Name = "换了一句话"
        calls = {"n": 0}

        class Flaky:
            def ControlFromHandle(self, handle):
                root = trees[min(calls["n"], 1)]
                calls["n"] += 1
                return root

        with patch.object(du.sys, "platform", "win32"), patch.dict(
            sys.modules, {"uiautomation": Flaky()}
        ):
            result = read_conversation(1234, "QQ")
        self.assertEqual(result.status, ReadStatus.UNCERTAIN)
        self.assertEqual(result.messages, ())


if __name__ == "__main__":
    unittest.main()
