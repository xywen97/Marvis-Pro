"""
browser-agent：网页浏览交互（骨架版）。

实现原则：
- 优先通过标准库 urllib 完成页面获取 / 链接提取，零第三方依赖即可运行。
- 预留 Playwright / requests 增强接口，接入真实浏览器自动化时替换。
- 仅执行只读类网页操作（GET 页面、提取标题与链接），不提交表单。

安全约束：
- 只允许 http/https 协议
- 拒绝内网/本机回环地址以外的敏感目标（骨架阶段仅做协议校验）
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

from agents.base import BaseAgent, register_agent

USER_AGENT = "MarvisXY-BrowserAgent/0.1 (+local desktop assistant)"


def _extract_title(body: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
    return html.unescape(m.group(1).strip()) if m else "(无标题)"


def _extract_links(base: str, body: str, limit: int = 10) -> list[str]:
    links: list[str] = []
    for m in re.finditer(r'href=["\']([^"\'#]+)["\']', body, re.IGNORECASE):
        href = m.group(1).strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = urllib.parse.urljoin(base, href)
        if absolute.startswith(("http://", "https://")):
            links.append(absolute)
        if len(links) >= limit:
            break
    return links


@register_agent
class BrowserAgent(BaseAgent):
    name = "browser-agent"
    description = "网页浏览 / 链接提取 / 页面信息获取"

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        url = self._extract_url(text)
        if not url:
            return (f"[{self.name}] 未在指令中找到有效网址。示例："
                    "打开 https://example.com 看看内容；提取 https://example.com 的链接")
        if not url.startswith(("http://", "https://")):
            return f"[{self.name}] 仅支持 http/https 协议: {url}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read(4096 * 4).decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return f"[{self.name}] 访问 {url} 失败: {e}"

        title = _extract_title(body)
        if "提取链接" in text or "链接" in text:
            links = _extract_links(url, body)
            out = [f"[{self.name}] {url} 标题: {title}", f"页面链接（前 {len(links)} 个）:"]
            out += [f"  - {l}" for l in links] or ["  (未提取到)"]
            return "\n".join(out)

        # 默认返回页面正文摘要（去标签）
        text_only = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.DOTALL | re.IGNORECASE)
        text_only = re.sub(r"<[^>]+>", " ", text_only)
        text_only = html.unescape(re.sub(r"\s+", " ", text_only)).strip()
        return f"[{self.name}] {url}\n标题: {title}\n摘要: {text_only[:300] or '(页面无可读文本)'}"

    def _extract_url(self, text: str) -> str:
        m = re.search(r"(https?://[^\s，。、\"'）)]+)", text)
        return m.group(1) if m else ""
