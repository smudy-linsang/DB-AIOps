# -*- coding: utf-8 -*-

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
decoded_lines = []

# Step 1: Decode each line
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
        before = line_bytes[:e.start].decode('utf-8', errors='replace')
        bad = line_bytes[e.start:e.end]
        after = line_bytes[e.end:].decode('utf-8', errors='replace')
        try:
            bad_str = bad.decode('gbk', errors='replace')
        except:
            bad_str = '[X]'
        decoded_lines.append((before + bad_str + after).rstrip('\r'))

content = '\n'.join(decoded_lines)

# Step 2: ONLY fix lines that are docstrings (have triple quotes with garbled content)
lines = content.split('\n')
fixed_lines = []

for line in lines:
    # ONLY fix lines that are docstrings (contain """ and have garbage)
    if '"""' in line:
        # Check if it has replacement characters or garbled text
        if chr(0xfffd) in line or '�' in line:
            # This is a corrupted docstring - detect type
            if 'db_label' in line:
                line = '        """Return database type label."""'
            elif 'get_connection' in line:
                line = '        """Get database connection - subclass implementation"""'
            elif 'collect' in line and 'metric' in line:
                line = '        """Collect metrics - subclass implementation"""'
            elif 'check' in line and ('unified' in line.lower() or 'input' in line.lower()):
                line = '        """Run unified check for database."""'
            else:
                # Generic fallback for other docstrings
                line = '        """[comment]"""'
    
    # Also fix lines with garbled Chinese that don't have replacement chars
    # but clearly contain corrupted docstring markers
    stripped = line.strip()
    if stripped.startswith('"""') and ('标绛' in line or '绾跨' in line):
        if 'db_label' in line:
            line = '        """Return database type label."""'
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
