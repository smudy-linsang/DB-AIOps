# -*- coding: utf-8 -*-
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
decoded_lines = []

# Step 1: Decode each line
for line_bytes in lines_bytes:
    has_high = any(b > 127 for b in line_bytes)
    if not has_high:
        decoded_lines.append(line_bytes.decode('utf-8', errors='replace').rstrip('\r'))
        continue
    try:
        decoded = line_bytes.decode('utf-8')
        if chr(0xfffd) in decoded:
            decoded = line_bytes.decode('gbk', errors='replace')
    except:
        try:
            decoded = line_bytes.decode('gbk', errors='replace')
        except:
            decoded = line_bytes.decode('latin-1', errors='replace')
    decoded_lines.append(decoded.rstrip('\r'))

# Step 2: Fix corrupted docstrings
fixed_lines = []
for line in decoded_lines:
    if chr(0xfffd) in line:
        if 'db_label' in line or ('return' in line.lower() and 'type' in line.lower() and 'label' in line.lower()):
            line = '        """Return database type label."""'
        elif 'get_connection' in line or 'connection' in line.lower():
            line = '        """Get database connection - subclass implementation"""'
        elif 'collect' in line.lower() and 'metric' in line.lower():
            line = '        """Collect metrics - subclass implementation"""'
        elif 'check' in line.lower():
            line = '        """Run unified check for database."""'
        else:
            line = re.sub(r'"""[^"]*' + chr(0xfffd) + r'[^"]*"""', '"""[comment]"""', line)
    fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# Step 3: Verify and save
try:
    compile(content, 'start_monitor.py', 'exec')
    print('COMPILE: SUCCESS!')
    with open('monitor/management/commands/start_monitor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('File fixed and saved!')
except SyntaxError as e:
    print('COMPILE: FAILED at line', e.lineno)
    lines = content.split('\n')
    print('Line:', repr(lines[e.lineno-1][:100]))
