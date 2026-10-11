"""
技能注册表（skills/registry.py）。

提供技能注册、查询与按领域过滤能力。
"""

from __future__ import annotations

from skills.base import BaseSkill, get_skill, list_skills, register_skill


def list_skills_by_domain(domain: str) -> list[str]:
    """按领域列出技能名。"""
    from skills.base import _SKILL_REGISTRY
    return [name for name, cls in _SKILL_REGISTRY.items() if cls.domain == domain]


__all__ = ["BaseSkill", "get_skill", "list_skills", "register_skill",
           "list_skills_by_domain"]
