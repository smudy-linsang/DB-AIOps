#!/usr/bin/env python
"""密钥扫描：拦截疑似硬编码凭据进入仓库。

扫描范围：git 跟踪的文本文件（不扫 node_modules/migrations/lock 文件）。
策略：宁可误报也不漏报，误报用 # noqa: secret 显式豁免。
"""
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    ('私钥块',        re.compile(r'-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----')),
    ('AWS AccessKey', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('Slack Token',   re.compile(r'\bxox[baprs]-[0-9A-Za-z-]{10,}')),
    ('GitHub Token',  re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b')),
    ('通用密钥赋值',   re.compile(
        r'(?i)\b(password|passwd|secret|token|api_?key|access_?key)\b\s*[:=]\s*'
        r'["\'][^"\'\s${}]{8,}["\']')),
]
SKIP_DIRS = {'node_modules', 'migrations', '.git', 'dist', 'staticfiles', '__pycache__', '.venv', 'venv'}
SKIP_FILES = {'package-lock.json', 'requirements.lock'}
# 明显是占位/示例的值不算
PLACEHOLDER = re.compile(
    r'(?i)(change[-_]?me|your[-_]|example|placeholder|xxx+|\*{4,}|dummy|sample|test[-_]?only|ci-only)')


def tracked_files():
    out = subprocess.run(['git', 'ls-files'], capture_output=True, text=True, check=True)
    for line in out.stdout.splitlines():
        p = Path(line)
        if set(p.parts) & SKIP_DIRS or p.name in SKIP_FILES:
            continue
        if p.suffix in {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.woff', '.woff2'}:
            continue
        yield p


def main() -> int:
    hits = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if 'noqa: secret' in line or PLACEHOLDER.search(line):
                continue
            for label, pat in PATTERNS:
                if pat.search(line):
                    hits.append((path, lineno, label, line.strip()[:100]))
    if hits:
        print('检测到疑似硬编码凭据：')
        for path, lineno, label, snippet in hits:
            print(f'  {path}:{lineno}  [{label}]  {snippet}')
        print('\n确认为误报时，在该行加注释 `# noqa: secret`。')
        return 1
    print('密钥扫描通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
