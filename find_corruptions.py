# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')

# Find all corrupted lines (those that fail UTF-8 but succeed with GBK)
print('=== Corrupted Lines Analysis ===')
corrupted_lines = []

for i, line_bytes in enumerate(lines_bytes):
    has_high = any(b > 127 for b in line_bytes)
    if not has_high:
        continue
    
    # Try UTF-8
    try:
        line_bytes.decode('utf-8')
        # Check if it has replacement chars
        decoded = line_bytes.decode('utf-8')
        if '\ufffd' in decoded:
            corrupted_lines.append((i+1, 'utf8_replacement', line_bytes))
    except UnicodeDecodeError:
        # Try GBK
        try:
            decoded_gbk = line_bytes.decode('gbk')
            if '\ufffd' not in decoded_gbk:
                corrupted_lines.append((i+1, 'gbk', line_bytes))
        except:
            corrupted_lines.append((i+1, 'failed', line_bytes))

print(f'Found {len(corrupted_lines)} problematic lines')

for line_num, method, raw_bytes in corrupted_lines[:20]:
    print(f'\nLine {line_num} ({method}):')
    print(f'  Raw hex: {raw_bytes[:50].hex()}')
    if method == 'utf8_replacement':
        decoded = raw_bytes.decode('utf-8', errors='replace')
        print(f'  UTF8: {decoded[:60]}')
        decoded_gbk = raw_bytes.decode('gbk', errors='replace')
        print(f'  GBK:  {decoded_gbk[:60]}')
    elif method == 'gbk':
        decoded_gbk = raw_bytes.decode('gbk', errors='replace')
        print(f'  GBK: {decoded_gbk[:60]}')
