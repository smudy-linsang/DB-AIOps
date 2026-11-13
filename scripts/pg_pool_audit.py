#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DB-026 连接池巡检脚本（只读，不做任何变更）。

用途：DB-026 任务单第 3 项「D2 之前只监不擅动」——每日巡检时序库连接池
占用（ESTABLISHED 数、占用方 PID），发现占满只报黑板，不重启任何进程。

安全边界：
  * 只读：全部查询为 SELECT / SHOW，不写库、不改配置、不重启容器与进程。
  * 不含任何凭据：连接一律走 `docker exec <容器> psql`（容器内本地 socket），
    不读取 .env、不落库密码；输出只含连接计数与进程名，不含口令。

用法：
    python scripts/pg_pool_audit.py [--container db-aiops-timescaledb] [--json out.json]
退出码：
    0 = 正常（占用低于阈值）
    3 = 超阈值（连接池占用 >= 阈值），仅告警，不做任何处置
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime

DEFAULT_CONTAINER = "db-aiops-timescaledb"
DEFAULT_THRESHOLD = 80  # 占用率百分比，达到即告警


def run_psql(container, sql):
    """在容器内执行只读 SQL，返回制表符分隔的原始文本。"""
    proc = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "postgres", "-d", "db_monitor",
         "-t", "-A", "-F", "\t", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError("psql 失败: %s" % (proc.stderr or "").strip())
    return [line for line in proc.stdout.splitlines() if line.strip()]


def scalar(container, sql):
    rows = run_psql(container, sql)
    if not rows:
        return None
    return rows[0].split("\t")[0]


def collect(container):
    total = int(scalar(container, "select count(*) from pg_stat_activity;") or 0)
    max_conn = int(scalar(container, "show max_connections;") or 0)
    reserved = int(scalar(container, "show superuser_reserved_connections;") or 0)

    by_state = {}
    for row in run_psql(
        container,
        "select coalesce(state,'(none)'), count(*) from pg_stat_activity "
        "where backend_type='client backend' group by 1 order by 2 desc;"):
        state, cnt = row.split("\t")
        by_state[state] = int(cnt)

    by_app = {}
    for row in run_psql(
        container,
        "select coalesce(nullif(application_name,''),'(none)'), count(*) "
        "from pg_stat_activity where backend_type='client backend' group by 1 order by 2 desc;"):
        app, cnt = row.split("\t")
        by_app[app] = int(cnt)

    oldest = scalar(
        container,
        "select coalesce(to_char(min(backend_start),'YYYY-MM-DD HH24:MI:SS'),'(none)') "
        "from pg_stat_activity where backend_type='client backend';")

    return {
        "container": container,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "max_connections": max_conn,
        "superuser_reserved": reserved,
        "total_backends": total,
        "client_backends": sum(by_state.values()),
        "by_state": by_state,
        "by_application_name": by_app,
        "oldest_backend_start": oldest,
        "usage_pct": round(total * 100.0 / max_conn, 2) if max_conn else None,
    }


def collect_host_holders(port=5432):
    """宿主侧：谁持有指向时序库端口的 TCP 连接（占用方 PID 的宿主粒度）。

    只读 netstat，不含任何凭据。仅统计 ESTABLISHED 态。
    """
    try:
        proc = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=30)
    except Exception:
        return {}
    holders = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(":%d" % port):
            continue
        if parts[3].upper() != "ESTABLISHED":
            continue
        pid = parts[4]
        holders[pid] = holders.get(pid, 0) + 1
    return holders


def main():
    ap = argparse.ArgumentParser(description="DB-026 连接池只读巡检")
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    try:
        report = collect(args.container)
    except Exception as exc:
        print("巡检失败（只读，未做任何变更）: %s" % exc, file=sys.stderr)
        return 1

    report["host_tcp_established_by_pid"] = collect_host_holders(args.port)
    report["threshold_pct"] = args.threshold
    report["alert"] = bool(report["usage_pct"] is not None
                           and report["usage_pct"] >= args.threshold)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 3 if report["alert"] else 0


if __name__ == "__main__":
    sys.exit(main())
