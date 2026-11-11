# -*- coding: utf-8 -*-
"""DB-005R 证据脱敏器（入库副本用）：本地地址/端口/实例名 → 占位符。

按 DB-005R 证据清单既有口径：本地地址 <local-host>、端口按实例角色给占位符、
实例名按 cid 给占位符；口令/令牌本就未出现在证据中。

映射只按 **cid** 定义，不嵌入任何真实实例名或账号名；
原始未脱敏副本仅留存在 C:\dispatcher\probe_out\（不入库）。
"""
import json, os, shutil, sys

D = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = r'C:\dispatcher\probe_out'
INSTANCE = {19: '<instance-mysql>', 18: '<instance-proxy>', 1: '<instance-mysql-test>',
            10: '<instance-tdsql-sim>', 16: '<instance-tdsql>', 2: '<instance-pg>',
            3: '<instance-oracle>', 9: '<instance-dm>', 11: '<instance-gbase>',
            15: '<instance-gbase-probe>'}
PORT = {19: '<mysql-port>', 18: '<proxy-port>', 1: '<mysql-test-port>',
        10: '<tdsql-sim-port>', 16: '<tdsql-port>', 2: '<pg-port>', 3: '<oracle-port>',
        9: '<dm-port>', 11: '<gbase-port>', 15: '<gbase-port>'}
HOST = '127.0.0.1'
TARGETS = ['g11_recovery_probe_20261111.json',
           'g11_recovery_digest_probe_20261111.json',
           'g11_recovery_cadence_20261111.json']


def mask_obj(obj, cid=None):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ('cid', 'id') and isinstance(v, int):
                cid = v
            if k == 'host' and isinstance(v, str) and v == HOST:
                out[k] = '<local-host>'
            elif k == 'port' and cid in PORT:
                out[k] = PORT[cid]
            elif k == 'name' and cid in INSTANCE:
                out[k] = INSTANCE[cid]
            else:
                out[k] = mask_obj(v, cid)
        return out
    if isinstance(obj, list):
        return [mask_obj(x, cid) for x in obj]
    return obj


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    report = []
    for name in TARGETS:
        path = os.path.join(D, name)
        if not os.path.exists(path):
            report.append({'file': name, 'status': 'missing'})
            continue
        shutil.copy2(path, os.path.join(RAW_DIR, name.replace('.json', '_raw.json')))
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        masked = mask_obj(data)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(masked, fh, ensure_ascii=False, indent=1)
        raw_txt = open(os.path.join(RAW_DIR, name.replace('.json', '_raw.json')), encoding='utf-8').read()
        new_txt = open(path, encoding='utf-8').read()
        leaks = [tok for tok in (HOST, '13306', '15002', 'M-team probe-mysql-8.0',
                                 'M-team probe-tdsql-proxy', 'MySQL_测试库', 'TDSQL_模拟测试库',
                                 'probe-tdsql', 'probe-gbase', 'GBase8A_测试库', 'PostgreSQL_测试库',
                                 'Oracle_测试库', 'DM_测试库') if tok in new_txt]
        report.append({'file': name, 'status': 'masked', 'bytes': len(new_txt.encode('utf-8')),
                       'raw_kept_at': os.path.join(RAW_DIR, name.replace('.json', '_raw.json')),
                       'leak_tokens_found': leaks})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
