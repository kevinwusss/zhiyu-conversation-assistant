"""为旧 wxauto 提供显式配对的 COM 生命周期；它的初始化类不支持 with。"""

from contextlib import contextmanager


@contextmanager
def uia_thread(uia):
    uia.InitializeUIAutomationInCurrentThread()
    try:
        yield
    finally:
        uia.UninitializeUIAutomationInCurrentThread()
