#!/usr/bin/env python
"""文档与版本一致性校验（V2.6 W5-03）。

动机：README 写 v2.1、PROJECT_SUMMARY 写 v2.2、而 dbmonitor/version.py 是
2.5.0 —— 文档漂移会让"当前能力边界"对运维与评审都不可信。人工维护必然腐烂，
故做成机械校验：单一版本源 = dbmonitor/version.py。

为什么只用 re.search 取首个匹配、不做全量比对：
README 正文里有历史版本号（更新日志的 v1.0/v2.1 等），全量比对必然误报。
本脚本只校验各文档**头部声明**的那一个版本号。

用法: python scripts/check_doc_version.py
退出码: 0=一致; 1=存在漂移
"""
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# 必须在显著位置声明当前版本的文件（正则捕获版本号声明）
# 已实测格式：
#   README.md:1            "# 数据库监控系统 v2.1"
#   PROJECT_SUMMARY.md:5   "**版本**: v2.2"   ← 有 markdown 加粗，需容忍
DOC_PATTERNS = {
    'README.md': r'v\d+\.\d+(?:\.\d+)?',
    'PROJECT_SUMMARY.md': r'\*\*版本\*\*\s*[:：]\s*v?\d+\.\d+(?:\.\d+)?',
}

# 只在这些行数内寻找声明，避免误取正文/历史日志里的版本号
HEAD_LINES = 40


def current_version() -> str:
    src = (PROJECT_DIR / 'dbmonitor' / 'version.py').read_text(encoding='utf-8')
    m = re.search(r"__version__\s*=\s*'([^']+)'", src)
    if not m:
        raise SystemExit('无法从 dbmonitor/version.py 解析 __version__')
    return m.group(1)


def declared_version(text: str, pattern: str) -> str:
    """从匹配文本中抽出纯版本号（去掉 v 前缀与 markdown 包装）。"""
    m = re.search(pattern, text)
    if not m:
        return ''
    num = re.search(r'\d+\.\d+(?:\.\d+)?', m.group(0))
    return num.group(0) if num else ''


def main() -> int:
    want = current_version()
    major_minor = '.'.join(want.split('.')[:2])
    problems = []
    for rel, pattern in DOC_PATTERNS.items():
        path = PROJECT_DIR / rel
        if not path.exists():
            problems.append(f'{rel}: 文件不存在')
            continue
        head = '\n'.join(
            path.read_text(encoding='utf-8').splitlines()[:HEAD_LINES])
        found = declared_version(head, pattern)
        if not found:
            problems.append(f'{rel}: 前 {HEAD_LINES} 行未找到版本号声明')
            continue
        if not found.startswith(major_minor):
            problems.append(
                f'{rel}: 声明版本 {found} 与 dbmonitor/version.py 的 {want} 不一致')
    if problems:
        print('文档版本漂移：')
        for p in problems:
            print('  -', p)
        print('\n请把上述文档的版本声明对齐到 dbmonitor/version.py（单一版本源）。')
        return 1
    print(f'文档版本一致: {want}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
