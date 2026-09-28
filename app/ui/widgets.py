"""界面自定义控件：平台滑块、聊天气泡视图、可折叠面板。

这些控件只负责呈现，不碰适配器、数据库和模型；业务逻辑仍在 actions.py。
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from app.ui.theme import colors


class SegmentedControl(QFrame):
    """微信 / QQ / 钉钉三选一滑块。

    用互斥的 checkable 按钮实现，键盘方向键和读屏都能正常工作；
    纯自绘的滑块虽然更好看，但会丢掉这些无障碍能力。
    """

    changed = Signal(str)

    def __init__(self, options: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for index, name in enumerate(options):
            button = QPushButton(name)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.StrongFocus)
            layout.addWidget(button, 1)
            self._group.addButton(button, index)
            self._buttons[name] = button
        self._group.idClicked.connect(self._clicked)
        self._options = options
        if options:
            self._buttons[options[0]].setChecked(True)

    def _clicked(self, index: int) -> None:
        self.changed.emit(self._options[index])

    def current(self) -> str:
        for name, button in self._buttons.items():
            if button.isChecked():
                return name
        return self._options[0] if self._options else ""

    def set_current(self, name: str) -> None:
        """只改选中态，不发信号；用于代码侧同步，避免回调递归。"""
        button = self._buttons.get(name)
        if button is not None:
            button.setChecked(True)


class CollapsibleSection(QFrame):
    """标题可点击折叠的容器；折叠状态下完全不占空间。"""

    toggled = Signal(bool)

    def __init__(self, title: str, parent=None, expanded: bool = False):
        super().__init__(parent)
        self.setObjectName("collapsible")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QToolButton()
        self.header.setObjectName("collapseHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._title = title
        self.header.setText(title)
        self.header.clicked.connect(self.set_expanded)
        outer.addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName("collapseBody")
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 8, 0, 0)
        self.content.setSpacing(8)
        self.body.setVisible(expanded)
        outer.addWidget(self.body)

    def set_title(self, title: str) -> None:
        self._title = title
        self.header.setText(title)

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.body.setVisible(expanded)
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        # 用 isHidden 而不是 isVisible：主窗口还没显示时子控件一律 isVisible()==False，
        # 那样就分不清"折叠了"和"整个窗口还没画出来"。
        return not self.body.isHidden()


def bubble_width(text: str, metrics, available: int) -> int:
    """按实测文字宽度算气泡占正文宽度的百分比。

    QTextBrowser 的表格不会收缩到内容宽度，必须给一个显式百分比；按字数估
    算在中英文混排下误差很大（"在吗"能撑出半屏），所以这里用 QFontMetrics
    真正量一遍最长的那一行。
    """
    lines = text.split("\n") or [""]
    longest = max(metrics.horizontalAdvance(line) for line in lines)
    # 26px 是气泡左右内边距，多留 8px 免得刚好卡在换行临界点上。
    percent = round((longest + 34) / max(available, 1) * 100)
    return max(12, min(74, percent))


class ChatView(QTextBrowser):
    """左右分栏的聊天记录：自己在右，对话人在左。"""

    MARGIN = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatView")
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.document().setDocumentMargin(self.MARGIN)
        self._messages: list = []
        self._placeholder = ""

    def set_placeholder(self, text: str) -> None:
        self._messages = []
        self._placeholder = text
        self.setHtml(
            f'<p style="color:{colors()["muted"]}; margin-top:40px; text-align:center">'
            f"{html.escape(text)}</p>"
        )

    def _available_width(self) -> int:
        """正文可用像素宽；窗口还没布局出来时给一个够用的兜底值。"""
        width = self.viewport().width() - 2 * self.MARGIN
        return width if width > 200 else 720

    def render_messages(self, messages) -> None:
        """messages 为字典序列，需含 sender / content，可选 timestamp。"""
        self._messages = list(messages)
        self._render()

    def repaint_theme(self) -> None:
        """换主题后重绘：气泡颜色写死在 HTML 里，QSS 管不到。"""
        if self._messages:
            self._render()
        else:
            self.set_placeholder(self._placeholder)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 气泡宽度是按当前视口算出来的百分比，窗口一变就得重新量。
        if self._messages:
            self._render()

    def _render(self) -> None:
        # 每次都取当前配色，而不是缓存一份：换主题后气泡必须跟着变。
        palette = colors()
        available = self._available_width()
        metrics = self.fontMetrics()
        blocks = []
        for message in self._messages:
            sender = str(message.get("sender") or "")
            content = str(message.get("content") or "")
            timestamp = str(message.get("timestamp") or "")
            if not content.strip():
                continue
            width = bubble_width(content, metrics, available)
            blocks.append(self._bubble(sender, content, timestamp, palette, width))
        if not blocks:
            self.set_placeholder("这里会显示读取或导入的聊天记录。")
            return
        self.setHtml("".join(blocks))
        self._scroll_to_bottom()
        # setHtml 之后文档布局还可能再调整一次（图片、字体回退），那时 maximum()
        # 会变大，只滚一次就会停在倒数第二条上，所以事件循环空下来时再滚一次。
        # 带 context 的重载：控件被销毁后回调自动作废，不会在关窗时炸出来。
        QTimer.singleShot(0, self, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _bubble(self, sender: str, content: str, timestamp: str, colors: dict, width: int) -> str:
        mine = sender == "我"
        system = sender == "系统"
        body = html.escape(content).replace("\n", "<br>")
        meta = html.escape(f"{sender}  {timestamp}".strip())

        if system:
            return (
                f'<p align="center" style="margin:10px 0; font-size:12px; '
                f'color:{colors["muted"]}">{body}</p>'
            )

        if mine:
            background, color, align = colors["accent"], "#FFFFFF", "right"
            spacer, cells = 100 - width, "right"
        else:
            background, color, align = colors["bubble"], colors["text"], "left"
            spacer, cells = 100 - width, "left"

        bubble = (
            f'<td width="{width}%" bgcolor="{background}" '
            f'style="padding:10px 13px; color:{color}">'
            f'<span style="color:{color}">{body}</span></td>'
        )
        blank = f'<td width="{spacer}%"></td>'
        row = blank + bubble if cells == "right" else bubble + blank
        return (
            f'<p align="{align}" style="margin:2px 0 0 0; font-size:11px; '
            f'color:{colors["muted"]}">{meta}</p>'
            f'<table width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin-bottom:12px"><tr>{row}</tr></table>'
        )


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sidebarSection")
    return label
