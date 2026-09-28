"""语法健康检查：对 app/ 下所有 .py 文件做 ast.parse，防止中文智能引号等
不可见字符替换导致的语法错误在提交前被发现。此前发生过 Write/Edit 工具把英文
直引号写成中文弯引号（U+201C/U+201D）的问题，本测试作为持续的安全网。"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestSyntaxHealth(unittest.TestCase):
    def test_all_app_files_parse(self):
        failures = []
        for path in sorted((ROOT / "app").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            try:
                ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
        if failures:
            self.fail("发现语法错误：\n" + "\n".join(failures))

    def test_no_smart_quotes_outside_comments_or_strings_is_not_checked(self):
        # 智能引号在字符串/注释内容中是合法的（如中文提示文案）；
        # 真正的风险点是引号被用作代码分隔符时的替换，ast.parse 已能捕获。
        # 这里保留一个占位说明，避免误加过严的全文件正则检查产生误报。
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
