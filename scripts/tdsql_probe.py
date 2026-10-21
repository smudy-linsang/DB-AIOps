# -*- coding: utf-8 -*-
"""TDSQL 内网验证探针 —— 一次跑完 TDSQL_SLOWSQL_DESIGN.md §8 的 V-1 ~ V-9。

平台的 TDSQL 慢 SQL 能力是按官方文档开发的，以下假设只能在真实 TDSQL 上确认：
透传语法、info 列、performance_schema 扩展列、只读事务与超时经 proxy 是否生效等。
本脚本逐项探测并输出结论表，把结论表（或 --json 输出的文件）发回即可。

安全性：
  - 只读。V-7 用"只读事务 + WHERE 1=0 的 UPDATE + ROLLBACK"验证，即使只读未生效也不会改动任何行；
    账号没有写权限时该项记为"无法判定"（这本身也是一道保护）。
  - V-8 只执行 SELECT SLEEP(3) 并设 500ms 超时，不扫描业务表。
  - 凭据只走环境变量（AGENTS.md 凭据通道约定）；输出中不打印主机、端口、账号、密码，
    节点地址默认打码（--show-addr 才显示）。

环境变量：
  TDSQL_PROBE_HOST / TDSQL_PROBE_PORT（默认 3306）/ TDSQL_PROBE_USER / TDSQL_PROBE_PASSWORD
  TDSQL_PROBE_DB（可选，默认不选库）
  账号需要 SELECT + PROCESS；V-7 想得到确定结论需对某张业务表有 UPDATE 权限（不会真改）。

用法（仓库根目录）：
  .venv/bin/python scripts/tdsql_probe.py                # 跑 V-1 ~ V-9
  .venv/bin/python scripts/tdsql_probe.py --only V-6,V-7
  .venv/bin/python scripts/tdsql_probe.py --json probe_result.json
"""
import argparse
import json
import os
import re
import sys
import time

try:
    import pymysql
    import pymysql.cursors
except ImportError:  # pragma: no cover
    sys.exit('需要 pymysql：请用项目虚拟环境运行（.venv/bin/python）')

PASS, FAIL, WARN, SKIP = 'PASS', 'FAIL', 'WARN', 'SKIP'
_SET_RE = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')
_IP_RE = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b')


class Probe:
    def __init__(self, show_addr=False):
        self.show_addr = show_addr
        self.results = []
        self.facts = {}
        self.conn = None

    # ── 连接 ──
    def connect(self):
        missing = [k for k in ('TDSQL_PROBE_HOST', 'TDSQL_PROBE_USER', 'TDSQL_PROBE_PASSWORD')
                   if not os.environ.get(k)]
        if missing:
            sys.exit(f'缺少环境变量: {", ".join(missing)}')
        kw = dict(host=os.environ['TDSQL_PROBE_HOST'],
                  port=int(os.environ.get('TDSQL_PROBE_PORT', '3306')),
                  user=os.environ['TDSQL_PROBE_USER'],
                  password=os.environ['TDSQL_PROBE_PASSWORD'],
                  connect_timeout=10, read_timeout=30, write_timeout=30,
                  autocommit=True, charset='utf8mb4',
                  cursorclass=pymysql.cursors.DictCursor)
        if os.environ.get('TDSQL_PROBE_DB'):
            kw['database'] = os.environ['TDSQL_PROBE_DB']
        try:
            self.conn = pymysql.connect(**kw)
        except pymysql.err.MySQLError as e:
            # 驱动报错会带上账号与主机，只输出错误码
            code = e.args[0] if e.args else '?'
            sys.exit(f'连接失败（错误码 {code}）：请检查 TDSQL_PROBE_* 环境变量与账号权限')

    def rows(self, sql):
        with self.conn.cursor() as c:
            c.execute(sql)
            return list(c.fetchall() or [])

    def mask(self, text):
        text = '' if text is None else str(text)
        return text if self.show_addr else _IP_RE.sub('<addr>', text)

    def record(self, vid, title, status, detail, evidence=None):
        self.results.append({'id': vid, 'title': title, 'status': status,
                             'detail': self.mask(detail),
                             'evidence': json.loads(self.mask(json.dumps(evidence, ensure_ascii=False,
                                                                          default=str)))
                             if evidence is not None else None})

    # ── 前置：拓扑 ──
    def topology(self):
        info = {'mode': None, 'sets': [], 'rw_split': None, 'proxy_version': None, 'masters': {}}
        try:
            cfg = {r.get('config_name'): r.get('value') for r in self.rows('/*proxy*/show config')}
            mode = (cfg.get('mode') or '').strip()
            info['mode'] = {'no shard': 'noshard', 'group shard': 'groupshard'}.get(mode, mode or None)
            info['rw_split'] = cfg.get('rw_split')
            info['proxy_version'] = cfg.get('version')
        except Exception as e:
            info['config_error'] = str(e)[:200]
        try:
            for r in self.rows('/*proxy*/show routes'):
                name = r.get('set_name') or r.get('cluster_name')
                if name:
                    info['sets'].append(name)
                    info['masters'][name] = str(r.get('master_ip') or '')
        except Exception as e:
            info['routes_error'] = str(e)[:200]
        self.facts['topology'] = info
        return info

    def one_set(self):
        sets = [s for s in self.facts.get('topology', {}).get('sets', []) if _SET_RE.match(str(s))]
        return sets[0] if sets else None

    # ── V-1 ~ V-9 ──
    def v1(self):
        t = 'allsets 透传可用且带 info 列'
        sets = self.facts['topology']['sets']
        try:
            rs = self.rows('/*sets:allsets*/SELECT 1 AS x')
        except Exception as e:
            return self.record('V-1', t, FAIL, f'透传报错：{e}')
        infos = sorted({str(r.get('info')) for r in rs if r.get('info') is not None})
        if not infos:
            return self.record('V-1', t, FAIL, f'返回 {len(rs)} 行但没有 info 列；列={list(rs[0]) if rs else []}',
                               {'rows': rs[:5]})
        ok = not sets or set(infos) == {str(s) for s in sets}
        self.record('V-1', t, PASS if ok else WARN,
                    f'返回 {len(rs)} 行，info={infos}；show routes 的 set={sets}'
                    + ('' if ok else '（不一致：平台按 info 列区分 set，请把本行发回）'))

    def v2(self):
        t = '透传查询 performance_schema digest 表'
        try:
            rs = self.rows('/*sets:allsets*/SELECT COUNT(*) AS n FROM '
                           'performance_schema.events_statements_summary_by_digest')
        except Exception as e:
            return self.record('V-2', t, FAIL, f'报错：{e}（需要 performance_schema=ON 且账号有 SELECT 权限）')
        if not any(r.get('info') is not None for r in rs):
            return self.record('V-2', t, FAIL, f'可查询但结果无 info 列（未经 TDSQL proxy 透传？）：{rs[:3]}')
        per = {str(r.get('info')): r.get('n') for r in rs}
        zero = [k for k, v in per.items() if not v]
        self.record('V-2', t, WARN if zero else PASS,
                    f'各 set digest 条数={per}' + (f'；{zero} 为 0，可能未开 performance_schema 或刚重启' if zero else ''))

    def v3(self):
        t = 'digest 扩展列（SUM_CPU_TIME / QUERY_SAMPLE_TEXT）与 CPU consumer'
        cols = {}
        for col in ('SUM_CPU_TIME', 'QUERY_SAMPLE_TEXT', 'SUM_LOCK_TIME', 'SUM_SORT_MERGE_PASSES',
                    'SUM_CREATED_TMP_DISK_TABLES', 'FIRST_SEEN', 'LAST_SEEN'):
            try:
                self.rows(f'/*sets:allsets*/SELECT {col} FROM '
                          'performance_schema.events_statements_summary_by_digest LIMIT 1')
                cols[col] = True
            except Exception:
                cols[col] = False
        consumer = {}
        try:
            for r in self.rows("/*sets:allsets*/SELECT NAME, ENABLED FROM performance_schema.setup_consumers "
                               "WHERE NAME = 'events_statements_cpu'"):
                consumer[str(r.get('info'))] = r.get('ENABLED')
        except Exception as e:
            consumer = {'error': str(e)[:120]}
        missing = [c for c, ok in cols.items() if not ok]
        cpu_off = any(v != 'YES' for v in consumer.values())
        if missing:
            status, detail = WARN, f'缺列 {missing}：平台会自动降级为基础列（无 CPU / 无字面量样例）'
        elif cpu_off:
            status, detail = WARN, ('扩展列齐全，但 events_statements_cpu consumer 未全部开启：'
                                    '「耗时构成」中 CPU 会显示"未采集"。开启方法见内网测试指南 §2.2')
        else:
            status, detail = PASS, '扩展列齐全，CPU consumer 已开启'
        self.record('V-3', t, status, detail, {'columns': cols, 'events_statements_cpu': consumer})

    def v4(self):
        t = '透传 SHOW GLOBAL STATUS'
        try:
            rs = self.rows("/*sets:allsets*/SHOW GLOBAL STATUS WHERE Variable_name IN ('Questions','Uptime')")
        except Exception as e:
            return self.record('V-4', t, FAIL, f'报错：{e}（QPS/TPS 将无法按 set 求和，平台会标注口径）')
        sets = sorted({str(r.get('info')) for r in rs if r.get('info') is not None})
        self.record('V-4', t, PASS if sets else FAIL,
                    f'返回 {len(rs)} 行，覆盖 set={sets}' if sets else f'无 info 列：{rs[:4]}')

    def v5(self):
        t = '指定单个 set 透传'
        s = self.one_set()
        if not s:
            return self.record('V-5', t, SKIP, 'show routes 未取到 set 名')
        try:
            rs = self.rows(f'/*sets:{s}*/SELECT @@hostname AS h, @@port AS p, @@read_only AS ro')
        except Exception as e:
            return self.record('V-5', t, FAIL, f'/*sets:{s}*/ 报错：{e}')
        self.record('V-5', t, PASS if rs else FAIL, f'{s} → {len(rs)} 行', {'rows': rs})

    def v6(self):
        t = '单 set 透传 EXPLAIN ANALYZE（TREE 计划）'
        s = self.one_set()
        prefix = f'/*sets:{s}*/' if s else ''
        sql = ('EXPLAIN ANALYZE SELECT /*+ MAX_EXECUTION_TIME(2000) */ COUNT(*) '
               'FROM information_schema.SCHEMATA')
        try:
            rs = self.rows(prefix + sql)
        except Exception as e:
            return self.record('V-6', t, FAIL, f'报错：{e}（平台的「执行 EXPLAIN ANALYZE」按钮将不可用）')
        text = ' '.join(str(v) for r in rs for k, v in r.items() if str(k).lower() != 'info')
        ok = '->' in text and 'actual time=' in text
        self.record('V-6', t, PASS if ok else FAIL,
                    ('返回 TREE 计划' if ok else '返回了内容但不是 TREE 格式（含 "-> ... actual time="）')
                    + f'；前 160 字符：{text[:160]}')

    def _writable_table(self):
        """找一张非系统库的表，用于 V-7 的 WHERE 1=0 写尝试。"""
        rs = self.rows("SELECT TABLE_SCHEMA AS s, TABLE_NAME AS t FROM information_schema.TABLES "
                       "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA NOT IN "
                       "('mysql','sys','information_schema','performance_schema','sysdb','tdsqlpcloud_monitor') "
                       "LIMIT 1")
        return (rs[0]['s'], rs[0]['t']) if rs else None

    def v7(self):
        t = '只读事务经 proxy 生效（EXPLAIN ANALYZE 的写保护）'
        s = self.one_set()
        prefix = f'/*sets:{s}*/' if s else ''
        tbl = None
        try:
            tbl = self._writable_table()
        except Exception as e:
            return self.record('V-7', t, SKIP, f'无法列出表：{e}')
        if not tbl:
            return self.record('V-7', t, SKIP, '没有业务表可用于验证')
        col_rows = self.rows("SELECT COLUMN_NAME AS c FROM information_schema.COLUMNS "
                             f"WHERE TABLE_SCHEMA = '{tbl[0]}' AND TABLE_NAME = '{tbl[1]}' "
                             "ORDER BY ORDINAL_POSITION LIMIT 1")
        col = col_rows[0]['c']
        # WHERE 1=0：即使只读没生效也不会改任何行；随后 ROLLBACK
        upd = f'{prefix}UPDATE `{tbl[0]}`.`{tbl[1]}` SET `{col}` = `{col}` WHERE 1 = 0'
        outcome = None
        try:
            with self.conn.cursor() as c:
                c.execute(prefix + 'START TRANSACTION READ ONLY')
                try:
                    c.execute(upd)
                    outcome = ('written', None)
                except pymysql.err.MySQLError as e:
                    outcome = ('error', e.args[0] if e.args else None, str(e)[:160])
                finally:
                    c.execute(prefix + 'ROLLBACK')
        except Exception as e:
            return self.record('V-7', t, FAIL, f'只读事务本身报错：{e}')
        if outcome[0] == 'error' and outcome[1] == 1792:
            self.record('V-7', t, PASS, '透传只读事务内的写被拒绝（1792）')
        elif outcome[0] == 'error' and outcome[1] in (1142, 1143, 1044, 1045):
            self.record('V-7', t, WARN, f'账号无写权限（{outcome[1]}），无法判定只读事务是否生效；'
                                        '平台 EXPLAIN ANALYZE 账号同样无写权限时风险已被权限兜住')
        elif outcome[0] == 'written':
            self.record('V-7', t, FAIL, '只读事务内的 UPDATE 未被拒绝（WHERE 1=0，未改动任何行）：'
                                        '请勿在该环境开启 PERF_EXPLAIN_ANALYZE_ENABLED，并把本行发回')
        else:
            self.record('V-7', t, WARN, f'非预期错误：{outcome[1:]}')

    def v8(self):
        t = 'MAX_EXECUTION_TIME 经 proxy 生效'
        s = self.one_set()
        prefix = f'/*sets:{s}*/' if s else ''
        t0 = time.monotonic()
        try:
            rs = self.rows(prefix + 'SELECT /*+ MAX_EXECUTION_TIME(500) */ SLEEP(3) AS r')
            err = None
        except pymysql.err.MySQLError as e:
            rs, err = [], str(e)[:160]
        el = time.monotonic() - t0
        if el < 1.5:
            self.record('V-8', t, PASS, f'约 {el:.2f}s 返回（期望 ≈0.5s）'
                        + (f'；返回 {rs[:1]}' if rs else f'；错误 {err}'))
        else:
            self.record('V-8', t, FAIL, f'{el:.2f}s 才返回：hint 未生效，EXPLAIN ANALYZE 只能靠 '
                                        'statement_timeout / read_timeout 兜底')

    def v9(self):
        t = '读写分离时透传是否落主'
        topo = self.facts['topology']
        s = self.one_set()
        if not s:
            return self.record('V-9', t, SKIP, 'show routes 未取到 set 名')
        try:
            rs = self.rows(f'/*sets:{s}*/SELECT @@read_only AS ro, @@super_read_only AS sro, '
                           '@@hostname AS h, @@port AS p')
        except Exception as e:
            return self.record('V-9', t, FAIL, f'报错：{e}')
        r = rs[0] if rs else {}
        on_master = str(r.get('ro')) == '0'
        self.record('V-9', t, PASS if on_master else WARN,
                    f'rw_split={topo.get("rw_split")}；落到的节点 read_only={r.get("ro")}'
                    + ('（主）' if on_master else '（疑似备机：采集到的 digest 只代表该备机，请把本行发回）'),
                    {'node': r, 'route_master': topo['masters'].get(s)})

    CHECKS = ('V-1', 'V-2', 'V-3', 'V-4', 'V-5', 'V-6', 'V-7', 'V-8', 'V-9')

    def run(self, only=None):
        self.connect()
        topo = self.topology()
        for vid in self.CHECKS:
            if only and vid not in only:
                continue
            fn = getattr(self, vid.lower().replace('-', ''))
            try:
                fn()
            except Exception as e:  # 单项失败不影响其他项
                self.record(vid, fn.__name__, FAIL, f'探针异常：{type(e).__name__}: {e}')
        try:
            self.conn.close()
        except Exception as e:
            print(f'[warn] 关闭连接失败：{e}', file=sys.stderr)
        return topo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--only', help='只跑指定项，逗号分隔，如 V-1,V-7')
    ap.add_argument('--json', help='把结论写入该 JSON 文件（便于发回）')
    ap.add_argument('--show-addr', action='store_true', help='输出中显示节点地址（默认打码）')
    args = ap.parse_args()
    only = {x.strip().upper() for x in args.only.split(',')} if args.only else None

    p = Probe(show_addr=args.show_addr)
    topo = p.run(only)
    print(f"模式={topo.get('mode')}  set={topo.get('sets')}  rw_split={topo.get('rw_split')}  "
          f"proxy={topo.get('proxy_version')}")
    for k in ('config_error', 'routes_error'):
        if topo.get(k):
            print(f'[warn] {k}: {p.mask(topo[k])}')
    print()
    for r in p.results:
        print(f"{r['id']:<4} {r['status']:<4} {r['title']}")
        print(f"          {r['detail']}")
    counts = {s: sum(1 for r in p.results if r['status'] == s) for s in (PASS, WARN, FAIL, SKIP)}
    print(f'\n合计：{counts}')
    if args.json:
        out = {'topology': {k: v for k, v in topo.items() if k != 'masters'},
               'results': p.results, 'counts': counts,
               'probed_at': time.strftime('%Y-%m-%d %H:%M:%S')}
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(json.loads(p.mask(json.dumps(out, ensure_ascii=False, default=str))),
                      f, ensure_ascii=False, indent=2)
        print(f'已写入 {args.json}')
    sys.exit(1 if counts[FAIL] else 0)


if __name__ == '__main__':
    main()
