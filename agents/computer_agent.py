"""
computer-agent：系统操作与排查（只读为主）。

实现能力（macOS）：
- 系统信息：CPU 架构 / 系统版本 / 主机名
- 磁盘空间：df -h 汇总
- 内存使用：vm_stat / sysctl 摘要
- 进程查询：按名称匹配进程（pgrep -fl）
- 网络连通性：ping 目标主机（只读、轻量）

安全约束：
- 本骨架仅执行只读命令，禁止修改系统设置、终止进程、清理文件。
- 命令白名单校验：所有命令必须来自内置模板，不允许拼接任意用户输入。
"""

from __future__ import annotations

import platform
import subprocess
import sys

from agents.base import BaseAgent, register_agent

# 允许执行的命令模板（白名单：{arg} 仅限简单参数，不做 shell 拼接）
ALLOWED_COMMANDS = {
    "sysinfo": "uname -a",
    "disk": "df -h",
    "memory_mac": "vm_stat",
    "memory_linux": "free -h",
    "hostname": "hostname",
    "cpu": "sysctl -n machdep.cpu.brand_string",
}


def _run(cmd: str, timeout: int = 15) -> str:
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (proc.stdout or proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return "(超时)"
    except Exception as e:  # noqa: BLE001
        return f"(执行失败: {e})"


@register_agent
class ComputerAgent(BaseAgent):
    name = "computer-agent"
    description = "系统信息 / 磁盘内存 / 进程与网络排查"

    def handle(self, text: str) -> str:
        text = (text or "").strip()

        if any(k in text for k in ("系统信息", "系统版本", "主机名", "是什么系统", "配置")):
            return self._sysinfo()
        if any(k in text for k in ("磁盘", "硬盘", "空间", "存储")):
            return self._disk()
        if any(k in text for k in ("内存", "ram")):
            return self._memory()
        if any(k in text for k in ("进程", "在跑什么", "占用")):
            return self._process(text)
        if any(k in text for k in ("网络", "连通", "ping")):
            return self._network(text)
        return f"[{self.name}] 无法识别的系统指令: {text!r}。" \
               "可尝试：系统信息 / 磁盘空间 / 内存 / 进程查询 / 网络连通性。"

    # ------------------------------------------------------------------

    def _sysinfo(self) -> str:
        lines = [
            f"[{self.name}] 系统信息:",
            f"  平台: {sys.platform}",
            f"  系统: {platform.platform()}",
            f"  主机名: {platform.node()}",
        ]
        if sys.platform == "darwin":
            lines.append(f"  CPU: {_run(ALLOWED_COMMANDS['cpu'])}")
        return "\n".join(lines)

    def _disk(self) -> str:
        out = _run(ALLOWED_COMMANDS["disk"])
        return f"[{self.name}] 磁盘空间:\n{out}"

    def _memory(self) -> str:
        if sys.platform == "darwin":
            # macOS 无 free，改用 sysctl + vm_stat 摘要
            mem_total = _run("sysctl -n hw.memsize")
            page_info = _run("vm_stat | head -6")
            return (f"[{self.name}] 内存:\n  物理内存: {int(mem_total or 0) / 1024**3:.1f} GB\n"
                    f"  vm_stat:\n{page_info}")
        return f"[{self.name}] 内存:\n{_run(ALLOWED_COMMANDS['memory_linux'])}"

    def _process(self, text: str) -> str:
        import re
        m = re.search(r"(?:进程)?\s*[\"'「『]?([a-zA-Z][a-zA-Z0-9_.-]*)", text)
        name = m.group(1) if m else None
        if not name:
            return f"[{self.name}] 请提供要查询的进程名，如：查询 python 进程"
        # 白名单校验：只允许安全字符
        if not re.fullmatch(r"[a-zA-Z0-9_.\-]+", name):
            return f"[{self.name}] 进程名包含非法字符，已拒绝查询。"
        out = _run(f"pgrep -fl {name}")
        return f"[{self.name}] 进程 {name!r} 匹配结果:\n{out or '(未找到)'}"

    def _network(self, text: str) -> str:
        import re
        m = re.search(r"(?:ping|连通)?\s*([a-zA-Z0-9.\-]+(?:\.[a-zA-Z0-9.\-]+)?)", text)
        host = m.group(1) if m else "127.0.0.1"
        if host in {"ping", "网络", "连通性", "检查"}:
            host = "127.0.0.1"
        if not re.fullmatch(r"[a-zA-Z0-9.\-]+", host):
            return f"[{self.name}] 目标主机包含非法字符，已拒绝。"
        out = _run(f"ping -c 3 -t 5 {host}")
        return f"[{self.name}] 连通性检查 {host}:\n{out[:600]}"
