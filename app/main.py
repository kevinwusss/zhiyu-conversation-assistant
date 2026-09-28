"""桌面启动入口；--smoke-test 执行无网络启动检查。"""

import argparse
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.adapters.windows import ensure_dpi_awareness
from app.ui.window import MainWindow
from app.utils.logging import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="知语 · Windows 智能对话助手")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    setup_logging()
    # 必须赶在 QApplication 之前。Qt 6 自己也会设 Per-Monitor v2，但一旦 Qt 先设过，
    # 后面就改不了了；显式设在前面，是为了让带界面运行和 scripts/ 里的脚本走完全
    # 相同的坐标系——否则同一段定位代码在两种环境下算出的点会差一个缩放比例。
    ensure_dpi_awareness()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(1200, window.close)
    return app.exec()
