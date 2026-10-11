"""
子 Agent 基类与注册机制。

所有子 Agent 继承 BaseAgent，实现 handle() 方法即可接入主控路由。
使用 @register_agent 装饰器完成注册。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    """子 Agent 抽象基类。"""

    #: Agent 唯一名称（注册名，路由规则中引用）
    name: str = "base"
    #: 一句话能力描述（用于路由展示 / 文档）
    description: str = ""
    #: 优先级排序字段（可选，默认 0）
    priority: int = 0

    def __init__(self, config: dict[str, Any] | None = None):
        self.config: dict[str, Any] = config or {}

    @abstractmethod
    def handle(self, text: str) -> str:
        """处理一条用户指令，返回执行结果文本。"""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} name={self.name!r}>"


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_AGENT_REGISTRY: dict[str, type[BaseAgent]] = {}


def register_agent(cls: type[BaseAgent]) -> type[BaseAgent]:
    """类装饰器：注册子 Agent。"""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"Agent 类 {cls.__name__} 必须定义 name 属性")
    if name in _AGENT_REGISTRY:
        raise ValueError(f"Agent 名称重复注册: {name}")
    _AGENT_REGISTRY[name] = cls
    return cls


def get_agent(name: str) -> BaseAgent | None:
    """按名称实例化子 Agent。"""
    cls = _AGENT_REGISTRY.get(name)
    return cls() if cls else None


def list_agents() -> dict[str, str]:
    """返回 {name: description} 清单。"""
    return {name: cls.description for name, cls in _AGENT_REGISTRY.items()}
