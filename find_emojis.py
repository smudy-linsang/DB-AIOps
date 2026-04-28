#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re

with open('monitor/management/commands/start_monitor.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all emoji characters
emojis = re.findall(r'[\U0001F000-\U0001F9FF]', content)
print('Total emojis found:', len(emojis))
print('Emoji set:', set(emojis))

# Find lines with emojis
for i, line in enumerate(content.split('\n'), 1):
    if re.search(r'[\U0001F000-\U0001F9FF]', line):
        print('Line %d: %s' % (i, line.strip()[:80]))
