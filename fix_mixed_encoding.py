# -*- coding: utf-8 -*-
"""
Fix mixed UTF-8/GBK encoding in start_monitor.py
"""
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

# Strategy: Split by lines, decode each line individually
# ASCII lines -> decode as UTF-8 (with error replacement)
# Lines with high-byte Chinese -> try UTF-8 first, then GBK

lines_bytes = raw.split(b'\n')
fixed_lines = []

for i, line_bytes in enumerate(lines_bytes):
    # Check if line has high bytes (Chinese characters)
    has_high_bytes = any(b > 127 for b in line_bytes)
    
    if not has_high_bytes:
        # Pure ASCII/UTF-8 line
        try:
            line = line_bytes.decode('utf-8')
        except:
            line = line_bytes.decode('utf-8', errors='replace')
    else:
        # Try UTF-8 first
        try:
            line = line_bytes.decode('utf-8')
        except UnicodeDecodeError:
            # Try GBK
            try:
                line = line_bytes.decode('gbk', errors='replace')
            except:
                line = line_bytes.decode('gbk', errors='ignore')
    
    fixed_lines.append(line)

# Join and verify
content = '\n'.join(fixed_lines)

# Try to compile
try:
    compile(content, 'start_monitor.py', 'exec')
    print('COMPILE: SUCCESS!')
    
    # Write fixed file
    with open('monitor/management/commands/start_monitor_fixed.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Written to: start_monitor_fixed.py')
    
except SyntaxError as e:
    print(f'COMPILE: FAILED at line {e.lineno}')
    print(f'Error: {e.msg}')
    # Show the problematic line
    if e.lineno:
        print(f'Line {e.lineno}: {repr(fixed_lines[e.lineno-1][:100])}')
