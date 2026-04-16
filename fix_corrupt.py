# -*- coding: utf-8 -*-
import re

# Mapping of corrupted Chinese to English
# Based on the patterns we found
CHINESE_TO_ENGLISH = {
    # Section headers
    '# =======================================': '# ' + '=' * 45,
    '# ': '# ',  # Keep as comment
    
    # Common patterns
    '鏉╂柨娲栭弫鐗堝祦鎼存挾琚哽崹瀣╂垼缁犵': 'Base database checker - Unified monitoring framework',
    '鎼存挾琚哽崹瀣╂垼缁犵': 'Monitor collection base class',
    '骞跺彂閲囬泦绾跨▼鏁?': 'Concurrent collection thread pool count',
    '閿佺瓑寰呭憡璀﹂槇鍊?(绉?': 'Lock wait alert threshold (seconds)',
    '閫氱敤鐩戞帶鏁版嵁閲囬泦鍣ㄥ熀绫?': 'Universal monitoring data collector base class',
    '鑾峰彇鏁版嵁搴撹繛鎺?- 瀛愮被瀹炵幇': 'Get database connection - subclass implementation',
    '杩斿洖鏁版嵁搴撶被鍨嬫爣绛?': 'Return database type label.',
    '杩炴帴鏁?': 'connections',
    '鎱㈡煡璇?': 'slow queries',
    '鏁版嵁搴撳ぇ灏?': 'database size',
    '閿佺瓑寰?': 'locks',
    '鏁版嵁搴撳彇寰?': 'database used',
    '鏁版嵁搴撴€婚噺': 'database total',
    '鍏ㄦ爤鐩戞帶灏堟姢杩涚▼': 'Full-stack monitoring maintenance process',
    '杩炴帴鏁板憡璀?': 'connection alert',
    '杩炴帴鏁版仮澶?': 'connection restored',
    '杩炴帠鏁版仮澶?': 'connection restored',
    '杩炴帠鏁板憡璀?': 'connection alert',
    '杩炴帠鏁板睃': 'connection count',
    '閿佺瓑寰呰В闄?': 'lock resolved',
    '鏋勫缓閿佺瓑寰呭憡璀︽秷鎭?': 'Build lock wait alert message',
    '鐩戞帶灏氭湭瀹炵幇锛岄渶瑕佸畨瑁?redis-py 搴?': 'Monitoring not implemented yet, need to install redis-py',
    '鏁版嵁搴撴槸鍚?': 'database type',
    '鍏ㄦ爤鏁版嵁搴撴槸鍚?': 'full-stack database type',
    '澶囦唤鏁版嵁搴撴槸鍚?': 'backup database type',
    '鏁版嵁搴撶姸鎬?': 'database status',
    '鏁版嵁搴撻€熷崟': 'database total',
    '鏁版嵁搴撻€熸按': 'database used',
    '鏁版嵁搴撴槸鍚?绾?': 'database type and',
    '鏁版嵁搴撴槸鍚?鏍囧噯': 'database type standard',
    '鏁版嵁搴撴槸鍚?涓撶': 'database type detail',
    '寮€濮嬬嚎绋?': 'start line',
    '鏍规嵁鏁版嵁搴撴槸鍚?鏍规嵁涓嶅悓鏁版嵁搴撴槸鍚?閫夋嫨': 'Select checker based on database type',
    '涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?': 'unsupported database type',
    '鎵ц鍔ㄦ敹闃?': 'execute collection',
    '鏁版嵁搴撻€?': 'database total',
    '鏁版嵁搴撻€熸按': 'database used',
    '鏁版嵁搴撻€熷崟': 'database size',
    '瀹炵幇鍒扮被': 'implementation class',
    '涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?': 'unsupported database type',
    '鏍规嵁鏁版嵁搴撴槸鍚?鏍规嵁涓嶅悓鏁版嵁搴撴槸鍚?閫夋嫨涓嶅悓妗?', 'select different',
    '涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?鍙?涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?': 'unsupported database type',
    '涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?浠呮敮鎸佺殑': 'unsupported database type only supports',
    '閿佺瓑寰呭憡璀?': 'lock wait alert',
    '閿佺瓑寰呭憡璀﹁В鍐?': 'lock wait alert resolved',
    '澶辫触涓?': 'failed once',
    '寮€濮嬬嚎绋?: 'start line: ',
    '鏍规嵁鏁版嵁搴撴槸鍚?': 'based on database type',
    '鏍规嵁涓嶅悓鏁版嵁搴撴槸鍚?': 'based on different database types',
    '鏍规嵁涓嶅悓鏁版嵁搴撴槸鍚?閫夋嫨涓嶅悓妗?': 'select different class',
    '鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?': 'unsupported database type',
    '涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?鏍规嵁鏁版嵁搴撴槸鍚?閫夋嫨涓嶅悓妗?': 'select different class',
    '涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑浠呮敮鎸佺殑': 'unsupported only supports',
    '澶辫触鍙?': 'failed only',
    '鏁版嵁搴撴槸鍚?鏍囧噯涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑涓嶆敮鎸佺殑涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑浠呮敮鎸佺殑鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑浠呮敮鎸佺殑涓嶆敮鎸佺殑涓嶆敮鎸佺殑浠呮敮鎸佺殑': 'unsupported',
    '鏍规嵁涓嶅悓鏁版嵁搴撴槸鍚?閫夋嫨涓嶅悓妗?浠呮敮鎸佺殑涓嶆敮鎸佺殑涓嶆敮鎸佺殑浠呮敮鎸佺殑涓嶆敮鎸佺殑浠呮敮鎸佺殑浠呮敮鎸佺殑涓嶆敮鎸佺殑浠呮敮鎸佺殑': 'select',
    '鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑鏁版嵁搴撴槸鍚?涓嶆敮鎸佺殑浠呮敮鎸佺殑': 'unsupported',
}

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')
fixed_lines = []

for i, line_bytes in enumerate(lines_bytes):
    # Try UTF-8 first
    try:
        line = line_bytes.decode('utf-8')
        fixed_lines.append(line.rstrip('\r'))
        continue
    except:
        pass
    
    # Try GBK
    try:
        line = line_bytes.decode('gbk', errors='replace')
        # Replace corrupted characters
        line = line.replace('\ufffd', '?')
        # Try to replace known Chinese patterns
        for cn, en in CHINESE_TO_ENGLISH.items():
            line = line.replace(cn, en)
        # If still has ? markers, try to clean up
        line = re.sub(r'\?[^\n]*\?', ' [comment]', line)
        fixed_lines.append(line.rstrip('\r'))
    except:
        # Last resort: use latin-1
        line = line_bytes.decode('latin-1', errors='replace')
        fixed_lines.append(line.rstrip('\r'))

content = '\n'.join(fixed_lines)

# Try to compile
try:
    compile(content, 'start_monitor.py', 'exec')
    print('COMPILE: SUCCESS!')
    with open('monitor/management/commands/start_monitor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('File fixed!')
except SyntaxError as e:
    print(f'COMPILE: FAILED at line {e.lineno}')
    lines = content.split('\n')
    print('Line:', repr(lines[e.lineno-1][:100]))
