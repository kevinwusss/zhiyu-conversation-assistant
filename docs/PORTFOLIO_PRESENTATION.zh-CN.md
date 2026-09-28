# 申请材料与演示提纲

## 项目定位

英文标题：**Zhiyu — Context-Aware Conversation Assistant**。

适合讨论的软件工程问题：如何将模型建议、历史上下文、用户编辑和外部执行分开；如何记录不确定性；如何验证一个依赖桌面客户端的系统。不要将项目描述为训练了自己的大模型或获得了科研结论。

## 简历表述草稿

以下是作品描述，不是个人贡献证明。确认自己理解实现、完成复现并据实修改后再使用：

> **Zhiyu — AI-Assisted Conversation Assistant | Python, PySide6, SQLite**  
> Developed an AI-assisted desktop prototype combining conversation history, contact-specific style features and editable model-generated reply candidates. Separated model transport, context management and platform adapters, with automated checks for duplicate delivery, context changes and uncertain outcomes. Documented privacy trade-offs and platform limitations using synthetic demonstrations.

如果你主要承担需求、验证和迭代，应把 “Developed” 改成更准确的 “Defined requirements and iteratively tested an AI-assisted desktop prototype”，再补上自己真实做过的工作。不要使用尚未发生的结果，如 improved response efficiency by 40%。

## 两分钟演示顺序

1. **0–20 秒**：说明场景——通用回复缺少上下文，自动执行还可能发错会话。
2. **20–50 秒**：运行 `scripts/demo.py`，明确这是合成输入与模拟输出，展示候选结构、风险与可解释分数。
3. **50–80 秒**：展示界面截图或手工会话，解释用户编辑和发送是不同操作。
4. **80–105 秒**：打开架构图与一项测试，解释快照变化或结果不确定时的处理。
5. **105–120 秒**：承认规则与客户端依赖的限制，说明下一步打算怎样做评估。

## 面试准备清单

能用自己的话解释 `ReplyEngine.generate` 如何获取上下文、模型响应如何校验、SQLite 存储哪些记录、为什么需要临时库和模拟对象，以及失败测试是实现错误还是测试前置条件失效。重点阅读 `app/core/engine.py`、`app/database.py`、`app/llm/provider.py`、`app/automation/safety.py` 和 `tests/test_pipeline.py`。

准备一个真实迭代例子：自动回复测试需要同时启用全局模式与联系人级开关；更新测试前置条件，不能为了让测试通过而删除保护开关。区分本次整理工具完成的工作与自己完成的工作。

本项目可作为辅助证据展示学习过程；是否适合某一课程、学校是否接受额外链接，需以该申请系统的实际要求为准。
