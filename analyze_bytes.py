# -*- coding: utf-8 -*-
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

print('Line 69 raw bytes:')
line_69_bytes = raw.split(b'\n')[68]
print('Bytes:', line_69_bytes[:100])
print('Hex:', line_69_bytes[:100].hex())

print('\nAnalyzing byte patterns for GBK...')
gbk_count = 0
for i, b in enumerate(line_69_bytes):
    if 0x81 <= b <= 0xFE:
        if i + 1 < len(line_69_bytes):
            next_b = line_69_bytes[i+1]
            if 0x40 <= next_b <= 0xFE:
                gbk_count += 1

print('Total GBK chars found:', gbk_count)
