# -*- coding: utf-8 -*-
"""
管理命令: 启动银行业务模拟器。

用法:
  ./manage.py run_bank_simulator                    # 跑全部活跃库
  ./manage.py run_bank_simulator --db-ids 1,2,3     # 指定库
  ./manage.py run_bank_simulator --dry-run          # 仅建表+种子, 不启动调度
  ./manage.py run_bank_simulator --duration 3600    # 运行 N 秒后退出 (默认一直跑)
"""
import logging
import signal
import threading
import time

from django.core.management.base import BaseCommand

from monitor.bank_simulator.worker import BankWorker

logger = logging.getLogger('bank_simulator')


class Command(BaseCommand):
    help = '启动银行业务模拟器, 对纳管库持续注入业务负载。'

    def add_arguments(self, parser):
        parser.add_argument('--db-ids', type=str, default='',
                            help='逗号分隔的 DatabaseConfig.id, 空=全部活跃库')
        parser.add_argument('--dry-run', action='store_true',
                            help='仅建表+种子, 不启动调度循环')
        parser.add_argument('--duration', type=int, default=0,
                            help='运行 N 秒后退出; 0=一直跑 (默认)')

    def handle(self, *args, **opts):
        from monitor.models import DatabaseConfig

        if opts['db_ids']:
            ids = [int(x) for x in opts['db_ids'].split(',') if x.strip()]
            qs = DatabaseConfig.objects.filter(id__in=ids)
        else:
            qs = DatabaseConfig.objects.filter(is_active=True)

        configs = list(qs)
        if not configs:
            self.stderr.write(self.style.ERROR('没有可模拟的数据库配置'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'== 银行业务模拟器启动: 目标库 {len(configs)} 个, '
            f'dry_run={opts["dry_run"]}, duration={opts["duration"]}s =='))

        stop_event = threading.Event()

        def _on_signal(signum, frame):
            self.stdout.write(self.style.WARNING(f'收到信号 {signum}, 准备退出...'))
            stop_event.set()

        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

        workers = []
        threads = []
        for cfg in configs:
            w = BankWorker(cfg, stop_event, dry_run=opts['dry_run'])
            workers.append(w)
            t = threading.Thread(target=w.run, name=f'bsim-{cfg.id}', daemon=True)
            threads.append(t)
            t.start()
            self.stdout.write(f'  - 启动 worker: {cfg.name} ({cfg.db_type})')

        # 主线程: 等待 duration 或 stop_event
        start = time.time()
        try:
            while not stop_event.is_set():
                stop_event.wait(1)
                if opts['duration'] and (time.time() - start) >= opts['duration']:
                    self.stdout.write(self.style.SUCCESS(
                        f'已达 duration={opts["duration"]}s, 准备退出'))
                    stop_event.set()
                    break
        except KeyboardInterrupt:
            stop_event.set()

        # 等待 worker 退出 (最多 10s)
        deadline = time.time() + 10
        for t in threads:
            remaining = max(0.1, deadline - time.time())
            t.join(timeout=remaining)

        # 汇总
        total_ok = sum(w.stats['ok'] for w in workers)
        total_fail = sum(w.stats['fail'] for w in workers)
        total_ddl = sum(w.stats['ddl'] for w in workers)
        self.stdout.write(self.style.SUCCESS(
            f'== 模拟器退出: ok={total_ok} fail={total_fail} ddl={total_ddl} =='))
