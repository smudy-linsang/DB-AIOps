import sys
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()
lines = raw.split(b'\n')

correct_db_label = b'        """Return database type label."""'

for i in range(len(lines)):
    line = lines[i]
    if (len(line) >= 15 and line[:8] == b'        ' and line[8:11] == b'"""' and line[-5:-2] == b'?""'):
        prev_line = lines[i-1] if i > 0 else b''
        next_line = lines[i+1] if i+1 < len(lines) else b''
        
        if b'db_label' in prev_line or b'db_label' in next_line:
            lines[i] = correct_db_label
            print(f'Fixed line {i+1}')
        elif b'get_connection' in prev_line or b'get_connection' in next_line:
            lines[i] = b'        """Get database connection - subclass implementation."""'
            print(f'Fixed line {i+1}')
        elif b'collect_metrics' in prev_line or b'collect_metrics' in next_line:
            lines[i] = b'        """Collect metrics - subclass implementation."""'
            print(f'Fixed line {i+1}')
        elif b'def check' in prev_line or b'def check' in next_line:
            lines[i] = b'        """Run unified check for database."""'
            print(f'Fixed line {i+1}')
        else:
            lines[i] = b'        """[description]"""'
            print(f'Fixed line {i+1}')

fixed = b'\n'.join(lines)
try:
    content = fixed.decode('utf-8')
    compile(content, 'test', 'exec')
    print('SUCCESS')
    with open('monitor/management/commands/start_monitor.py', 'wb') as f:
        f.write(fixed)
    print('SAVED')
except Exception as e:
    print('ERROR:', e)
