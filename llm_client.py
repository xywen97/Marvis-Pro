"""
LLM 客户端模块（零第三方依赖，urllib 实现 OpenAI 兼容 /chat/completions 调用）。

配置来源优先级：
1. 显式传入（__init__ 参数）
2. .env 文件（OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL / LLM_TEMPERATURE）
3. 进程环境变量（仅最后兜底；避免终端环境变量覆盖 .env 的正确配置）

安全约束：
- 敏感密钥（OPENAI_API_KEY）仅从 .env 读取，严禁在代码中硬编码；
- 请求体不落盘、不打印完整密钥。
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env 轻量解析（零依赖；不支持复杂语法，仅 KEY=VALUE 与 # 注释）
# ---------------------------------------------------------------------------

def load_env_file(path: str | Path = ".env") -> dict[str, str]:
    """读取 .env 文件为 dict。文件不存在时返回空 dict。"""
    env_path = Path(path)
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------

@dataclass
class ChatResult:
    """一次 chat 调用的结果。"""

    ok: bool
    content: str = ""
    message: str = ""
    error: str = ""
    status_code: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, content: str, raw: dict[str, Any] | None = None,
                tool_calls: list[dict[str, Any]] | None = None) -> "ChatResult":
        return cls(ok=True, content=content, message="success",
                   tool_calls=tool_calls or [], raw=raw or {})

    @classmethod
    def failure(cls, error: str, status_code: int = 0, message: str = "") -> "ChatResult":
        return cls(ok=False, error=error, status_code=status_code, message=message or error)

    def __str__(self) -> str:  # pragma: no cover
        if self.ok:
            return f"<ChatResult ok content={self.content[:60]!r}>"
        return f"<ChatResult fail status={self.status_code} error={self.error!r}>"


# ---------------------------------------------------------------------------
# DSML 文本工具调用解析
# （DeepSeek 部分模型会以 <|DSML|calls> 文本形式输出工具调用而非标准 tool_calls）
# ---------------------------------------------------------------------------

_DSML_INVOKE_RE = re.compile(
    r"<[^>]*DSML[^>]*invoke\s+name=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)<[^>]*DSML[^>]*invoke[^>]*>",
    re.IGNORECASE,
)
_DSML_PARAM_RE = re.compile(
    r"<[^>]*DSML[^>]*parameter\s+name=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)<[^>]*DSML[^>]*parameter[^>]*>",
    re.IGNORECASE,
)


def parse_dsml_tool_calls(content: str) -> list[dict[str, Any]]:
    """解析 DeepSeek 的 DSML 文本工具调用为 OpenAI tool_calls 结构。

    匹配 <|DSML|invoke name="agent">...</|DSML|invoke> 内嵌的
    <|DSML|parameter name="k">v</|DSML|parameter>；兼容全角竖线/多余空格的变体
    （如 <｜｜DSML｜｜ calls>）。无命中返回空列表。
    """
    calls: list[dict[str, Any]] = []
    for idx, (name, body) in enumerate(_DSML_INVOKE_RE.findall(content)):
        args: dict[str, str] = {}
        for pname, pval in _DSML_PARAM_RE.findall(body):
            args[pname] = pval.strip()
        calls.append({
            "id": f"dsml_{idx}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
    return calls


# ---------------------------------------------------------------------------
# LLM 客户端
# ---------------------------------------------------------------------------

class LLMClient:
    """OpenAI 兼容 /chat/completions 客户端（urllib 实现，零第三方依赖）。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        timeout: int = 60,
        env_file: str | Path = ".env",
    ):
        # 配置优先级：显式传入 > .env > 进程环境变量（环境变量仅最后兜底）
        env = load_env_file(env_file)
        self.base_url = (base_url or env.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        self.model = model or env.get("OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or ""
        try:
            self.temperature = float(
                temperature if temperature is not None
                else env.get("LLM_TEMPERATURE") or os.environ.get("LLM_TEMPERATURE") or 0.2
            )
        except ValueError:
            self.temperature = 0.2
        self.timeout = timeout

    # ------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        """是否具备调用条件（base_url / api_key / model 齐备）。"""
        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: list[dict[str, str]], temperature: float | None = None,
             tools: list[dict[str, Any]] | None = None,
             tool_choice: str | dict[str, Any] = "auto", on_delta=None) -> ChatResult:
        """调用 /chat/completions。

        messages: OpenAI 消息数组 [{"role": "system"|"user"|"assistant"|"tool", ...}]
        tools:    OpenAI function calling 工具定义列表（传入后模型可返回 tool_calls）
        tool_choice: "auto" | "none" | {"type":"function","function":{"name":...}}
        """
        if not self.configured:
            missing = [n for n, v in (("base_url", self.base_url), ("api_key", self.api_key),
                                      ("model", self.model)) if not v]
            return ChatResult.failure(f"LLM 未配置完整，缺少: {', '.join(missing)}")

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "stream": on_delta is not None,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    # A model endpoint must not redirect local prompts or credentials elsewhere.
                    raise urllib.error.URLError('模型端点重定向已拒绝，请配置最终端点。')
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(req, timeout=self.timeout) as resp:
                if on_delta is not None and 'text/event-stream' in resp.headers.get('Content-Type', ''):
                    content_parts, calls = [], {}
                    finished = False
                    for line in resp:
                        line = line.decode('utf-8').strip()
                        if not line.startswith('data:'): continue
                        data = line[5:].strip()
                        if data == '[DONE]':
                            finished = True
                            break
                        chunk = json.loads(data)
                        if chunk.get('error'):
                            raise ValueError(str(chunk['error']))
                        for choice in chunk.get('choices', []):
                            if choice.get('index', 0) != 0: continue
                            if choice.get('finish_reason'): finished = True
                            delta = choice.get('delta', {})
                            # Display only public assistant text, not private reasoning fields.
                            text = delta.get('content') or ''
                            if text:
                                content_parts.append(text)
                                on_delta(text)
                            for part in delta.get('tool_calls', []):
                                call = calls.setdefault(part['index'], {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                                if part.get('id'): call['id'] = part['id']
                                fn = part.get('function', {})
                                call['function']['name'] += fn.get('name') or ''
                                call['function']['arguments'] += fn.get('arguments') or ''
                    if not finished: raise ValueError('流式连接提前结束，回复未完成。')
                    raw = {'choices': [{'message': {'content': ''.join(content_parts), 'tool_calls': [calls[i] for i in sorted(calls)]}}]}
                else:
                    raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                err_body = json.loads(e.read().decode("utf-8", errors="replace"))
                detail = err_body.get("error", {}).get("message", "") or json.dumps(err_body, ensure_ascii=False)[:300]
            except Exception:  # noqa: BLE001
                detail = f"HTTP {e.code}"
            return ChatResult.failure(
                f"接口返回错误 (HTTP {e.code}): {detail}", status_code=e.code
            )
        except urllib.error.URLError as e:
            return ChatResult.failure(f"网络错误: {e.reason}")
        except Exception as e:  # noqa: BLE001
            return ChatResult.failure(f"请求异常: {e}")

        # 解析返回（支持 tool_calls）
        try:
            message = raw["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return ChatResult.failure("响应缺少 choices[0].message")
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        # DeepSeek 部分模型以 DSML 文本输出工具调用：识别后标准化为 tool_calls
        if not tool_calls and content and "<" in content and "DSML" in content:
            tool_calls = parse_dsml_tool_calls(content)
            if tool_calls:
                content = ""  # DSML 标记剥离，交由上层工具循环执行
        return ChatResult.success(content, raw=raw, tool_calls=tool_calls)
