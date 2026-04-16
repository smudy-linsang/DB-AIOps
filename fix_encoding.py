# -*- coding: utf-8 -*-
"""
智能修复 start_monitor.py 的混合编码问题
策略：用 GBK 解码整个文件（因为包含 GBK 编码的中文注释），
提取关键代码部分，重新生成干净的 UTF-8 文件
"""

import re

# 读取原始文件
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

# 尝试用 GBK 解码整个文件
try:
    content = raw.decode('gbk', errors='replace')
    print('GBK decode: OK')
except:
    content = raw.decode('gbk', errors='ignore')
    print('GBK decode: with errors')

# 检查关键代码结构是否完整
code_markers = [
    'def handle(',
    'def _run_single_check(',
    'def monitor_job(',
    'def process_result(',
    'class OracleChecker(',
    'class MySQLChecker(',
    'class PostgreSQLChecker(',
    'class DamengChecker(',
    'CHECKER_MAP = {',
    'def send_alert(',
]

print('\n=== Code Structure Check ===')
for marker in code_markers:
    found = marker in content
    print(f'  [{"OK" if found else "MISS"}] {marker[:50]}')

# 统计
print(f'\nFile size: {len(raw)} bytes')
print(f'Content length: {len(content)} chars')

# 提取所有 def 和 class 定义（用于验证结构）
print('\n=== Found Functions/Classes ===')
func_pattern = r'^(class|def) \w+'
for match in re.finditer(func_pattern, content, re.MULTILINE):
    print(f'  {match.group()}')
