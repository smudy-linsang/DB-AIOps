# -*- coding: utf-8 -*-
"""6C 破坏性演练公共库。

约定 (与 e2e_lock 一致):
- 演练进程自驱采集周期 (start_monitor 守护未必在跑), 事件由常驻 start_pipeline
  守护消费 → 建事故 → 自动诊断; 验证回路同样由守护完成, 演练只轮询 DB 断言。
- 每个演练: 注入真实故障 → wait_incident → wait_plan → run_playbook →
  pump_collect 维持快照新鲜 → wait_resolved → assert_1_5_10。
"""
import os
import sys
import threading
import time

PROJECT_ROOT = "/Users/mac/DB_Monitor/db-aiops"
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")

# 项目无自动 dotenv 加载 (守护靠 shell source .env); 演练自己兜底,
# 否则 DB_MONITOR_SECRET_KEY 缺失导致实例密码解密失败。
with open(os.path.join(PROJECT_ROOT, '.env')) as _f:
    for _line in _f:
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())
# 演练只验证 Phase 6 链路, 跳过 Phase 2 重型分析加速采集
os.environ.setdefault("ENABLE_PHASE2_ENGINES", "False")
import django  # noqa

django.setup()

from django.utils import timezone  # noqa
from monitor.models import DatabaseConfig, Incident, Playbook, PlaybookRun  # noqa
from monitor.db_connector import DbConnector  # noqa

_collector_cmd = None


def get_collector():
    """进程内单例 Command (计数器增量缓存依赖同一实例跨周期)。"""
    global _collector_cmd
    if _collector_cmd is None:
        from monitor.management.commands.start_monitor import Command
        _collector_cmd = Command()
    return _collector_cmd


def collect_once(cfg):
    """驱动一轮真实采集: checker 采集 → MonitorLog → 6A 检测发事件。"""
    from django.db import close_old_connections
    close_old_connections()
    get_collector()._run_single_check(cfg)


def pump_collect(cfg, stop_evt, interval=10):
    """后台线程: 验证窗口期间保持采集快照新鲜。"""
    def _loop():
        while not stop_evt.is_set():
            try:
                collect_once(cfg)
            except Exception as e:
                print(f"  [pump] 采集失败: {e}")
            stop_evt.wait(interval)
    t = threading.Thread(target=_loop, daemon=True, name="drill-pump")
    t.start()
    return t


def close_stale_incidents(cfg, dedup_prefix):
    """关掉同 dedup 前缀的历史未决事故, 避免新事件被聚合进旧事故。"""
    n = Incident.objects.filter(
        config=cfg, dedup_key__startswith=dedup_prefix,
        status__in=['open', 'diagnosing', 'plan_ready', 'executing', 'verifying'],
    ).update(status='closed', closed_at=timezone.now())
    if n:
        print(f"  (清理 {n} 条历史未决事故 {dedup_prefix}*)")


def wait_incident(cfg, category, since, timeout=90, signal=None,
                  event_dedup_contains=None):
    """轮询等待事故生成 (由 start_pipeline 守护建)。返回 Incident 或 None。

    事故 dedup 是 {config_id}:{category} (同类并单), 对象级匹配用
    event_dedup_contains 过滤归属事件的分键 (如表空间名)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        qs = Incident.objects.filter(config=cfg, category=category, created_at__gte=since)
        for inc in qs.order_by('-created_at'):
            evs = inc.events.all()
            if signal:
                evs = evs.filter(signal=signal)
            if event_dedup_contains:
                evs = evs.filter(dedup_key__contains=event_dedup_contains)
            if not (signal or event_dedup_contains) or evs.exists():
                return inc
        time.sleep(2)
    return None


def wait_incident_status(inc, statuses, timeout=90, label=''):
    """轮询等待事故进入指定状态集合。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        inc.refresh_from_db()
        if inc.status in statuses:
            return True
        time.sleep(2)
    print(f"  [WAIT超时] {label or statuses}: 当前 status={inc.status}")
    return False


def run_playbook(inc, playbook_id, params=None, approved_by=''):
    """按授权决策触发 Playbook (对齐 API 路径): decide→建Run→execute。"""
    from monitor.playbook_authz import decide_trigger, record_auto_action
    from monitor.playbook_engine import execute_run
    pb = Playbook.objects.get(playbook_id=playbook_id)
    decision = decide_trigger(inc, pb)
    print(f"  [授权] {playbook_id}: mode={decision['mode']} ({decision['reason']})")
    run = PlaybookRun.objects.create(
        run_id=f"PBR-DRILL-{int(time.time() * 1000) % 10**10}",
        playbook=pb, incident=inc, params=params or {},
        trigger_mode=decision['mode'],
        approved_by=approved_by or ('' if decision['mode'] == 'auto' else 'drill-operator'),
        status='pending_approval')
    if decision['mode'] in ('auto', 'one_click'):
        record_auto_action(inc.config_id)
    res = execute_run(run.run_id)
    run.refresh_from_db()
    print(f"  [执行] run={run.status} steps={len(run.step_results)} → {res}")
    return run


def wait_run_final(run, timeout=240):
    """等待 PlaybookRun 到终态 (验证回路由守护完成)。"""
    final = ('succeeded', 'failed', 'rolled_back', 'timeout')
    deadline = time.time() + timeout
    while time.time() < deadline:
        run.refresh_from_db()
        if run.status in final:
            return run.status
        time.sleep(3)
    return run.status


def assert_1_5_10(inc, run=None, resolve_required=True):
    """1-5-10 SLA 断言, 打印结论, 返回 bool。"""
    inc.refresh_from_db()
    td, tp, tr = inc.t_detect_sec, inc.t_plan_sec, inc.t_resolve_sec
    ok_d = td is not None and td <= 60
    ok_p = tp is not None and tp <= 300
    ok_r = (not resolve_required) or (tr is not None and tr <= 600
                                      and inc.status == 'resolved')
    ok_run = run is None or run.status == 'succeeded'
    print("\n" + "=" * 56)
    print(f"1分钟发现: t_detect={td}s <=60 => {'PASS' if ok_d else 'FAIL'}")
    print(f"5分钟方案: t_plan={tp}s <=300 => {'PASS' if ok_p else 'FAIL'}")
    if resolve_required:
        print(f"10分钟解决: t_resolve={tr}s <=600 (status={inc.status}) "
              f"=> {'PASS' if ok_r else 'FAIL'}")
    if run is not None:
        print(f"Playbook: {run.playbook.playbook_id} run={run.status} "
              f"=> {'PASS' if ok_run else 'FAIL'}")
    verdict = ok_d and ok_p and ok_r and ok_run
    print(f"闭环结论: {'PASS' if verdict else 'FAIL'}")
    print("=" * 56)
    return verdict


def mysql_conn(cfg, db=None):
    """裸连接 (演练注入用)。"""
    c = DbConnector.get_connection(cfg)
    if db:
        cur = c.cursor()
        cur.execute(f"USE `{db}`")
        cur.close()
    return c
