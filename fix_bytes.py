# -*- coding: utf-8 -*-
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
decoded_lines = []

for line_bytes in lines_bytes:
    has_high = any(b > 127 for b in line_bytes)
    if not has_high:
        decoded_lines.append(line_bytes.decode('utf-8', errors='replace').rstrip(b'\r').decode('utf-8'))
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
            bad_str = '[?]' * len(bad_part)
        
        try:
            rest_str = rest_part.decode('utf-8', errors='replace')
        except:
            rest_str = rest_part.decode('latin-1', errors='replace')
        
        decoded_lines.append((good_str + bad_str + rest_str).rstrip('\r'))

content = '\n'.join(decoded_lines)

# Fix all docstrings with garbled content
content = content.replace(chr(0xfffd), ' ')

# Apply specific replacements
content = re.sub(r'"""[^"]*\?+[^"]*"""', '"""[comment]"""', content)

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
