"""OpenAI 兼容传输；只返回经过验证的结构化候选。"""

import json
import logging
import time
from typing import Protocol
from urllib.parse import urlparse
import requests
from app.config import Settings

log = logging.getLogger("assistant")


class ProviderError(RuntimeError):
    pass


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> dict: ...


class DeepSeekProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def complete(self, messages: list[dict[str, str]]) -> dict:
        if not self.settings.api_key:
            raise ProviderError("未配置 API Key。请在设置中填写，或配置项目根目录的 .env。")
        url = self.settings.base_url.rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        ):
            raise ProviderError("模型地址必须为 HTTPS；本机服务可以使用 HTTP。")
        start = time.monotonic()
        try:
            response = requests.post(
                url + "/chat/completions",
                timeout=(10, self.settings.timeout),
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json={
                    "model": self.settings.model,
                    "messages": messages,
                    "temperature": 0.65,
                    "max_tokens": 1200,
                    "response_format": {"type": "json_object"},
                },
            )
            if response.status_code != 200:
                reason = {401: "API Key 无效", 402: "账户余额不足", 429: "请求过于频繁"}.get(
                    response.status_code, "模型服务异常"
                )
                raise ProviderError(f"{reason}（HTTP {response.status_code}），请检查设置后重试。")
            text = response.json()["choices"][0]["message"]["content"]
            data = json.loads(text)
            candidates = data["replies"]
            if not isinstance(candidates, list) or len(candidates) != 3:
                raise ValueError("候选数量")
            if any(not isinstance(s, str) or not s.strip() or len(s) > 1500 for s in candidates):
                raise ValueError("候选格式")
            if len(set(s.strip() for s in candidates)) != 3:
                raise ValueError("重复候选")
            log.info("模型响应成功 耗时=%.2fs 候选=3", time.monotonic() - start)
            return {"replies": [s.strip() for s in candidates]}
        except requests.RequestException as exc:
            raise ProviderError("模型网络连接失败或超时，请检查网络和 API 地址。") from exc
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            raise ProviderError("模型没有返回三个有效 JSON 候选，请重新生成。") from exc
