"""在临时数据库里验证 UI；合成对话不进入用户数据库，不连接真实微信/API。"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication
from app.config import ROOT, Settings
from app.ui.window import MainWindow


class PreviewProvider:
    def complete(self, messages):
        return {"replies": ["可以哥，我再看看😂", "哈哈我再琢磨一下", "我再看看"]}


def settle(milliseconds=80):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(Settings(db_path=Path(directory) / "preview.db"))
        window.show()
        settle()
        assert not window.send_btn.isEnabled()
        window.contact_id = window.db.ensure_contact("张先生（演示数据）", "手工")
        window.db.execute(
            "UPDATE contacts SET relationship='老客户' WHERE id=?", (window.contact_id,)
        )
        for sender, content in [
            ("对方", "上次那套方案我们已经看过了"),
            ("我", "可以哥，有想法直接说😂"),
            ("对方", "整体挺合适的，就是预算有点紧"),
            ("我", "嗯嗯，我再看看"),
            ("对方", "这次价格还能便宜吗？"),
        ]:
            window.db.save_message(window.contact_id, sender, content)
        window.refresh_contacts()
        window.refresh_details()
        window.incoming.setPlainText("这次价格还能便宜吗？")
        window.engine.provider = PreviewProvider()
        window.generate()
        assert window.busy and not window.generate_btn.isEnabled()
        for _ in range(100):
            settle(30)
            if not window.busy:
                break
        assert not window.busy and window.candidates.count() == 3
        assert not window.send_btn.isEnabled(), "手工会话不可发送微信"
        window.candidates.setCurrentRow(1)
        window.editor.setPlainText("可以，我再看看")
        window.copy_reply()
        assert app.clipboard().text() == "可以，我再看看"
        window.model_status.setText("预览：合成数据 / 模拟模型")
        out = ROOT / ".impeccable/review"
        out.mkdir(parents=True, exist_ok=True)
        for theme, size, name in [
            ("浅色", (1380, 850), "desktop-light.png"),
            ("深色", (1080, 730), "desktop-dark-small.png"),
        ]:
            # 走界面自己的换肤入口，气泡才会跟着重绘。
            window.change_theme(theme)
            window.resize(*size)
            settle()
            assert window.grab().save(str(out / name))
        window.change_theme("浅色")
        window.resize(1380, 850)

        # 平台滑块：联系人必须跟着换，微信的会话不能出现在 QQ 列表里。
        window.db.ensure_contact("QQ 演示联系人", "QQ")
        window.switch_platform("QQ")
        settle()
        assert window.current_platform == "QQ"
        assert window.platform_switch.current() == "QQ", "滑块要跟着代码侧的切换走"
        assert "QQ" in window.settings_page.platform_note.text()
        assert "QQ" in window.contacts_label.text()
        listed = [
            window.contacts.item(i).text().split("\n")[0]
            for i in range(window.contacts.count())
            if window.contacts.item(i).data(Qt.UserRole) is not None
        ]
        assert "QQ 演示联系人" in listed, listed
        assert window.snapshot is None and not window.send_btn.isEnabled()
        assert window.grab().save(str(out / "desktop-qq.png"))

        # ☰ 设置页
        window.menu_btn.setChecked(True)
        settle()
        assert window.stack.currentIndex() == 1
        assert window.grab().save(str(out / "desktop-settings.png"))
        window.menu_btn.setChecked(False)
        settle()
        assert window.stack.currentIndex() == 0
        window.switch_platform("微信")
        assert window.platform_switch.current() == "微信"
        window.stop()
        assert window.gate.stopped.is_set() and not window.send_btn.isEnabled()
        window.toggle_pause()
        assert not window.gate.stopped.is_set()
        window.close()
        settle()
        print(json.dumps({"ui_checks": "passed", "screenshots": str(out)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
