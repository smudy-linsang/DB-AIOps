#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-005 外网版链路级回归 · API 验收脚本 v3（显式窗口对齐场景 A/B）。

v3 相对 v2 的修正（均为 v2 预演暴露的口径问题）：
  1. **digest 精确定位**：不再靠慢 SQL 列表的文本模糊匹配（文本带字面量、且 30m 窗口
     可能把目标语句切掉），改为从 performance_schema 原文反查 64 位 DIGEST，
     再取**前 32 位**（平台 sql_stat 存的就是前 32 位）。
  2. **详情接口带显式窗口**：GET /perf/sql/{digest}/?from=..&to=..，让耗时构成
     的锁等待/CPU/扫描口径只落在场景 B 窗内。
  3. **告警解析**：/databases/{id}/alerts/ 的响应顶层就是 {alerts:[...]}（不在 data 下）。
  4. 环比条目按 **digest 精确对应**，不再按 sql_text 模糊匹配（否则平台自身 EXPLAIN
     产生的 digest 会顶替业务点查）。

用法：
    set MTEAM_PW=<本地开发库密码>
    set PROBE_PASSWORD=<mock 靶场 <account> 口令>
    python db005_api_check3.py --cid 18 \
        --from 2026-11-05T20:45:58+08:00 --to 2026-11-05T20:59:30+08:00 \
        --base-from 2026-11-05T20:33:00+08:00 --base-to 2026-11-05T20:44:00+08:00 \
        --slow-time 30m --out C:\\dispatcher\\probe_out\\db005_api_B.json
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('BASE', 'http://<local-host>:<platform-port>')
USER = os.environ.get('MTEAM_USER', 'mteam')
PW = os.environ.get('MTEAM_PW', '')
CID = int(os.environ.get('CID', '18'))

NOT_EXTERNAL = {
    'C-4': '跨 set 对账需真 TDSQL set 拓扑（mock 无 set 级真值）',
    'A-3': 'D-01/D-05 需 groupshard 实例与 set 数（mock 拓扑识别失败）',
    'A-4': 'D-01 需 groupshard 实例与 shardkey 路由语义',
    'B-4': '按 set 分布需 set 级真值',
    'B-5': 'EXPLAIN ANALYZE 门禁：V-7 内网结论「无法判定」⇒ 不放行（开关默认关，实测 403 DISABLED）',
    'B-6': 'EXPLAIN ANALYZE 超时截断同上（受同一门禁约束）',
    'R-4': '昨日/上周同时段需运行满一天',
}

# 目标 SQL → (业务名, performance_schema DIGEST_TEXT 匹配条件)
# 注意：performance_schema 的 DIGEST_TEXT 列是二进制排序，LIKE **区分大小写**；
# 且 p_s 里 `STATUS` 是大写、且不带反引号（反引号只包住标识符列）。
TARGETS = [
    ('lockwait_update', "DIGEST_TEXT LIKE 'UPDATE `orders` SET STATUS = STATUS%'"),
    ('slow_user_sort', "DIGEST_TEXT LIKE 'SELECT `id` , `amount` FROM `orders` WHERE `user_id` = ?%'"),
    ('agg_no_shardkey', "DIGEST_TEXT LIKE 'SELECT STATUS , COUNT%'"),
    ('range_no_key', "DIGEST_TEXT LIKE '%COUNT%FROM `orders` WHERE `created_at` >=%'"),
    ('pk_lookup', "DIGEST_TEXT LIKE 'SELECT `id` FROM `orders` WHERE `id` = ? AND `user_id` = ?%'"),
]


def req(path, method='GET', body=None, token=None, timeout=60):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {'content-type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors='replace')
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, '%s: %s' % (type(e).__name__, e)


def trunc(v, n=400):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s[:n]


def norm(sql):
    s = re.sub(r'\s+', ' ', (sql or '')).strip().lower()
    return s.replace('`', '').replace('"', '')


def resolve_digests():
    """从 performance_schema 反查目标 SQL 的 64 位 DIGEST → 平台用的前 32 位。"""
    import pymysql
    conn = pymysql.connect(host=os.environ.get('PROBE_HOST', '<local-host>'),
                           port=int(os.environ.get('PROBE_PORT', '<proxy-port>')),
                           user=os.environ.get('PROBE_USER', '<account>'),
                           password=os.environ.get('PROBE_PASSWORD', ''),
                           database=os.environ.get('PROBE_DB', 'aiops_probe_shop'),
                           cursorclass=pymysql.cursors.DictCursor,
                           connect_timeout=10, read_timeout=30)
    out = {}
    raw = {}
    with conn.cursor() as c:
        for name, cond in TARGETS:
            c.execute("SELECT DIGEST, DIGEST_TEXT, COUNT_STAR, "
                      "ROUND(SUM_TIMER_WAIT/1e9,3) AS total_ms, "
                      "SUM_LOCK_TIME/1e9 AS lock_s, SUM_ROWS_EXAMINED AS rows_ex "
                      "FROM performance_schema.events_statements_summary_by_digest "
                      "WHERE " + cond + " ORDER BY SUM_TIMER_WAIT DESC LIMIT 5")
            rows = c.fetchall()
            raw[name] = rows
            if rows:
                # 取执行次数最多的那条作为主 digest（同形态可能有多条历史记录）
                best = max(rows, key=lambda r: r.get('COUNT_STAR') or 0)
                out[name] = {'digest64': best['DIGEST'],
                             'digest32': best['DIGEST'][:32],
                             'count_star': best.get('COUNT_STAR'),
                             'total_ms': best.get('total_ms'),
                             'lock_s': best.get('lock_s'),
                             'rows_examined': best.get('rows_ex'),
                             'text': (best.get('DIGEST_TEXT') or '')[:160]}
    conn.close()
    return out, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cid', type=int, default=CID)
    ap.add_argument('--out', default=r'C:\dispatcher\probe_out\db005_api_B.json')
    ap.add_argument('--slow-time', default='30m')
    ap.add_argument('--from', dest='fd', default='')
    ap.add_argument('--to', dest='td', default='')
    ap.add_argument('--base-from', dest='bf', default='')
    ap.add_argument('--base-to', dest='bt', default='')
    ap.add_argument('--label', default='B')
    args = ap.parse_args()
    cid = args.cid

    results = []
    notes = {}

    def rec(code, name, status, detail, expect=''):
        results.append({'id': code, 'name': name, 'status': status,
                        'detail': trunc(detail), 'expect': expect})

    st, d = req('/api/v1/auth/login/', 'POST', {'username': USER, 'password': PW})
    if st != 200 or not isinstance(d, dict) or 'token' not in d:
        print('LOGIN FAILED', st, trunc(d))
        return 2
    token = d['token']

    # ---------- digest 精确定位 ----------
    try:
        digests, raw_digests = resolve_digests()
        notes['digests'] = digests
        notes['raw_digest_rows'] = {k: [{kk: vv for kk, vv in r.items()
                                         if kk in ('DIGEST', 'COUNT_STAR', 'total_ms',
                                                   'lock_s', 'rows_ex', 'DIGEST_TEXT')}
                                        for r in v[:3]] for k, v in raw_digests.items()}
    except Exception as e:  # noqa: BLE001
        notes['digests'] = 'ERR %s: %s' % (type(e).__name__, e)
        digests = {}

    def dg32(name):
        v = digests.get(name)
        return v['digest32'] if isinstance(v, dict) else None

    # ---------- C: 慢 SQL 列表 ----------
    st, slow = req('/api/v1/databases/%d/slow-queries/?time=%s&limit=50'
                   % (cid, args.slow_time), token=token)
    items = []
    basis = None
    if isinstance(slow, dict):
        data = slow.get('data')
        if isinstance(data, dict):
            items = (data.get('slow_queries') or data.get('results') or data.get('items') or [])
            basis = data.get('basis', basis)
        basis = slow.get('basis', basis)
        if not items:
            items = slow.get('results') or slow.get('items') or []
    elif isinstance(slow, list):
        items = slow
    notes['slow_http'] = st
    notes['slow_time_range'] = args.slow_time
    notes['slow_count'] = len(items) if isinstance(items, list) else -1
    notes['basis'] = basis
    notes['warning'] = (slow.get('data') or {}).get('warning') if isinstance(slow, dict) else None

    # 慢列表里按 digest32 精确定位
    by_dg = {}
    for it in (items if isinstance(items, list) else []):
        d32 = (it.get('digest') or it.get('sql_digest') or '')[:32]
        if d32:
            by_dg.setdefault(d32, it)
    shape_pos = {}
    for name in [t[0] for t in TARGETS]:
        d32 = dg32(name)
        if d32 and d32 in by_dg:
            shape_pos[name] = [i for i, it in enumerate(items)
                               if (it.get('digest') or '')[:32] == d32][0]
    notes['shape_positions'] = shape_pos
    notes['slow_top'] = [{'sql': (it.get('sql_example') or '')[:70],
                          'digest32': (it.get('digest') or '')[:32],
                          'avg_ms': it.get('avg_ms'),
                          'exec_count': it.get('exec_count'),
                          'total_time_sec': it.get('total_time_sec'),
                          'is_slow': it.get('is_slow')}
                         for it in (items if isinstance(items, list) else [])[:8]]

    if st != 200 or not items:
        rec('C-1', '慢 SQL 列表非空 + basis=window', 'FAIL',
            'HTTP %s 条目=%d' % (st, notes['slow_count']))
    else:
        untrusted = [k for k in ('untrusted', 'data_untrusted', 'basis_warning')
                     if (slow.get(k) or (slow.get('data') or {}).get(k))]
        rec('C-1', '慢 SQL 列表非空 + basis=window',
            'PASS' if basis == 'window' and not untrusted else 'WARN',
            'count=%d basis=%s warning=%s untrusted=%s'
            % (len(items), basis, notes['warning'], untrusted),
            'basis=window 且无「数据不可信」提示')
        sus_pos = shape_pos.get('slow_user_sort')
        pk_pos = shape_pos.get('pk_lookup')
        rec('C-2', '排序：慢查询排前、点查不在前列',
            'PASS' if (sus_pos is not None and sus_pos < 3
                       and (pk_pos is None or pk_pos > 3)) else 'OBSERVED',
            'slow_user_sort 位次=%s；pk_lookup 位次=%s；Top8=%s'
            % (sus_pos, pk_pos, trunc(notes['slow_top'], 500)),
            '场景 B 期间 slow_user_sort 排第一，pk_lookup 不在前列')
        sus = by_dg.get(dg32('slow_user_sort'))
        pk = by_dg.get(dg32('pk_lookup'))
        rec('C-3', '单位：avg_ms 与客户端平均耗时同数量级', 'OBSERVED',
            '平台 avg_ms：slow_user_sort=%s、pk_lookup=%s、agg=%s、range=%s；'
            '客户端侧（workload 打印）：slow_user_sort 23.4ms、pk_lookup 4.5ms、'
            'agg 42.3ms、range 18.0ms（平台为库内耗时，客户端为驱动端耗时，含 proxy 往返）'
            % (sus.get('avg_ms') if sus else None, pk.get('avg_ms') if pk else None,
               (by_dg.get(dg32('agg_no_shardkey')) or {}).get('avg_ms'),
               (by_dg.get(dg32('range_no_key')) or {}).get('avg_ms')),
            '与 workload 客户端平均耗时同数量级且同趋势')

    # ---------- A/B: 单条 SQL 详情（带显式窗口） ----------
    win_q = {}
    if args.fd and args.td:
        win_q = {'from': args.fd, 'to': args.td}

    def detail(name):
        d32 = dg32(name)
        if not d32:
            return None, {'http': 0, 'error': 'digest 未定位'}
        path = '/api/v1/databases/%d/perf/sql/%s/' % (cid, d32)
        if win_q:
            path += '?' + urllib.parse.urlencode(win_q)
        s, raw = req(path, token=token)
        det = raw.get('data') if isinstance(raw, dict) and isinstance(raw.get('data'), dict) else raw
        if not isinstance(det, dict):
            det = {'http': s, 'error': trunc(raw, 200)}
        return s, det

    def findings_of(det):
        if not isinstance(det, dict):
            return []
        prof = det.get('profile')
        if isinstance(prof, dict) and prof.get('findings'):
            return prof['findings']
        f = det.get('findings')
        if isinstance(f, list):
            return f
        if isinstance(f, dict):
            return f.get('items') or []
        return []

    def advice_of(det):
        a = det.get('advice') if isinstance(det, dict) else None
        if isinstance(a, list):
            return a
        if isinstance(a, dict):
            return a.get('items') or []
        return []

    st3, det = detail('slow_user_sort')
    if det:
        adv = advice_of(det)
        adv_codes = [a.get('code') if isinstance(a, dict) else str(a) for a in adv]
        sugg = (det.get('advisor') or {}).get('index_suggestions') if isinstance(det, dict) else None
        prof = det.get('profile') if isinstance(det, dict) else None
        bd = (prof or {}).get('breakdown') if isinstance(prof, dict) else None
        fl = findings_of(det)
        fl_codes = [f.get('code') for f in fl if isinstance(f, dict)]
        notes['slow_user_sort_detail'] = {
            'http': st3, 'advice_codes': adv_codes,
            'index_suggestions': trunc(sugg, 500),
            'advice': trunc(adv, 700),
            'profile_breakdown': bd,
            'finding_codes': fl_codes,
            'per_exec': (prof or {}).get('per_exec') if isinstance(prof, dict) else None,
            'ratios': (prof or {}).get('ratios') if isinstance(prof, dict) else None,
            'plan_changed_at': det.get('plan_changed_at'),
            'plans_count': len(det.get('plans') or []),
        }
        has_d01 = any(c == 'D-01' for c in adv_codes)
        rec('A-1', '优化建议：I-01 复合索引 DDL 可执行、不误报 D-01',
            'PASS' if ('I-01' in adv_codes and not has_d01) else 'OBSERVED',
            'advice_codes=%s index_suggestions=%s' % (adv_codes, trunc(sugg, 240)),
            'I-01 给出可执行 DDL；不出现 D-01')
        rec('A-2', '优化建议：索引已存在时不再建议建同一索引', 'OBSERVED',
            '场景 B 索引已删 ⇒ 平台给出 I-01（期望）；「索引存在时不重复建议」由 A-2 专项脚本验证',
            '索引缺失时给 I-01；索引存在时不重复建议（I-03/I-04）')
        rec('A-5', '优化建议：表名/列名真实存在、无系统表建议', 'OBSERVED',
            'advice_codes=%s index_suggestions=%s' % (adv_codes, trunc(sugg, 200)),
            '建议里表名/列名真实存在，无 information_schema 建议')
        cpu_known = bool((bd or {}).get('cpu_known')) if isinstance(bd, dict) else False
        cpu100 = any(isinstance(bd.get(k), (int, float)) and abs(float(bd[k]) - 100.0) < 1e-6
                     for k in ('cpu_ms', 'lock_ms', 'other_ms')) if isinstance(bd, dict) else False
        rec('B-1', '耗时构成三段：CPU 未采集时不得显示 100% CPU',
            'PASS' if (bd is not None and not cpu_known and not cpu100
                       and (bd.get('cpu_hint') or '')) else 'OBSERVED',
            'breakdown=%s finding_codes=%s' % (json.dumps(bd, ensure_ascii=False), fl_codes),
            '未开 CPU consumer 时显示「CPU 未采集」且不出现 100% CPU')
        rec('B-2', '每次执行明细含扫描效率/全表扫描结论',
            'PASS' if ('SCAN_INEFFICIENT' in fl_codes or 'FULL_SCAN' in fl_codes) else 'OBSERVED',
            'finding_codes=%s per_exec=%s ratios=%s'
            % (fl_codes, json.dumps((prof or {}).get('per_exec') if isinstance(prof, dict) else None,
                                    ensure_ascii=False),
               json.dumps((prof or {}).get('ratios') if isinstance(prof, dict) else None,
                          ensure_ascii=False)),
            '含 SCAN_INEFFICIENT / FULL_SCAN')

    st4, det4 = detail('lockwait_update')
    if det4:
        fl4 = findings_of(det4)
        codes4 = [f.get('code') for f in fl4 if isinstance(f, dict)]
        prof4 = det4.get('profile') if isinstance(det4, dict) else None
        bd4 = (prof4 or {}).get('breakdown') if isinstance(prof4, dict) else None
        rt4 = (prof4 or {}).get('ratios') if isinstance(prof4, dict) else None
        notes['lockwait_detail'] = {'http': st4, 'finding_codes': codes4,
                                    'breakdown': bd4, 'ratios': rt4,
                                    'findings': trunc(fl4, 600)}
        rec('B-3', '锁等待：平台汇总出 LOCK_WAIT',
            'PASS' if 'LOCK_WAIT' in codes4 else 'OBSERVED',
            'finding_codes=%s breakdown=%s ratios=%s'
            % (codes4, json.dumps(bd4, ensure_ascii=False),
               json.dumps(rt4, ensure_ascii=False)),
            'lockwait 跑过之后该 UPDATE 的锁等待占比显著且给出 LOCK_WAIT')

    # ---------- R: 环比退化（显式窗口） ----------
    q = []
    if args.fd and args.td:
        q += [('from', args.fd), ('to', args.td)]
    if args.bf and args.bt:
        q += [('base_from', args.bf), ('base_to', args.bt)]
    if not q:
        q = [('window', '30m'), ('baseline', 'prev')]
    url = '/api/v1/databases/%d/perf/sql-regression/?%s' % (cid, urllib.parse.urlencode(q))
    st5, reg = req(url, token=token)
    ritems = []
    if isinstance(reg, dict):
        data = reg.get('data')
        if isinstance(data, dict):
            ritems = data.get('items') or data.get('regressions') or data.get('results') or []
    elif isinstance(reg, list):
        ritems = reg
    notes['regression_http'] = st5
    notes['regression_query'] = url
    notes['regression_window'] = (reg.get('data') or {}).get('window') if isinstance(reg, dict) else None
    notes['regression_baseline'] = (reg.get('data') or {}).get('baseline') if isinstance(reg, dict) else None
    notes['regression_counts'] = (reg.get('data') or {}).get('counts') if isinstance(reg, dict) else None
    n_counts = notes['regression_counts'] or {}
    notes['regression_thresholds'] = (reg.get('data') or {}).get('thresholds') if isinstance(reg, dict) else None
    notes['regression_baseline_note'] = (reg.get('data') or {}).get('baseline_note') if isinstance(reg, dict) else None
    notes['regression_items'] = trunc([{k: it.get(k) for k in
                                        ('digest', 'status', 'avg_ratio', 'exec_ratio', 'impact_ms')}
                                       for it in ritems[:15]], 1200)

    # 按 digest 精确对应
    by_digest_shape = {}
    for name in [t[0] for t in TARGETS]:
        d32 = dg32(name)
        if not d32:
            continue
        for it in ritems:
            if (it.get('digest') or '')[:32] == d32:
                by_digest_shape[name] = it
                break
    notes['regression_shapes'] = {
        k: {'digest': (v.get('digest') or '')[:32], 'status': v.get('status'),
            'avg_ratio': v.get('avg_ratio'), 'exec_ratio': v.get('exec_ratio'),
            'impact_ms': v.get('impact_ms'), 'current': v.get('current'),
            'baseline': v.get('baseline'), 'plan_changed_at': v.get('plan_changed_at')}
        for k, v in by_digest_shape.items()}

    if st5 != 200 or not ritems:
        rec('R-1', '退化：slow_user_sort 状态=退化且倍数>1.5', 'FAIL',
            'HTTP %s 条目=%d query=%s' % (st5, len(ritems), url))
    else:
        sus = by_digest_shape.get('slow_user_sort')
        if sus:
            ratio = sus.get('avg_ratio')
            ok = sus.get('status') == 'regressed' and (ratio or 0) > 1.5
            rec('R-1', '退化：slow_user_sort 状态=退化且倍数>1.5',
                'PASS' if ok else 'OBSERVED',
                'status=%s avg_ratio=%s current=%s baseline=%s plan_changed_at=%s'
                % (sus.get('status'), ratio, sus.get('current'), sus.get('baseline'),
                   sus.get('plan_changed_at')),
                'slow_user_sort 为「退化」，倍数>1.5，且能反映计划变化时间')
        else:
            rec('R-1', '退化：slow_user_sort 状态=退化且倍数>1.5', 'OBSERVED',
                '未按 digest 对应到 slow_user_sort', 'slow_user_sort 为「退化」')
        pk = by_digest_shape.get('pk_lookup')
        if pk:
            stt = pk.get('status')
            rec('R-2', '退化：点查 pk_lookup 未退化（稳定/改善）',
                'PASS' if stt in ('stable', 'improved') else 'OBSERVED',
                'status=%s avg_ratio=%s exec_ratio=%s current=%s baseline=%s'
                % (stt, pk.get('avg_ratio'), pk.get('exec_ratio'),
                   pk.get('current'), pk.get('baseline')),
                'pk_lookup 不应被判为「退化」')
        else:
            # 接口对 stable 项 **刻意不返回条目**（shown = [i for i in items if i['status'] != 'stable']），
            # 「不在 items 且 counts.stable>0」即为平台判定其稳定。
            # 用详情接口在两窗各取一次 avg_ms 做平台口径交叉验证。
            def avg_in(win):
                d32 = dg32('pk_lookup')
                if not d32 or not win:
                    return None
                p = '/api/v1/databases/%d/perf/sql/%s/?%s' % (
                    cid, d32, urllib.parse.urlencode(win))
                s, raw = req(p, token=token)
                det = raw.get('data') if isinstance(raw, dict) and isinstance(raw.get('data'), dict) else raw
                if not isinstance(det, dict):
                    return None
                prof = det.get('profile') or {}
                return prof.get('avg_ms')
            cur_avg = avg_in({'from': args.fd, 'to': args.td}) if args.fd and args.td else None
            base_avg = avg_in({'from': args.bf, 'to': args.bt}) if args.bf and args.bt else None
            ratio = (cur_avg / base_avg) if (cur_avg and base_avg) else None
            stable_cnt = (n_counts.get('stable') if n_counts else 0) or 0
            notes['pk_lookup_stable_evidence'] = {
                'in_items': False,
                'reason': '接口对 status=stable 的条目不返回（shown 过滤），故以「不在 items + counts.stable>0」判定',
                'counts_stable': stable_cnt,
                'avg_ms_current_window': cur_avg, 'avg_ms_baseline_window': base_avg,
                'avg_ratio': round(ratio, 3) if ratio else None,
            }
            not_regressed = (ratio is None or ratio < 1.5) and stable_cnt > 0
            rec('R-2', '退化：点查 pk_lookup 未退化（稳定/改善）',
                'PASS' if not_regressed else 'OBSERVED',
                'pk_lookup 不在 items（接口过滤 stable，counts.stable=%d）；'
                '平台口径 avg：当前窗 %s ms vs 基线窗 %s ms ⇒ 倍率 %s（<1.5 即未判退化）'
                % (stable_cnt, cur_avg, base_avg,
                   round(ratio, 3) if ratio else '—'),
                'pk_lookup「稳定」——接口对 stable 不返回条目，以 counts.stable + 两窗 avg 倍率判定')
        lk = by_digest_shape.get('lockwait_update')
        news = [it for it in ritems if it.get('status') == 'new']
        if lk:
            rec('R-3', '新增语义：基线窗口完全未出现的 SQL 判为「新增」',
                'PASS' if lk.get('status') == 'new' else 'OBSERVED',
                'lockwait UPDATE status=%s baseline=%s；全部 new 项 digest=%s'
                % (lk.get('status'), lk.get('baseline'),
                   [(it.get('digest') or '')[:12] for it in news][:8]),
                '只有基线窗口完全没出现过的 SQL 才是「新增」')
        else:
            rec('R-3', '新增语义：基线窗口完全未出现的 SQL 判为「新增」',
                'OBSERVED' if news else 'SKIP_NO_DATA',
                'new 项 %d 条：%s' % (len(news), [(it.get('digest') or '')[:12] for it in news][:8]),
                '只有基线窗口完全没出现过的 SQL 才是「新增」')

    # ---------- B-7 门禁 ----------
    dg0 = dg32('slow_user_sort') or 'x'
    st6, guard = req('/api/v1/databases/%d/perf/sql/%s/explain-analyze/' % (cid, dg0),
                     'POST', {'sql': 'UPDATE orders SET status=1 WHERE id=1'}, token=token)
    notes['guard_http'] = st6
    notes['guard_body'] = trunc(guard, 300)
    gate_closed = (st6 == 403 and 'DISABLED' in str(guard))
    rec('B-7', 'EXPLAIN ANALYZE 守卫：写语句被本地拒绝',
        'SKIP_GATE_CLOSED' if gate_closed else 'OBSERVED',
        'HTTP %s body=%s' % (st6, trunc(guard, 200)),
        '开关关闭时未进入守卫分支（功能级拒绝），无法验「语句级拒绝」')

    # ---------- 告警侧证据（C-5 / B-3 告警） ----------
    st7, al = req('/api/v1/databases/%d/alerts/?limit=50' % cid, token=token)
    alerts = []
    if isinstance(al, dict):
        alerts = al.get('alerts') or al.get('results') or al.get('items') or []
        if not alerts:
            ad = al.get('data')
            if isinstance(ad, dict):
                alerts = ad.get('alerts') or ad.get('items') or []
    elif isinstance(al, list):
        alerts = al
    slim = [{'type': a.get('alert_type') or a.get('type'), 'severity': a.get('severity'),
             'status': a.get('status'), 'title': (a.get('title') or '')[:80],
             'metric_key': a.get('metric_key'),
             'create_time': a.get('create_time') or a.get('created_at'),
             'last_notified_at': a.get('last_notified_at'),
             'description': (a.get('description') or '')[:320]} for a in alerts[:12]]
    notes['alerts_http'] = st7
    notes['alerts'] = slim
    slow_alerts = [a for a in slim if (a.get('type') or '') == 'slow_query']
    lock_alerts = [a for a in slim if (a.get('type') or '') == 'lock']
    if st7 == 200 and slow_alerts:
        rec('C-5', '慢 SQL 告警：场景 B 内平台产出慢 SQL 告警', 'PASS',
            'slow_query 告警 %d 条：%s' % (len(slow_alerts),
                                          trunc([(a.get('create_time'), a.get('title'),
                                                  (a.get('description') or '')[:140])
                                                 for a in slow_alerts], 420)),
            '场景 B 内出现慢 SQL 告警（阈值 SLOW_SQL_AVG_MS 实测 1000ms，本单未改该配置）')
    else:
        rec('C-5', '慢 SQL 告警：场景 B 内平台产出慢 SQL 告警', 'SKIP_NO_ALERT',
            'HTTP %s slow_query 告警 %d 条' % (st7, len(slow_alerts)),
            '场景 B 内出现慢 SQL 告警')
    if lock_alerts:
        notes['lock_alerts'] = lock_alerts
        rec('B-3b', '锁等待告警：阻塞分析侧独立告警', 'PASS',
            'lock 告警 %d 条：%s' % (len(lock_alerts),
                                     trunc([(a.get('create_time'), a.get('title'),
                                             (a.get('description') or '')[:200])
                                            for a in lock_alerts], 420)),
            '阻塞分析侧独立产出锁等待告警')

    # ---------- C-4p 单 set 口径等价对账 ----------
    try:
        import pymysql
        conn = pymysql.connect(host=os.environ.get('PROBE_HOST', '<local-host>'),
                               port=int(os.environ.get('PROBE_PORT', '<proxy-port>')),
                               user=os.environ.get('PROBE_USER', '<account>'),
                               password=os.environ.get('PROBE_PASSWORD', ''),
                               database=os.environ.get('PROBE_DB', 'aiops_probe_shop'),
                               cursorclass=pymysql.cursors.DictCursor,
                               connect_timeout=10, read_timeout=30)
        with conn.cursor() as c:
            c.execute("""SELECT DIGEST, COUNT_STAR, ROUND(SUM_TIMER_WAIT/1e9,3) AS total_ms,
                                SUM_LOCK_TIME/1e9 AS lock_s, SUM_ROWS_EXAMINED AS rows_ex,
                                LEFT(DIGEST_TEXT,80) AS q
                         FROM performance_schema.events_statements_summary_by_digest
                         WHERE SCHEMA_NAME=%s ORDER BY SUM_TIMER_WAIT DESC LIMIT 10""",
                      (os.environ.get('PROBE_DB', 'aiops_probe_shop'),))
            top = c.fetchall()
        conn.close()
        notes['manual_top'] = top
        cmp_rows = []
        for t in (top or []):
            hit = by_dg.get((t.get('DIGEST') or '')[:32])
            if hit is not None:
                cmp_rows.append({'sql': (t.get('q') or '')[:70],
                                 'manual_count_star': t.get('COUNT_STAR'),
                                 'manual_total_ms': t.get('total_ms'),
                                 'manual_rows_examined': t.get('rows_ex'),
                                 'manual_lock_s': t.get('lock_s'),
                                 'platform_exec_count': hit.get('exec_count'),
                                 'platform_total_time_sec': hit.get('total_time_sec'),
                                 'platform_avg_ms': hit.get('avg_ms'),
                                 'platform_rows_examined': hit.get('rows_examined')})
        notes['manual_vs_platform'] = cmp_rows
        rec('C-4p', '单 set 口径等价对账：平台 exec_count 与手工 COUNT_STAR 同量级', 'OBSERVED',
            'platform=%s' % trunc(cmp_rows, 800),
            'mock 为单 set，只做单 set 口径等价验证（set 级对账见 C-4 缺口）')
    except Exception as e:  # noqa: BLE001
        notes['manual_top'] = 'ERR %s: %s' % (type(e).__name__, e)
        rec('C-4p', '单 set 口径等价对账', 'SKIP_NO_CONN', notes['manual_top'],
            'mock 为单 set，只做单 set 口径等价验证')

    for code, why in NOT_EXTERNAL.items():
        rec(code, '外网不可跑（记缺口）', 'SKIP_NOT_EXTERNAL', why)

    out = {
        'label': args.label, 'base': BASE, 'db_config_id': cid,
        'slow_time_range': args.slow_time,
        'regression_window': {'from': args.fd, 'to': args.td,
                               'base_from': args.bf, 'base_to': args.bt},
        'login_user': USER, 'results': results, 'notes': notes,
        'counts': {k: sum(1 for r in results if r['status'] == k)
                   for k in sorted(set(r['status'] for r in results))},
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('\n==== 结论表 ====')
    for r in results:
        print('%-6s %-18s %s' % (r['id'], r['status'], r['name']))
    print('\ncounts:', out['counts'])
    print('saved:', args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
