# -*- coding: utf-8 -*-
"""
Restore corrupted docstrings in start_monitor.py
"""

# Dictionary of known corrupted docstrings -> their intended English version
# Based on analysis of similar files and the pattern

CORRUPTED_DOCSTRINGS = {
    # From line 69
    '\u67d2\u8f6e\u6570\u636e\u5e93\u7c7b\u578b\u6807\u7b7e': 'return database type label',
    # Common patterns found in the file
}

# Let's analyze the corrupted text we found
print('Analyzing corrupted docstrings...')

# Read the original file
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

# Decode line by line with smart detection
lines_bytes = raw.split(b'\n')
fixed_lines = []

for i, line_bytes in enumerate(lines_bytes):
    has_high = any(b > 127 for b in line_bytes)
    
    if not has_high:
        # Pure ASCII
        fixed_lines.append(line_bytes.decode('utf-8', errors='replace').rstrip('\r'))
    else:
        # Try UTF-8 first
        try:
            line = line_bytes.decode('utf-8')
            # Check if it has replacement characters
            if '\ufffd' in line:
                # Has corruption, try GBK
                line = line_bytes.decode('gbk', errors='replace')
        except UnicodeDecodeError:
            try:
                line = line_bytes.decode('gbk', errors='replace')
            except:
                line = line_bytes.decode('latin-1', errors='replace')
        
        fixed_lines.append(line.rstrip('\r'))

# Now we have the content, let's find and fix the specific corrupted lines
content = '\n'.join(fixed_lines)

# The corrupted docstrings in the file appear to be:
# Looking at the pattern, they seem to be Chinese text that got corrupted
# Let's replace them with ASCII-safe versions

# Key corrupted strings we found (from the GBK decode of line 69):
# The docstrings appear to be short descriptions that we can replace with English

# For BaseDBChecker.check() method docstring
old_docstring_1 = '        """\u67d2\u8f6e\u6570\u636e\u5e93\u7c7b\u578b\u6807\u7b7e"""'
if old_docstring_1 in content:
    content = content.replace(old_docstring_1, '        """Return database type label."""')
    print('Fixed docstring 1')

# Let's also check for the pattern we saw in the raw bytes
# The original line 69 was: b'        """\xe8\xbf\x94\xe5\x9b\x9e\xe6\x95\xb0\xe6\x8d\xae\xe5\xba\x93\xe7\xb1\xbb\xe5\x9e\x8b\xe6\xa0\x87\xe7\xad?"""'
# Which should decode to something like "返回数据库类型标签" but got corrupted

# Search for any remaining corruption
if '\ufffd' in content:
    print(f'Warning: Still have {content.count(chr(0xfffd))} replacement characters')
    
    # Find lines with replacement characters
    for i, line in enumerate(content.split('\n')):
        if '\ufffd' in line:
            print(f'Line {i+1} has replacement chars: {line[:60]}')

# Try to compile
try:
    compile(content, 'start_monitor.py', 'exec')
    print('\nCOMPILE: SUCCESS!')
    
    # Write fixed file
    with open('monitor/management/commands/start_monitor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('File updated successfully!')
    
except SyntaxError as e:
    print(f'\nCOMPILE: FAILED at line {e.lineno}')
    print(f'Error: {e.msg}')
    lines = content.split('\n')
    print(f'Problematic line: {repr(lines[e.lineno-1][:100])}')
