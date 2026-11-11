# -*- coding: utf-8 -*-
"""manifest 自洽性独立复核（只读）+ 新增证据敏感扫描。

1) 目录实有文件数 vs manifest.file_count
2) files 列表逐份字节 vs 磁盘实测，合计 vs total_bytes
3) scan_scope 份数 vs 数据类文件实数
4) 新增 8 份证据的敏感串扫描（沿用 manifest.scan_list 词表）
"""
import json
import os
import re

D = r'C:\DB-AIOps\docs\pm\reports\evidence\DB-005R-m-team'
MP = os.path.join(D, 'manifest.json')
NEW_FILES = ['g11_bucket_verify_20261111.json', 'g11_collection_gap_20261111.json',
             'g11_drive_20261111.json', 'g11_rootcause_20261111.json',
             'reproduce_g11_gap_20261111.py', 'reproduce_g11_verify_20261111.py',
             'g11_recheck_20261111.json', 'g11_manifest_selfcheck_20261111.py']

m = json.loads(open(MP, encoding='utf-8').read())
files = m['files']
disk = {n: os.path.getsize(os.path.join(D, n)) for n in os.listdir(D)
        if os.path.isfile(os.path.join(D, n)) and n != 'manifest.json'}

listed = {f['file']: f for f in files}
diffs = []
for name, size in sorted(disk.items()):
    if name not in listed:
        diffs.append({'file': name, 'issue': 'on_disk_but_unlisted'})
        continue
    if listed[name]['bytes'] != size:
        diffs.append({'file': name, 'issue': 'bytes_mismatch',
                       'listed': listed[name]['bytes'], 'actual': size})
for name in listed:
    if name not in disk:
        diffs.append({'file': name, 'issue': 'listed_but_missing'})

data_files = [f for f in files
              if not f['file'].endswith('.py') and f['file'] != 'README-evidence.md']
scope_m = re.search(r'^(\d+) 份实测产物', m.get('scan_scope', ''))
scope_n = int(scope_m.group(1)) if scope_m else None

scan_list = m.get('scan_list', [])
hits = {}
for name in NEW_FILES:
    p = os.path.join(D, name)
    if not os.path.exists(p):
        hits[name] = ['<file missing>']
        continue
    text = open(p, encoding='utf-8').read()
    h = [w for w in scan_list if w in text]
    if h:
        hits[name] = h

out = {
    'dir_actual_files_excl_manifest': len(disk),
    'manifest_file_count': m['file_count'],
    'file_count_self_consistent': len(disk) + 1 == m['file_count'],
    'files_len': len(files),
    'total_bytes_listed': m['total_bytes'],
    'total_bytes_actual': sum(disk.values()),
    'total_bytes_self_consistent': m['total_bytes'] == sum(disk.values()),
    'per_file_diffs': diffs,
    'scan_scope_listed': scope_n,
    'data_files_actual': len(data_files),
    'scan_scope_self_consistent': scope_n == len(data_files),
    'new_evidence_scan_hits': hits,
    'new_evidence_scan_hits_total': sum(len(v) for v in hits.values()),
}
print(json.dumps(out, ensure_ascii=False, indent=2))
with open(r'C:\dispatcher\probe_out\g11_manifest_selfcheck_20261111.json', 'w', encoding='utf-8') as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
