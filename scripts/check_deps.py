#!/usr/bin/env python
"""依赖完整性检查：requirements.txt 里声明的包必须真的可导入。

动因（BUG-137）：jsonschema 在 requirements.txt 中声明了，但纯净环境实测缺失，
而 llm/schemas.py 当时的行为是"缺了就跳过校验并放行" —— LLM 输出的结构校验
被静默关闭，而这些输出会驱动 RCA 结论与自动修复预案。
依赖缺失必须在 CI 阶段暴露，而不是等运行时静默降级。
"""
import importlib
import re
import sys
from pathlib import Path

# 包名 → 导入名（不一致的显式映射）
IMPORT_NAME = {
    'psycopg2-binary': 'psycopg2',
    'PyMySQL': 'pymysql',
    'python-dotenv': 'dotenv',
    'elasticsearch': 'elasticsearch',
    'scikit-learn': 'sklearn',
    'APScheduler': 'apscheduler',
    'Django': 'django',
    'PyYAML': 'yaml',
    'pyodbc': 'pyodbc',
}
# 平台相关/可选依赖：缺失只告警不失败
OPTIONAL = {'pyodbc'}


def parse_requirements(path: Path):
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith('-'):
            continue
        name = re.split(r'[<>=!~\[]', line, 1)[0].strip()
        if name:
            yield name


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    missing, optional_missing = [], []
    for pkg in parse_requirements(root / 'requirements.txt'):
        mod = IMPORT_NAME.get(pkg, pkg.replace('-', '_'))
        try:
            importlib.import_module(mod)
        except Exception as e:
            (optional_missing if pkg in OPTIONAL else missing).append(f'{pkg} ({mod}): {e}')

    for item in optional_missing:
        print(f'  [可选] 未安装: {item}')
    if missing:
        print('依赖完整性检查失败，以下声明的包无法导入：')
        for item in missing:
            print(f'  - {item}')
        print('\n这类缺失会让依赖它的代码走进静默降级分支（参见 BUG-137）。')
        return 1
    print('依赖完整性检查通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
