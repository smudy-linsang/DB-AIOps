import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

print(f'File size: {len(raw)} bytes')

# Find the problematic area around position 747
problem_area_start = max(0, 747 - 50)
problem_area_end = min(len(raw), 747 + 50)
print(f'\nArea around position 747 (bytes {problem_area_start}-{problem_area_end}):')
print('Bytes:', raw[problem_area_start:problem_area_end])
print('Hex:', raw[problem_area_start:problem_area_end].hex())

# Let's try to find all non-ASCII sequences
print('\n--- Looking for multi-byte sequences ---')
for i in range(len(raw) - 1):
    if raw[i] > 127:
        # Found a non-ASCII byte, check if it's start of UTF-8 sequence
        b = raw[i]
        if (b & 0xE0) == 0xC0:  # 2-byte UTF-8
            if i + 1 < len(raw):
                if (raw[i+1] & 0xC0) != 0x80:
                    print(f'Invalid 2-byte UTF-8 at {i}: 0x{b:02x} 0x{raw[i+1]:02x}')
        elif (b & 0xF0) == 0xE0:  # 3-byte UTF-8
            if i + 2 < len(raw):
                if (raw[i+1] & 0xC0) != 0x80 or (raw[i+2] & 0xC0) != 0x80:
                    print(f'Invalid 3-byte UTF-8 at {i}: 0x{b:02x} 0x{raw[i+1]:02x} 0x{raw[i+2]:02x}')
        elif (b & 0xF8) == 0xF0:  # 4-byte UTF-8
            pass  # Less common
        else:
            print(f'Lone high byte at {i}: 0x{b:02x}')