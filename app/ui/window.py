"""桌面布局；后台操作与事件协调位于 actions.py。

布局分三层，从左到右：

* 左侧导航栏——顶部是设置入口（☰）与平台滑块（微信 / QQ / 钉钉），
  往下依次是功能区和联系人列表。联系人随滑块切换，各平台互不混杂。
* 中间是聊天区——对方在左、自己在右的气泡，底部是输入框。
* 候选回复折叠在输入框正上方，展开后才占空间，默认不打扰视线。

设置不是一个平级的标签页，而是从 ☰ 切换出来的整页，避免把"日常对话"
和"改配置"这两种完全不同的操作摆在同一排标签里。
"""

from dataclasses import replace
from PySide6.QtCore import Qt, QSize, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from app.api import create_engine
from app.config import Settings
from app.adapters.registry import PLATFORMS, create_adapter
from app.automation.hotkey import EmergencyHotkey
from app.automation.window_manager import WindowFocusManager
from app.ui.pages import SettingsPage, ContactPage, MemoryPage
from app.ui.theme import apply_theme
from app.ui.actions import WindowActions
from app.ui.candidates import CandidateDelegate
from app.ui.widgets import ChatView, CollapsibleSection, SegmentedControl, section_label


def nav_button(text: str, tooltip: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("navItem")
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


class MainWindow(WindowActions, QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.engine = create_engine(self.settings)
        self.db = self.engine.db
        self.gate = self.engine.gate
        self.settings = replace(
            self.settings,
            base_url=self.db.setting("base_url", self.settings.base_url),
            model=self.db.setting("model", self.settings.model),
        )
        self.engine = create_engine(self.settings, self.gate)
        self.current_platform = PLATFORMS[0]
        self.adapter = create_adapter(self.current_platform)
        self.platform_connected = False
        self.focus_manager = WindowFocusManager()
        self.contact_id = None
        self.snapshot = None
        self.batch = None
        self.selected = 0
        self.busy = False
        self.epoch = 0
        self.baselines = {}
        self.auto_fresh = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle("知语 · Windows 智能对话助手")
        self.resize(1320, 840)
        self.setMinimumSize(1040, 700)

        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_sidebar())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_chat_page())
        self.stack.addWidget(self._build_settings_page())
        right_layout.addWidget(self.stack, 1)
        self.notice = QLabel(
            "从 ☰ 设置里配置模型。历史保存在本机，生成时会把选取的上下文发送到模型 API。"
        )
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        notice_wrap = QWidget()
        notice_layout = QVBoxLayout(notice_wrap)
        notice_layout.setContentsMargins(16, 8, 16, 12)
        notice_layout.addWidget(self.notice)
        right_layout.addWidget(notice_wrap)
        shell.addWidget(right, 1)
        self.setCentralWidget(root)

        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(lambda: self.read_chat(True))
        self.hotkey = EmergencyHotkey(self.stop)
        QApplication.instance().installNativeEventFilter(self.hotkey)
        if not self.hotkey.registered:
            self.notice.setText(
                "全局 Ctrl+Alt+Q 注册失败，可能已被占用；请使用右上角紧急停止按钮。"
            )
        self.incoming.textChanged.connect(self.input_changed)
        self.candidates.currentRowChanged.connect(self.choose)
        self.editor.textChanged.connect(self.update_actions)
        apply_theme(QApplication.instance(), self.db.setting("theme", "浅色"))
        self.refresh_contacts()
        self.refresh_details()
        self.update_actions()

    # ---- 左侧导航栏 ------------------------------------------------

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(272)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.menu_btn = QToolButton()
        self.menu_btn.setObjectName("hamburger")
        self.menu_btn.setText("☰")
        self.menu_btn.setCheckable(True)
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.menu_btn.setToolTip("设置与配置")
        self.menu_btn.setAccessibleName("打开设置")
        self.menu_btn.toggled.connect(self.toggle_settings)
        top.addWidget(self.menu_btn)
        brand = QLabel("知语")
        brand.setObjectName("brand")
        top.addWidget(brand)
        top.addStretch()
        layout.addLayout(top)

        self.platform_switch = SegmentedControl(PLATFORMS)
        self.platform_switch.changed.connect(self.switch_platform)
        layout.addWidget(self.platform_switch)

        layout.addWidget(section_label("功能"))
        self.read_btn = nav_button("读取当前会话", "把当前打开的聊天读进来")
        self.read_btn.clicked.connect(lambda: self.read_chat(False))
        layout.addWidget(self.read_btn)
        self.listen = QCheckBox("监听当前聊天")
        self.listen.setToolTip("每 5 秒重读一次，只处理建立基线之后的新消息")
        self.listen.toggled.connect(self.toggle_listen)
        layout.addWidget(self.listen)
        self.mode = QComboBox()
        self.mode.addItems(["建议模式", "辅助模式", "自动回复"])
        self.mode.setToolTip("建议：只生成；辅助：自动填入不发送；自动回复：确认后自动发送")
        self.mode.currentTextChanged.connect(self.mode_changed)
        self.contact_auto = QCheckBox("仅对当前联系人自动回复")
        self.contact_auto.toggled.connect(self.set_contact_auto_reply)
        layout.addWidget(self.contact_auto)
        layout.addWidget(self.mode)
        self.new_contact = nav_button("添加手工会话")
        self.new_contact.clicked.connect(self.add_contact)
        layout.addWidget(self.new_contact)
        self.import_btn = nav_button("导入聊天 JSON")
        self.import_btn.clicked.connect(self.import_chat)
        layout.addWidget(self.import_btn)

        self.contacts_label = section_label("联系人 · 微信")
        layout.addWidget(self.contacts_label)
        self.search = QLineEdit()
        self.search.setObjectName("sidebarSearch")
        self.search.setPlaceholderText("搜索联系人")
        self.search.textChanged.connect(self.filter_contacts)
        layout.addWidget(self.search)
        self.contacts = QListWidget()
        self.contacts.setObjectName("contactList")
        self.contacts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.contacts.setWordWrap(True)
        self.contacts.currentItemChanged.connect(self.select_contact)
        layout.addWidget(self.contacts, 1)

        self.platform_status = QLabel("微信：未连接  ·  自动回复：关闭")
        self.platform_status.setObjectName("muted")
        self.platform_status.setWordWrap(True)
        layout.addWidget(self.platform_status)
        return sidebar

    # ---- 中间聊天页 ------------------------------------------------

    def _build_chat_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("chatHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 16, 12)
        header_layout.setSpacing(10)
        self.contact_title = QLabel("先读取当前会话，或添加一个手工会话")
        self.contact_title.setObjectName("chatTitle")
        self.contact_title.setWordWrap(True)
        header_layout.addWidget(self.contact_title, 1)
        self.model_status = QLabel(
            "DeepSeek：已配置，待验证" if self.settings.api_key else "DeepSeek：未配置"
        )
        self.model_status.setObjectName("muted")
        header_layout.addWidget(self.model_status)
        self.pause = QPushButton("暂停 AI")
        self.pause.clicked.connect(self.toggle_pause)
        header_layout.addWidget(self.pause)
        stop = QPushButton("紧急停止")
        stop.setObjectName("stop")
        stop.setToolTip("全局快捷键 Ctrl+Alt+Q")
        stop.clicked.connect(self.stop)
        header_layout.addWidget(stop)
        layout.addWidget(header)

        self.chat = ChatView()
        self.chat.set_placeholder("这里会显示读取或导入的聊天记录。")
        # 候选面板展开时也要给聊天记录留下能看清上下文的高度。
        self.chat.setMinimumHeight(180)
        layout.addWidget(self.chat, 1)
        layout.addWidget(self._build_composer())
        return page

    def _build_composer(self) -> QWidget:
        composer = QWidget()
        composer.setObjectName("composer")
        layout = QVBoxLayout(composer)
        layout.setContentsMargins(20, 10, 20, 14)
        layout.setSpacing(8)

        # 候选回复折叠在输入框正上方：不展开就不占地方，展开后直接挑。
        self.suggestions = CollapsibleSection("AI 回复建议")
        self.incoming = QPlainTextEdit()
        self.incoming.setPlaceholderText("正在回复的消息：读取后自动填入，也可手工改写")
        self.incoming.setMaximumHeight(54)
        self.suggestions.content.addWidget(self.incoming)
        self.risk = QLabel("等待上下文")
        self.risk.setObjectName("muted")
        self.risk.setWordWrap(True)
        self.suggestions.content.addWidget(self.risk)
        self.candidates = QListWidget()
        self.candidates.setItemDelegate(CandidateDelegate(self.candidates))
        self.candidates.setAccessibleName("三个候选回复，使用上下键选择，选中后填入下方输入框")
        self.candidates.setTextElideMode(Qt.ElideRight)
        # 三条候选刚好不出滚动条；窗口矮的时候允许压到 110，免得和按钮挤在一起。
        self.candidates.setMinimumHeight(110)
        self.candidates.setMaximumHeight(172)
        self.candidates.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.suggestions.content.addWidget(self.candidates)
        self.ignore_btn = QPushButton("忽略本次建议")
        self.ignore_btn.clicked.connect(self.ignore)
        self.suggestions.content.addWidget(self.ignore_btn, 0, Qt.AlignLeft)
        layout.addWidget(self.suggestions)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("composerInput")
        self.editor.setPlaceholderText("在这里输入要发送的内容，或展开上方建议挑一个")
        self.editor.setMaximumHeight(96)
        row.addWidget(self.editor, 1)
        # 生成和发送必须在折叠面板之外，否则折起来就够不着了。
        buttons = QGridLayout()
        buttons.setSpacing(6)
        self.generate_btn = QPushButton("生成候选")
        self.generate_btn.clicked.connect(self.generate)
        buttons.addWidget(self.generate_btn, 0, 0)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("primary")
        self.send_btn.setToolTip("确认联系人和最终内容后发送")
        self.send_btn.clicked.connect(lambda: self.deliver("send"))
        buttons.addWidget(self.send_btn, 0, 1)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.clicked.connect(self.copy_reply)
        buttons.addWidget(self.copy_btn, 1, 0)
        self.fill_btn = QPushButton("填入")
        self.fill_btn.setToolTip("只写进对方客户端的输入框，不发送")
        self.fill_btn.clicked.connect(lambda: self.deliver("fill"))
        buttons.addWidget(self.fill_btn, 1, 1)
        row.addLayout(buttons)
        layout.addLayout(row)
        return composer

    # ---- 设置页（☰） ----------------------------------------------

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 8)
        layout.setSpacing(10)
        title = QLabel("设置与配置")
        title.setObjectName("title")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.settings_page = SettingsPage(self.db, self.settings)
        self.settings_page.applied.connect(self.apply_settings)
        self.settings_page.theme_changed.connect(self.change_theme)
        self.tabs.addTab(self.settings_page, "模型与安全")
        self.contact_page = ContactPage(self.db)
        self.contact_page.changed.connect(self.refresh_details)
        self.tabs.addTab(self.contact_page, "联系人 / Persona")
        self.memory_page = MemoryPage(self.db)
        self.memory_page.changed.connect(self.memory_changed)
        self.tabs.addTab(self.memory_page, "记忆")

        logs_page = QWidget()
        logs_layout = QVBoxLayout(logs_page)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        logs_layout.addWidget(self.log_view)
        log_refresh = QPushButton("刷新运行日志")
        log_refresh.clicked.connect(self.refresh_logs)
        logs_layout.addWidget(log_refresh)
        self.tree_btn = QPushButton("导出当前平台控件树（隐藏聊天文字）")
        self.tree_btn.clicked.connect(self.dump_tree)
        logs_layout.addWidget(self.tree_btn)
        self.tabs.addTab(logs_page, "日志 / 诊断")
        layout.addWidget(self.tabs, 1)

        back = QPushButton("返回对话")
        back.setMaximumWidth(140)
        back.clicked.connect(lambda: self.menu_btn.setChecked(False))
        layout.addWidget(back)
        return page

    # ---- 页面与平台切换 --------------------------------------------

    def toggle_settings(self, opened: bool) -> None:
        self.stack.setCurrentIndex(1 if opened else 0)
        if opened:
            self.refresh_logs()

    def change_theme(self, name: str) -> None:
        apply_theme(QApplication.instance(), name)
        # 聊天气泡是手写 HTML，拿不到 QSS，换主题后必须重绘。
        self.chat.repaint_theme()

    def switch_platform(self, platform: str):
        """切换 IM 平台：适配器、联系人列表和会话状态一起换掉。"""
        try:
            self.current_platform = platform
            self.adapter = create_adapter(platform)
            self.platform_connected = False
            # 代码里直接调用时滑块不会自己动，同步一次免得显示和实际平台对不上。
            self.platform_switch.set_current(platform)
            self.settings_page.set_platform(platform)
            # 监听的是上一个平台的窗口，换平台后必须停掉，否则会拿新适配器去轮询。
            if self.listen.isChecked():
                self.listen.setChecked(False)
            self.platform_status.setText(f"{platform}：未连接  ·  自动回复：关闭")
            self.snapshot = None
            self.batch = None
            self.contact_id = None
            self.contacts_label.setText(f"联系人 · {platform}")
            self.contact_title.setText(f"先读取{platform}，或添加一个手工会话")
            self.chat.set_placeholder(f"切换到 {platform}。读取当前会话后，聊天内容会显示在这里。")
            self.incoming.clear()
            self.clear_candidates()
            self.refresh_contacts()
            self.notice.setText(f"已切换到 {platform}。联系人列表只显示 {platform} 的会话。")
            self.update_actions()
        except Exception as exc:
            self.notice.setText(f"切换平台失败：{exc}")

    def candidate_size_hint(self) -> QSize:
        return QSize(220, 50)

    def closeEvent(self, event):
        if self.busy:
            self.stop()
            self.notice.setText("已停止后续操作；后台请求结束后请再次关闭窗口。")
            event.ignore()
            return
        self.timer.stop()
        self.hotkey.close()
        QApplication.instance().removeNativeEventFilter(self.hotkey)
        super().closeEvent(event)
