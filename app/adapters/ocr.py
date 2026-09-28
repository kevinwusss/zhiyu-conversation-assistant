"""受限 OCR 回退：只对聊天消息区域矩形截图识别，绝不对整窗识别。

OCR 结果一律标记 needs_review=True，只能作为"待核验消息"展示，不允许直接
进入自动回复链路。本机没有可用 OCR 引擎时返回 ocr_unavailable，不做任何猜测。
"""

from __future__ import annotations

import sys
from hashlib import sha1

from app.core.models import ChatMessage
from .read_status import ReadResult, ReadStatus
from .desktop_uia import is_ui_chrome, looks_like_time

# 单次 OCR 允许的最大区域，避免误传整屏矩形。
_MAX_REGION_PIXELS = 4000 * 4000


def available_engine() -> str:
    """返回本机可用的 OCR 引擎名，没有则返回空字符串。"""
    if sys.platform != "win32":
        return ""
    for module, label in (
        ("winocr", "winocr"),
        ("pytesseract", "pytesseract"),
        ("easyocr", "easyocr"),
        ("paddleocr", "paddleocr"),
    ):
        try:
            __import__(module)
            return label
        except Exception:
            continue
    return ""


def _grab(region: tuple[int, int, int, int]):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=region, all_screens=True)


def _run_ocr(image, engine: str) -> list[str]:
    if engine == "winocr":
        import winocr

        result = winocr.recognize_pil_sync(image, "zh-Hans-CN")
        return [line.text for line in getattr(result, "lines", [])]
    if engine == "pytesseract":
        import pytesseract

        text = pytesseract.image_to_string(image, lang="chi_sim+eng")
        return text.splitlines()
    if engine == "easyocr":
        import numpy
        import easyocr

        reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
        return [item[1] for item in reader.readtext(numpy.array(image))]
    if engine == "paddleocr":
        import numpy
        from paddleocr import PaddleOCR

        reader = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        rows = reader.ocr(numpy.array(image), cls=True) or []
        out = []
        for page in rows:
            for line in page or []:
                out.append(line[1][0])
        return out
    return []


def read_region(
    region: tuple[int, int, int, int] | None,
    contact: str,
    limit: int = 50,
) -> ReadResult:
    """对给定聊天区域矩形做 OCR。region 为空时直接拒绝，不允许整窗 OCR。"""
    if not region:
        return ReadResult(
            status=ReadStatus.MESSAGE_REGION_NOT_FOUND,
            detail="没有聊天区域矩形，拒绝对整个窗口做 OCR。",
        )
    left, top, right, bottom = region
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return ReadResult(
            status=ReadStatus.MESSAGE_REGION_NOT_FOUND,
            detail="聊天区域矩形非法，已拒绝 OCR。",
        )
    if width * height > _MAX_REGION_PIXELS:
        return ReadResult(
            status=ReadStatus.MESSAGE_REGION_NOT_FOUND,
            detail="聊天区域矩形过大，疑似整窗，已拒绝 OCR。",
        )

    engine = available_engine()
    if not engine:
        return ReadResult(
            status=ReadStatus.OCR_UNAVAILABLE,
            detail="本机未安装可用的 OCR 引擎（winocr / pytesseract / easyocr / paddleocr）。",
        )

    try:
        image = _grab(region)
        lines = _run_ocr(image, engine)
    except Exception as exc:
        return ReadResult(
            status=ReadStatus.OCR_UNAVAILABLE,
            detail=f"OCR 执行失败：{exc}",
        )

    messages: list[ChatMessage] = []
    counter: dict[str, int] = {}
    timestamp = ""
    for raw in lines:
        text = (raw or "").strip()
        if not text:
            continue
        if looks_like_time(text):
            timestamp = text
            continue
        if is_ui_chrome(text):
            continue
        if len(text) < 2:
            continue
        counter[text] = counter.get(text, 0) + 1
        source = sha1(f"ocr|{text}|{counter[text]}".encode("utf-8")).hexdigest()[:16]
        messages.append(ChatMessage(contact or "待确认", text, source, "text", timestamp))

    if not messages:
        return ReadResult(
            status=ReadStatus.NO_MESSAGES,
            contact=contact,
            detail=f"OCR（{engine}）在聊天区域内没有识别到文本。",
            region=region,
        )

    return ReadResult(
        status=ReadStatus.MESSAGES_READ,
        messages=tuple(messages[-limit:]),
        contact=contact,
        needs_review=True,
        detail=f"结果来自 OCR（{engine}），发送者与顺序未经界面结构确认，必须人工核验。",
        region=region,
        diagnostics={"engine": engine, "lines": len(lines)},
    )
