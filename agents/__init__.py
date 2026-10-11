"""
子 Agent 模块包：导入各 Agent 模块以触发注册。
"""

from agents.base import BaseAgent, get_agent, list_agents, register_agent
from agents import file_agent, app_agent, browser_agent, computer_agent  # noqa: F401

__all__ = ["BaseAgent", "get_agent", "list_agents", "register_agent"]
