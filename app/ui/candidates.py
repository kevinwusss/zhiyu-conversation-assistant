"""候选标题与正文分别绘制，避免原生列表把换行正文一起省略。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle


def candidate_title(data: dict) -> str:
    """候选的第一行：风格名 + 分数。

    分数在引擎里是浮点数，直接插进字符串会显示成"75.0 分"；界面上没有小数
    位的意义，这里统一取整。
    """
    variant = str(data.get("variant") or "候选")
    try:
        return f"{variant} · {float(data['score']):.0f} 分"
    except (KeyError, TypeError, ValueError):
        return variant


class CandidateDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        data = index.data(Qt.UserRole)
        if not data:
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, option.widget)
        painter.save()
        painter.setPen(option.palette.text().color())
        rect = option.rect.adjusted(10, 5, -10, -4)
        line_height = option.fontMetrics.height()
        painter.drawText(rect.x(), rect.y() + option.fontMetrics.ascent(), candidate_title(data))
        body = data["text"].replace("\n", " ")
        preview = option.fontMetrics.elidedText(body, Qt.ElideRight, rect.width())
        painter.drawText(
            rect.x(), rect.y() + line_height + 5 + option.fontMetrics.ascent(), preview
        )
        painter.restore()
