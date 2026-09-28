"""深浅色主题共享控件状态和尺寸。采用现代化、高级感的配色方案。"""

from PySide6.QtGui import QColor, QPalette

PALETTES = {
    "浅色": dict(
        bg="#FAFBFC",
        panel="#FFFFFF",
        text="#1A1F36",
        muted="#697386",
        line="#E3E8EE",
        accent="#5B5FC7",
        hover="#F6F7FB",
        selected="#E8E9F3",
        danger="#E74C3C",
        success="#27AE60",
        # 侧边栏比主区稍深一档，形成 Claude 那种"导航 / 内容"的层次。
        sidebar="#F3F4F8",
        # 对方消息气泡；自己的气泡直接用 accent。
        bubble="#EEF0F6",
    ),
    "深色": dict(
        bg="#0D1117",
        panel="#161B22",
        text="#E6EDF3",
        muted="#8B949E",
        line="#30363D",
        accent="#6E76E5",
        hover="#21262D",
        selected="#2D333B",
        danger="#F85149",
        success="#3FB950",
        sidebar="#12171F",
        bubble="#242B36",
    ),
}

# 最近一次应用的配色。聊天气泡是手写 HTML，拿不到 QSS，只能查这里。
_current = PALETTES["浅色"]


def colors(name: str | None = None) -> dict:
    """取配色表；不传名字时返回当前正在用的那一套。"""
    if name is None:
        return _current
    return PALETTES.get(name, PALETTES["浅色"])


def apply_theme(app, name: str) -> None:
    global _current
    c = PALETTES.get(name, PALETTES["浅色"])
    _current = c
    palette = QPalette()
    for role, color in (
        (QPalette.Window, c["bg"]),
        (QPalette.Base, c["panel"]),
        (QPalette.Text, c["text"]),
        (QPalette.WindowText, c["text"]),
        (QPalette.ButtonText, c["text"]),
        (QPalette.Button, c["panel"]),
        (QPalette.Highlight, c["accent"]),
        (QPalette.HighlightedText, "#FFFFFF"),
        (QPalette.PlaceholderText, c["muted"]),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
            font-size: 14px;
            color: {c["text"]};
        }}
        QMainWindow, QDialog {{ background: {c["bg"]}; }}
        QLabel#title {{
            font-size: 24px;
            font-weight: 600;
            letter-spacing: -0.5px;
            padding: 4px 0;
        }}
        QLabel#section {{
            font-size: 16px;
            font-weight: 600;
            letter-spacing: -0.2px;
            padding: 2px 0;
        }}
        QLabel#muted {{ color: {c["muted"]}; font-size: 13px; }}
        QLabel#notice {{
            background: {c["hover"]};
            border: 1px solid {c["line"]};
            border-radius: 10px;
            padding: 12px 16px;
            font-size: 13px;
        }}
        QFrame#panel {{
            background: {c["panel"]};
            border: 1px solid {c["line"]};
            border-radius: 14px;
        }}
        QPushButton {{
            background: {c["panel"]};
            border: 1px solid {c["line"]};
            border-radius: 9px;
            padding: 9px 14px;
            min-height: 22px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: {c["hover"]};
            border-color: {c["muted"]};
        }}
        QPushButton:pressed {{ background: {c["selected"]}; }}
        QPushButton:focus {{
            border: 2px solid {c["accent"]};
            outline: none;
        }}
        QPushButton:disabled {{
            color: {c["muted"]};
            background: {c["bg"]};
            border-color: {c["line"]};
        }}
        QPushButton#primary {{
            background: {c["accent"]};
            color: #FFFFFF;
            border: 1px solid {c["accent"]};
            font-weight: 600;
        }}
        QPushButton#primary:hover {{
            background: {c["accent"]};
            opacity: 0.9;
        }}
        QPushButton#primary:disabled {{
            background: {c["line"]};
            color: {c["muted"]};
        }}
        QPushButton#stop {{
            border: 1px solid {c.get("danger", "#E74C3C")};
            color: {c.get("danger", "#E74C3C")};
        }}
        QPushButton#stop:hover {{
            background: {c.get("danger", "#E74C3C")};
            color: #FFFFFF;
        }}
        QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QComboBox {{
            background: {c["panel"]};
            border: 1px solid {c["line"]};
            border-radius: 9px;
            padding: 10px;
            selection-background-color: {c["accent"]};
            selection-color: #FFFFFF;
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus {{
            border: 2px solid {c["accent"]};
            outline: none;
        }}
        QListWidget::item {{
            padding: 12px 8px;
            border-bottom: 1px solid {c["line"]};
        }}
        QListWidget::item:selected {{
            background: {c["selected"]};
            color: {c["text"]};
            border-radius: 8px;
        }}
        QTabWidget::pane {{ border: 0; }}
        QTabBar::tab {{
            padding: 11px 20px;
            color: {c["muted"]};
            border: 0;
            background: transparent;
            font-weight: 500;
        }}
        QTabBar::tab:hover {{ color: {c["text"]}; }}
        QTabBar::tab:selected {{
            color: {c["text"]};
            border-bottom: 3px solid {c["accent"]};
        }}
        QComboBox::drop-down {{ border: 0; width: 24px; }}
        QSplitter::handle {{ background: {c["bg"]}; width: 12px; }}
        QCheckBox {{ spacing: 10px; }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border: 2px solid {c["line"]};
            border-radius: 4px;
            background: {c["panel"]};
        }}
        QCheckBox::indicator:hover {{ border-color: {c["accent"]}; }}
        QCheckBox::indicator:checked {{
            background: {c["accent"]};
            border: 2px solid {c["accent"]};
        }}
        QToolTip {{
            background: {c["panel"]};
            color: {c["text"]};
            border: 1px solid {c["line"]};
            border-radius: 6px;
            padding: 6px 10px;
        }}

        /* ---- 左侧导航栏 ---- */
        QFrame#sidebar {{
            background: {c["sidebar"]};
            border-right: 1px solid {c["line"]};
        }}
        QLabel#brand {{ font-size: 16px; font-weight: 600; }}
        QLabel#sidebarSection {{
            color: {c["muted"]};
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.4px;
            padding: 2px 4px;
        }}
        QToolButton#hamburger {{
            border: 0;
            border-radius: 8px;
            padding: 5px 9px;
            font-size: 17px;
            color: {c["text"]};
        }}
        QToolButton#hamburger:hover {{ background: {c["selected"]}; }}
        QToolButton#hamburger:checked {{
            background: {c["accent"]};
            color: #FFFFFF;
        }}
        QPushButton#navItem {{
            background: transparent;
            border: 0;
            border-radius: 8px;
            padding: 8px 10px;
            text-align: left;
            font-weight: 500;
        }}
        QPushButton#navItem:hover {{ background: {c["selected"]}; }}
        QPushButton#navItem:checked {{
            background: {c["selected"]};
            font-weight: 600;
        }}
        QPushButton#navItem:disabled {{ color: {c["muted"]}; background: transparent; }}

        /* ---- 平台滑块 ---- */
        QFrame#segmented {{
            background: {c["selected"]};
            border: 1px solid {c["line"]};
            border-radius: 11px;
        }}
        QPushButton#segment {{
            background: transparent;
            border: 0;
            border-radius: 8px;
            padding: 7px 4px;
            font-weight: 500;
            color: {c["muted"]};
        }}
        QPushButton#segment:hover {{ color: {c["text"]}; }}
        QPushButton#segment:checked {{
            background: {c["panel"]};
            color: {c["text"]};
            font-weight: 600;
        }}
        QPushButton#segment:focus {{ border: 2px solid {c["accent"]}; }}

        /* ---- 联系人列表 ---- */
        QListWidget#contactList {{
            background: transparent;
            border: 0;
            padding: 0;
        }}
        QListWidget#contactList::item {{
            padding: 9px 10px;
            border: 0;
            border-radius: 9px;
            margin-bottom: 2px;
        }}
        QListWidget#contactList::item:hover {{ background: {c["selected"]}; }}
        QListWidget#contactList::item:selected {{
            background: {c["accent"]};
            color: #FFFFFF;
        }}
        QLineEdit#sidebarSearch {{
            background: {c["panel"]};
            border-radius: 9px;
            padding: 8px 10px;
        }}

        /* ---- 聊天区 ---- */
        QWidget#chatHeader {{
            background: {c["panel"]};
            border-bottom: 1px solid {c["line"]};
        }}
        QLabel#chatTitle {{ font-size: 16px; font-weight: 600; }}
        QTextBrowser#chatView {{
            background: {c["bg"]};
            border: 0;
            border-radius: 0;
            padding: 0;
        }}
        QWidget#composer {{
            background: {c["panel"]};
            border-top: 1px solid {c["line"]};
        }}
        QPlainTextEdit#composerInput {{
            background: {c["bg"]};
            border: 1px solid {c["line"]};
            border-radius: 12px;
            padding: 10px 12px;
        }}
        QPlainTextEdit#composerInput:focus {{ border: 2px solid {c["accent"]}; }}

        /* ---- 折叠的候选面板 ---- */
        QToolButton#collapseHeader {{
            border: 0;
            background: transparent;
            color: {c["muted"]};
            font-size: 13px;
            font-weight: 600;
            padding: 4px 2px;
            text-align: left;
        }}
        QToolButton#collapseHeader:hover {{ color: {c["text"]}; }}
        QWidget#collapseBody QListWidget {{
            background: {c["bg"]};
            border: 1px solid {c["line"]};
        }}
    """)
