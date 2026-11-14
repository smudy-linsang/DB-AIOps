#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DB-026 连接池巡检脚本 v2（只读，不做任何变更）。

用途：DB-026 任务单第 3 项「D2 之前只监不擅动」——每日巡检时序库连接池
占用（ESTABLISHED 数、占用方 PID），发现占满只报黑板，不重启任何进程。

v2 相对 v1 的订正（据 DB-026 独立复核 D-1/D-2/D-3/D-7 与建议 S-6）：
  D-1/D-2：netstat 必须「分侧」统计。只筛「本地端口＝服务端口」的行只能拿到
           服务端侧/中继侧 socket 属主（如 wslrelay.exe）——那不是占用方。
           本脚本同时输出 client_side（远端＝5432，即真正连着库的一方）与
           server_side（本地＝5432）与 listening_owners（监听属主），并把
           客户端侧 PID 映射到进程名与脱敏命令行。
  D-3：max_connections 只约束「客户端槽位」，后台进程不占槽（受
       max_worker_processes 约束）。占用率分子改用 backend_type='client backend'，
       分母用 max_connections - superuser_reserved_connections（非 superuser 可用槽位），
       并把含后台进程的裸计数单列，避免被引用为「占用数」。
  D-7：PG 侧 timestamp 为 UTC，渲染时显式同时给出 UTC 与本地时（+08:00），
       并标注时区，避免被误读成凌晨。
  S-6：读数字段标准化，每轮带读时点。

安全边界：
  * 只读：全部查询为 SELECT / SHOW，不写库、不改配置、不重启容器与进程。
  * 不含任何凭据：连接一律走 `docker exec <容器> psql`（容器内本地 socket），
    不读取 .env、不落库密码；输出只含连接计数、进程名与已脱敏命令行。

用法：
    python scripts/pg_pool_audit.py [--container db-aiops-timescaledb] [--json out.json]
退出码：
    0 = 正常（客户端槽位占用低于阈值）
    3 = 超阈值（客户端槽位占用 >= 阈值），仅告警，不做任何处置
    1 = 巡检失败（只读，未做任何变更）
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

DEFAULT_CONTAINER = "db-aiops-timescaledb"
DEFAULT_THRESHOLD = 80   # 客户端槽位占用率百分比，达到即告警
LOCAL_TZ = timezone(timedelta(hours=8))

_SECRET_RE = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*\S+")


def redact(text):
    """命令行脱敏：抹掉形如 password=xxx / pwd: xxx 的片段。"""
    if not text:
        return text
    return _SECRET_RE.sub(
        lambda m: m.group(0).split("=")[0].split(":")[0] + "=<redacted>", text)


def run_psql(container, sql, db="db_monitor"):
    proc = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "postgres", "-d", db,
         "-t", "-A", "-F", "\t", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError("psql 失败: %s" % (proc.stderr or "").strip())
    return [line for line in proc.stdout.splitlines() if line.strip()]


def scalar(container, sql, db="db_monitor"):
    rows = run_psql(container, sql, db)
    if not rows:
        return None
    return rows[0].split("\t")[0]


def collect_pg(container):
    max_conn = int(scalar(container, "show max_connections;") or 0)
    reserved = int(scalar(container, "show superuser_reserved_connections;") or 0)
    worker_proc = int(scalar(container, "show max_worker_processes;") or 0)

    total = int(scalar(container, "select count(*) from pg_stat_activity;") or 0)
    client = int(scalar(
        container,
        "select count(*) from pg_stat_activity where backend_type='client backend';") or 0)
    background = int(scalar(
        container,
        "select count(*) from pg_stat_activity where backend_type <> 'client backend';") or 0)

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

    by_addr = {}
    for row in run_psql(
        container,
        "select coalesce(client_addr::text,'(local)'), count(*) "
        "from pg_stat_activity where backend_type='client backend' group by 1 order by 2 desc;"):
        addr, cnt = row.split("\t")
        by_addr[addr] = int(cnt)

    # 最老客户端连接：同时给 UTC 与本地时，避免时区误读（D-7）
    oldest = {"utc": None, "local_cst": None}
    rows = run_psql(
        container,
        "select to_char(min(backend_start) at time zone 'UTC','YYYY-MM-DD HH24:MI:SS'), "
        "to_char(min(backend_start) at time zone 'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS') "
        "from pg_stat_activity where backend_type='client backend';")
    if rows:
        parts = rows[0].split("\t")
        oldest["utc"] = parts[0] if parts else None
        oldest["local_cst"] = parts[1] if len(parts) > 1 else None

    usable = max_conn - reserved
    return {
        "max_connections": max_conn,
        "superuser_reserved_connections": reserved,
        "max_worker_processes": worker_proc,
        "client_usable_slots": usable,
        "total_backends_all_types": total,
        "client_backends": client,
        "background_backends": background,
        "client_slot_usage_pct": round(client * 100.0 / usable, 2) if usable else None,
        # 净口径：本脚本自身的只读 psql 查询也占 1 个客户端槽位（见 2026-11-14 复核侧 r37
        # 「D-3 读数的净口径」：11/97＝11.34% 含该连接，净占用为 10/97＝10.31%）。
        # 两个口径都输出，引用时必须写明用哪一个；alert 按 raw（保守）判定。
        "net_client_backends_excluding_audit_connection": max(client - 1, 0),
        "net_client_slot_usage_pct": round((client - 1) * 100.0 / usable, 2) if usable else None,
        "reading_basis_note": "client_backends 含本脚本自身那条只读 psql 查询连接（1 条）；净占用＝client_backends−1；两个口径并列输出，引用时须写明用哪一个",
        "total_ratio_pct_including_background": round(total * 100.0 / max_conn, 2) if max_conn else None,
        "by_state_client_only": by_state,
        "by_application_name": by_app,
        "by_client_addr": by_addr,
        "oldest_client_backend": oldest,
        "oldest_client_backend_tz_note": "PG timestamp 为 UTC；local_cst 为 Asia/Shanghai (+08:00)",
    }


def _port(addr):
    return addr.rsplit(":", 1)[-1] if ":" in addr else addr


def collect_host_tcp(port=5432):
    """宿主侧 TCP 归组：必须分侧，否则拿不到真正的占用方（D-1/D-2）。"""
    try:
        proc = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=30)
    except Exception:
        return {"available": False}
    client_side, server_side, listening = [], [], []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, remote, state, pid = parts[1], parts[2], parts[3], parts[4]
        if state.upper() == "LISTENING" and _port(local) == str(port):
            listening.append({"local": local, "pid": pid})
        if state.upper() != "ESTABLISHED":
            continue
        if _port(remote) == str(port) and _port(local) != str(port):
            client_side.append({"local": local, "remote": remote, "pid": pid})
        elif _port(local) == str(port) and _port(remote) != str(port):
            server_side.append({"local": local, "remote": remote, "pid": pid})
    return {
        "available": True,
        "client_side": client_side,
        "server_side": server_side,
        "listening_owners": listening,
        "note": "client_side = 远端为服务端口者（真正连着库的一方＝占用方）；"
                "server_side / listening_owners = 服务端侧或中继侧 socket 属主，不是占用方",
    }


def _count_by_pid(rows):
    out = {}
    for row in rows:
        out[row["pid"]] = out.get(row["pid"], 0) + 1
    return out


def map_pids(pids):
    """把 PID 映射为进程名与脱敏命令行（只读）。"""
    if not pids:
        return {}
    plist = ",".join("'%s'" % p for p in pids)
    ps = ("Get-CimInstance Win32_Process | Where-Object { @(%s) -contains $_.ProcessId } "
          "| ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.Name,$_.CommandLine }" % plist)
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              capture_output=True, text=True, timeout=30)
    except Exception:
        return {}
    mapping = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        mapping[parts[0]] = {"name": parts[1], "cmdline": redact(parts[2])[:300]}
    return mapping


def main():
    ap = argparse.ArgumentParser(description="DB-026 连接池只读巡检 (v2)")
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    try:
        report = collect_pg(args.container)
    except Exception as exc:
        print("巡检失败（只读，未做任何变更）: %s" % exc, file=sys.stderr)
        return 1

    tcp = collect_host_tcp(args.port)
    client_rows = tcp.get("client_side") or []
    server_rows = tcp.get("server_side") or []
    client_pids = _count_by_pid(client_rows)
    server_pids = _count_by_pid(server_rows)
    listen_pids = _count_by_pid(tcp.get("listening_owners") or [])

    report["checked_at_local_cst"] = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    report["checked_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    report["container"] = args.container
    report["host_tcp"] = {
        "client_side_by_pid": client_pids,
        "client_side_processes": map_pids(sorted(client_pids)),
        "server_side_by_pid": server_pids,
        "server_side_processes": map_pids(sorted(server_pids)),
        "listening_owners_by_pid": listen_pids,
        "listening_owner_processes": map_pids(sorted(listen_pids)),
        "note": tcp.get("note"),
    }
    report["threshold_pct"] = args.threshold
    report["threshold_basis"] = "client_backends / client_usable_slots（已扣 superuser_reserved_connections；分子含本脚本自身只读连接 1 条）"
    usage = report.get("client_slot_usage_pct")
    report["alert"] = bool(usage is not None and usage >= args.threshold)
    net_usage = report.get("net_client_slot_usage_pct")
    report["alert_by_net"] = bool(net_usage is not None and net_usage >= args.threshold)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 3 if report["alert"] else 0


if __name__ == "__main__":
    sys.exit(main())
