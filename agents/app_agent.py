"""
app-agent：应用操作（打开 / 关闭 / 安装 / 卸载 / 状态查询）。

macOS 实现：
- 打开应用：open -a <AppName> 或 open <app_path>
- 关闭应用：osascript 退出指定应用（安全：仅允许退出用户应用）
- 查询状态：pgrep 匹配进程名

安全约束：
- 卸载/删除类操作仅输出建议，不直接执行
- 拒绝操作系统核心进程
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from agents.base import BaseAgent, register_agent

# 系统核心进程黑名单（匹配即拒绝操作）
SYSTEM_PROCESS_BLACKLIST = {"kernel_task", "launchd", "WindowServer", "loginwindow"}


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return proc.returncode, (proc.stdout or proc.stderr).strip()
    except FileNotFoundError:
        return -1, f"命令不存在: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "命令执行超时"


@register_agent
class AppAgent(BaseAgent):
    name = "app-agent"
    description = "应用打开 / 关闭 / 安装 / 卸载 / 状态查询"

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        app_name = self._extract_app_name(text)

        if any(k in text for k in ("打开", "启动", "运行")):
            return self._open(app_name)
        if any(k in text for k in ("关闭", "退出", "杀掉")):
            return self._close(app_name)
        if any(k in text for k in ("状态", "在跑", "是否运行", "有没有开")):
            return self._status(app_name)
        if any(k in text for k in ("安装", "下载")):
            return self._install_hint(app_name)
        if any(k in text for k in ("卸载", "删除应用")):
            return self._uninstall_hint(app_name)
        return f"[{self.name}] 无法识别的应用指令: {text!r}"

    # ------------------------------------------------------------------

    def _extract_app_name(self, text: str) -> str:
        # 去掉指令词后取第一个剩余词作为应用名
        cleaned = text
        for w in ("帮我", "请", "打开", "启动", "运行", "关闭", "退出", "杀掉",
                  "状态", "是否运行", "安装", "下载", "卸载", "应用", "软件"):
            cleaned = cleaned.replace(w, " ")
        cleaned = cleaned.strip()
        if not cleaned:
            return "Finder"
        # 优先取括号/引号中的完整名，否则取第一个词
        import re
        m = re.search(r"[「『\"']([^」』\"']+)[」』\"']", text)
        return m.group(1) if m else cleaned.split()[0]

    def _open(self, app_name: str) -> str:
        if sys.platform != "darwin":
            return f"[{self.name}] 当前平台 {sys.platform} 暂仅实现 macOS 命令。"
        code, out = _run(["open", "-a", app_name])
        return f"[{self.name}] 打开应用 {app_name!r}: {'成功' if code == 0 else '失败'}" + (f" | {out}" if out else "")

    def _close(self, app_name: str) -> str:
        if app_name.lower() in SYSTEM_PROCESS_BLACKLIST:
            return f"[{self.name}] 拒绝操作系统核心进程: {app_name}"
        if sys.platform != "darwin":
            return f"[{self.name}] 当前平台 {sys.platform} 暂仅实现 macOS 命令。"
        script = f'tell application "{app_name}" to quit'
        code, out = _run(["osascript", "-e", script])
        return f"[{self.name}] 关闭应用 {app_name!r}: {'成功' if code == 0 else '失败'}" + (f" | {out}" if out else "")

    def _status(self, app_name: str) -> str:
        code, out = _run(["pgrep", "-x", app_name])
        if code == 0 and out:
            return f"[{self.name}] 应用 {app_name!r} 正在运行（PID: {out.splitlines()[0]}）"
        return f"[{self.name}] 应用 {app_name!r} 未在运行"

    def _install_hint(self, app_name: str) -> str:
        return (f"[{self.name}] 安装应用 {app_name!r} 属于网络下载 + 系统级变更，"
                "建议使用官方渠道（App Store / 官网 dmg）手动安装，或交由具备浏览器能力的 Agent 处理。")

    def _uninstall_hint(self, app_name: str) -> str:
        return (f"[{self.name}] 卸载应用 {app_name!r} 将删除应用及其数据，属于高风险操作，"
                "骨架阶段不自动执行。请先备份并确认后手动处理（拖入废纸篓）。")
