"""Structured capabilities. All paths pass through the same allowlist boundary."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import threading
import importlib.util
import urllib.parse
import urllib.request
from pathlib import Path
from html.parser import HTMLParser
from marvis.team import AGENTS, TOOL_AGENTS

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
    schema('agent_inspect', 'Main Agent: 检查角色、工具、平台和可选依赖是否可用，不调用模型。', {}),
    schema('file_list', 'File Agent: 分页列出允许目录中的文件，按文件名或文本内容搜索。', {'path': STRING, 'query': STRING, 'content': {'type': 'boolean'}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 5000}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, ['path']),
    schema('file_read', 'File Agent: 分页读取文本/PDF/DOCX/XLSX 内容，offset 为字符偏移。文档最多提取 100000 字符。', {'path': STRING, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 20000000}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100000}}, ['path']),
    schema('file_write', 'File Agent: 创建文件并写入完整文本；需要用户确认；不覆盖。', {'path': STRING, 'text': STRING}, ['path', 'text']),
    schema('file_organize', 'File Agent: 按类型移动目录内散落文件，展示计划并等待确认。', {'path': STRING}, ['path']),
    schema('file_duplicates', 'File Agent: 按 SHA256 查找重复文件，不删除。', {'path': STRING}, ['path']),
    schema('document_convert', 'File Agent: CSV 转 JSON/Markdown、JSON 转 CSV、文档提取为 TXT；不覆盖；需确认。', {'path': STRING, 'format': {'type': 'string', 'enum': ['json', 'csv', 'md', 'txt']}}, ['path', 'format']),
    schema('knowledge_index', 'File Agent: 建立本地文档和图片元数据索引（不提供人像/语义识别）。', {'path': STRING}, ['path']),
    schema('knowledge_search', 'Search Agent: 搜索已索引文件名和文本内容。', {'query': STRING}, ['query']),
    schema('image_ocr', 'File Agent: 使用已安装的 tesseract 本地 OCR，不上传；默认选择已安装的中英文语言。', {'path': STRING, 'language': {'type': 'string', 'enum': ['chi_sim+eng', 'chi_sim', 'eng']}}, ['path']),
    schema('system_info', 'Computer Agent: 查询实际系统、磁盘、内存、电池、进程。', {'section': {'type': 'string', 'enum': ['overview', 'disk', 'memory', 'processes', 'battery']} }),
    schema('app_list', 'App Agent: 列出本机已安装应用，可按名称筛选。', {'query': STRING}),
    schema('app_open', 'App Agent: 打开已安装 macOS 应用，等待用户确认。', {'name': STRING}, ['name']),
    schema('browser_read', 'Browser Agent: 获取公开 HTTP(S) 网页正文，非浏览器点击自动化。', {'url': STRING}, ['url']),
    schema('browser_open', 'Browser Agent: 在系统浏览器打开网页，等待用户确认。', {'url': STRING}, ['url']),
    schema('web_search', 'Search Agent: DuckDuckGo 网页搜索，返回原始链接；网络失败如实报错。', {'query': STRING}, ['query']),
]
MUTATIONS = {'file_write', 'file_organize', 'document_convert', 'app_open', 'browser_open'}
TOOL_SCHEMAS = {t['function']['name']: t['function']['parameters'] for t in TOOLS}


def validate_arguments(name, args):
    if not isinstance(name, str): raise ValueError('工具名称必须为字符串。')
    if name not in TOOL_SCHEMAS: raise ValueError('未知工具：' + str(name))
    if not isinstance(args, dict): raise ValueError('工具参数必须为对象。')
    definition = TOOL_SCHEMAS[name]; fields = definition['properties']
    missing = set(definition['required']) - set(args)
    if missing: raise ValueError('缺少工具参数：' + ', '.join(sorted(missing)))
    extra = set(args) - set(fields)
    if extra: raise ValueError('未知工具参数：' + ', '.join(sorted(extra)))
    for key, value in args.items():
        field = fields[key]; kind = field['type']
        valid = isinstance(value, str) if kind == 'string' else type(value) is bool if kind == 'boolean' else type(value) is int
        if not valid: raise ValueError(key + ' 参数类型必须为 ' + kind)
        if 'enum' in field and value not in field['enum']: raise ValueError(key + ' 必须是：' + ', '.join(field['enum']))
        if kind == 'integer' and not field.get('minimum', value) <= value <= field.get('maximum', value): raise ValueError(key + ' 超出允许范围。')
        if kind == 'string' and key != 'text' and not (name == 'file_list' and key == 'query') and not value.strip(): raise ValueError(key + ' 不能为空。')
        if kind == 'string' and len(value) > (1000000 if key == 'text' else 20000): raise ValueError(key + ' 超出长度限制。')


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0; self.title_parts = []; self.in_title = False
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.hidden += 1
        if tag == 'title': self.in_title = True
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self.hidden = max(0, self.hidden - 1)
        if tag == 'title': self.in_title = False
    def handle_data(self, data):
        if not self.hidden and data.strip():
            if self.in_title: self.title_parts.append(data.strip())
            else: self.parts.append(data.strip())


class SearchResults(HTMLParser):
    """Parse search anchors independent of attribute order and CSS classes."""
    def __init__(self):
        super().__init__(); self.results = []; self.current = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'result__a' in attrs.get('class', '').split():
            self.current = {'url': attrs.get('href', ''), 'parts': []}
    def handle_data(self, data):
        if self.current is not None: self.current['parts'].append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            href = self.current['url']
            if href.startswith('//'): href = 'https:' + href
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get('uddg', [href])[0]
            parsed = urllib.parse.urlparse(href)
            if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
                if href not in {r['url'] for r in self.results}:
                    self.results.append({'title': ''.join(self.current['parts']).strip(), 'url': href})
            self.current = None


class Capabilities:
    _app_cache = None
    _app_lock = threading.Lock()
    def __init__(self, store, check=lambda: None, local=False):
        self.store, self.check, self.local = store, check, local
        self._roots_revision = -1; self._roots = []

    def path(self, value):
        p = Path(value).expanduser().resolve()
        with self.store.lock:
            if self._roots_revision != self.store.settings_revision:
                self._roots = [Path(r).expanduser().resolve() for r in self.store.setting('roots', [str(Path.cwd())])]
                self._roots_revision = self.store.settings_revision
        if not any(p == r or r in p.parents for r in self._roots):
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
                self.check()
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
        if ext in {'.pdf', '.docx', '.xlsx'}: return self.document_text(p)['text']
        if ext not in TEXT: raise ValueError('不支持读取此格式；图片可使用 OCR。')
        with p.open('r', encoding='utf-8', errors='replace') as f: return f.read(100000)

    def document_text(self, p):
        """Bound extraction work and report page/row/character limits alike."""
        ext = p.suffix.lower(); limited = False; workbook = None
        if ext == '.pdf':
            try: from pypdf import PdfReader
            except ImportError: raise ValueError('PDF 解析需要安装 requirements-office.txt。')
            reader = PdfReader(p); limited = len(reader.pages) > 100
            parts = ((page.extract_text() or '') for page in reader.pages[:100])
        elif ext == '.docx':
            try: from docx import Document
            except ImportError: raise ValueError('Word 解析需要安装 requirements-office.txt。')
            doc = Document(p)
            def word_parts():
                for paragraph in doc.paragraphs: yield paragraph.text
                for table in doc.tables:
                    for row in table.rows: yield ' | '.join(c.text for c in row.cells)
            parts = word_parts()
        elif ext == '.xlsx':
            try: from openpyxl import load_workbook
            except ImportError: raise ValueError('Excel 解析需要安装 requirements-office.txt。')
            workbook = load_workbook(p, read_only=True, data_only=True)
            def excel_parts():
                nonlocal limited
                for sheet in workbook:
                    limited = limited or bool(sheet.max_row and sheet.max_row > 1000)
                    yield '[' + sheet.title + ']'
                    max_row = min(1001, sheet.max_row) if sheet.max_row else 1001
                    for index, row in enumerate(sheet.iter_rows(max_row=max_row, values_only=True)):
                        if index >= 1000: limited = True; break
                        yield ' | '.join(str(c) if c is not None else '' for c in row)
            parts = excel_parts()
        else: raise ValueError('不支持的文档格式。')
        try:
            out = []; length = 0
            for part in parts:
                self.check()
                remaining = 100001 - length
                if out: out.append('\n'); remaining -= 1; length += 1
                out.append(part[:max(0, remaining)]); length += min(len(part), max(0, remaining))
                if length > 100000: limited = True; break
            return {'text': ''.join(out)[:100000], 'extraction_limited': limited}
        finally:
            if workbook is not None: workbook.close()

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
        validate_arguments(name, args)
        if name == 'file_organize':
            moves = self.organize_plan(args['path'])
            return {'action': '按类型移动文件', 'moves': moves, 'count': len(moves)}
        if name == 'file_write':
            p = self.path(args['path'])
            if p.exists(): raise ValueError('禁止覆盖已有文件：' + str(p))
            return {'action': '创建文件', 'path': str(p), 'text': args['text']}
        if name == 'document_convert':
            p = self.path(args['path']); dst = self.path(str(p.with_suffix('.' + args['format'])))
            fmt = args['format']
            if not p.is_file(): raise ValueError('转换源文件不存在。')
            if not (fmt == 'txt' or (p.suffix.lower() == '.csv' and fmt in ('json', 'md')) or (p.suffix.lower() == '.json' and fmt == 'csv')):
                raise ValueError('不支持此转换组合；支持 CSV→JSON/Markdown、JSON→CSV、文档→TXT。')
            if dst.exists(): raise ValueError('输出文件已存在：' + str(dst))
            st = p.stat()
            return {'action': '转换格式', 'source': str(p), 'target': str(dst), 'size': st.st_size, 'modified': st.st_mtime_ns}
        if name == 'app_open':
            if sys.platform != 'darwin': raise ValueError('应用启动目前仅支持 macOS。')
            return {'action': name, 'name': self.resolve_app(args['name'])}
        if name == 'browser_open': self.validate_url(args['url'])
        return {'action': name, 'arguments': args}

    def execute(self, name, args, plan=None):
        self.check()
        validate_arguments(name, args)
        if name in MUTATIONS and plan is None: raise PermissionError('操作必须先通过确认。')
        if name == 'agent_inspect': return self.inspect_agents()
        if name == 'file_list':
            base = self.path(args['path'])
            if not base.is_dir(): raise ValueError('路径必须是目录。')
            q = args.get('query', '').casefold(); rows = []; matched = 0; scanned = 0
            offset, limit = args.get('offset', 0), args.get('limit', 100)
            for p in self.walk(base):
                scanned += 1
                match = q in p.name.casefold()
                if q and not match and args.get('content'):
                    try: match = q in self.read(str(p)).casefold()
                    except (ValueError, OSError): pass
                if match:
                    matched += 1
                    if matched <= offset: continue
                    if len(rows) == limit: break
                    stat = p.stat(); rows.append({'path': str(p), 'name': p.name, 'category': category(p), 'size': stat.st_size, 'modified': stat.st_mtime})
            more = matched > offset + len(rows)
            return {'files': rows, 'limit': limit, 'offset': offset, 'scan_limit': 5000, 'scanned': scanned,
                    'has_more': more, 'next_offset': offset + len(rows) if more else None, 'scan_truncated': scanned >= 5000,
                    'content_scan_limit_chars': 100000 if args.get('content') else None}
        if name == 'file_read':
            p = self.path(args['path']); offset, limit = args.get('offset', 0), args.get('limit', 100000)
            if p.suffix.lower() in TEXT:
                if not p.is_file() or p.stat().st_size > 20 * 1024 * 1024: raise ValueError('文件不存在或超过 20 MB 读取限制。')
                with p.open('r', encoding='utf-8', errors='replace') as stream:
                    remaining = offset
                    while remaining:
                        self.check(); chunk = stream.read(min(remaining, 65536))
                        if not chunk: break
                        remaining -= len(chunk)
                    text = stream.read(limit + 1)
                extraction_limited = False
            else:
                # Keep the common path and size boundary even for document reads.
                if not p.is_file() or p.stat().st_size > 20 * 1024 * 1024: raise ValueError('文件不存在或超过 20 MB 读取限制。')
                extracted = self.document_text(p); text = extracted['text'][offset:offset + limit + 1]
                extraction_limited = extracted['extraction_limited']
            more = len(text) > limit
            return {'path': str(p), 'text': text[:limit], 'offset': offset, 'has_more': more,
                    'next_offset': offset + limit if more else None, 'extraction_limited': extraction_limited}
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
                    os.link(src, dst)
                    try: src.unlink()
                    except Exception:
                        dst.unlink(); raise
                    done.append(move)
            except Exception as error:
                incomplete = []
                for move in reversed(done):
                    try:
                        os.link(move['target'], move['source']); Path(move['target']).unlink()
                    except OSError: incomplete.append(move)
                if incomplete: raise ValueError('整理失败且部分移动无法回滚：' + json.dumps(incomplete, ensure_ascii=False)) from error
                raise
            return {'moved': done, 'count': len(done)}
        if name == 'file_duplicates':
            groups = {}; sizes = {}; scanned = 0; hashed = 0
            for p in self.walk(self.path(args['path'])):
                st = p.stat(); sizes.setdefault(st.st_size, []).append((p, st.st_mtime_ns)); scanned += 1
            for size, paths in sizes.items():
                if len(paths) < 2: continue
                # Cheap samples only eliminate candidates. Final equality is
                # always based on a complete SHA256, including middle bytes.
                samples = {}
                for p, modified in paths:
                    self.check()
                    with p.open('rb') as f:
                        sample = hashlib.sha256(f.read(65536))
                        if size > 65536: f.seek(max(65536, size - 65536)); sample.update(f.read(65536))
                    samples.setdefault(sample.digest(), []).append((p, modified))
                for candidates in samples.values():
                    if len(candidates) < 2: continue
                    for p, modified in candidates:
                        digest = hashlib.sha256()
                        with p.open('rb') as f:
                            while True:
                                self.check(); chunk = f.read(1024 * 1024)
                                if not chunk: break
                                digest.update(chunk)
                        st = p.stat()
                        if st.st_size != size or st.st_mtime_ns != modified: raise ValueError('查重期间文件发生变化，请重新扫描：' + str(p))
                        hashed += 1; groups.setdefault(digest.hexdigest(), []).append(str(p))
            return {'duplicates': [v for v in groups.values() if len(v) > 1], 'scanned': scanned, 'fully_hashed': hashed,
                    'scan_limit': 5000, 'scan_truncated': scanned >= 5000}
        if name == 'document_convert':
            p = self.path(args['path']); fmt = args['format']; st = p.stat()
            if str(p) != plan['source'] or st.st_size != plan['size'] or st.st_mtime_ns != plan['modified']:
                raise ValueError('转换源文件已变化，请重新生成计划。')
            if st.st_size > 20 * 1024 * 1024: raise ValueError('文件超过 20 MB 转换限制。')
            if p.suffix.lower() in TEXT:
                raw = p.read_text(encoding='utf-8')
            else:
                extracted = self.document_text(p); raw = extracted['text']
                if extracted['extraction_limited']: raise ValueError('文档提取达到页数、行数或字符上限，转换中止以避免写入不完整内容。')
            if p.suffix.lower() == '.csv' and fmt in ('json', 'md'):
                rows = list(csv.DictReader(io.StringIO(raw)))
                if fmt == 'json': output = json.dumps(rows, ensure_ascii=False, indent=2)
                else:
                    cols = list(rows[0]) if rows else next(csv.reader(io.StringIO(raw)), [])
                    escape = lambda v: str(v or '').replace('|', '\\|').replace('\n', ' ')
                    output = '\n'.join([' | '.join(escape(c) for c in cols), ' | '.join('---' for _ in cols)] + [' | '.join(escape(r[c]) for c in cols) for r in rows])
            elif p.suffix.lower() == '.json' and fmt == 'csv':
                rows = json.loads(raw)
                if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows): raise ValueError('JSON 必须是对象数组。')
                out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
                writer.writeheader(); writer.writerows(rows); output = out.getvalue()
            elif fmt == 'txt': output = raw
            else: raise ValueError('当前支持 CSV→JSON/Markdown、JSON→CSV、文档→TXT。')
            after = p.stat()
            if after.st_size != plan['size'] or after.st_mtime_ns != plan['modified']: raise ValueError('转换期间源文件发生变化，未写入输出。')
            self.check()
            dst = self.path(plan['target']);
            with dst.open('x', encoding='utf-8') as f: f.write(output)
            return {'created': str(dst)}
        if name == 'knowledge_index':
            base = self.path(args['path']); indexed = 0; skipped = 0; unchanged = 0; batch = []; scanned = 0
            existing = {r['path']: r for r in self.store.rows('SELECT path,size,modified,length(text)>0 AS has_text FROM documents')}
            if not base.is_dir(): raise ValueError('索引目标必须是目录。')
            for p in self.walk(base):
                scanned += 1
                content = ''; st = p.stat(); old = existing.get(str(p))
                # Failed/empty extraction is retried so newly installed parsers work.
                if old and old['size'] == st.st_size and old['modified'] == st.st_mtime and (old['has_text'] or p.suffix.lower() in IMAGES):
                    unchanged += 1; continue
                if p.suffix.lower() in TEXT | {'.pdf', '.docx', '.xlsx'}:
                    try: content = self.read(str(p))
                    except (ValueError, OSError): skipped += 1
                elif p.suffix.lower() not in IMAGES: continue
                after = p.stat()
                if after.st_mtime_ns != st.st_mtime_ns or after.st_size != st.st_size:
                    skipped += 1; continue
                batch.append((str(p), p.name, category(p), content, st.st_size, st.st_mtime)); indexed += 1
                if len(batch) >= 128: self.store.upsert_documents(batch); batch.clear()
            self.check()
            if batch: self.store.upsert_documents(batch)
            # Remove stale entries only when their files have actually disappeared.
            for row in existing.values():
                p = Path(row['path'])
                if base in p.parents and not p.exists(): self.store.execute('DELETE FROM documents WHERE path=?', (row['path'],))
            return {'indexed': indexed, 'unchanged': unchanged, 'text_unavailable': skipped, 'scan_limit': 5000, 'scanned': scanned,
                    'scan_truncated': scanned >= 5000, 'images': '仅文件名、类型和时间元数据；不包含人像识别'}
        if name == 'knowledge_search':
            query = args['query'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            allowed = []; offset = 0
            while len(allowed) < 101:
                self.check()
                rows = self.store.rows("SELECT path,name,category,size,modified,substr(text,max(1,instr(lower(text),lower(?))-80),240) AS preview FROM documents WHERE name LIKE ? ESCAPE '\\' OR text LIKE ? ESCAPE '\\' ORDER BY CASE WHEN name LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END,modified DESC,path LIMIT 200 OFFSET ?", (args['query'], '%' + query + '%', '%' + query + '%', '%' + query + '%', offset))
                for row in rows:
                    self.check()
                    try:
                        p = self.path(row['path'])
                        if not p.is_file(): continue
                        st = p.stat(); row['stale'] = st.st_size != row['size'] or st.st_mtime != row['modified']
                        allowed.append(row)
                    except (PermissionError, OSError): continue
                    if len(allowed) == 101: break
                if len(rows) < 200: break
                offset += len(rows)
            return {'files': allowed[:100], 'has_more': len(allowed) > 100, 'source': '本地知识索引',
                    'notice': 'stale=true 表示索引过期，请重新索引后验证内容。'}
        if name == 'image_ocr':
            p = self.path(args['path'])
            if p.suffix.lower() not in IMAGES: raise ValueError('OCR 输入必须是图片。')
            exe = shutil.which('tesseract')
            if not exe: raise ValueError('请先安装 tesseract 和中文语言包；当前 OCR 未就绪。')
            languages = set(self.command([exe, '--list-langs']).splitlines())
            language = args.get('language') or ('chi_sim+eng' if {'chi_sim', 'eng'} <= languages else 'chi_sim' if 'chi_sim' in languages else 'eng')
            if not set(language.split('+')) <= languages: raise ValueError('OCR 语言包未安装：' + language)
            self.check()
            return {'text': self.command([exe, str(p), 'stdout', '-l', language], timeout=60), 'language': language,
                    'notice': '未安装中文语言包，本次仅识别英文。' if language == 'eng' else '本地 OCR，未上传图片。'}
        if name == 'system_info': return self.system(args.get('section', 'overview'))
        if name == 'app_list': return {'apps': [n for n in self.app_names() if args.get('query', '').casefold() in n.casefold()], 'supported': sys.platform == 'darwin'}
        if name == 'app_open':
            if sys.platform != 'darwin': raise ValueError('应用启动目前仅支持 macOS。')
            resolved = self.resolve_app(args['name'])
            if resolved != plan['name']: raise ValueError('应用名称已变化，请重新确认。')
            self.command(['open', '-a', resolved]); return {'opened': resolved}
        if name == 'browser_open':
            self.validate_url(args['url'])
            if self.local: raise PermissionError('本地模式禁止访问网页，请切换效率模式。')
            if sys.platform != 'darwin': raise ValueError('浏览器启动目前仅支持 macOS。')
            self.command(['open', args['url']]); return {'opened': args['url']}
        if name == 'browser_read':
            body = self.fetch(args['url']); parser = PageText(); parser.feed(body)
            text = '\n'.join(parser.parts)
            if not text.strip(): raise ValueError('网页没有可读静态正文，可能需要登录或 JavaScript；当前网页工具不支持这些交互。')
            return {'url': args['url'], 'title': ' '.join(parser.title_parts), 'text': text[:20000],
                    'truncated': len(text) > 20000, 'notice': '仅静态网页内容，未执行 JavaScript。'}
        if name == 'web_search':
            url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(args['query'])
            body = self.fetch(url)
            if re.search(r'anomaly-modal|challenge-form|captcha', body, re.I):
                raise ValueError('搜索服务要求人机验证，本轮没有获得搜索结果；请直接提供目标网页 URL。')
            parser = SearchResults(); parser.feed(body); found = parser.results
            if not found: raise ValueError('搜索服务未返回结果或触发访问限制；可直接提供网页 URL。')
            return {'results': found[:8], 'source': 'DuckDuckGo', 'notice': '搜索链接尚未验证正文；请用 browser_read 读取来源再下结论。'}
        raise ValueError('工具尚未实现。')

    @staticmethod
    def command(args, timeout=15):
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, shell=False)
        if p.returncode: raise ValueError((p.stderr or p.stdout or '命令失败')[:1000])
        return p.stdout.strip()

    @classmethod
    def app_names(cls):
        if sys.platform != 'darwin': return []
        with cls._app_lock:
            now = time.monotonic()
            if cls._app_cache and now - cls._app_cache[0] < 15: return list(cls._app_cache[1])
            names = set()
            for base in [Path('/Applications'), Path('/System/Applications'), Path.home() / 'Applications']:
                for root, dirs, _ in os.walk(base):
                    for name in dirs:
                        if name.endswith('.app'): names.add(Path(name).stem)
                    # Never traverse application bundles or hidden support data.
                    dirs[:] = [d for d in dirs if not d.endswith('.app') and not d.startswith('.')]
            result = sorted(names); cls._app_cache = (now, result)
            return list(result)

    def resolve_app(self, name):
        names = self.app_names()
        if name in names: return name
        matches = [n for n in names if n.casefold() == name.casefold()]
        if len(matches) == 1: return matches[0]
        raise ValueError('应用未安装或名称不唯一，请先使用 app_list 查询准确名称。')

    def inspect_agents(self):
        dependencies = {ext: importlib.util.find_spec(module) is not None for ext, module in
                        [('pdf', 'pypdf'), ('docx', 'docx'), ('xlsx', 'openpyxl')]}
        return {'agents': AGENTS, 'mode': 'local' if self.local else 'efficiency',
                'document_parsers': dependencies, 'ocr_installed': bool(shutil.which('tesseract')),
                'macos': sys.platform == 'darwin', 'network_enabled': not self.local,
                'roots': self.store.setting('roots', [str(Path.cwd())]),
                'unavailable_tools': [t for t in TOOL_AGENTS if (self.local and t in ('browser_read', 'browser_open', 'web_search'))
                                      or (sys.platform != 'darwin' and t in ('app_open', 'browser_open'))],
                'notice': 'OCR 中文语言包和网络服务可达性以实际调用为准。'}

    def system(self, section):
        if section == 'disk':
            disk = shutil.disk_usage(Path.home()); return {'total_gb': round(disk.total / 2**30, 1), 'used_gb': round(disk.used / 2**30, 1), 'free_gb': round(disk.free / 2**30, 1), 'used_percent': round(disk.used / disk.total * 100, 1)}
        commands = {'memory': ['vm_stat'], 'battery': ['pmset', '-g', 'batt'], 'processes': ['ps', '-axo', 'pid,comm,%cpu,%mem']}
        if section in commands:
            if sys.platform != 'darwin' and section != 'processes': raise ValueError('此查询目前仅支持 macOS。')
            if section == 'processes': commands[section] = ['ps', '-axo', 'pid,%cpu,%mem,comm']
            raw = self.command(commands[section]); result = {'section': section, 'output': raw[:10000], 'truncated': len(raw) > 10000}
            if section == 'processes':
                processes = []
                for line in raw.splitlines()[1:]:
                    fields = line.strip().split(None, 3)
                    if len(fields) != 4: continue
                    try: processes.append({'pid': int(fields[0]), 'cpu_percent': float(fields[1]), 'memory_percent': float(fields[2]), 'command': fields[3]})
                    except ValueError: continue
                result.update({'process_count': len(processes), 'top_processes': sorted(processes, key=lambda p: p['cpu_percent'], reverse=True)[:20],
                               'notice': 'CPU 为 ps 报告的进程平均占用，不是瞬时采样；未终止任何进程。'})
            elif section == 'memory':
                page_size = re.search(r'page size of (\d+) bytes', raw)
                pages = {key.strip(): int(value) for key, value in re.findall(r'^([^:\n]+):\s*(\d+)\.', raw, re.M)}
                if page_size:
                    size = int(page_size.group(1))
                    result['page_size_bytes'] = size
                    result['metrics_mb'] = {key: round(value * size / 2**20, 1) for key, value in pages.items() if key.startswith('Pages ')}
                result['notice'] = 'vm_stat 页计数；内存压力还受压缩与交换影响，不能仅凭空闲页判断故障。'
            elif section == 'battery':
                charge = re.search(r'(\d+)%', raw)
                if charge: result['charge_percent'] = int(charge.group(1))
                result['power_source'] = 'battery' if 'Battery Power' in raw else 'ac' if 'AC Power' in raw else 'unknown'
                result['battery_present'] = bool(charge)
            return result
        if section != 'overview': raise ValueError('未知系统查询：' + str(section))
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
        self.check()
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
            chunks = []; length = 0
            while True:
                self.check(); chunk = resp.read(65536)
                if not chunk: break
                length += len(chunk)
                if length > 2 * 1024 * 1024: raise ValueError('网页超过 2 MB 限制。')
                chunks.append(chunk)
            self.check(); raw = b''.join(chunks)
            return raw.decode(resp.headers.get_content_charset() or 'utf-8', errors='replace')
