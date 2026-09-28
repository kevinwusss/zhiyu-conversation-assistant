# 知语：具有上下文与发送控制的 AI 对话助手

这是用于作品集展示的 Windows 桌面原型，采用 Python、PySide6、SQLite 和可替换的模型接口。界面主要为中文，英文 README 面向评审阅读。

## 先运行离线演示

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 scripts/demo.py
.\.venv\Scripts\python.exe -m app
```

演示使用明确标注的合成对话、临时数据库和固定模拟回复，不连接真实联系人、不调用 API。界面可导入 `examples/synthetic-chat.json`。实际生成需要将 `.env.example` 复制成 `.env` 并填写自己的密钥。

基础安装不包含微信与 OCR 的可选后端。如需真机接入，在 Python 3.12 环境单独安装 `requirements-windows-integrations.txt`；安装及客户端行为需要另行验证。基础项目在本机 Python 3.14 上也进行验证。

## 如何理解这个项目

- 重点是上下文组织、模块边界、可编辑候选、反馈记录、错误状态和测试，不是训练了自己的大模型。
- 项目包含实验性的自动回复逻辑，申请演示建议使用默认建议模式和手工会话。
- 微信依赖第三方后端，QQ 联系人识别存在不确定性，钉钉读取受客户端限制。离线测试不能替代真机验证。
- 历史摘要采用摘录，风格与排序采用统计及规则；没有测得准确率、效率提升或招生效果。
- 本发布目录不含原始聊天库、真实日志、私有配置、第三方整仓库或过往本机诊断报告。

测试命令、隐私数据流与限制见英文首页。[上传及申请使用步骤](docs/GITHUB_GUIDE.zh-CN.md)。
