"""
工具抽象层（tools/）。

工具是子 Agent 的最小可执行单元：一次输入、一个结果。
- BaseTool: 工具基类
- ToolResult: 统一返回结构
- register_tool / get_tool / list_tools: 注册与查询
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """工具执行结果。"""

    ok: bool
    data: Any = None
    message: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, data: Any = None, message: str = "", meta: dict[str, Any] | None = None) -> "ToolResult":
        return cls(ok=True, data=data, message=message, meta=meta or {})

    @classmethod
    def failure(cls, error: str, message: str = "") -> "ToolResult":
        return cls(ok=False, error=error, message=message)

    def __str__(self) -> str:  # pragma: no cover
        if self.ok:
            return f"<ToolResult ok message={self.message!r} data={self.data!r}>"
        return f"<ToolResult fail error={self.error!r}>"


class BaseTool(ABC):
    """工具基类。"""

    name: str = "base-tool"
    description: str = ""
    #: 工具风险等级: readonly | normal | risky（用于上层安全策略）
    risk: str = "normal"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config: dict[str, Any] = config or {}

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, type[BaseTool]] = {}


def register_tool(cls: type[BaseTool]) -> type[BaseTool]:
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"工具类 {cls.__name__} 必须定义 name 属性")
    if name in _TOOL_REGISTRY:
        raise ValueError(f"工具名称重复注册: {name}")
    _TOOL_REGISTRY[name] = cls
    return cls


def get_tool(name: str) -> BaseTool | None:
    cls = _TOOL_REGISTRY.get(name)
    return cls() if cls else None


def list_tools() -> dict[str, str]:
    return {name: cls.description for name, cls in _TOOL_REGISTRY.items()}
