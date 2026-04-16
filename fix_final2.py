"""
Fix corrupted docstrings in start_monitor.py
"""
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
fixed_lines = []
current_method = None

for i, line_bytes in enumerate(lines_bytes):
    try:
        line_str = line_bytes.decode('utf-8', errors='replace')
    except:
        line_str = line_bytes.decode('latin-1', errors='replace')
    
    # Track method names
    if 'def db_label' in line_str:
        current_method = 'db_label'
    elif 'def get_connection' in line_str:
        current_method = 'get_connection'
    elif 'def collect_metrics' in line_str:
        current_method = 'collect_metrics'
    elif 'def check' in line_str or 'def unified_check' in line_str:
        current_method = 'check'
    elif line_str.strip().startswith('def '):
        current_method = None
    
    # Check for corruption: 8 spaces + "" + high bytes + ? + ""
    has_corruption = False
    if (line_bytes[:8] == b'        ' and 
        line_bytes[8:10] == b'""' and
        len(line_bytes) >= 5 and
        line_bytes[-5:-2] == b'?""'):
        has_corruption = True
    
    if has_corruption:
        if current_method == 'db_label':
            fixed_lines.append(b'        """Return database type label."""')
        elif current_method == 'get_connection':
            fixed_lines.append(b'        """Get database connection - subclass implementation."""')
        elif current_method == 'collect_metrics':
            fixed_lines.append(b'        """Collect metrics - subclass implementation."""')
        elif current_method == 'check':
            fixed_lines.append(b'        """Run unified check for database."""')
        else:
            fixed_lines.append(b'        """[description]"""')
        current_method = None
    else:
        fixed_lines.append(line_bytes)

fixed_content = b'\n'.join(fixed_lines)

try:
    content = fixed_content.decode('utf-8')
    compile(content, 'start_monitor.py', 'exec')
    print('COMPILE: SUCCESS!')
    with open('monitor/management/commands/start_monitor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('File fixed!')
except Exception as e:
    print('ERROR:', e)
"