"""统一的大模型客户端。

关键设计：
    1. 可插拔（DeepSeek / OpenAI / 任何 OpenAI 兼容网关），只改 .env；
    2. 全链路可降级：网络失败 / 无 Key / 返回非法 JSON → 自动回落离线规则引擎，
       绝不让 demo 因为模型问题中断；
    3. 强约束 JSON 输出：先尝试 response_format=json_object，再兜底做括号截取解析，
       解析失败带上原始响应重试一次。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from app import config

_stats = {"calls": 0, "failures": 0, "tokens": 0, "last_error": ""}


@dataclass
class LLMResponse:
    ok: bool
    text: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    error: str = ""


def extract_json(text: str) -> Any:
    """从模型输出里抠出 JSON：兼容 markdown 代码块、前后废话、尾随逗号。"""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    # 括号配对截取：从第一个 { 或 [ 开始
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            snippet = cleaned[start:end + 1]
            snippet = re.sub(r",\s*([}\]])", r"\1", snippet)  # 去尾随逗号
            try:
                return json.loads(snippet)
            except ValueError:
                continue
    return None


class LLMClient:
    def __init__(self) -> None:
        self.provider = config.LLM_PROVIDER
        self.model = config.LLM_MODEL
        self.enabled = config.llm_enabled()

    # ------------------------------------------------------------------ 基础调用
    def chat(self, system: str, user: str, temperature: float | None = None,
             json_mode: bool = False) -> LLMResponse:
        if not self.enabled:
            return LLMResponse(ok=False, error="LLM 未启用（offline 模式）",
                               provider=self.provider, model=self.model)

        url = f"{config.LLM_BASE_URL}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": config.LLM_TEMPERATURE if temperature is None else temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if config.LLM_MAX_TOKENS > 0:
            payload["max_tokens"] = config.LLM_MAX_TOKENS
        headers = {"Authorization": f"Bearer {config.LLM_API_KEY}",
                   "Content-Type": "application/json"}

        last_error = ""
        for attempt in range(config.LLM_MAX_RETRIES + 1):
            started = time.time()
            try:
                resp = requests.post(url, json=payload, headers=headers,
                                     timeout=config.LLM_TIMEOUT)
                latency = int((time.time() - started) * 1000)
                _stats["calls"] += 1
                if resp.status_code >= 400:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    # 4xx（除 429）不重试，避免白白消耗配额
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        break
                    time.sleep(1.2 * (attempt + 1))
                    continue
                body = resp.json()
                choice = (body.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                text = message.get("content") or ""
                finish = choice.get("finish_reason")
                usage = body.get("usage") or {}
                _stats["tokens"] += int(usage.get("total_tokens") or 0)
                if not text.strip():
                    # 推理模型的典型失败：token 全花在 reasoning_content 上，正文被截断
                    reasoning = message.get("reasoning_content") or ""
                    last_error = (
                        f"模型返回空内容（finish_reason={finish}；"
                        f"推理内容 {len(reasoning)} 字，疑似 max_tokens={config.LLM_MAX_TOKENS} 不足）"
                        if reasoning else f"模型返回空内容（finish_reason={finish}）"
                    )
                    continue
                if finish == "length":
                    last_error = "模型输出被 max_tokens 截断（finish_reason=length）"
                    _stats["last_error"] = last_error
                return LLMResponse(ok=True, text=text, provider=self.provider,
                                   model=self.model, latency_ms=latency)
            except requests.RequestException as exc:  # 网络抖动
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(1.2 * (attempt + 1))

        _stats["failures"] += 1
        _stats["last_error"] = last_error
        return LLMResponse(ok=False, error=last_error, provider=self.provider, model=self.model)

    def chat_json(self, system: str, user: str) -> dict[str, Any] | None:
        resp = self.chat(system, user, json_mode=True)
        if resp.ok:
            data = extract_json(resp.text)
            if isinstance(data, dict):
                return data
            # 解析失败：把原始输出回灌，要求重新按 JSON 输出
            retry = self.chat(system, user + "\n\n注意：上次输出无法解析为 JSON，"
                                            f"原始输出如下，请只输出合法 JSON：\n{resp.text[:800]}",
                              temperature=0.0, json_mode=True)
            if retry.ok:
                data = extract_json(retry.text)
                if isinstance(data, dict):
                    return data
            _stats["last_error"] = "JSON 解析失败"
        return None

    def text(self, system: str, user: str) -> str | None:
        resp = self.chat(system, user)
        return resp.text if resp.ok else None


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def usage_stats() -> dict[str, Any]:
    return dict(_stats)
