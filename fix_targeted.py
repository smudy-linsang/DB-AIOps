# -*- coding: utf-8 -*-
import re

# Read file as binary
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

# Split into lines
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
        # Find the error position
        pos = e.start
        # Split at error point
        before = line_bytes[:pos]
        bad = line_bytes[pos:pos+3]  # Usually 3 bytes for UTF-8 error
        after = line_bytes[pos+3:]
        
        # Decode parts
        try:
            before_str = before.decode('utf-8')
        except:
            before_str = before.decode('latin-1', errors='replace')
        
        # Try to decode bad part as single byte first
        if len(bad) >= 1:
            bad_str = '?'
        
        try:
            after_str = after.decode('utf-8')
        except:
            after_str = after.decode('latin-1', errors='replace')
        
        decoded_lines.append((before_str + bad_str + after_str).rstrip('\r'))

content = '\n'.join(decoded_lines)

# Step 2: Find ALL lines with corrupted docstrings and fix them
lines = content.split('\n')
fixed = []

for i, line in enumerate(lines):
    # Only fix lines that have docstrings (triple quotes) with corruption
    if '"""' in line:
        # Check for corruption indicators
        has_corruption = ('?' in line and ('绾' in line or '跨' in line or '绛' in line)) or chr(0xfffd) in line
        
        if has_corruption or ('标' in line and 'label' in line.lower()):
            # This is a corrupted docstring - determine type and fix
            # Look at the context (next line often has the method name)
            next_line = lines[i+1] if i+1 < len(lines) else ''
            prev_line = lines[i-1] if i > 0 else ''
            
            if 'db_label' in next_line or 'db_label' in prev_line or 'db_label' in line:
                fixed.append('        """Return database type label."""')
            elif 'get_connection' in next_line or 'get_connection' in prev_line or 'connection' in next_line.lower():
                fixed.append('        """Get database connection - subclass implementation."""')
            elif 'collect' in next_line.lower() or 'collect' in prev_line.lower() or 'collect_metrics' in prev_line:
                fixed.append('        """Collect metrics - subclass implementation."""')
            elif 'def check' in next_line or 'def check' in prev_line or 'check(' in prev_line:
                fixed.append('        """Run unified check for database."""')
            else:
                fixed.append('        """[description]"""')
        else:
            fixed.append(line)
    else:
        fixed.append(line)

content = '\n'.join(fixed)

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
    # Show more context
    if e.lineno > 1:
        print('Prev:', repr(lines[e.lineno-2][:60]))
