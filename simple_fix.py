
# -*- coding: utf-8 -*-
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()
lines_bytes = raw.split(b'\n')
fixed_lines = []

for i, line_bytes in enumerate(lines_bytes):
    # Try UTF-8 first
    try:
        line = line_bytes.decode('utf-8')
        fixed_lines.append(line.rstrip('\r'))
        continue
    except:
        pass
    
    # Try GBK
    try:
        line = line_bytes.decode('gbk', errors='replace')
        line = line.replace('\ufffd', ' ')
        fixed_lines.append(line.rstrip('\r'))
    except:
        line = line_bytes.decode('latin-1', errors='replace')
        fixed_lines.append(line.rstrip('\r'))

content = '\n'.join(fixed_lines)

try:
    compile(content, 'test', 'exec')
    print('SUCCESS')
    with open('monitor/management/commands/start_monitor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SAVED')
except SyntaxError as e:
    print('FAIL at line', e.lineno)
    print('Line:', repr(content.split('\n')[e.lineno-1][:100]))
