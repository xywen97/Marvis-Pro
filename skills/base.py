"""
技能抽象层（skills/）。

技能是"多步骤、领域化"的能力封装，可组合多个工具完成复杂任务。
- BaseSkill: 技能基类
- register_skill / get_skill / list_skills: 注册与查询
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseSkill(ABC):
    """技能基类。"""

    name: str = "base-skill"
    description: str = ""
    #: 技能涉及的领域（如 file / app / browser / computer），便于路由
    domain: str = ""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config: dict[str, Any] = config or {}

    @abstractmethod
    def execute(self, task: str, **kwargs: Any) -> Any:
        """执行技能任务，返回结果（文本 / 结构化数据）。"""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_SKILL_REGISTRY: dict[str, type[BaseSkill]] = {}


def register_skill(cls: type[BaseSkill]) -> type[BaseSkill]:
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"技能类 {cls.__name__} 必须定义 name 属性")
    if name in _SKILL_REGISTRY:
        raise ValueError(f"技能名称重复注册: {name}")
    _SKILL_REGISTRY[name] = cls
    return cls


def get_skill(name: str) -> BaseSkill | None:
    cls = _SKILL_REGISTRY.get(name)
    return cls() if cls else None


def list_skills() -> dict[str, str]:
    return {name: cls.description for name, cls in _SKILL_REGISTRY.items()}
