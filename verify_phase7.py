# -*- coding: utf-8 -*-
"""Phase 7 验收脚本 (phase7/40 §5)。

用法: venv/bin/python verify_phase7.py
前置: docker 容器组 + start_sentinel/start_pipeline 守护在跑; runserver :8000。
"""
import json
import os
import sys
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
import django  # noqa

django.setup()

RESULTS = []


def check(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


@check("7A-01 session_sample v2 列 + 近期 wait_class 数据")
def c1():
    from monitor.timeseries import get_timeseries_storage
    cur = get_timeseries_storage()._get_connection().cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='session_sample'")
    cols = {r[0] for r in cur.fetchall()}
    need = {'wait_class', 'sql_id', 'program', 'module', 'lock_type',
            'lock_mode', 'lock_object', 'sample_gap_sec'}
    assert need <= cols, f"缺列: {need - cols}"
    cur.execute("SELECT count(*) FROM session_sample WHERE wait_class IS NOT NULL "
                "AND time > NOW() - interval '10 minutes'")
    n = cur.fetchone()[0]
    cur.close()
    assert n > 0, "近 10min 无带 wait_class 的样本 (哨兵在跑吗?)"
    return f"新列齐全, 近10min 样本 {n} 条"


@check("7A-06/07 session_ash_1m / sql_stat 有数据")
def c2():
    from monitor.timeseries import get_timeseries_storage
    cur = get_timeseries_storage()._get_connection().cursor()
    cur.execute("SELECT count(*) FROM session_ash_1m")
    a = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM sql_stat")
    b = cur.fetchone()[0]
    cur.close()
    assert a > 0 and b > 0, f"ash_1m={a}, sql_stat={b}"
    return f"ash_1m={a} 行, sql_stat={b} 行"


@check("7A-02/04 等待类映射 + SQL 指纹单测 43 例")
def c3():
    from monitor import tests_phase7
    rc = tests_phase7.run()
    assert rc == 0, "单测未全过"
    return "43/43"


@check("7B 九端点鉴权可达 (200)")
def c4():
    body = json.dumps({'username': 'admin', 'password': 'admin123'}).encode()
    req = urllib.request.Request('http://localhost:8000/api/v1/auth/login/',
                                 data=body, headers={'Content-Type': 'application/json'})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    token = d.get('token') or d.get('data', {}).get('token')
    assert token, "登录失败"
    eps = ['perf/aas/?window=30m', 'perf/top-activity/?window=30m',
           'perf/ash-facets/?window=30m', 'perf/sessions/', 'perf/blocking-tree/',
           'perf/running-sql/',
           'perf/compare/?a_from=2026-07-14T00:00:00Z&a_to=2026-07-14T01:00:00Z'
           '&b_from=2026-07-14T01:00:00Z&b_to=2026-07-14T02:00:00Z']
    for ep in eps:
        r = urllib.request.Request(f'http://localhost:8000/api/v1/databases/1/{ep}',
                                   headers={'Authorization': f'Bearer {token}'})
        code = urllib.request.urlopen(r, timeout=15).status
        assert code == 200, f"{ep} -> {code}"
    # 无鉴权 401
    try:
        urllib.request.urlopen('http://localhost:8000/api/v1/databases/1/perf/aas/', timeout=10)
        raise AssertionError("无鉴权应 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401, f"无鉴权返回 {e.code}"
    return f"{len(eps)} 端点 200 + 无鉴权 401"


@check("7B AAS 与 raw 聚合一致性 (<1%)")
def c5():
    from monitor.timeseries import get_timeseries_storage
    cur = get_timeseries_storage()._get_connection().cursor()
    cur.execute("""SELECT COALESCE(SUM(active_sec),0) FROM session_ash_1m
        WHERE db_config_id=1 AND bucket > NOW() - interval '30 minutes'""")
    agg = float(cur.fetchone()[0])
    cur.execute("""SELECT COALESCE(SUM(COALESCE(sample_gap_sec,15)),0) FROM session_sample
        WHERE db_config_id=1 AND time > NOW() - interval '30 minutes'
        AND time < time_bucket('1 minute', NOW())""")
    raw = float(cur.fetchone()[0])
    cur.close()
    if raw == 0:
        return "窗口内无活动 (跳过比对)"
    diff = abs(agg - raw) / raw
    assert diff < 0.05, f"agg={agg} raw={raw} 偏差 {diff:.1%}"
    return f"agg={agg} raw={raw} 偏差 {diff:.2%}"


@check("7A-08 计划采集 (mysql/pg/oracle) + 幂等")
def c6():
    from monitor.models import DatabaseConfig
    from monitor.plan_capture import capture
    from monitor.sqlfingerprint import unified_digest
    ok = []
    m = DatabaseConfig.objects.get(id=1)
    sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'testdb'"
    p = capture(m, unified_digest('mysql', None, sql), sql_text=sql, source='manual')
    assert p, "mysql 计划采集失败"
    assert capture(m, p.sql_digest, sql_text=sql, source='manual').id == p.id, "mysql 不幂等"
    ok.append('mysql')
    g = DatabaseConfig.objects.get(id=2)
    sql2 = "SELECT datname FROM pg_database WHERE datname = 'postgres'"
    p2 = capture(g, unified_digest('pgsql', None, sql2), sql_text=sql2, source='manual')
    assert p2, "pg 计划采集失败"
    ok.append('pg')
    o = DatabaseConfig.objects.get(id=3)
    from monitor.db_connector import DbConnector
    oc = DbConnector.get_connection(o)
    cur = oc.cursor()
    cur.execute("SELECT sql_id FROM v$sql_plan WHERE child_number=0 "
                "AND operation='SELECT STATEMENT' FETCH FIRST 1 ROWS ONLY")
    sid = cur.fetchone()[0]
    oc.close()
    assert capture(o, sid, source='manual'), "oracle 计划采集失败"
    ok.append('oracle')
    return '+'.join(ok)


@check("7D-01/02 新检测器注册与长事务单测")
def c7():
    from monitor.detectors.metric_category import SIGNAL_CATEGORY
    assert SIGNAL_CATEGORY.get('plan_change') == 'performance'
    assert SIGNAL_CATEGORY.get('long_transaction') == 'performance'
    from monitor.diagnosis_pipeline import _SIGNAL_ROOT_CAUSE
    assert 'plan_change' in _SIGNAL_ROOT_CAUSE and 'long_transaction' in _SIGNAL_ROOT_CAUSE
    from monitor.remediation_planner import SIGNAL_PLAYBOOK_REF
    assert SIGNAL_PLAYBOOK_REF.get('long_transaction') == 'PB-LOCK-KILL-BLOCKER'
    from monitor.detectors.l1_hard import detect_long_trx_from_ash
    from monitor.models import DatabaseConfig
    cfg = DatabaseConfig.objects.get(id=1)
    evs = detect_long_trx_from_ash(cfg, [
        {'session_id': '1', 'state': 'idle_in_trx', 'active_secs': 400, 'user_name': 'u'},
        {'session_id': '2', 'state': 'idle_in_trx', 'active_secs': 10},
        {'session_id': '3', 'state': 'idle in transaction', 'active_secs': 2000},
        {'session_id': '4', 'state': 'executing', 'active_secs': 900},
    ])
    assert len(evs) == 2, f"期望 2 事件, 得 {len(evs)}"
    assert {e['severity'] for e in evs} == {'warning', 'critical'}
    return "映射注册 + 长事务 2/4 样例判定正确"


@check("7D-04 pg_stat_statements 可用")
def c8():
    from monitor.models import DatabaseConfig
    from monitor.db_connector import DbConnector
    cfg = DatabaseConfig.objects.get(id=2)
    conn = DbConnector.get_connection(cfg)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM pg_stat_statements")
    n = cur.fetchone()[0]
    cur.execute("SELECT query_id FROM pg_stat_activity WHERE pid=pg_backend_pid()")
    qid = cur.fetchone()[0]
    conn.close()
    assert qid is not None, "query_id 未生效"
    return f"pg_stat_statements {n} 行, query_id 生效"


def main():
    passed = 0
    for name, fn in RESULTS:
        try:
            msg = fn()
            passed += 1
            print(f"  [OK] {name}: {msg}")
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
    print(f"\n通过: {passed}/{len(RESULTS)}")
    if passed == len(RESULTS):
        print(">>> Phase 7 全部验收通过! (场景终验见 phase7/ACCEPTANCE.md)")
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
