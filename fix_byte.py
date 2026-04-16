# -*- coding: utf-8 -*-
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

# Split into lines preserving bytes
lines_bytes = raw.split(b'\n')
decoded_lines = []

for i, line_bytes in enumerate(lines_bytes):
    has_high = any(b > 127 for b in line_bytes)
    if not has_high:
        decoded_lines.append(line_bytes.decode('utf-8', errors='replace').rstrip(b'\r').decode('utf-8'))
        continue
    
    # Try UTF-8 first
    try:
        decoded = line_bytes.decode('utf-8')
        decoded_lines.append(decoded.rstrip('\r'))
    except UnicodeDecodeError as e:
        # UTF-8 failed - this line has mixed encodings
        # Replace only the corrupted bytes with placeholder, keep rest
        good_part = line_bytes[:e.start]
        bad_part = line_bytes[e.start:e.end]
        rest_part = line_bytes[e.end:]
        
        # Decode good part as UTF-8
        good_str = good_part.decode('utf-8', errors='replace')
        
        # Try to decode bad part as GBK
        try:
            bad_str = bad_part.decode('gbk', errors='replace')
        except:
            bad_str = '[?]' * len(bad_part)
        
        # Decode rest normally
        try:
            rest_str = rest_part.decode('utf-8', errors='replace')
        except:
            rest_str = rest_part.decode('latin-1', errors='replace')
        
        decoded_lines.append((good_str + bad_str + rest_str).rstrip('\r'))

content = '\n'.join(decoded_lines)

# Now fix all corrupted docstrings with known replacements
# Look for lines with '?' (replacement char) or garbled Chinese in docstrings

# Specific replacements based on method names
replacements = [
    # get_connection docstring corruption
    (r'\?\?\?.*connection.*\?\?\?', '"""Get database connection - subclass implementation"""'),
    # collect_metrics docstring corruption
    (r'\?\?\?.*collect.*\?\?\?', '"""Collect metrics - subclass implementation"""'),
    # db_label docstring corruption
    (r'\?\?\?.*type.*label.*\?\?\?', '"""Return database type label."""'),
    # check docstring corruption
    (r'\?\?\?.*check.*\?\?\?', '"""Run unified check for database."""'),
]

# Apply regex replacements
for pattern, replacement in replacements:
    content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)

# Also fix lines with replacement chars
content = content.replace(chr(0xfffd), ' ')

# Try to compile
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
