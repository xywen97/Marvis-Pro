"""
主控 Agent 模块。

职责：
1. 定义主控 Agent 的角色与人格（对用户称呼"老板"，回答风格"理智高效"）。
2. 实现意图识别与路由分发：将用户指令路由到最合适的子 Agent。
3. 作为所有子 Agent 的统一调度入口。

设计：
- 路由规则可配置（见 config.yaml -> routing.rules），支持关键词 + 优先级。
- 路由结果返回 (agent_name, agent, matched_rule)，由上层执行。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agents.base import BaseAgent
from agents import get_agent, list_agents
from llm_client import LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class RoutingRule:
    """一条意图路由规则。"""

    agent: str
    keywords: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    priority: int = 100  # 数字越小优先级越高

    def match(self, text: str) -> bool:
        """判断输入文本是否命中本规则：命中任一关键词或正则即可。"""
        lowered = text.lower()
        if any(kw.lower() in lowered for kw in self.keywords):
            return True
        return any(re.search(p, text, re.IGNORECASE) for p in self.patterns)


@dataclass
class RoutingDecision:
    """一次路由决策的结果。"""

    text: str
    agent_name: str
    rule: Optional[RoutingRule] = None
    confidence: float = 0.0


def build_agent_tools() -> list[dict[str, Any]]:
    """把已注册子 Agent 暴露为 OpenAI function calling 工具列表。

    每个子 Agent 对应一个 function（name=Agent 名，参数 query=用户指令原文）。
    """
    tools: list[dict[str, Any]] = []
    for name, desc in list_agents().items():
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "交给该子 Agent 处理的用户指令原文或精简指令",
                        },
                    },
                    "required": ["query"],
                },
            },
        })
    return tools


# ---------------------------------------------------------------------------
# 主控 Agent
# ---------------------------------------------------------------------------

class MainAgent:
    """主控 Agent：角色定义 + 意图路由 + 调度。"""

    # 角色定义（可在 config.yaml 中覆盖）
    DEFAULT_PERSONA = {
        "name": "MarvisXY",
        "description": "多 Agent 协作桌面智能助手",
        "user_title": "老板",            # 对用户的称呼
        "style": "理智高效",             # 回答风格
        "greeting": "老板，我在。有什么需要我处理的？",
    }

    def __init__(self, config: Optional[dict[str, Any]] = None):
        config = config or {}
        persona = config.get("persona", {})
        self.persona = {**self.DEFAULT_PERSONA, **persona}

        rules_cfg = config.get("routing", {}).get("rules", [])
        self.rules: list[RoutingRule] = []
        for item in rules_cfg:
            self.rules.append(
                RoutingRule(
                    agent=item["agent"],
                    keywords=item.get("keywords", []),
                    patterns=item.get("patterns", []),
                    priority=item.get("priority", 100),
                )
            )
        # 按优先级升序排列，优先级相同时保持配置顺序
        self.rules.sort(key=lambda r: (r.priority,))

        # LLM 配置：config.yaml -> llm 节 + .env 敏感项（api_key 从 .env 读取）
        llm_cfg = config.get("llm", {}) or {}
        self.llm = LLMClient(
            base_url=llm_cfg.get("base_url") or None,
            api_key=llm_cfg.get("api_key") or None,   # config 中留空，自动回落 .env
            model=llm_cfg.get("model") or None,
            temperature=float(llm_cfg.get("temperature", 0.2)),
            env_file=llm_cfg.get("env_file", ".env"),
        )

        # 会话历史（保留最近 10 轮，供交互模式关联上文）
        self._history: list[dict[str, str]] = []
        # 工具调用循环上限（单轮最多 2 次工具调用）
        self.MAX_TOOL_LOOPS = 2

    # ------------------------------------------------------------------
    # 角色 API
    # ------------------------------------------------------------------

    def user_title(self) -> str:
        """主控对用户的称呼。"""
        return self.persona["user_title"]

    def style(self) -> str:
        """主控的回答风格。"""
        return self.persona["style"]

    def greet(self) -> str:
        """开场白。"""
        return self.persona["greeting"]

    # ------------------------------------------------------------------
    # 意图路由 API
    # ------------------------------------------------------------------

    def route(self, text: str) -> RoutingDecision:
        """将用户输入路由到最合适的子 Agent。

        规则：按优先级遍历全部规则，返回第一个命中的结果；
        若无规则命中，则回退到默认兜底 Agent（可配置，默认 main 自己兜底）。
        """
        text = (text or "").strip()
        if not text:
            return RoutingDecision(text=text, agent_name="main", confidence=0.0)

        for rule in self.rules:
            if rule.match(text):
                logger.debug("路由命中: agent=%s rule=%s", rule.agent, rule)
                return RoutingDecision(
                    text=text,
                    agent_name=rule.agent,
                    rule=rule,
                    confidence=0.9,
                )

        fallback = "main"  # 无命中时由主控兜底
        logger.debug("无规则命中，回退到 %s", fallback)
        return RoutingDecision(text=text, agent_name=fallback, confidence=0.1)

    def dispatch(self, text: str) -> tuple[str, BaseAgent | None]:
        """路由 + 获取子 Agent 实例。

        返回 (agent_name, agent_instance)；instance 为 None 表示交给主控兜底处理。
        """
        decision = self.route(text)
        if decision.agent_name == "main":
            return decision.agent_name, None
        agent = get_agent(decision.agent_name)
        return decision.agent_name, agent

    # ------------------------------------------------------------------
    # 真实对话 API
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """构造带角色人设的 system prompt。"""
        persona = self.persona
        model = self.llm.model or "未配置"
        return (
            f"你是 {persona['name']}，一个 {persona['description']}。\n"
            f"你的底层模型是 {model}。\n"
            f"当前工作目录：~/project/marvis-xy（用户说「当前项目/本地/这里」时默认指该目录）。\n"
            f"你对用户的称呼是「{persona['user_title']}」。\n"
            f"你的回答风格要求：{persona['style']}——直接给出结论或行动，不啰嗦、不寒暄、"
            f"不用表情符号，引用事实或数据时说明来源。\n"
            f"你有多个专业子 Agent 可调用（file-agent 文件、app-agent 应用、"
            f"browser-agent 网页、computer-agent 系统）。当系统提供子 Agent 执行结果或工具调用结果时，"
            f"请基于该结果组织最终回复；结果缺失或失败时如实说明，不要编造。\n"
            f"工具调用由系统调度：需要调用工具时系统会自动执行并回传结果，"
            f"你禁止以文本形式输出 tool_call JSON（如 {{'type': 'function', 'name': ...}}），"
            f"也不要虚构工具调用。\n"
            f"无法处理的需求，用一句话说明原因并给出可执行的替代建议。"
        )

    # 会话历史保留上限（轮数；每条 user/assistant 消息计半轮）
    MAX_HISTORY_ROUNDS = 10

    def _trim_history(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        """裁剪历史到最近 MAX_HISTORY_ROUNDS 轮。"""
        max_msgs = self.MAX_HISTORY_ROUNDS * 2
        return history[-max_msgs:] if len(history) > max_msgs else history

    def _inject_path_context(self, text: str, history: list[dict[str, str]]) -> str:
        """若当前指令未指明目录，且历史中出现过路径，则附加路径上下文。

        用于让「列出目录树 / 就在本地」等指令关联上文提到的目录。
        """
        if re.search(r"[~/]", text):
            return text
        path_hint = ""
        for m in reversed(history):
            if m.get("role") == "user":
                found = re.search(r"([~/][^\s，。；）】\"']*)", m.get("content", ""))
                if found:
                    path_hint = found.group(1)
                    break
        if path_hint:
            return f"{text}（上文提到的目录：{path_hint}）"
        return text

    def chat(self, text: str, history: list[dict[str, str]] | None = None) -> str:
        """真实对话入口：规则路由 -> 子 Agent 执行 / 模型工具调用 -> 大模型生成最终回复。

        - 命中子 Agent：把子 Agent 执行结果作为上下文交给大模型组织最终回复；
        - 未命中（兜底）：把子 Agent 注册为 functions，由模型决定是否调用
          （工具调用循环最多 2 轮），执行结果作为 tool 消息回传后生成最终回复；
        - history：外部传入的会话历史（保留最近 10 轮）；None 时使用内部维护的历史；
        - LLM 未配置或调用失败：回退为子 Agent 结果直出，保证骨架可离线运行。
        """
        text = (text or "").strip()
        if not text:
            return f"{self.persona['user_title']}，请说点什么吧。"

        if history is None:
            history = self._history

        decision = self.route(text)

        # 1. 子 Agent 执行（规则命中时）
        tool_context = ""
        if decision.agent_name != "main":
            agent = get_agent(decision.agent_name)
            if agent is not None:
                try:
                    tool_context = agent.handle(self._inject_path_context(text, history))
                except Exception as e:  # noqa: BLE001
                    tool_context = f"（子 Agent {decision.agent_name} 执行异常: {e}）"
                logger.info("路由至 %s 完成", decision.agent_name)
            else:
                tool_context = f"（子 Agent {decision.agent_name} 未注册）"

        # 2. 构造 messages：system + 会话历史 + 本轮
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._build_system_prompt()}
        ]
        messages.extend(self._trim_history(history))
        if tool_context:
            messages.append({
                "role": "user",
                "content": (
                    f"用户指令：{text}\n\n"
                    f"【子 Agent（{decision.agent_name}）执行结果】\n{tool_context}\n\n"
                    f"请结合以上子 Agent 执行结果，以你的角色和风格回复用户。"
                ),
            })
        else:
            messages.append({"role": "user", "content": text})

        # 3. 调用大模型
        if decision.agent_name == "main":
            # 规则未命中：走工具调用循环（模型可调用子 Agent functions）
            reply = self._chat_with_tools(messages)
            if reply is None:
                # 工具链路失败：退化为普通请求
                result = self.llm.chat(messages)
                if result.ok:
                    reply = result.content.strip()
                else:
                    logger.warning("LLM 调用失败: %s", result.error)
                    reply = f"[说明] 大模型调用失败：{result.error}"
        else:
            # 规则命中：子 Agent 结果直出上下文，由模型组织最终回复
            result = self.llm.chat(messages)
            if result.ok:
                reply = result.content.strip()
            else:
                logger.warning("LLM 调用失败: %s", result.error)
                if tool_context:
                    reply = (
                        f"[{decision.agent_name}] 执行结果（LLM 暂不可用，以下为原始结果）：\n"
                        f"{tool_context}\n"
                        f"\n[说明] 大模型调用失败：{result.error}"
                    )
                else:
                    reply = f"[说明] 大模型调用失败：{result.error}"

        # 4. 记录本轮历史（保留最近 10 轮）
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        history[:] = self._trim_history(history)
        return reply

    def _chat_with_tools(self, messages: list[dict[str, str]]) -> str | None:
        """工具调用循环：规则未命中时，模型可调用子 Agent functions。

        把模型返回的 tool_calls 逐一执行对应子 Agent，执行结果作为 tool 消息
        回传模型，再请求生成最终回复；单轮最多 MAX_TOOL_LOOPS 次工具调用循环。
        返回最终回复文本；链路失败返回 None（由上层退化为普通请求）。
        """
        tools = build_agent_tools()
        # 记录本轮用户指令（用于 arguments 为空时的兜底）
        user_text = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )

        result = self.llm.chat(messages, tools=tools, tool_choice="auto")
        loop = 0
        while result.ok and result.tool_calls and loop < self.MAX_TOOL_LOOPS:
            loop += 1
            # 回传 assistant 消息（须原样保留 tool_calls，OpenAI 协议要求）
            messages.append({
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": result.tool_calls,
            })
            for tc in result.tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                query = (args.get("query")
                         or next(iter(args.values()), "")
                         or user_text)
                agent = get_agent(name)
                if agent is not None:
                    try:
                        tool_output = agent.handle(str(query))
                    except Exception as e:  # noqa: BLE001
                        tool_output = f"（子 Agent {name} 执行异常: {e}）"
                else:
                    tool_output = f"（子 Agent {name} 未注册）"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_output,
                })
            result = self.llm.chat(messages, tools=tools, tool_choice="auto")

        if not result.ok:
            return None
        # 达到循环上限后若仍返回工具调用且无正文，强制无工具请求生成最终回复
        if result.tool_calls and not result.content:
            final = self.llm.chat(messages, tools=None)
            return final.content.strip() if final.ok else None
        return result.content.strip()

    # ------------------------------------------------------------------
    # 便捷信息
    # ------------------------------------------------------------------

    def describe_routing(self) -> str:
        """打印当前路由规则清单（用于调试 / 展示）。"""
        lines = []
        for rule in self.rules:
            lines.append(
                f"- [{rule.priority}] -> {rule.agent}: "
                f"keywords={rule.keywords or '-'} patterns={rule.patterns or '-'}"
            )
        return "\n".join(lines) if lines else "(无路由规则)"
