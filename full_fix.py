# -*- coding: utf-8 -*-
"""
Complete fix for mixed encoding in start_monitor.py
"""
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')

# Step 1: Decode each line intelligently
decoded_lines = []
for i, line_bytes in enumerate(lines_bytes):
    has_high = any(b > 127 for b in line_bytes)
    
    if not has_high:
        # Pure ASCII/UTF-8 line
        decoded_lines.append(line_bytes.decode('utf-8', errors='replace').rstrip('\r'))
        continue
    
    # Has non-ASCII bytes - determine encoding
    try:
        decoded = line_bytes.decode('utf-8')
        # Check for replacement characters
        if '\ufffd' in decoded:
            # Try GBK instead
            decoded = line_bytes.decode('gbk', errors='replace')
    except UnicodeDecodeError:
        try:
            decoded = line_bytes.decode('gbk', errors='replace')
        except:
            decoded = line_bytes.decode('latin-1', errors='replace')
    
    decoded_lines.append(decoded.rstrip('\r'))

# Step 2: Find and fix corrupted docstrings
# Look for lines with replacement characters or garbled text
problem_docstrings = []
for i, line in enumerate(decoded_lines):
    if '\ufffd' in line and '"""' in line:
        problem_docstrings.append((i+1, line))

print(f'Found {len(problem_docstrings)} corrupted docstrings')

# Step 3: Replace corrupted docstrings with clean English versions
# Based on analysis of the file structure

# Map of regex patterns to replacements for docstrings
docstring_fixes = {
    # Line 38 - BaseDBChecker.get_connection docstring
    r'.*get_connection.*\?\?\?.*subclass.*': 
        '        """Get database connection - subclass implementation"""',
    # Line 46 - BaseDBChecker.collect_metrics docstring
    r'.*collect_metrics.*\?\?\?.*subclass.*': 
        '        """Collect metrics - subclass implementation"""',
    # Line 69 - BaseDBChecker.db_label docstring (the specific corruption we found)
    r'.*\?\?\?.*type.*label.*\?\?\?.*':
        '        """Return database type label."""',
}

# Apply fixes based on line content analysis
fixed_content = '\n'.join(decoded_lines)

# Fix specific known corrupted docstrings (identified from the analysis)
# Line 38: """鑾峰彇鏁版嵁搴撹繛鎺?- 瀛愮被瀹炵幇"""  
# Should be: """Get database connection - subclass implementation"""
fixed_content = fixed_content.replace(
    '"""\u97ad\u94\ufb3f\u8d38\u6362\u8f93\u5165\u94fe\u63a5 - \u6d3e\u751f\u7c7b\u5b9e\u73b0\"""',
    '"""Get database connection - subclass implementation"""'
)

# More general approach: find any line with "???" in docstring and replace
lines = fixed_content.split('\n')
fixed_lines = []
for line in lines:
    if '\ufffd' in line:
        # Try to extract the meaning from context
        if 'db_label' in line or 'return database' in line.lower() or 'type' in line.lower() and 'label' in line.lower():
            line = '        """Return database type label."""'
        elif 'get_connection' in line or 'connection' in line.lower():
            line = '        """Get database connection - subclass implementation"""'
        elif 'collect' in line.lower() and 'metric' in line.lower():
            line = '        """Collect metrics - subclass implementation"""'
        elif 'check' in line.lower():
            line = '        """Run unified check - entry point"""'
        elif 'db_label' in line:
            line = '        """Return database type label."""'
        elif 'unified check' in line.lower() or 'input' in line.lower():
            line = '        """Run unified check for database."""'
        else:
            # Generic replacement - try to preserve structure
            # Keep the triple quotes but replace garbled content
            line = re.sub(r'"""[^"]*\?\?[^"]*"""', '"""[comment]"""', line)
    fixed_lines.append(line)

fixed_content = '\n'.join(fixed_lines)

# Step 4: Verify
try:
    compile(fixed_content, 'start_monitor.py', 'exec')
    print('COMPILE: SUCCESS!')
    
    # Write
    with open('monitor/management/commands/start_monitor.py', 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    print('File fixed and saved!')
    
except SyntaxError as e:
    print(f'COMPILE: FAILED at line {e.lineno}')
    print(f'Error: {e.msg}')
    lines = fixed_content.split('\n')
    print(f'Line: {repr(lines[e.lineno-1][:100])}')
