# -*- coding: utf-8 -*-
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()
lines = raw.split(b'\n')

print('Analyzing corrupted lines...')
for i, line in enumerate(lines):
    try:
        line.decode('utf-8')
    except:
        has_comment_hash = b'#' in line
        has_docstring = b'"""' in line
        
        try:
            gbk_decoded = line.decode('gbk', errors='replace')
            print(f'Line {i+1}: has_comment={has_comment_hash}, has_docstring={has_docstring}')
            print(f'  GBK: {gbk_decoded[:60]}')
        except:
            print(f'Line {i+1}: Cannot decode')
