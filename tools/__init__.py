"""
工具抽象层包：注册内置示例工具。
"""

from tools.base import BaseTool, ToolResult, get_tool, list_tools, register_tool

# 内置示例工具（骨架阶段提供 2 个可运行示例）
from tools.sample_tools import EchoTool, NowTool  # noqa: E402,F401

__all__ = ["BaseTool", "ToolResult", "get_tool", "list_tools", "register_tool"]
