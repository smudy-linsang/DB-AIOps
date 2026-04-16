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
# Find all lines with garbled content in docstrings and replace them
fixed_lines = []
for line in decoded_lines:
    # Check for replacement characters indicating corruption
    if chr(0xfffd) in line:
        # Detect what type of method this is based on surrounding context
        if 'db_label' in line or 'type' in line.lower():
            line = '        """Return database type label."""'
        elif 'get_connection' in line or 'connection' in line.lower():
            line = '        """Get database connection - subclass implementation"""'
        elif 'collect' in line.lower() and 'metric' in line.lower():
            line = '        """Collect metrics - subclass implementation"""'
        elif 'check' in line.lower():
            line = '        """Run unified check for database."""'
        else:
            # Generic fix
            line = re.sub(r'"""[^"]*' + chr(0xfffd) + r'[^"]*"""', '"""[comment]"""', line)
    
    # Also fix lines that look like corrupted Chinese text in docstrings
    # These start with Chinese-looking characters followed by garbled content
    if '"""' in line and ('杩' in line or '鑾' in line or '瀛' in line):
        # This is a corrupted Chinese docstring - replace with English
        if 'db_label' in line:
            line = '        """Return database type label."""'
        elif 'get_connection' in line or 'connection' in line:
            line = '        """Get database connection - subclass implementation"""'
        elif 'collect' in line and 'metric' in line:
            line = '        """Collect metrics - subclass implementation"""'
        elif 'check' in line:
            line = '        """Run unified check for database."""'
    
    fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# Step 3: Verify and save
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
