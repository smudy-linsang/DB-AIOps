# -*- coding: utf-8 -*-
"""6C 破坏演练 5/8: 配置漂移全自动闭环 (真实改参数)。

注入: 容器 root 直接 SET GLOBAL sync_binlog=0 (模拟未走变更流程的私改)
链路: 采集 config_params → 对比 Redis 参数基线快照 → config_drift 事件(按参数分键)
      → 事故 → 守护自动诊断 → PB-CONFIG-ROLLBACK 回滚 → live_config 实时验证
      参数==原值 → resolved
环境准备(幂等): 处置账号授 SYSTEM_VARIABLES_ADMIN (SET GLOBAL 回滚权限)。

用法: python phase6/drills/e2e_config.py [config_id=1]
"""
import subprocess
import sys
import time

from _drill_common import (  # noqa
    DatabaseConfig, timezone,
    assert_1_5_10, close_stale_incidents, collect_once,
    run_playbook, wait_incident, wait_incident_status, wait_run_final,
)

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
CONTAINER = 'db-aiops-mysql'
PARAM = 'sync_binlog'
DRIFT_VALUE = '0'


def root_sql(sql):
    r = subprocess.run(
        ['docker', 'exec', CONTAINER, 'mysql', '-uroot', '-proot123', '-N', '-e', sql],
        capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[:200])
    return r.stdout.decode().strip()


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    print(f"=== 演练5 配置漂移: {cfg.name} ({cfg.db_type}) ===")

    # ---- 环境准备: 处置账号可 SET GLOBAL ----
    root_sql(f"GRANT SYSTEM_VARIABLES_ADMIN ON *.* TO '{cfg.username}'@'%';")

    base_value = root_sql(f"SELECT @@GLOBAL.{PARAM}")
    print(f"基线值: {PARAM}={base_value}")
    close_stale_incidents(cfg, f'{cfg.id}:config')

    try:
        # ---- 建立参数基线快照 (首采种快照, 已有则跳过) ----
        collect_once(cfg)

        # ---- 注入: root 私改参数 ----
        t0 = timezone.now()
        root_sql(f"SET GLOBAL {PARAM}={DRIFT_VALUE}")
        print(f"已注入: SET GLOBAL {PARAM}={DRIFT_VALUE}")
        collect_once(cfg)

        # ---- 发现 ----
        inc = wait_incident(cfg, 'config', since=t0, timeout=60,
                            signal='config_drift', event_dedup_contains=PARAM)
        if not inc:
            print("[FAIL] 未生成配置漂移事故")
            return 1
        evt = inc.events.filter(signal='config_drift',
                                dedup_key__contains=PARAM).first()
        print(f"[发现] {inc.incident_id} [{inc.priority}] {inc.title} "
              f"t_detect={inc.t_detect_sec}s 证据={evt.detail}")

        # ---- 诊断 ----
        if not wait_incident_status(inc, ('plan_ready',), timeout=90, label='plan_ready'):
            return 1
        print(f"[诊断] t_plan={inc.t_plan_sec}s 方案={len(inc.plans)}")

        # ---- 处置: 回滚参数 (参数取自事件证据链) ----
        run = run_playbook(inc, 'PB-CONFIG-ROLLBACK', params={
            'param': evt.detail['param'],
            'old_value': evt.detail['old_value'],
            'new_value': evt.detail['new_value']})
        if run.status == 'failed':
            print(f"[FAIL] Playbook 执行失败: {run.error_message}")
            return 1

        # ---- 验证 (live_config 实时读参数, 无需泵采集) ----
        final = wait_run_final(run, timeout=180)
        print(f"[验证] run={final} 当前值: {PARAM}={root_sql(f'SELECT @@GLOBAL.{PARAM}')}")
        wait_incident_status(inc, ('resolved',), timeout=30, label='resolved')

        return 0 if assert_1_5_10(inc, run) else 1
    finally:
        try:
            root_sql(f"SET GLOBAL {PARAM}={base_value}")
        except Exception:
            pass
        print(f"(清理: {PARAM} 已还原为 {base_value})")


if __name__ == "__main__":
    sys.exit(main())
