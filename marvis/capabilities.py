"""Structured capabilities. All paths pass through the same allowlist boundary."""
from __future__ import annotations
import csv
import hashlib
import html
import io
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from html.parser import HTMLParser

TEXT = {'.txt', '.md', '.py', '.js', '.ts', '.json', '.csv', '.html', '.css', '.yaml', '.yml', '.toml', '.log', '.rst'}
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.heic', '.gif', '.bmp'}
SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.marvis', '.aws', '.ssh', '.codex', '.agents'}


def category(path):
    ext = path.suffix.lower()
    if ext in IMAGES: return '图片'
    if ext in {'.pdf', '.docx', '.txt', '.md', '.rtf'}: return '文档'
    if ext in {'.csv', '.xlsx', '.xls'}: return '表格'
    if ext in {'.mp4', '.mov', '.mkv'}: return '视频'
    if ext in {'.mp3', '.wav', '.m4a'}: return '音频'
    if ext in {'.zip', '.gz', '.tar'}: return '压缩包'
    if ext in TEXT: return '代码'
    return '其他'


def schema(name, description, fields, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description, 'parameters': {'type': 'object', 'properties': fields, 'required': list(required), 'additionalProperties': False}}}


STRING = {'type': 'string'}
TOOLS = [
    schema('file_list', 'File Agent: 列出允许目录中的文件，按文件名或文本内容搜索。', {'path': STRING, 'query': STRING, 'content': {'type': 'boolean'}}, ['path']),
    schema('file_read', 'File Agent: 读取文本/PDF/DOCX/XLSX 内容。', {'path': STRING}, ['path']),
    schema('file_write', 'File Agent: 创建文件并写入完整文本；需要用户确认；不覆盖。', {'path': STRING, 'text': STRING}, ['path', 'text']),
    schema('file_organize', 'File Agent: 按类型移动目录内散落文件，展示计划并等待确认。', {'path': STRING}, ['path']),
    schema('file_duplicates', 'File Agent: 按 SHA256 查找重复文件，不删除。', {'path': STRING}, ['path']),
    schema('document_convert', 'File Agent: CSV 转 JSON/Markdown、JSON 转 CSV、文档提取为 TXT；不覆盖；需确认。', {'path': STRING, 'format': {'type': 'string', 'enum': ['json', 'csv', 'md', 'txt']}}, ['path', 'format']),
    schema('knowledge_index', 'File Agent: 建立本地文档和图片元数据索引（不提供人像/语义识别）。', {'path': STRING}, ['path']),
    schema('knowledge_search', 'Search Agent: 搜索已索引文件名和文本内容。', {'query': STRING}, ['query']),
    schema('image_ocr', 'File Agent: 使用已安装的 tesseract 本地 OCR，不上传。', {'path': STRING}, ['path']),
    schema('system_info', 'Computer Agent: 查询实际系统、磁盘、内存、电池、进程。', {'section': {'type': 'string', 'enum': ['overview', 'disk', 'memory', 'processes', 'battery']} }),
    schema('app_list', 'App Agent: 列出本机已安装应用。', {}),
    schema('app_open', 'App Agent: 打开已安装 macOS 应用，等待用户确认。', {'name': STRING}, ['name']),
    schema('browser_read', 'Browser Agent: 获取公开 HTTP(S) 网页正文，非浏览器点击自动化。', {'url': STRING}, ['url']),
    schema('browser_open', 'Browser Agent: 在系统浏览器打开网页，等待用户确认。', {'url': STRING}, ['url']),
    schema('web_search', 'Search Agent: DuckDuckGo 网页搜索，返回原始链接；网络失败如实报错。', {'query': STRING}, ['query']),
]
MUTATIONS = {'file_write', 'file_organize', 'document_convert', 'app_open', 'browser_open'}


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden and data.strip(): self.parts.append(data.strip())


class Capabilities:
    def __init__(self, store, check=lambda: None, local=False):
        self.store, self.check, self.local = store, check, local

    def path(self, value):
        p = Path(value).expanduser().resolve()
        roots = [Path(r).expanduser().resolve() for r in self.store.setting('roots', [str(Path.cwd())])]
        if not any(p == r or r in p.parents for r in roots):
            raise PermissionError('目录未授权，请先在设置中添加：' + str(p))
        if any(part in SKIP or part.startswith('.env') for part in p.parts):
            raise PermissionError('凭据、内部数据和隐藏配置目录不允许读取或修改。')
        if self.store.directory == p or self.store.directory in p.parents:
            raise PermissionError('不能操作助手自己的数据库。')
        return p

    def walk(self, base, limit=5000):
        count = 0
        for root, dirs, files in os.walk(base, followlinks=False):
            self.check()
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.') and not (Path(root) / d).is_symlink())
            for name in sorted(files):
                if name.startswith('.'): continue
                p = Path(root) / name
                if p.is_symlink(): continue
                try: p = self.path(str(p))
                except PermissionError: continue
                yield p
                count += 1
                if count >= limit: return

    def read(self, path):
        p = self.path(path)
        if not p.is_file(): raise ValueError('文件不存在：' + str(p))
        if p.stat().st_size > 20 * 1024 * 1024: raise ValueError('文件超过 20 MB 读取限制。')
        ext = p.suffix.lower()
        if ext == '.pdf':
            try: from pypdf import PdfReader
            except ImportError: raise ValueError('PDF 解析需要安装 requirements-office.txt。')
            return '\n'.join((page.extract_text() or '') for page in PdfReader(p).pages[:100])[:100000]
        if ext == '.docx':
            try: from docx import Document
            except ImportError: raise ValueError('Word 解析需要安装 requirements-office.txt。')
            doc = Document(p)
            return '\n'.join([x.text for x in doc.paragraphs] + [' | '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows])[:100000]
        if ext == '.xlsx':
            try: from openpyxl import load_workbook
            except ImportError: raise ValueError('Excel 解析需要安装 requirements-office.txt。')
            wb = load_workbook(p, read_only=True, data_only=True)
            try:
                out = []
                for sheet in wb:
                    out.append('[' + sheet.title + ']')
                    for row in sheet.iter_rows(max_row=1000, values_only=True): out.append(' | '.join(str(c) if c is not None else '' for c in row))
                return '\n'.join(out)[:100000]
            finally: wb.close()
        if ext not in TEXT: raise ValueError('不支持读取此格式；图片可使用 OCR。')
        with p.open('r', encoding='utf-8', errors='replace') as f: return f.read(100000)

    def organize_plan(self, path):
        base = self.path(path)
        if not base.is_dir(): raise ValueError('整理目标必须是目录。')
        moves = []
        for p in sorted(base.iterdir()):
            self.check()
            if p.is_file() and not p.is_symlink() and not p.name.startswith('.'):
                try: self.path(str(p))
                except PermissionError: continue
                dst = self.path(str(base / category(p) / p.name))
                if dst.exists(): raise ValueError('目标文件已存在，整理中止：' + str(dst))
                moves.append({'source': str(p), 'target': str(dst), 'size': p.stat().st_size, 'modified': p.stat().st_mtime_ns})
        return moves

    def preview(self, name, args):
        if name == 'file_organize':
            moves = self.organize_plan(args['path'])
            return {'action': '按类型移动文件', 'moves': moves, 'count': len(moves)}
        if name == 'file_write':
            p = self.path(args['path'])
            if p.exists(): raise ValueError('禁止覆盖已有文件：' + str(p))
            return {'action': '创建文件', 'path': str(p), 'text': args['text']}
        if name == 'document_convert':
            p = self.path(args['path']); dst = self.path(str(p.with_suffix('.' + args['format'])))
            if dst.exists(): raise ValueError('输出文件已存在：' + str(dst))
            return {'action': '转换格式', 'source': str(p), 'target': str(dst)}
        if name == 'app_open':
            if args['name'] not in self.app_names(): raise ValueError('应用未安装或名称不准确。请先查看应用列表。')
        if name == 'browser_open': self.validate_url(args['url'])
        return {'action': name, 'arguments': args}

    def execute(self, name, args, plan=None):
        self.check()
        if name not in {t['function']['name'] for t in TOOLS}: raise ValueError('未知工具：' + name)
        if name in MUTATIONS and plan is None: raise PermissionError('操作必须先通过确认。')
        if name == 'file_list':
            base = self.path(args['path'])
            if not base.is_dir(): raise ValueError('路径必须是目录。')
            q = args.get('query', '').casefold(); rows = []
            for p in self.walk(base):
                match = q in p.name.casefold()
                if q and not match and args.get('content'):
                    try: match = q in self.read(str(p)).casefold()
                    except (ValueError, OSError): pass
                if match:
                    stat = p.stat(); rows.append({'path': str(p), 'name': p.name, 'category': category(p), 'size': stat.st_size, 'modified': stat.st_mtime})
                if len(rows) >= 100: break
            return {'files': rows, 'limit': 100, 'scan_limit': 5000}
        if name == 'file_read': return {'path': str(self.path(args['path'])), 'text': self.read(args['path'])}
        if name == 'file_write':
            p = self.path(args['path']); p.parent.mkdir(parents=True, exist_ok=True)
            with p.open('x', encoding='utf-8') as f: f.write(args['text'])
            return {'created': str(p), 'bytes': p.stat().st_size}
        if name == 'file_organize':
            # Validate the entire snapshot before moving anything.
            for move in plan['moves']:
                src, dst = self.path(move['source']), self.path(move['target'])
                st = src.stat()
                if st.st_size != move['size'] or st.st_mtime_ns != move['modified'] or dst.exists(): raise ValueError('文件已发生变化，请重新生成整理计划。')
            done = []
            try:
                for move in plan['moves']:
                    self.check(); src, dst = self.path(move['source']), self.path(move['target'])
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    # Hard-link then unlink provides no-clobber semantics on the same filesystem.
                    os.link(src, dst); src.unlink(); done.append(move)
            except Exception:
                for move in reversed(done):
                    try:
                        os.link(move['target'], move['source']); Path(move['target']).unlink()
                    except OSError: pass
                raise
            return {'moved': done, 'count': len(done)}
        if name == 'file_duplicates':
            groups = {}; sizes = {}
            for p in self.walk(self.path(args['path'])):
                sizes.setdefault(p.stat().st_size, []).append(p)
            for size, paths in sizes.items():
                if len(paths) < 2: continue
                for p in paths:
                    digest = hashlib.sha256()
                    with p.open('rb') as f:
                        while True:
                            self.check(); chunk = f.read(1024 * 1024)
                            if not chunk: break
                            digest.update(chunk)
                    groups.setdefault(digest.hexdigest(), []).append(str(p))
            return {'duplicates': [v for v in groups.values() if len(v) > 1]}
        if name == 'document_convert':
            p = self.path(args['path']); fmt = args['format']; raw = self.read(str(p))
            if p.suffix.lower() == '.csv' and fmt in ('json', 'md'):
                rows = list(csv.DictReader(io.StringIO(raw)))
                if fmt == 'json': output = json.dumps(rows, ensure_ascii=False, indent=2)
                else:
                    cols = list(rows[0]) if rows else []
                    escape = lambda v: str(v or '').replace('|', '\\|').replace('\n', ' ')
                    output = '\n'.join([' | '.join(cols), ' | '.join('---' for _ in cols)] + [' | '.join(escape(r[c]) for c in cols) for r in rows])
            elif p.suffix.lower() == '.json' and fmt == 'csv':
                rows = json.loads(raw)
                if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows): raise ValueError('JSON 必须是对象数组。')
                out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
                writer.writeheader(); writer.writerows(rows); output = out.getvalue()
            elif fmt == 'txt': output = raw
            else: raise ValueError('当前支持 CSV→JSON/Markdown、JSON→CSV、文档→TXT。')
            dst = self.path(plan['target']);
            with dst.open('x', encoding='utf-8') as f: f.write(output)
            return {'created': str(dst)}
        if name == 'knowledge_index':
            base = self.path(args['path']); indexed = 0; skipped = 0; seen = set()
            if not base.is_dir(): raise ValueError('索引目标必须是目录。')
            for p in self.walk(base):
                seen.add(str(p)); content = ''
                if p.suffix.lower() in TEXT | {'.pdf', '.docx', '.xlsx'}:
                    try: content = self.read(str(p))
                    except (ValueError, OSError): skipped += 1
                elif p.suffix.lower() not in IMAGES: continue
                st = p.stat()
                self.store.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)', (str(p), p.name, category(p), content, st.st_size, st.st_mtime)); indexed += 1
            # Remove stale entries only when their files have actually disappeared.
            for row in self.store.rows('SELECT path FROM documents'):
                if not Path(row['path']).exists(): self.store.execute('DELETE FROM documents WHERE path=?', (row['path'],))
            return {'indexed': indexed, 'text_unavailable': skipped, 'scan_limit': 5000, 'images': '仅文件名、类型和时间元数据；不包含人像识别'}
        if name == 'knowledge_search':
            query = args['query'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            rows = self.store.rows("SELECT path,name,category,size,modified,substr(text,1,240) AS preview FROM documents WHERE name LIKE ? ESCAPE '\\' OR text LIKE ? ESCAPE '\\' ORDER BY modified DESC LIMIT 100", ('%' + query + '%', '%' + query + '%'))
            allowed = []
            for row in rows:
                try: self.path(row['path']); allowed.append(row)
                except PermissionError: continue
            return {'files': allowed}
        if name == 'image_ocr':
            p = self.path(args['path'])
            if p.suffix.lower() not in IMAGES: raise ValueError('OCR 输入必须是图片。')
            exe = shutil.which('tesseract')
            if not exe: raise ValueError('请先安装 tesseract 和中文语言包；当前 OCR 未就绪。')
            return {'text': self.command([exe, str(p), 'stdout', '-l', 'chi_sim+eng'], timeout=60)}
        if name == 'system_info': return self.system(args.get('section', 'overview'))
        if name == 'app_list': return {'apps': self.app_names()}
        if name == 'app_open':
            if sys.platform != 'darwin': raise ValueError('应用启动目前仅支持 macOS。')
            if args['name'] not in self.app_names(): raise ValueError('应用未安装。')
            self.command(['open', '-a', args['name']]); return {'opened': args['name']}
        if name == 'browser_open':
            self.validate_url(args['url'])
            if self.local: raise PermissionError('本地模式禁止访问网页，请切换效率模式。')
            if sys.platform != 'darwin': raise ValueError('浏览器启动目前仅支持 macOS。')
            self.command(['open', args['url']]); return {'opened': args['url']}
        if name == 'browser_read':
            body = self.fetch(args['url']); parser = PageText(); parser.feed(body)
            return {'url': args['url'], 'text': '\n'.join(parser.parts)[:20000]}
        if name == 'web_search':
            url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(args['query'])
            body = self.fetch(url); found = []
            for href, label in re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
                href = html.unescape(href)
                if href.startswith('//'): href = 'https:' + href
                href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get('uddg', [href])[0]
                found.append({'title': html.unescape(re.sub('<[^>]+>', '', label)), 'url': href})
            if not found: raise ValueError('搜索服务未返回结果或触发访问限制；可直接提供网页 URL。')
            return {'results': found[:8], 'source': 'DuckDuckGo'}
        raise ValueError('工具尚未实现。')

    @staticmethod
    def command(args, timeout=15):
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, shell=False)
        if p.returncode: raise ValueError((p.stderr or p.stdout or '命令失败')[:1000])
        return p.stdout.strip()

    @staticmethod
    def app_names():
        if sys.platform != 'darwin': return []
        return sorted({p.stem for root in [Path('/Applications'), Path('/System/Applications'), Path.home() / 'Applications'] if root.exists() for p in root.rglob('*.app') if '.app/' not in str(p)})

    def system(self, section):
        if section == 'disk':
            disk = shutil.disk_usage(Path.home()); return {'total_gb': round(disk.total / 2**30, 1), 'used_gb': round(disk.used / 2**30, 1), 'free_gb': round(disk.free / 2**30, 1)}
        commands = {'memory': ['vm_stat'], 'battery': ['pmset', '-g', 'batt'], 'processes': ['ps', '-axo', 'pid,comm,%cpu,%mem']}
        if section in commands:
            if sys.platform != 'darwin' and section != 'processes': raise ValueError('此查询目前仅支持 macOS。')
            return {'section': section, 'output': self.command(commands[section])[:10000]}
        return {'system': platform.platform(), 'hostname': platform.node(), 'architecture': platform.machine(), 'cpu_count': os.cpu_count(), 'disk': self.system('disk')}

    def validate_url(self, url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('只支持无凭据的 HTTP(S) URL。')
        if self.local: raise PermissionError('本地模式禁止外部网络工具。')
        # Reject loopback, private networks and link-local metadata endpoints.
        import ipaddress
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): raise PermissionError('网页工具仅允许公开网络地址。')
        return parsed

    def fetch(self, url):
        self.validate_url(url)
        owner = self
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                owner.validate_url(newurl)
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        opener = urllib.request.build_opener(Redirect)
        req = urllib.request.Request(url, headers={'User-Agent': 'MarvisXY/0.2'})
        with opener.open(req, timeout=20) as resp:
            kind = resp.headers.get('Content-Type', '')
            if not any(x in kind for x in ('text/', 'json', 'xml')): raise ValueError('网页工具仅支持文本响应。')
            raw = resp.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024: raise ValueError('网页超过 2 MB 限制。')
            return raw.decode(resp.headers.get_content_charset() or 'utf-8', errors='replace')
