import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')

# Try reading as UTF-8
try:
    with open('monitor/management/commands/start_monitor.py', 'r', encoding='utf-8') as f:
        content = f.read()
    print('UTF-8 decode: SUCCESS')
    # Check for common Chinese words
    print('Found 表空间:', content.count('表空间'))
    print('Found 锁等待:', content.count('锁等待'))
    print('Found 采集:', content.count('采集'))
    print('Found 连接:', content.count('连接'))
except UnicodeDecodeError as e:
    print('UTF-8 decode: FAILED')
    print(str(e))
    
    # Try GBK
    try:
        with open('monitor/management/commands/start_monitor.py', 'r', encoding='gbk') as f:
            content = f.read()
        print('GBK decode: SUCCESS')
        print('Found 表空间:', content.count('表空间'))
        print('Found 锁等待:', content.count('锁等待'))
    except Exception as e2:
        print('GBK decode: FAILED')
        print(str(e2))