"""
工具注册表（tools/registry.py）。

子 Agent 通过 @register_tool 注册工具，此处提供统一的
查询 / 清单 / 按风险等级筛选能力。
"""

from __future__ import annotations

from tools.base import BaseTool, ToolResult, get_tool, list_tools, register_tool


def list_tools_by_risk(risk: str) -> list[str]:
    """按风险等级列出工具名。risk: readonly | normal | risky。"""
    from tools.base import _TOOL_REGISTRY
    return [name for name, cls in _TOOL_REGISTRY.items() if cls.risk == risk]


def run_tool(name: str, **kwargs: object) -> ToolResult:
    """便捷执行工具；未注册或执行失败统一返回失败结果，不抛异常。"""
    tool = get_tool(name)
    if tool is None:
        return ToolResult.failure(f"工具未注册: {name}")
    try:
        return tool.run(**kwargs)
    except Exception as e:  # noqa: BLE001
        return ToolResult.failure(f"工具 {name} 执行异常: {e}")


__all__ = ["BaseTool", "ToolResult", "get_tool", "list_tools", "register_tool",
           "list_tools_by_risk", "run_tool"]
