# -*- coding: utf-8 -*-
"""6C 破坏演练 8/8: 实例宕机闭环 (真实 docker stop, 高风险审批通道)。

环境: 临时起一次性 mysql:8.0 实例并纳管, 等常驻哨兵接管探活。
注入: docker stop — 真实进程级宕机
链路: 哨兵连续 3 次探活失败(约24s) → instance_down P1 事故 → 守护自动诊断
      → PB-DOWN-GUIDE 高风险需完整审批(不自动执行) → 审批通过 + 按指引
      人工恢复实例(docker start) → 执行确认步骤 → 验证 instance_up==1 → resolved
说明: 宕机类语义即"检测+指引+恢复验证", 恢复动作本身由人执行 (演练模拟)。

用法: python phase6/drills/e2e_down.py
"""
import subprocess
import sys
import threading
import time

from _drill_common import (  # noqa
    DatabaseConfig, timezone,
    assert_1_5_10, collect_once, pump_collect, run_playbook,
    wait_incident, wait_incident_status, wait_run_final,
)

VICTIM = 'db-aiops-mysql-down'
PORT = 3310
ROOT_PWD = 'root123'
SENTINEL_LOG = '/Users/mac/DB_Monitor/db-aiops/logs/sentinel.log'


def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, timeout=120)


def wait_mysql_ready(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = docker('exec', VICTIM, 'mysql', '-h127.0.0.1', '-uroot',
                   f'-p{ROOT_PWD}', '-N', '-e', 'SELECT 1')
        if r.returncode == 0:
            return True
        time.sleep(3)
    return False


def wait_sentinel_pickup(cfg_name, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(SENTINEL_LOG) as f:
                if f"哨兵启动: {cfg_name}" in f.read():
                    return True
        except FileNotFoundError:
            pass
        time.sleep(5)
    return False


def main():
    print("=== 演练8 实例宕机: 一次性实例真实 stop ===")
    cfg = None
    try:
        # ---- 环境: 一次性实例 + 纳管 ----
        docker('rm', '-f', VICTIM)
        r = docker('run', '-d', '--name', VICTIM, '-p', f'{PORT}:3306',
                   '-e', f'MYSQL_ROOT_PASSWORD={ROOT_PWD}', 'mysql:8.0')
        if r.returncode != 0:
            print(f"[FAIL] 容器启动失败: {r.stderr.decode()[:200]}")
            return 1
        if not wait_mysql_ready():
            print("[FAIL] 实例未就绪")
            return 1
        cfg = DatabaseConfig(name='演练-宕机实例', db_type='mysql',
                             host='127.0.0.1', port=PORT,
                             username='root', is_active=True)
        cfg.set_password(ROOT_PWD)
        cfg.save()
        print(f"已纳管一次性实例 config_id={cfg.id}, 等待常驻哨兵接管...")
        if not wait_sentinel_pickup(cfg.name):
            print("[FAIL] 哨兵 90s 内未接管 (start_sentinel 在跑吗?)")
            return 1

        # ---- 注入: 真实宕机 ----
        t0 = timezone.now()
        docker('stop', VICTIM)
        print("已注入: docker stop (哨兵约 3x8s 判 DOWN)")

        # ---- 发现 (哨兵连续失败) ----
        inc = wait_incident(cfg, 'availability', since=t0, timeout=120,
                            signal='instance_down')
        if not inc:
            print("[FAIL] 未生成宕机事故")
            return 1
        print(f"[发现] {inc.incident_id} [{inc.priority}] {inc.title} "
              f"t_detect={inc.t_detect_sec}s")

        # ---- 诊断 ----
        if not wait_incident_status(inc, ('plan_ready',), timeout=90, label='plan_ready'):
            return 1
        print(f"[诊断] t_plan={inc.t_plan_sec}s 方案={len(inc.plans)}")

        # ---- 处置: 高风险走完整审批, 人按指引恢复实例 ----
        from monitor.playbook_authz import decide_trigger
        from monitor.models import Playbook
        pb = Playbook.objects.get(playbook_id='PB-DOWN-GUIDE')
        decision = decide_trigger(inc, pb)
        print(f"  [授权] PB-DOWN-GUIDE: mode={decision['mode']} ({decision['reason']})")
        if decision['mode'] != 'approved' or decision['execute_now']:
            print("[FAIL] 高风险剧本必须走审批且不得立即执行")
            return 1

        print("  [人工] 审批通过, 按指引恢复实例 (docker start)...")
        docker('start', VICTIM)
        if not wait_mysql_ready():
            print("[FAIL] 实例重启失败")
            return 1
        run = run_playbook(inc, 'PB-DOWN-GUIDE', approved_by='drill-approver')
        if run.status == 'failed':
            print(f"[FAIL] 确认步骤失败: {run.error_message}")
            return 1

        # ---- 验证 instance_up==1 ----
        stop_pump = threading.Event()
        pump_collect(cfg, stop_pump, interval=10)
        try:
            final = wait_run_final(run, timeout=300)
            print(f"[验证] run={final}")
            wait_incident_status(inc, ('resolved',), timeout=30, label='resolved')
        finally:
            stop_pump.set()
            time.sleep(1)

        return 0 if assert_1_5_10(inc, run) else 1
    finally:
        if cfg and cfg.id:
            try:
                cfg.delete()
                print("(清理: 临时实例配置已删除)")
            except Exception as e:
                print(f"(清理配置失败: {e})")
        docker('rm', '-f', VICTIM)
        print("(清理: 一次性容器已删除)")


if __name__ == "__main__":
    sys.exit(main())
