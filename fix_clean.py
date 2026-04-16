# -*- coding: utf-8 -*-
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
decoded_lines = []

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
        good_part = line_bytes[:e.start]
        bad_part = line_bytes[e.start:e.end]
        rest_part = line_bytes[e.end:]
        
        good_str = good_part.decode('utf-8', errors='replace')
        try:
            bad_str = bad_part.decode('gbk', errors='replace')
        except:
            bad_str = ' GARBLED '
        
        try:
            rest_str = rest_part.decode('utf-8', errors='replace')
        except:
            rest_str = rest_part.decode('latin-1', errors='replace')
        
        decoded_lines.append((good_str + bad_str + rest_str).rstrip('\r'))

content = '\n'.join(decoded_lines)

# Replace all garbage characters
content = content.replace(chr(0xfffd), ' ')

# Fix docstrings - replace any garbled text inside triple quotes
# Pattern: triple quotes with any garbage characters between them
lines = content.split('\n')
fixed_lines = []
for line in lines:
    if '"""' in line and ('GARBLED' in line or chr(0xfffd) in line):
        # This is a corrupted docstring - detect type and replace
        if 'db_label' in line or ('return' in line.lower() and 'type' in line.lower()):
            line = '        """Return database type label."""'
        elif 'get_connection' in line or 'connection' in line.lower():
            line = '        """Get database connection - subclass implementation"""'
        elif 'collect' in line.lower() and 'metric' in line.lower():
            line = '        """Collect metrics - subclass implementation"""'
        elif 'check' in line.lower():
            line = '        """Run unified check for database."""'
        else:
            line = '        """[comment]"""'
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
