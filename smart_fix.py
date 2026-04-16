# -*- coding: utf-8 -*-
"""
Smart encoding detection and fixing
"""
with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

lines_bytes = raw.split(b'\n')

# Test each line: try UTF-8 first, if fails try GBK
utf8_ok = 0
gbk_fallback = 0

for i, line_bytes in enumerate(lines_bytes):
    has_high = any(b > 127 for b in line_bytes)
    if not has_high:
        continue
    
    # Try UTF-8 first
    try:
        line_bytes.decode('utf-8')
        utf8_ok += 1
    except UnicodeDecodeError:
        # Try GBK
        try:
            decoded = line_bytes.decode('gbk')
            if '???' in decoded or '\ufffd' in decoded:
                print(f'Line {i+1} (GBK): Still has garbage')
            else:
                print(f'Line {i+1}: GBK worked')
            gbk_fallback += 1
        except:
            print(f'Line {i+1}: Both failed')

print(f'\nUTF-8 OK: {utf8_ok}')
print(f'GBK fallback: {gbk_fallback}')

# Now let's try a different approach:
# For lines with high bytes, try to decode as UTF-8
# If it contains a valid docstring pattern, it's probably UTF-8
# If not, try GBK

print('\n--- Checking specific problematic lines ---')
for i, line_bytes in enumerate(lines_bytes[65:75], start=66):
    has_high = any(b > 127 for b in line_bytes)
    if has_high:
        # Check UTF-8 first
        try:
            utf8_decoded = line_bytes.decode('utf-8')
            print(f'Line {i} (UTF-8): {repr(utf8_decoded[:60])}')
        except UnicodeDecodeError:
            try:
                gbk_decoded = line_bytes.decode('gbk')
                print(f'Line {i} (GBK): {repr(gbk_decoded[:60])}')
            except:
                print(f'Line {i}: Both failed')
    else:
        print(f'Line {i} (ASCII): OK')
