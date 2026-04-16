# -*- coding: utf-8 -*-

# Read file as binary
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
decoded_lines = []

# Step 1: Decode each line with better error handling
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
        # UTF-8 failed - decode each byte manually
        result = []
        i = 0
        while i < len(line_bytes):
            b = line_bytes[i]
            if b < 128:
                # ASCII
                result.append(chr(b))
                i += 1
            else:
                # High byte - determine how many bytes
                if 0xC0 <= b <= 0xDF:  # 2-byte UTF-8
                    if i+1 < len(line_bytes) and 0x80 <= line_bytes[i+1] <= 0xBF:
                        try:
                            char = line_bytes[i:i+2].decode('utf-8')
                            result.append(char)
                        except:
                            result.append('?')
                        i += 2
                    else:
                        result.append('?')
                        i += 1
                elif 0xE0 <= b <= 0xEF:  # 3-byte UTF-8
                    if i+2 < len(line_bytes):
                        try:
                            char = line_bytes[i:i+3].decode('utf-8')
                            result.append(char)
                        except:
                            result.append('?')
                        i += 3
                    else:
                        result.append('?')
                        i += 1
                else:  # 4-byte UTF-8 or invalid
                    result.append('?')
                    i += 1
        
        decoded_lines.append(''.join(result).rstrip('\r'))

content = '\n'.join(decoded_lines)

# Step 2: Find and fix corrupted docstrings
lines = content.split('\n')
fixed = []

for i, line in enumerate(lines):
    if '"""' in line:
        # Count quotes in this line
        quote_count = line.count('"')
        
        # If it's a corrupted docstring (wrong number of quotes)
        if quote_count != 6:
            # Look at context to determine what it should be
            prev_line = lines[i-1] if i > 0 else ''
            next_line = lines[i+1] if i+1 < len(lines) else ''
            
            if 'db_label' in prev_line:
                fixed.append('        """Return database type label."""')
            elif 'get_connection' in prev_line or 'getConnection' in prev_line:
                fixed.append('        """Get database connection - subclass implementation."""')
            elif 'collect_metrics' in prev_line:
                fixed.append('        """Collect metrics - subclass implementation."""')
            elif 'def check' in prev_line or 'def check_input' in prev_line:
                fixed.append('        """Run unified check for database."""')
            elif 'unified_check' in prev_line:
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
