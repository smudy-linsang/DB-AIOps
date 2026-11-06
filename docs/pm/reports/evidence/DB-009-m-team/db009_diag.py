import io
import importlib.util

spec = importlib.util.spec_from_file_location(
    "af", r'docs/pm/reports/evidence/DB-009-m-team/db009_apply_dialect_fixes.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

old = m.CHECKER_PATCHES[9][0]
src = io.open(r'monitor/checkers/dm.py', encoding='utf-8', newline='').read()
rx = m._flex_regex(old)
print('COUNT:', len(rx.findall(src)))
print('PATTERN:', rx.pattern[:700])
i = src.find('"program"')
print('SRC_REPR:', repr(src[i - 30:i + 360]))
print('OLD_REPR:', repr(old[:360]))
