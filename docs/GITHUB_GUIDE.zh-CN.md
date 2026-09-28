# 上传 GitHub 与申请使用步骤

## 1. 只使用整理后的目录

本文件所在项目根目录为 `zhiyu-conversation-assistant`。不要上传外层原项目，不要上传整个 ZIP 代替仓库源码。发布前若旧配置中的疑似真实密钥仍有效，请在服务商控制台撤销并生成新密钥；不要将新密钥写入示例文件。

## 2. 本机验证

安装 Python 3.12 后，在项目根目录打开 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -X utf8 scripts/demo.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_release.py
```

不必激活虚拟环境，避免 PowerShell 执行策略影响。首次安装需要网络，演示无需密钥。

## 3. 建立空的 GitHub 仓库

登录 GitHub，选择 New repository，名称建议 `zhiyu-conversation-assistant`。Description 可填：

> Windows conversation-assistant prototype with contextual memory, editable AI replies, SQLite persistence and delivery controls.

需要评审直接访问时选择 Public；未准备好可以先选 Private。不要勾选自动生成 README、gitignore 或 licence，因为这些文件已在本地。点击 Create repository。许可证留待确认权属后选择。

## 4. 提交和上传

先安装 Git for Windows，再在本项目根目录运行以下命令。将 `YOUR_USERNAME` 换成你的实际账号。姓名和邮箱使用你希望展示的署名；邮箱可使用 GitHub Settings → Emails 中的 noreply 地址。

```powershell
git init -b main
git config user.name "YOUR_NAME"
git config user.email "YOUR_GITHUB_NOREPLY_EMAIL"
git add .
git diff --cached --stat
.\.venv\Scripts\python.exe scripts/check_release.py
git diff --cached
git commit -m "Prepare Zhiyu conversation assistant portfolio"
git remote add origin https://github.com/YOUR_USERNAME/zhiyu-conversation-assistant.git
git push -u origin main
```

检查暂存区不含真实联系人、聊天、密钥、日志或个人路径。登录按 Git 凭据管理器的浏览器提示进行；不要把 token 放进命令、截图或 README。若提示 origin 已存在，先运行 `git remote -v` 检查，不要盲目覆盖。不要使用强制推送。

## 5. 上传后检查

打开仓库，确认英文首页、文档、合成截图和目录显示正常。到 Actions 等待 Windows tests 完成；失败时按日志修复，不要在申请里写 CI 已通过。About 中可添加 `python`、`pyside6`、`sqlite`、`human-ai-interaction`、`llm` 等主题，并在个人主页固定该仓库。

## 6. 申请与面试使用

简历用 2–3 行说明问题、实现和验证，并附实际仓库链接。可参考 `CONTRIBUTIONS.md` 的英文表述，但必须改成符合本人贡献的说法。尚未亲自运行或解释清楚的部分，不写成独立完成。

准备约 2 分钟演示：说明问题 → 运行离线 demo → 展示合成数据界面 → 解释引擎/数据/适配器分层 → 展示一项防重复或上下文切换测试 → 说明真实客户端与规则局限。不要录入真实聊天或密钥。

面试至少能说明：为什么用 SQLite；接口为何可替换；模拟测试与真机测试的区别；评分为什么不是准确率；自动操作不确定时为什么不能直接重试；数据何时离开本机；AI 工具实际承担了什么。不要伪造提交历史、开发时长、用户规模、性能提升或个人贡献。

官方上传流程参考：https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github
