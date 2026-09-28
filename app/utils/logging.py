"""日志只记录运行元数据，异常在进入日志前脱敏。"""

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from app.config import ROOT


def redact(value: str) -> str:
    value = re.sub(r"(?i)(bearer\s+|sk-)[\w.\-]+", r"[密钥已隐藏]", value)
    value = re.sub(
        r"(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[隐私已隐藏]", value
    )
    return re.sub(
        r"(?i)(api[_ -]?key|password|密码|验证码)\s*[:=：]\s*[^\s,;]+", r"\1=[已隐藏]", value
    )


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def force_utf8_console() -> None:
    """把控制台切成 UTF-8，否则中文日志在默认 GBK 代码页下全是乱码。

    第三方库（wechatauto 在 import 时就往 root logger 挂了一个 stderr
    handler）会绕过本模块直接往控制台写中文，所以这里改的是流本身，
    而不只是我们自己的 handler。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 流被重定向成不支持重配的对象（打包成 GUI 后 stdout 可能是 None 代理）。
            pass


def setup_logging() -> None:
    force_utf8_console()
    directory = ROOT / "logs"
    directory.mkdir(exist_ok=True)
    logger = logging.getLogger("assistant")
    logger.setLevel(logging.INFO)
    # 不向 root 冒泡：第三方库挂在 root 上的 handler 既不脱敏，也不走 UTF-8。
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / "app.log", maxBytes=500_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
