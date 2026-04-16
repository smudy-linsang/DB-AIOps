# -*- coding: utf-8 -*-
"""
智能解码混合编码的 start_monitor.py
策略：识别文件中的编码边界，混合解码
"""

with open('monitor/management/commands/start_monitor.py', 'rb') as f:
    raw = f.read()

# 策略：先尝试 UTF-8 解码，将失败的字节替换为占位符
# 然后尝试 GBK 解码注释部分
# 最终用正确的方式重新编码

# 简单方案：先尝试纯UTF-8解码（Python字符串需要）
try:
    # 先用UTF-8解码，将错误字节替换为 ?
    content_utf8 = raw.decode('utf-8', errors='replace')
    print('UTF-8 partial decode: OK')
except:
    content_utf8 = raw.decode('utf-8', errors='ignore')
    print('UTF-8 partial decode: with errors')

# 提取docstring中的乱码行
lines = content_utf8.split('\n')
print(f'Total lines: {len(lines)}')

# 找到所有包含乱码的行（包含非ASCII Unicode但不是正常中文的）
problem_lines = []
for i, line in enumerate(lines):
    # 检查是否有可疑的乱码字符
    for char in line:
        if 0x4E00 <= ord(char) <= 0x9FFF:  # CJK Unified Ideographs
            continue  # 正常中文
        if 0x3000 <= ord(char) <= 0x303F:  # CJK Symbols
            continue
        if ord(char) < 128:  # ASCII
            continue
        # 其他非ASCII字符可能是乱码
        problem_lines.append(i)
        break

print(f'Problem lines (non-standard CJK): {len(set(problem_lines))}')

# 统计哪些行的docstring有乱码
docstring_problems = []
for i, line in enumerate(lines):
    if '\u' in repr(line)[:100]:  # 检查是否有转义序列
        if '"""' in line or "'''" in line:
            docstring_problems.append(i+1)

print(f'Docstring lines with issues: {docstring_problems[:10]}')

# 看看第69行附近的内容
print('\nLine 69 raw bytes:')
line_69_bytes = raw.split(b'\n')[68]
print(f'Bytes: {line_69_bytes[:100]}')
print(f'Hex: {line_69_bytes[:100].hex()}')

# 检查是否是GBK编码的中文
# GBK中文字符通常是 0x81-0xFE 范围的两个字节
print('\nAnalyzing byte patterns...')
for i, b in enumerate(line_69_bytes):
    if 0x81 <= b <= 0xFE:
        print(f'  Byte {i}: 0x{b:02x} (possible GBK high byte)')
        if i + 1 < len(line_69_bytes):
            next_b = line_69_bytes[i+1]
            if 0x40 <= next_b <= 0xFE:
                print(f'    Next byte 0x{next_b:02x} - likely GBK pair!')
