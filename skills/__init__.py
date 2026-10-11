"""
技能抽象层包：注册内置示例技能。
"""

from skills.base import BaseSkill, get_skill, list_skills, register_skill

# 内置示例技能（骨架阶段提供 1 个可运行示例）
from skills.sample_file_skill import FileSummarySkill  # noqa: E402,F401

__all__ = ["BaseSkill", "get_skill", "list_skills", "register_skill"]
