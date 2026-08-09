# -*- coding: utf-8 -*-
"""6A 演练: 实例宕机注入 (phase6/10 §11)。

用一个指向不可达地址的临时 config 模拟宕机, 让哨兵连续探活失败达阈值 → instance_down 事故。
(不停真实容器, 避免影响其他演练; 用不可达实例验证探活判 DOWN 逻辑。)

用法: python phase6/drills/inject_down.py
"""
import os
import sys
import time

PROJECT_ROOT = "/Users/mac/DB_Monitor/db-aiops"
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")
import django  # noqa
django.setup()

from django.conf import settings  # noqa
from monitor.models import DatabaseConfig, Incident  # noqa
from monitor.sentinel import InstanceSentinel  # noqa
from monitor.pipeline import process_pending_once  # noqa


def main():
    print("=== 实例宕机注入 (不可达实例) ===")
    # 临时不可达实例 (不写库, 仅内存对象)
    cfg = DatabaseConfig(
        id=99001, name='演练-不可达实例', db_type='mysql',
        host='10.255.255.1', port=3306, username='x', password='x', is_active=True)
    # get_password 需要真实解密; 用 monkeypatch 返回明文
    cfg.get_password = lambda: 'x'

    # 需要真实落库才能建 Event/Incident (config 外键)。这里插入一条临时 config。
    real = DatabaseConfig.objects.create(
        name='演练-不可达实例', db_type='mysql', host='10.255.255.1', port=3306,
        username='x', password='enc:invalid', is_active=False)  # noqa: secret 演练哨兵值
    real.get_password = lambda: 'x'
    try:
        s = InstanceSentinel(real)
        threshold = int(getattr(settings, 'SENTINEL_FAIL_THRESHOLD', 3))
        print(f"连续探活 {threshold} 次 (每次约 {settings.SENTINEL_CONNECT_TIMEOUT_SEC}s 超时)...")
        t0 = time.time()
        for i in range(threshold):
            s.probe()
            print(f"  探活 {i+1}: consecutive_fail={s.consecutive_fail}")
        counts = process_pending_once()
        print(f"pipeline: {counts}")

        inc = Incident.objects.filter(
            config_id=real.id, category='availability').order_by('-created_at').first()
        if not inc:
            print("[FAIL] 未生成宕机事故")
            return 1
        detect = inc.t_detect_sec
        elapsed = time.time() - t0
        print(f"[OK] 事故 {inc.incident_id} | {inc.priority} | {inc.title}")
        print(f"     探活耗时={elapsed:.1f}s t_detect_sec={detect}")
        ok = detect is not None and detect <= 60
        print(f"验收: MTTD<=60s => {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        Incident.objects.filter(config_id=real.id).delete()
        from monitor.models import Event
        Event.objects.filter(config_id=real.id).delete()
        real.delete()


if __name__ == "__main__":
    sys.exit(main())
