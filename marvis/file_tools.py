"""Bounded file discovery, line evidence and reviewed exact replacements."""
from __future__ import annotations

import difflib
import fnmatch
import hashlib
import itertools
import os
import stat
import tempfile
from functools import lru_cache
from pathlib import Path


def matches_glob(path, pattern, case_sensitive=False):
    """Segment globs: * never crosses /, ** matches zero or more directories."""
    if not case_sensitive: path, pattern = path.casefold(), pattern.casefold()
    names, patterns = path.split('/'), pattern.split('/')
    if len(patterns) == 1: return fnmatch.fnmatchcase(names[-1], pattern)

    @lru_cache(None)
    def match(i, j):
        if j == len(patterns): return i == len(names)
        if patterns[j] == '**':
            return match(i, j + 1) or (i < len(names) and match(i + 1, j))
        return i < len(names) and fnmatch.fnmatchcase(names[i], patterns[j]) and match(i + 1, j + 1)
    return match(0, 0)


class FileTools:
    def directory_entries(self, base):
        entries = sorted(itertools.islice(base.iterdir(), 5001), key=lambda p: p.name.casefold())
        truncated = len(entries) > 5000
        allowed = []
        for p in entries[:5000]:
            self.check()
            if p.name.startswith('.') or p.is_symlink(): continue
            try: allowed.append(self.path(str(p)))
            except PermissionError: continue
        return allowed, truncated

    def text_snapshot(self, value):
        from marvis.capabilities import TEXT
        p = self.path(value)
        if not p.is_file() or p.suffix.lower() not in TEXT:
            raise ValueError('此工具只支持现有 UTF-8 文本文件。')
        if p.stat().st_size > 2 * 1024 * 1024: raise ValueError('文本超过 2 MB 限制，请缩小文件或使用 file_read。')
        self.check()
        with p.open('rb') as stream: raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024: raise ValueError('文本超过 2 MB 限制。')
        if b'\0' in raw: raise ValueError('不支持包含 NUL 的二进制内容。')
        try: text = raw.decode('utf-8')
        except UnicodeDecodeError as error: raise ValueError('此工具仅支持有效 UTF-8 文本。') from error
        return p, raw, text, hashlib.sha256(raw).hexdigest()

    def edit_preview(self, args):
        if Path(args['path']).expanduser().is_symlink(): raise ValueError('不允许编辑符号链接。')
        p, raw, text, digest = self.text_snapshot(args['path'])
        if digest != args['expected_sha256']: raise ValueError('文件已变化，请重新读取后生成编辑计划。')
        old, new = args['old_text'], args['new_text']
        if old == new: raise ValueError('新旧内容相同，无需编辑。')
        count = text.count(old)
        if not count: raise ValueError('原文未匹配，请重新读取相关区域。')
        if count != 1 and not args.get('replace_all', False): raise ValueError('原文匹配多处，请增加上下文或明确 replace_all。')
        updated = text.replace(old, new, -1 if args.get('replace_all', False) else 1)
        if len(updated.encode('utf-8')) > 2 * 1024 * 1024: raise ValueError('编辑结果超过 2 MB 限制。')
        chunks = difflib.unified_diff(text.splitlines(keepends=True), updated.splitlines(keepends=True),
                                     fromfile=str(p), tofile=str(p), n=3)
        diff = ''.join(chunk if chunk.endswith('\n') else chunk + '\n\\ No newline at end of file\n' for chunk in chunks)
        # Never ask the user to approve a silently abbreviated patch.
        if len(diff) > 20000: raise ValueError('修改差异超过 20000 字符，请分批编辑。')
        return {'action': '精确替换文本', 'path': str(p), 'expected_sha256': digest,
                'replacements': count if args.get('replace_all', False) else 1, 'diff': diff}

    def execute_file_tool(self, name, args, plan):
        if name == 'file_edit':
            current = self.edit_preview(args)
            if current != plan: raise ValueError('编辑计划已变化，请重新确认。')
            p, raw, text, digest = self.text_snapshot(args['path'])
            if digest != plan['expected_sha256']: raise ValueError('文件已变化，请重新读取。')
            updated = text.replace(args['old_text'], args['new_text'], -1 if args.get('replace_all', False) else 1)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=p.parent, prefix='.marvis-edit-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(updated.encode('utf-8')); stream.flush(); os.fsync(stream.fileno())
                os.chmod(temporary, stat.S_IMODE(p.stat().st_mode))
                self.check()
                # Recheck authorization and contents immediately before publishing.
                if self.text_snapshot(str(p))[3] != digest: raise ValueError('文件已变化，编辑中止。')
                os.replace(temporary, p)
            finally:
                if temporary is not None: temporary.unlink(missing_ok=True)
            return {'edited': str(p), 'replacements': current['replacements'],
                    'sha256': hashlib.sha256(updated.encode('utf-8')).hexdigest(), 'bytes': p.stat().st_size}
        if name == 'file_read_lines':
            p, raw, text, digest = self.text_snapshot(args['path'])
            start, limit = args.get('start_line', 1), args.get('limit', 100)
            lines = text.splitlines(keepends=True); rows = []; used = 0
            for index in range(start - 1, min(len(lines), start - 1 + limit)):
                self.check()
                value = lines[index].rstrip('\r\n')
                shown = value[:2000]
                if used + len(shown) > 20000: break
                rows.append({'line': index + 1, 'text': shown, 'line_truncated': len(value) > 2000})
                used += len(shown)
            more = start - 1 + len(rows) < len(lines)
            return {'path': str(p), 'sha256': digest, 'lines': rows, 'total_lines': len(lines),
                    'newline': 'crlf' if '\r\n' in text and '\n' not in text.replace('\r\n', '') else 'lf' if '\n' in text and '\r\n' not in text else 'mixed' if '\r' in text else 'none',
                    'has_more': more, 'next_start_line': start + len(rows) if more else None,
                    'notice': '行号是元数据；长行截断时用 file_read 按字符读取，编辑保留原始换行符。'}
        base = self.path(args['path'])
        if name == 'directory_list':
            if not base.is_dir(): raise ValueError('路径必须是目录。')
            entries, truncated = self.directory_entries(base)
            offset, limit = args.get('offset', 0), args.get('limit', 100)
            rows = [{'path': str(p), 'name': p.name, 'type': 'directory' if p.is_dir() else 'file'}
                    for p in entries[offset:offset + limit]]
            more = offset + len(rows) < len(entries)
            return {'path': str(base), 'entries': rows, 'has_more': more,
                    'next_offset': offset + len(rows) if more else None, 'scan_truncated': truncated, 'scan_limit': 5000}
        if name == 'file_glob' and not base.is_dir(): raise ValueError('路径必须是目录。')
        if not base.exists(): raise ValueError('搜索路径不存在。')
        truncated = False
        if base.is_file(): candidates = [base]
        elif args.get('recursive', True): candidates = self.walk(base)
        else:
            entries, truncated = self.directory_entries(base)
            candidates = (p for p in entries if p.is_file())
        rows = []; scanned = 0; skipped = 0; more = False; used = 0; bytes_scanned = 0; content_limited = False
        limit, offset = args.get('limit', 100), args.get('offset', 0)
        matched = 0
        for p in candidates:
            self.check(); scanned += 1
            relative = p.relative_to(base).as_posix() if base.is_dir() else p.name
            if not matches_glob(relative, args.get('pattern') if name == 'file_glob' else args.get('glob', '*'), args.get('case_sensitive', False)): continue
            if name == 'file_glob': items = [{'path': str(p), 'relative_path': relative}]
            else:
                try:
                    size = p.stat().st_size
                    if size <= 2 * 1024 * 1024 and bytes_scanned + size > 20 * 1024 * 1024:
                        content_limited = True; break
                    _, raw, text, _ = self.text_snapshot(str(p))
                    bytes_scanned += len(raw)
                except (ValueError, OSError): skipped += 1; continue
                query = args['query'] if args.get('case_sensitive') else args['query'].casefold()
                context = args.get('context', 0); lines = text.splitlines()
                def matching_lines():
                    for index, line in enumerate(lines):
                        self.check()
                        if query not in (line if args.get('case_sensitive') else line.casefold()): continue
                        row = {'path': str(p), 'line': index + 1, 'text': line[:2000], 'line_truncated': len(line) > 2000}
                        if context:
                            row['context'] = [{'line': i + 1, 'text': lines[i][:2000], 'line_truncated': len(lines[i]) > 2000}
                                              for i in range(max(0, index - context), min(len(lines), index + context + 1))]
                        yield row
                items = matching_lines()
            for row in items:
                matched += 1
                if matched <= offset: continue
                cost = len(str(row))
                if len(rows) == limit or (rows and used + cost > 20000): more = True; break
                rows.append(row); used += cost
            if more: break
        return {'path': str(base), 'matches': rows, 'has_more': more,
                'next_offset': offset + len(rows) if more else None, 'scanned': scanned,
                'scan_limit': 5000, 'scan_truncated': truncated or scanned >= 5000 or content_limited,
                'bytes_scanned': bytes_scanned, 'content_scan_limit_bytes': 20 * 1024 * 1024,
                'skipped_files': skipped, 'notice': '只代表已扫描范围；不读取隐藏/受保护文件或符号链接，不解析 .gitignore。'}
