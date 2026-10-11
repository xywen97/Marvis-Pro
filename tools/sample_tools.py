"""
示例工具集：演示工具注册与统一返回结构。

- EchoTool: 回显输入（只读）
- NowTool: 返回当前本地时间（只读）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tools.base import BaseTool, ToolResult, register_tool


@register_tool
class EchoTool(BaseTool):
    name = "echo"
    description = "回显传入的 message 参数"
    risk = "readonly"

    def run(self, **kwargs: Any) -> ToolResult:
        msg = kwargs.get("message", "")
        return ToolResult.success(data={"echo": msg}, message=f"已回显: {msg}")


@register_tool
class NowTool(BaseTool):
    name = "now"
    description = "返回当前本地时间（ISO 格式）"
    risk = "readonly"

    def run(self, **kwargs: Any) -> ToolResult:
        now = datetime.now().isoformat(timespec="seconds")
        return ToolResult.success(data={"now": now}, message=f"当前时间: {now}")
