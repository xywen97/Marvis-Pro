"""
file-agent：本地文件搜索 / 读写 / 整理。

本骨架实现安全可用的基础能力：
- 按文件名关键词搜索（递归）
- 安全读取文本文件
- 写入文件（自动创建父目录，防覆盖：存在时自动追加序号）
- 文件整理预览（生成分类计划，不实际搬运，避免误操作）

安全约束：
- 拒绝操作系统保护目录（/System、/Library、/bin 等）
- 删除/搬运类破坏性操作仅输出计划，不直接执行
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from agents.base import BaseAgent, register_agent

# 系统保护目录黑名单（前缀匹配即拒绝）
PROTECTED_DIRS = (
    "/System", "/Library", "/bin", "/sbin", "/usr", "/private",
    "/Applications", "/Volumes",
)

# 默认项目工作目录（「当前项目/本地/这里」默认指向该目录）
DEFAULT_PROJECT_DIR = os.path.expanduser("~/project/marvis-xy")

# 目录树/分析时忽略的目录与文件
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".idea", ".vscode"}
CODE_EXTS = {
    ".py", ".js", ".ts", ".go", ".java", ".c", ".cpp", ".h", ".sh",
    ".yaml", ".yml", ".json", ".md", ".txt", ".toml", ".ini", ".html", ".css",
}

# 文件类型 -> 分类目录名（整理预览用）
EXT_CATEGORY = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic"},
    "doc": {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx", ".md", ".txt"},
    "code": {".py", ".js", ".ts", ".go", ".java", ".c", ".cpp", ".h", ".sh"},
    "archive": {".zip", ".tar", ".gz", ".rar", ".7z"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "audio": {".mp3", ".wav", ".flac", ".aac"},
}


def _is_protected(path: Path) -> bool:
    return any(str(path).startswith(p) for p in PROTECTED_DIRS)


@register_agent
class FileAgent(BaseAgent):
    name = "file-agent"
    description = "本地文件搜索 / 读写 / 整理"

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        # 指令识别：优先级 整理 > 目录树 > 项目分析 > 搜索 > 写 > 读
        if any(k in text for k in ("整理", "归类", "归档", "分类")):
            return self._organize_plan(text)
        if (any(k in text for k in ("目录树", "列目录", "项目结构"))
                or re.search(r"\b(ls|tree)\b", text, re.IGNORECASE)):
            return self._tree(text)
        if any(k in text for k in ("分析项目", "分析", "当前项目", "这个项目", "项目")):
            return self._analyze(text)
        if any(k in text for k in ("搜索", "查找", "找一下", "找文件", "在哪里")):
            return self._search(text)
        if any(k in text for k in ("写入", "创建", "新建", "生成文件", "保存为")):
            return self._write(text)
        if any(k in text for k in ("读取", "读一下", "查看内容", "打开文件", "内容")):
            return self._read(text)
        return f"[{self.name}] 暂无法识别文件指令：{text!r}。可尝试：搜索/整理/读写关键词。"

    # ------------------------------------------------------------------
    # 目录提取 / 目录树 / 项目分析
    # ------------------------------------------------------------------

    def _extract_dir(self, text: str, default: str = DEFAULT_PROJECT_DIR) -> Path:
        """从指令文本提取目录（支持 ~ 与绝对路径），未指明时用默认目录。"""
        m = re.search(r"([~/][^\s，。；）】\"']*)", text)
        if m:
            p = Path(os.path.expanduser(m.group(1)))
            if p.exists():
                return p
        return Path(default).expanduser()

    def _tree(self, text: str) -> str:
        base = self._extract_dir(text)
        if not base.exists():
            return f"[{self.name}] 目录不存在: {base}"
        lines: list[str] = [f"{base}/"]
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            rel = Path(dirpath).relative_to(base)
            depth = 0 if str(rel) == "." else len(rel.parts)
            indent = "    " * depth + ("├── " if depth else "")
            lines.append(f"{indent}{Path(dirpath).name}/")
            for fname in sorted(filenames):
                if fname == ".DS_Store" or fname.startswith("."):
                    continue
                lines.append(f"{indent}    {fname}")
        return f"[{self.name}] 目录树：{base}\n" + "\n".join(lines)

    def _analyze(self, text: str) -> str:
        base = self._extract_dir(text)
        if not base.exists():
            return f"[{self.name}] 目录不存在: {base}"
        total_files = 0
        total_dirs = 0
        ext_count: dict[str, int] = {}
        code_lines = 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            total_dirs += len(dirnames)
            for fname in filenames:
                if fname == ".DS_Store" or fname.startswith("."):
                    continue
                total_files += 1
                ext = Path(fname).suffix.lower() or "(无后缀)"
                ext_count[ext] = ext_count.get(ext, 0) + 1
                if ext in CODE_EXTS:
                    try:
                        with open(os.path.join(dirpath, fname), "r", encoding="utf-8", errors="ignore") as f:
                            code_lines += sum(1 for _ in f)
                    except OSError:
                        pass
        top_exts = sorted(ext_count.items(), key=lambda x: -x[1])[:8]
        ext_desc = ", ".join(f"{k} x{v}" for k, v in top_exts) if top_exts else "(无)"
        return (
            f"[{self.name}] 项目分析：{base}\n"
            f"- 目录数：{total_dirs}\n"
            f"- 文件数：{total_files}\n"
            f"- 代码/文档行数（估）：{code_lines}\n"
            f"- 主要文件类型：{ext_desc}\n"
            f"- 目录树：\n{self._tree(text)}"
        )

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    def _search(self, text: str) -> str:
        # 提取可能的目录（路径中带 / 或 ~），否则默认家目录
        m = re.search(r"(?:在|位于|目录)?\s*([~/][^\s，。]*)", text)
        base = os.path.expanduser(m.group(1)) if m else "~"
        base_path = Path(base).expanduser()
        if not base_path.exists():
            return f"[{self.name}] 目录不存在: {base_path}"

        # 提取关键词：先剔除路径本身，再去掉指令词后剩余的实词
        kw_part = text
        if m:
            kw_part = kw_part.replace(m.group(1), " ")
        kw_part = re.sub(r"搜索|查找|找一下|找文件|在哪里|文件|目录|下的|下|在|位于", "", kw_part)
        keywords = [w for w in re.split(r"[\s，。]+", kw_part.strip()) if w]

        hits: list[Path] = []
        for root, _dirs, files in os.walk(base_path):
            root_path = Path(root)
            if _is_protected(root_path):
                continue
            for fname in files:
                fp = root_path / fname
                if not keywords or all(k.lower() in fname.lower() for k in keywords):
                    hits.append(fp)
                    if len(hits) >= 50:  # 结果上限，避免刷屏
                        break
            if len(hits) >= 50:
                break

        if not hits:
            return f"[{self.name}] 在 {base_path} 下未找到匹配文件（关键词: {keywords or '全部'}）。"
        lines = [f"[{self.name}] 共找到 {len(hits)} 个匹配文件（关键词: {keywords or '全部'}）:"]
        lines += [f"  - {fp}" for fp in hits[:20]]
        if len(hits) > 20:
            lines.append(f"  ... 等共 {len(hits)} 个")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def _read(self, text: str) -> str:
        m = re.search(r"([~/][^\s，。]+)", text)
        if not m:
            return f"[{self.name}] 读取指令缺少文件路径。示例：读取 ~/notes.txt 的内容"
        path = Path(os.path.expanduser(m.group(1)))
        if not path.exists():
            return f"[{self.name}] 文件不存在: {path}"
        if path.is_dir():
            return f"[{self.name}] {path} 是目录，可尝试：搜索 {path} 下的文件"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return f"[{self.name}] 读取失败（可能为二进制或权限问题）: {e}"
        preview = content[:500].replace("\n", "\n    ")
        return f"[{self.name}] {path}:\n    {preview}" + ("\n    ...(已截断)" if len(content) > 500 else "")

    # ------------------------------------------------------------------
    # 写入（安全：自动建目录、防覆盖）
    # ------------------------------------------------------------------

    def _write(self, text: str) -> str:
        m = re.search(r"([~/][^\s，。]+)", text)
        if not m:
            return f"[{self.name}] 写入指令缺少目标路径。示例：写入 ~/notes.md 内容为 xxx"
        target = Path(os.path.expanduser(m.group(1)))
        if _is_protected(target):
            return f"[{self.name}] 拒绝写入系统保护目录: {target}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # 防覆盖：已存在则自动追加序号
            final = target
            idx = 1
            while final.exists():
                final = target.with_name(f"{target.stem}_{idx}{target.suffix}")
                idx += 1
            final.write_text("", encoding="utf-8")
            return f"[{self.name}] 已创建空文件（内容请通过接口/后续步骤写入）: {final}"
        except Exception as e:  # noqa: BLE001
            return f"[{self.name}] 写入失败: {e}"

    # ------------------------------------------------------------------
    # 整理（仅输出计划，不执行搬运）
    # ------------------------------------------------------------------

    def _organize_plan(self, text: str) -> str:
        m = re.search(r"(?:整理|归类|归档|分类)?\s*([~/][^\s，。]*)", text)
        base = os.path.expanduser(m.group(1)) if m and m.group(1) else "~"
        base_path = Path(base).expanduser()
        if not base_path.is_dir():
            return f"[{self.name}] 目录不存在: {base_path}"

        plan: dict[str, list[Path]] = {}
        for entry in base_path.iterdir():
            if not entry.is_file():
                continue
            cat = next((c for c, exts in EXT_CATEGORY.items() if entry.suffix.lower() in exts), "other")
            plan.setdefault(cat, []).append(entry)

        if not plan:
            return f"[{self.name}] {base_path} 下没有可整理的散落文件。"
        lines = [f"[{self.name}] 整理预览（不会自动执行，请确认后通过工具执行）: {base_path}"]
        for cat, files in sorted(plan.items()):
            lines.append(f"  [{cat}] {len(files)} 个:")
            lines += [f"    - {p.name}" for p in files[:5]]
            if len(files) > 5:
                lines.append(f"    ... 等 {len(files)} 个")
        lines.append("提示：如需实际移动，请提供明确的目标目录规划后再执行。")
        return "\n".join(lines)
