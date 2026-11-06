#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 修复补丁正则吞掉换行导致的 `try:` 与语句粘连。

现象：补丁用 `\\s+` 匹配缩进时可能把 `try:` 之后的换行一起吃掉，
使 `try:` 与其后第一条语句粘在同一行 → SyntaxError。
本脚本把 `try:` / `except ...:` 与后续语句之间的粘连还原为「换行 + 原缩进」。
"""
import io
import re
import sys

TARGETS = [
    r'C:\DB-AIOps\monitor\checkers\dm.py',
    r'C:\DB-AIOps\monitor\sentinel.py',
    r'C:\DB-AIOps\monitor\management\commands\start_monitor.py',
]

RX = re.compile(r'((?:try:|except[^:\n]*:))([ \t]{2,})(?=\S)')


def main():
    total = 0
    for path in TARGETS:
        src = io.open(path, encoding='utf-8', newline='').read()
        crlf = '\r\n' in src
        nl = '\r\n' if crlf else '\n'
        fixed, n = RX.subn(lambda m: m.group(1) + nl + m.group(2), src)
        if n:
            io.open(path, 'w', encoding='utf-8', newline='').write(fixed)
        print('%-60s fixed=%d' % (path, n))
        total += n
    print('total fixed:', total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
