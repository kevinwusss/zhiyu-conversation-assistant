"""设置、联系人和记忆页；字段均通过服务层读写。"""

import json
from dataclasses import replace
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QPlainTextEdit,
    QPushButton,
    QLabel,
    QMessageBox,
)
from app.memory import MemoryService
from app.persona import PersonaService


class SettingsPage(QWidget):
    applied = Signal(object)
    theme_changed = Signal(str)

    def __init__(self, db, settings):
        super().__init__()
        self.db, self.settings = db, settings
        layout = QVBoxLayout(self)
        title = QLabel("模型与偏好")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel(
            "聊天历史保存在本机；生成回复时，选取的聊天上下文、Persona 和记忆会发送到配置的模型 API。\nAPI Key 在本窗口输入后仅用于本次运行；长期配置请使用项目根目录 .env。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText(
            "已从 .env 加载" if settings.api_key else "输入 DeepSeek API Key"
        )
        self.url = QLineEdit(db.setting("base_url", settings.base_url))
        self.model = QLineEdit(db.setting("model", settings.model))
        self.theme = QComboBox()
        self.theme.addItems(["浅色", "深色"])
        self.theme.setCurrentText(db.setting("theme", "浅色"))
        self.rules = QPlainTextEdit(
            db.setting("prohibitions", "不承诺价格、付款或合同；不泄露私人资料")
        )
        self.rules.setMaximumHeight(130)
        for title, field in (
            ("API Key", self.key),
            ("API 地址", self.url),
            ("模型名称", self.model),
            ("主题", self.theme),
            ("禁止事项", self.rules),
        ):
            form.addRow(title, field)
        layout.addLayout(form)
        button = QPushButton("应用设置")
        button.setObjectName("primary")
        button.clicked.connect(self.save)
        layout.addWidget(button)
        self.platform_note = QLabel("")
        self.platform_note.setWordWrap(True)
        layout.addWidget(self.platform_note)
        self.set_platform("微信")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.theme.currentTextChanged.connect(self.theme_changed)

    # 各平台的读写手段不同，出问题时先看这一行能少走很多弯路。
    BACKENDS = {
        "微信": "wechatauto-replica / wxauto（本地消息库 + UI 自动化）",
        "QQ": "Windows UI Automation（按几何位置读取聊天区域文本）",
        "钉钉": "Windows UI Automation（按几何位置读取聊天区域文本）",
    }

    def set_platform(self, name: str) -> None:
        """把当前平台写进说明栏；滑块切换时由主窗口调用。"""
        backend = self.BACKENDS.get(name, "未接入")
        self.platform_note.setText(
            f"当前平台：{name} · {backend}\n"
            "紧急停止：Ctrl + Alt + Q\n"
            "自动保护：60 秒冷却 / 每小时最多 10 次 / 每联系人每次运行最多 3 次\n"
            "写入链路：核对窗口状态 → 复核快照与句柄 → 定位输入框并确认焦点 → "
            "剪贴板粘贴 → 回显确认；任一步不通过都会中止。"
        )

    def save(self):
        if not self.url.text().strip() or not self.model.text().strip():
            self.status.setText("请填写 API 地址和模型名称。")
            return
        self.settings = replace(
            self.settings,
            api_key=self.key.text().strip() or self.settings.api_key,
            base_url=self.url.text().strip(),
            model=self.model.text().strip(),
        )
        for key, value in (
            ("base_url", self.settings.base_url),
            ("model", self.settings.model),
            ("theme", self.theme.currentText()),
            ("prohibitions", self.rules.toPlainText()),
        ):
            self.db.set_setting(key, value)
        self.key.clear()
        self.key.setPlaceholderText(
            "已配置，仅用于本次运行" if self.settings.api_key else "尚未配置"
        )
        self.status.setText("设置已应用。连接状态以实际生成请求为准。")
        self.applied.emit(self.settings)


class ContactPage(QWidget):
    changed = Signal()

    def __init__(self, db):
        super().__init__()
        self.db, self.cid = db, None
        layout = QVBoxLayout(self)
        self.title = QLabel("先选择联系人")
        self.title.setObjectName("title")
        layout.addWidget(self.title)
        form = QFormLayout()
        self.relationship = QLineEdit()
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(100)
        form.addRow("关系", self.relationship)
        form.addRow("重要信息 / 备注", self.notes)
        layout.addLayout(form)
        self.save_btn = QPushButton("保存联系人信息")
        self.save_btn.clicked.connect(self.save)
        layout.addWidget(self.save_btn)
        layout.addWidget(QLabel("表达风格（仅从真实聊天和已确认发送的回复学习）"))
        self.persona = QPlainTextEdit()
        self.persona.setReadOnly(True)
        layout.addWidget(self.persona)
        self.save_btn.setEnabled(False)

    def load(self, cid):
        self.cid = cid
        c = self.db.contact(cid)
        self.title.setText(c["name"])
        self.relationship.setText(c["relationship"] or "")
        self.notes.setPlainText(c["notes"] or "")
        self.persona.setPlainText(
            json.dumps(PersonaService(self.db).profiles(cid), ensure_ascii=False, indent=2)
        )
        self.save_btn.setEnabled(True)

    def save(self):
        if self.cid:
            self.db.execute(
                "UPDATE contacts SET relationship=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.relationship.text(), self.notes.toPlainText(), self.cid),
            )
            self.changed.emit()


class MemoryPage(QWidget):
    changed = Signal()

    def __init__(self, db):
        super().__init__()
        self.db, self.cid = db, None
        layout = QVBoxLayout(self)
        self.title = QLabel("先选择联系人")
        self.title.setObjectName("title")
        layout.addWidget(self.title)
        self.content = QPlainTextEdit()
        self.content.setReadOnly(True)
        layout.addWidget(self.content)
        self.input = QLineEdit()
        self.input.setPlaceholderText("经你确认的重要事实、偏好或约定")
        layout.addWidget(self.input)
        self.add = QPushButton("添加长期记忆")
        self.add.clicked.connect(self.remember)
        layout.addWidget(self.add)
        self.clear = QPushButton("清空此联系人的 AI 记忆")
        self.clear.clicked.connect(self.forget)
        layout.addWidget(self.clear)
        self.add.setEnabled(False)
        self.clear.setEnabled(False)

    def load(self, cid):
        self.cid = cid
        self.title.setText(f"{self.db.contact(cid)['name']} · 记忆")
        context = MemoryService(self.db).context(cid, "")
        lines = ["长期记忆（用户确认）"] + [f"• {r['content']}" for r in context["long_term"]]
        lines += [
            "\n较早聊天摘要（原文摘录）",
            context["summary_extract"] or "历史不足 20 条，暂无摘要。",
            f"\n短期记忆：{len(context['recent'])} 条；历史检索使用关键词匹配。",
        ]
        self.content.setPlainText("\n".join(lines))
        self.add.setEnabled(True)
        self.clear.setEnabled(True)

    def remember(self):
        if self.cid and self.input.text().strip():
            MemoryService(self.db).remember(self.cid, self.input.text())
            self.input.clear()
            self.load(self.cid)
            self.changed.emit()

    def forget(self):
        if (
            self.cid
            and QMessageBox.question(
                self,
                "清空 AI 记忆",
                "将删除此联系人的记忆、Persona 和回复反馈。聊天历史保留，但旧消息不再进入 AI 上下文。是否继续？",
            )
            == QMessageBox.Yes
        ):
            self.db.clear_memory(self.cid)
            self.load(self.cid)
            self.changed.emit()
