"""
示例技能：文本文件摘要技能。

演示技能如何封装"多步骤任务"：
1. 解析任务提取文件路径
2. 读取文件内容
3. 生成结构化摘要

后续新增技能（如文件整理、发票识别、文档写作等）遵循同一模式：
继承 BaseSkill + @register_skill + 实现 execute()。
"""

from __future__ import annotations

import re
from pathlib import Path

from skills.base import BaseSkill, register_skill


@register_skill
class FileSummarySkill(BaseSkill):
    name = "file-summary"
    description = "对文本文件生成结构化摘要（行数/字数/关键词/首尾预览）"
    domain = "file"

    def execute(self, task: str, **kwargs: object) -> str:
        # 提取路径：优先取 kwargs 显式传入，否则从任务文本中正则提取
        path = kwargs.get("path")
        if not path:
            m = re.search(r"([~/][^\s，。]+)", task or "")
            if not m:
                return "[技能 file-summary] 缺少文件路径，示例：总结 ~/notes.md"
            path = m.group(1)

        fp = Path(path).expanduser()
        if not fp.exists():
            return f"[技能 file-summary] 文件不存在: {fp}"
        if not fp.is_file():
            return f"[技能 file-summary] {fp} 不是文件"

        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return f"[技能 file-summary] 读取失败: {e}"

        lines = text.splitlines()
        words = len(text.split())
        # 提取出现频次最高的几个词作为关键词（过滤常见停用词）
        stop = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
                "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好"}
        freq: dict[str, int] = {}
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", text):
            if token not in stop:
                freq[token] = freq.get(token, 0) + 1
        top = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:8]

        return (
            f"[技能 file-summary] {fp.name}\n"
            f"  大小: {fp.stat().st_size} 字节\n"
            f"  行数: {len(lines)} | 字数: {words}\n"
            f"  关键词: {', '.join(w for w, _ in top) or '(无)'}\n"
            f"  开头: {lines[0][:80] if lines else '(空文件)'}\n"
            f"  结尾: {lines[-1][:80] if lines else '(空文件)'}"
        )
