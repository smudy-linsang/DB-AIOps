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

# Step 2: Fix ALL corrupted docstrings with exact string replacement
# These are the known corrupted docstrings we found in the analysis
known_corruptions = [
    # Line 38 - get_connection docstring
    ('        """\u9272\u94\ufffd\u8d38\u6362\u8f93\u5165\u94fe\u63a5 - \u6d3e\u751f\u7c7b\u5b9e\u73b0"""',
     '        """Get database connection - subclass implementation"""'),
    
    # Line 46 - collect_metrics docstring
    ('        """\u7bb9\ufffd\u4e00\u6a80\u69fd\u5851\u5b58\ufffd"""',
     '        """Collect metrics - subclass implementation"""'),
    
    # Line 69 - db_label docstring (the specific one we found)
    ('        """\u6740\u8f93\u5165\u6362\u8f93\u5165\u8f93\u5165\u94fe\u63a5 - \u6d3e\u751f\u7c7b\u5b9e\u73b0"""',
     '        """Return database type label."""'),
]

# Let's use a different approach: look for lines with 6+ consecutive high Unicode chars
# that contain docstring markers and fix them
fixed_lines = []
for line in decoded_lines:
    # Check if this is a corrupted docstring line
    if '\ufffd' in line:
        # Fix based on keyword detection
        if 'db_label' in line or ('return' in line.lower() and ('type' in line.lower() or 'database' in line.lower())):
            line = '        """Return database type label."""'
        elif 'get_connection' in line or ('connection' in line.lower()):
            line = '        """Get database connection - subclass implementation"""'
        elif 'collect' in line.lower() and 'metric' in line.lower():
            line = '        """Collect metrics - subclass implementation"""'
        elif 'check' in line.lower():
            line = '        """Run unified check for database."""'
        else:
            # Use regex to replace only the garbage inside docstrings
            line = re.sub(r'"""[^"]*\ufffd[^"]*"""', '"""[comment]"""', line)
    
    # Also fix lines that were decoded from GBK but contain garbled text
    # These appear to be Chinese that got corrupted - replace with English
    # Check for lines containing both triple quotes and garbled content
    if '"""' in line and ('\ufffd' in line or len(line) > 30):
        # Look for specific garbled patterns
        if chr(0x6740) + chr(0x8f93) in line:  # 杩斿 = 杩斿洖
            line = '        """Return database type label."""'
        elif chr(0x9272) + chr(0x94) in line:  # 鑾峰 = 鑾峰彇
            line = '        """Get database connection - subclass implementation"""'
        elif chr(0x7bb9) in line:  # 筱 = 第一个乱码字
            line = '        """Collect metrics - subclass implementation"""'
    
    fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# Step 3: Verify
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
