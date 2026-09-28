"""保守规则分类：未知场景默认中风险，只有明确闲聊可自动化。"""

import re

HIGH = (
    "报价",
    "价格",
    "便宜",
    "折扣",
    "合同",
    "付款",
    "退款",
    "转账",
    "投诉",
    "法律",
    "财务",
    "承诺",
    "保证",
    "一定",
    "验证码",
    "密码",
    "身份证",
    "银行卡",
    "保密",
    "账户",
    "多少钱",
    "发票",
    "支付",
)
MEDIUM = ("产品", "安排", "时间", "明天", "后天", "会议", "客户", "订单", "功能", "什么时候")
LOW = {
    "在吗",
    "在",
    "你好",
    "你好呀",
    "嗨",
    "收到",
    "谢谢",
    "谢谢你",
    "不客气",
    "好的",
    "好",
    "嗯",
    "哈哈",
    "晚安",
    "早",
    "早上好",
    "没问题",
    "可以",
    "可以的",
}


def classify(text: str) -> tuple[str, str, str]:
    if any(word in text for word in HIGH) or re.search(
        r"[¥￥$€]|\d[\d,.]*\s*(?:元|万|块|美元)|\b\d{6,}\b", text
    ):
        return "HIGH", "涉及金额、隐私或重要承诺，必须人工确认", "敏感事项"
    if any(word in text for word in MEDIUM):
        return "MEDIUM", "涉及业务或时间安排，请核对事实", "咨询 / 安排"
    normalized = re.sub(r"[\s，。！？!?～~😂👌]", "", text)
    if normalized in LOW:
        return "LOW", "明确的问候或简短确认", "日常沟通"
    return "MEDIUM", "未能可靠识别为低风险，请人工处理", "一般对话"


def combined_risk(incoming: str, reply: str, recent: list[dict]) -> str:
    levels = [classify(incoming)[0], classify(reply)[0]]
    # 最近讨论敏感事项时，即使当前只说“好”也不能自动承诺。
    if any(classify(r["content"])[0] == "HIGH" for r in recent[-6:]):
        levels.append("HIGH")
    return max(levels, key={"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get)
