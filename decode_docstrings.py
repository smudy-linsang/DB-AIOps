# -*- coding: utf-8 -*-
"""
Fix corrupted docstrings in start_monitor.py
"""
import re

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

# Try UTF-8 first, then fallback to GBK
try:
    content = raw.decode('utf-8', errors='strict')
    print('UTF-8 decode: OK')
except UnicodeDecodeError as e:
    print(f'UTF-8 decode failed at {e.start}: {e.reason}')
    print(f'Context: {repr(raw[e.start-20:e.start+20])}')

# For now, let's check what the corrupted docstrings look like
lines = content.split('\n')
for i, line in enumerate(lines):
    if '\ufffd' in line or '?' in repr(line):
        print(f'Line {i+1}: {repr(line[:80])}')
        if i > 60 and i < 80:
            break
