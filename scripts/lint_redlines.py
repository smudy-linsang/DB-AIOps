#!/usr/bin/env python
"""红线约束静态扫描。

四条检查（只检查本次工作区或 CI 提交新增/修改的代码，不追溯存量）：
1. except 块中的 pass 而无 degrade.note() 调用 —— 静默降级必须留痕
2. 新增 getattr(settings, 'KEY', ...) 引用未在 appconf.SPECS 中登记 —— 配置项不可散落
3. git diff 中删除的 migration 文件 —— 迁移链不可断
4. Copilot 工具层新增固定业务样例字面量 —— 样例不得冒充观测事实

无外部依赖，纯标准库。用法: python scripts/lint_redlines.py
退出码: 0=通过; 1=发现违规
"""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# 不扫描的目录
SKIP_DIRS = {
    'migrations', '__pycache__', 'venv', '.venv',
    'node_modules', 'dist', 'staticfiles',
}

# settings 相关文件不检查"未登记配置"（它们本身就是配置定义处）
SKIP_SETTINGS_FILES = {
    'monitor/appconf.py',
    'monitor/settings.py',
    'monitor/settings_test_unit.py',
    'dbmonitor/settings.py',
    'dbmonitor/settings_test_unit.py',
}


def _git(*args):
    """运行 git 命令，返回 stdout（失败返回空字符串）。"""
    try:
        r = subprocess.run(['git', *args], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ''
    except Exception:
        return ''


def _diff_args():
    """本地扫描工作区；CI 干净检出扫描本次提交。"""
    if _git('diff', 'HEAD', '--name-status').strip():
        return ('HEAD',)
    if os.environ.get('CI') and _git('rev-parse', '--verify', 'HEAD^').strip():
        return ('HEAD^', 'HEAD')
    return ('HEAD',)


def _changed_files(diff_args=None):
    """返回指定 diff 范围内的 [(status, relpath)]。"""
    out = _git('diff', *(diff_args or _diff_args()), '--name-status')
    result = []
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        status = parts[0][0]  # A / M / D / R ...
        relpath = parts[-1]
        result.append((status, relpath))
    return result


def _added_lines_by_file(diff_args=None):
    """解析选定 git diff --unified=0，返回 {relpath: set(行号)}。

    只提取新增行（+ 开头）的行号，用于精确定位"本次改动引入的违规"，
    避免对存量代码产生噪音。
    """
    out = _git('diff', *(diff_args or _diff_args()), '--unified=0')
    result = {}
    cur_file = None
    new_line = 0
    for line in out.splitlines():
        if line.startswith('+++ b/'):
            cur_file = line[6:]
            result[cur_file] = set()
        elif line.startswith('@@'):
            m = re.search(r'\+(\d+)', line)
            if m:
                new_line = int(m.group(1))
        elif line.startswith('+') and not line.startswith('+++') and cur_file:
            result[cur_file].add(new_line)
            new_line += 1
        elif line.startswith('-') and not line.startswith('---'):
            pass  # 删除行不递增 new_line
        elif not line.startswith('\\'):
            new_line += 1  # 上下文行递增（-U0 通常没有，保险起见）
    return result


def _parse_specs_keys():
    """从 appconf.py 提取 SPECS 中登记的配置键名集合。"""
    appconf = PROJECT_DIR / 'monitor' / 'appconf.py'
    try:
        tree = ast.parse(appconf.read_text(encoding='utf-8'))
    except Exception:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'SPECS':
                    if isinstance(node.value, ast.Dict):
                        return {
                            k.value for k in node.value.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)
                        }
    return set()


# ─── 检查 1：except-pass 无 degrade.note() ───

def _has_degrade_note(handler):
    """ExceptHandler 子树中是否存在 degrade.note() 调用。"""
    for child in ast.walk(handler):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        # degrade.note(...)
        if isinstance(func, ast.Attribute) and func.attr == 'note':
            if isinstance(func.value, ast.Name) and func.value.id == 'degrade':
                return True
        # note(...) —— from monitor.degrade import note
        if isinstance(func, ast.Name) and func.id == 'note':
            return True
    return False


def check_except_pass(changed_py, added_lines):
    """except 块中有 pass 而无 degrade.note() —— 只报新增行上的违规。"""
    violations = []
    for relpath in changed_py:
        if any(d in SKIP_DIRS for d in Path(relpath).parts):
            continue
        fpath = PROJECT_DIR / relpath
        try:
            tree = ast.parse(fpath.read_text(encoding='utf-8'), filename=relpath)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            has_pass = any(isinstance(c, ast.Pass) for c in ast.walk(node))
            if not has_pass or _has_degrade_note(node):
                continue
            # pass 行号是否落在本次新增行集合中
            for child in ast.walk(node):
                if isinstance(child, ast.Pass) and child.lineno in added_lines.get(relpath, set()):
                    violations.append((relpath, child.lineno))
                    break
    return violations


# ─── 检查 2：未登记的 settings 引用 ───

def check_unregistered_settings(changed_py, added_lines, specs_keys):
    """新增 getattr(settings, 'KEY', ...) 未在 SPECS 中登记。

    用 AST 而非正则，避免匹配文档字符串与注释中的文本。
    """
    violations = []
    for relpath in changed_py:
        if relpath in SKIP_SETTINGS_FILES:
            continue
        if any(d in SKIP_DIRS for d in Path(relpath).parts):
            continue
        fpath = PROJECT_DIR / relpath
        try:
            tree = ast.parse(fpath.read_text(encoding='utf-8'), filename=relpath)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == 'getattr'):
                continue
            if len(node.args) < 2:
                continue
            # 第一参数必须是 settings
            first = node.args[0]
            if not (isinstance(first, ast.Name) and first.id == 'settings'):
                continue
            # 第二参数必须是字符串字面量
            second = node.args[1]
            if not (isinstance(second, ast.Constant) and isinstance(second.value, str)):
                continue
            key = second.value
            if key not in specs_keys and node.lineno in added_lines.get(relpath, set()):
                violations.append((relpath, node.lineno, key))
    return violations


# ─── 检查 3：删除的 migration 文件 ───

def check_deleted_migrations(changed=None):
    """git diff 中删除的 migration 文件。"""
    violations = []
    for status, relpath in (changed if changed is not None else _changed_files()):
        if status != 'D':
            continue
        parts = Path(relpath).parts
        if 'migrations' in parts and relpath.endswith('.py') \
                and not relpath.endswith('__init__.py'):
            violations.append(relpath)
    return violations


# ─── 检查 4：Copilot 固定业务样例 ───

COPILOT_FORBIDDEN_LITERALS = (
    'trade_order', 'app_trade_user', '8a7fbc6d', 'USERS_TBS',
)


def check_copilot_fabricated_literals(added_lines):
    """阻止已知样例业务对象重新进入 Copilot 生产工具。"""
    relpath = 'monitor/copilot.py'
    path = PROJECT_DIR / relpath
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()
    violations = []
    for lineno in added_lines.get(relpath, set()):
        if 1 <= lineno <= len(lines):
            for literal in COPILOT_FORBIDDEN_LITERALS:
                if literal in lines[lineno - 1]:
                    violations.append((relpath, lineno, literal))
    return violations


def main() -> int:
    specs_keys = _parse_specs_keys()
    diff_args = _diff_args()
    changed = _changed_files(diff_args)
    changed_py = [p for s, p in changed if s in ('A', 'M') and p.endswith('.py')]
    added_lines = _added_lines_by_file(diff_args)

    all_violations = []

    for relpath, lineno in check_except_pass(changed_py, added_lines):
        all_violations.append(
            f'  [except-pass] {relpath}:{lineno}  '
            f'except 块含 pass 但无 degrade.note()'
        )

    for relpath, lineno, key in check_unregistered_settings(changed_py, added_lines, specs_keys):
        all_violations.append(
            f'  [未登记配置] {relpath}:{lineno}  '
            f'getattr(settings, "{key}", ...) 未在 appconf.SPECS 中登记'
        )

    for relpath in check_deleted_migrations(changed):
        all_violations.append(
            f'  [删除迁移] {relpath}  不应删除 migration 文件'
        )

    for relpath, lineno, literal in check_copilot_fabricated_literals(added_lines):
        all_violations.append(
            f'  [Copilot样例事实] {relpath}:{lineno}  '
            f'禁止在生产工具新增固定业务字面量 {literal!r}'
        )

    if all_violations:
        print('红线约束扫描发现违规：')
        for v in all_violations:
            print(v)
        print(f'\n共 {len(all_violations)} 处违规。')
        return 1

    print('红线约束扫描通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
