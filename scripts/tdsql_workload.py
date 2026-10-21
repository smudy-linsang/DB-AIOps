# -*- coding: utf-8 -*-
"""TDSQL 内网端到端验证负载（TDSQL_INTRANET_TEST_GUIDE.md §4，对应设计文档 §8 V-10）。

只在独立的测试库里建表与写数据（默认库名 aiops_probe_shop），不碰任何业务库。
与 docs/review/tdsql/workload.sql 同一形态（40 万行订单、user_id 无索引），但：
  - 分布式（groupshard）实例用 shardkey=user_id 建分片表（官方语法），集中式建普通表；
  - 分批 INSERT 灌数，不依赖 INSERT ... SELECT 递归 CTE（分布式下跨 set 的 INSERT SELECT 受限）；
  - 提供持续负载与"加索引 / 删索引"开关，用来人为制造一次可预期的"环比退化"。

环境变量（写账号，与平台监控账号分开；只需对测试库有 DDL/DML 权限）：
  TDSQL_WL_HOST / TDSQL_WL_PORT（默认 3306）/ TDSQL_WL_USER / TDSQL_WL_PASSWORD
  TDSQL_WL_DB（默认 aiops_probe_shop）

子命令：
  load [--rows 400000] [--groupshard]   建库建表并灌数
  run  [--minutes 30] [--qps 5]         按固定配比持续执行查询（慢查询 / 不带分片键 / 点查 / 锁等待可选）
  index add|drop                        加 / 删 (user_id, created_at) 复合索引
  lockwait [--seconds 20]               制造一次行锁等待（两个连接，测试表内）
  batch [--minutes 10]                  "夜间报表"类查询（SQL 形态与 run 不同），配合 cron 每天固定时段跑，验证周期 SQL
  drop                                  删除测试库
"""
import argparse
import os
import random
import sys
import threading
import time

try:
    import pymysql
except ImportError:  # pragma: no cover
    sys.exit('需要 pymysql：请用项目虚拟环境运行（.venv/bin/python）')

DB = os.environ.get('TDSQL_WL_DB', 'aiops_probe_shop')
if not DB.replace('_', '').isalnum():
    sys.exit('TDSQL_WL_DB 只能包含字母、数字与下划线')


def connect(db=True):
    missing = [k for k in ('TDSQL_WL_HOST', 'TDSQL_WL_USER', 'TDSQL_WL_PASSWORD') if not os.environ.get(k)]
    if missing:
        sys.exit(f'缺少环境变量: {", ".join(missing)}')
    kw = dict(host=os.environ['TDSQL_WL_HOST'], port=int(os.environ.get('TDSQL_WL_PORT', '3306')),
              user=os.environ['TDSQL_WL_USER'], password=os.environ['TDSQL_WL_PASSWORD'],
              autocommit=True, charset='utf8mb4', connect_timeout=10)
    if db:
        kw['database'] = DB
    try:
        return pymysql.connect(**kw)
    except pymysql.err.MySQLError as e:
        sys.exit(f'连接失败（错误码 {e.args[0] if e.args else "?"}）：请检查 TDSQL_WL_* 环境变量')


def cmd_load(args):
    conn = connect(db=False)
    with conn.cursor() as c:
        c.execute(f'CREATE DATABASE IF NOT EXISTS `{DB}`')
        c.execute(f'USE `{DB}`')
        c.execute('DROP TABLE IF EXISTS orders')
        # 分片表：主键 / 唯一键必须包含 shardkey（官方约束）
        ddl = ('CREATE TABLE orders (id BIGINT NOT NULL AUTO_INCREMENT, user_id INT NOT NULL, '
               'status TINYINT, amount DECIMAL(10,2), created_at DATETIME, note VARCHAR(200), '
               + ('PRIMARY KEY (id, user_id))' if args.groupshard else 'PRIMARY KEY (id))'))
        if args.groupshard:
            ddl += ' shardkey=user_id'
        c.execute(ddl)
        batch, done, t0 = 1000, 0, time.time()
        while done < args.rows:
            n = min(batch, args.rows - done)
            vals = ','.join(
                f"({random.randint(0, 49999)},{random.randint(0, 4)},{random.random() * 1000:.2f},"
                f"NOW() - INTERVAL {random.randint(0, 86400 * 30)} SECOND,REPEAT('x',120))"
                for _ in range(n))
            c.execute('INSERT INTO orders (user_id,status,amount,created_at,note) VALUES ' + vals)
            done += n
            if done % 50000 == 0:
                print(f'  已写入 {done} 行（{time.time() - t0:.0f}s）')
        c.execute('ANALYZE TABLE orders')
    conn.close()
    print(f'完成：{DB}.orders {args.rows} 行（{"分片表 shardkey=user_id" if args.groupshard else "普通表"}）')


# 查询配比：名称 → (权重, SQL 生成函数)。名称会打印在汇总里，便于与平台列表对照
QUERIES = {
    'slow_user_sort':  (3, lambda: 'SELECT id, amount FROM orders WHERE user_id = %d '
                                   'ORDER BY created_at DESC LIMIT 10' % random.randint(0, 49999)),
    'agg_no_shardkey': (1, lambda: 'SELECT status, COUNT(*), SUM(amount) FROM orders GROUP BY status'),
    'range_no_key':    (1, lambda: "SELECT COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL %d HOUR"
                                   % random.randint(1, 48)),
    'pk_lookup':       (20, lambda: 'SELECT id FROM orders WHERE id = %d AND user_id = %d'
                                    % (random.randint(1, 400000), random.randint(0, 49999))),
}


def cmd_run(args):
    names = [k for k, (w, _) in QUERIES.items() for _ in range(w)]
    deadline = time.time() + args.minutes * 60
    stats = {k: [0, 0.0] for k in QUERIES}
    conn = connect()
    interval = 1.0 / max(args.qps, 0.1)
    last_print = time.time()
    with conn.cursor() as c:
        while time.time() < deadline:
            name = random.choice(names)
            t0 = time.perf_counter()
            c.execute(QUERIES[name][1]())
            c.fetchall()
            el = (time.perf_counter() - t0) * 1000
            stats[name][0] += 1
            stats[name][1] += el
            time.sleep(max(interval - el / 1000, 0))
            if time.time() - last_print > 60:
                last_print = time.time()
                print(time.strftime('%H:%M:%S'), {k: (n, round(t / n, 1) if n else 0) for k, (n, t) in stats.items()})
    conn.close()
    print('客户端侧汇总（次数, 平均 ms）：')
    for k, (n, t) in stats.items():
        print(f'  {k:<16} {n:>6}  {t / n if n else 0:8.1f}')


def cmd_index(args):
    conn = connect()
    with conn.cursor() as c:
        if args.action == 'add':
            c.execute('ALTER TABLE orders ADD INDEX idx_user_created (user_id, created_at)')
        else:
            c.execute('ALTER TABLE orders DROP INDEX idx_user_created')
    conn.close()
    print(f'索引 idx_user_created 已{"添加" if args.action == "add" else "删除"}')


def cmd_lockwait(args):
    """A 持有 id 最小那一行的锁 args.seconds 秒，B 去更新同一行 → B 产生锁等待。"""
    a, b = connect(), connect()
    with a.cursor() as ca:
        ca.execute('SELECT id, user_id FROM orders ORDER BY id LIMIT 1')
        rid, uid = ca.fetchone()
        a.autocommit(False)
        ca.execute('UPDATE orders SET status = status WHERE id = %s AND user_id = %s', (rid, uid))

        def waiter():
            with b.cursor() as cb:
                cb.execute('SET SESSION innodb_lock_wait_timeout = %s', (args.seconds + 10,))
                t0 = time.time()
                cb.execute('UPDATE orders SET status = status WHERE id = %s AND user_id = %s', (rid, uid))
                print(f'B 等锁 {time.time() - t0:.1f}s 后完成')

        th = threading.Thread(target=waiter)
        th.start()
        print(f'A 持锁 {args.seconds}s（此时到平台「会话 / 锁」页查看阻塞树）')
        time.sleep(args.seconds)
        a.rollback()
        th.join()
    a.close()
    b.close()


BATCH_SQL = ('SELECT user_id, COUNT(*) AS cnt, MAX(amount) AS mx FROM orders '
             'WHERE status IN (1, 2) GROUP BY user_id ORDER BY cnt DESC LIMIT 20')


def cmd_batch(args):
    deadline, n, total = time.time() + args.minutes * 60, 0, 0.0
    conn = connect()
    with conn.cursor() as c:
        while time.time() < deadline:
            t0 = time.perf_counter()
            c.execute(BATCH_SQL)
            c.fetchall()
            total += (time.perf_counter() - t0) * 1000
            n += 1
            time.sleep(2)
    conn.close()
    print(f'batch：{n} 次，客户端平均 {total / n if n else 0:.1f} ms')


def cmd_drop(_args):
    conn = connect(db=False)
    with conn.cursor() as c:
        c.execute(f'DROP DATABASE IF EXISTS `{DB}`')
    conn.close()
    print(f'已删除 {DB}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('load')
    p.add_argument('--rows', type=int, default=400000)
    p.add_argument('--groupshard', action='store_true', help='分布式实例：建 shardkey=user_id 分片表')
    p.set_defaults(fn=cmd_load)
    p = sub.add_parser('run')
    p.add_argument('--minutes', type=float, default=30)
    p.add_argument('--qps', type=float, default=5)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser('index')
    p.add_argument('action', choices=('add', 'drop'))
    p.set_defaults(fn=cmd_index)
    p = sub.add_parser('lockwait')
    p.add_argument('--seconds', type=int, default=20)
    p.set_defaults(fn=cmd_lockwait)
    p = sub.add_parser('batch')
    p.add_argument('--minutes', type=float, default=10)
    p.set_defaults(fn=cmd_batch)
    sub.add_parser('drop').set_defaults(fn=cmd_drop)
    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
