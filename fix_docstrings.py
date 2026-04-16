# -*- coding: utf-8 -*-
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
decoded_lines = []

# Step 1: Decode each line with smart handling
for line_bytes in lines_bytes:
    has_high = any(b > 127 for b in line_bytes)
    if not has_high:
        decoded = line_bytes.decode('utf-8')
        decoded_lines.append(decoded.rstrip('\r'))
        continue
    
    try:
        decoded = line_bytes.decode('utf-8')
        decoded_lines.append(decoded.rstrip('\r'))
    except UnicodeDecodeError as e:
        # Mixed encoding - try to salvage
        before = line_bytes[:e.start].decode('utf-8', errors='replace')
        bad = line_bytes[e.start:e.end]
        after = line_bytes[e.end:].decode('utf-8', errors='replace')
        
        # Try to decode bad part as GBK
        try:
            bad_str = bad.decode('gbk', errors='replace')
        except:
            bad_str = '[X]'
        
        decoded_lines.append((before + bad_str + after).rstrip('\r'))

content = '\n'.join(decoded_lines)

# Step 2: Fix ALL corrupted docstrings in one pass
lines = content.split('\n')
fixed_lines = []

for line in lines:
    # Check if this line has a corrupted docstring
    has_replacement = chr(0xfffd) in line
    has_garbled = ('标绛' in line or '�' in line)
    has_db_label = 'db_label' in line
    
    if has_replacement or has_garbled or has_db_label:
        # Determine replacement based on context
        stripped = line.strip()
        
        # db_label method
        if 'db_label' in line or ('返回' in line and ('类型' in line or '标' in line)):
            fixed_lines.append('        """Return database type label."""')
        # get_connection method
        elif 'get_connection' in line or ('连接' in line and '获取' in line):
            fixed_lines.append('        """Get database connection - subclass implementation"""')
        # collect_metrics method  
        elif 'collect' in line.lower() or ('收集' in line and '指标' in line):
            fixed_lines.append('        """Collect metrics - subclass implementation"""')
        # check method
        elif 'check' in line.lower() or ('检查' in line and '统一' in line):
            fixed_lines.append('        """Run unified check for database."""')
        # Generic fallback
        elif '"""' in line:
            # Check if it's inside a method
            fixed_lines.append('        """[comment]"""')
        else:
            fixed_lines.append(line)
    else:
        fixed_lines.append(line)

content = '\n'.join(fixed_lines)

try:
    compile(content, 'start_monitor.py', 'exec')
    print('COMPILE: SUCCESS!')
    with open('monitor/management/commands/start_monitor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('File fixed!')
except SyntaxError as e:
    print('COMPILE: FAILED at line', e.lineno)
    lines = content.split('\n')
    print('Line:', repr(lines[e.lineno-1][:100]))
