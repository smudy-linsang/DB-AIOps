# -*- coding: utf-8 -*-
"""DB-005R 补充单《恢复采集与新窗口重证》· 恢复报告（只读、可复现）。

事实采集（全部只读，不驱动 workload、不改数据、不重启任何进程）：
 ① 采集器 / Web 进程：新 PID 与启动时刻（读 Win32_Process）；
 ② sql_stat 两路首次采样时刻与持续前推；
 ③ 逐小时写入分布（近 5 天）——证明"正常采集时即使无业务负载也持续写基线行"，
    故 11-11 08:53 之后无行 = **采集器进程已死**，而非"无负载不写行"；
 ④ 采集缺口量化：最后中断采样点 → 恢复后首采样点；
 ⑤ 新窗口装置：连驱/核验脚本的 DRIVE_DATES 与相关计划任务清单。

输出：与本脚本同目录的 g11_recovery_20261111.json
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'g11_recovery_20261111.json')
LOCAL_TZ = dt_tz(timedelta(hours=8))
CIDS = (18, 19)
NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.api_views_perf import _ts_cursor


def ps(cmd):
    """跑一条只读 powershell，返回 stdout 文本。"""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=60, creationflags=NOWIN)
        return (out.stdout or b'').decode('utf-8', 'replace')
    except Exception as exc:
        return 'err:%s: %s' % (type(exc).__name__, exc)


def mask_cmd(s):
    """入库脱敏（DB-005 同一口径）：本地地址/端口/本地账号目录统一掩码。"""
    s = s.replace('127.0.0.1:8009', '<local-host>:<platform-port>')
    s = s.replace('C:\\Users\\linsa', 'C:\\Users\\<account>')
    return s


def proc_facts():
    txt = ps("Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object { $_.CommandLine -match 'start_monitor|runserver 127\\.0\\.0\\.1:8009' } | "
             "ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.ParentProcessId, "
             "$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss'), $_.CommandLine.Trim() }")
    out = []
    for line in txt.splitlines():
        line = line.strip()
        if not line or '|' not in line:
            continue
        pid, ppid, ctime, cmd = line.split('|', 3)
        role = 'collector' if 'start_monitor' in cmd else ('web' if 'runserver' in cmd else 'other')
        out.append({'pid': int(pid), 'ppid': int(ppid), 'started': ctime,
                    'role': role, 'cmd': mask_cmd(cmd)})
    return out


def task_facts():
    names = ['MMMan-DBAIOps-G11DailyDrive', 'MMMan-DBAIOps-G11BucketVerify',
             'MMMan-DBAIOps-G11Rejudge-D1', 'MMMan-DBAIOps-G11Rejudge-D2',
             'MMMan-DBAIOps-G11Rejudge-W18', 'MMMan-DBAIOps-G11Rejudge-W19',
             'MMMan-DBAIOps-G11Rejudge-Mature', 'MMMan-DBAIOps-WebWatchdog']
    out = []
    for n in names:
        txt = ps("$t=Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
                 "if($t){$i=Get-ScheduledTaskInfo -TaskName '%s'; "
                 "'{0}|{1}|{2}|{3}|{4}|{5}' -f $t.State, $i.NextRunTime, $i.LastRunTime, "
                 "$i.LastTaskResult, $t.Actions.Execute, $t.Actions.Arguments}" % (n, n))
        line = txt.strip().splitlines()
        if not line or '|' not in line[0]:
            out.append({'task': n, 'error': 'not found'})
            continue
        st, nxt, last, res, exe, args = line[0].split('|', 5)
        out.append({'task': n, 'state': st, 'next_run': nxt, 'last_run': last,
                    'last_result': res, 'execute': exe, 'arguments': args})
    return out


def drive_dates():
    out = {}
    for name in ('g11_daily_drive.py', 'g11_verify_buckets.py'):
        p = os.path.join(r'C:\dispatcher', name)
        try:
            with open(p, encoding='utf-8') as fh:
                for line in fh:
                    if line.startswith('DRIVE_DATES'):
                        out[name] = line.strip()
                        break
        except Exception as exc:
            out[name] = 'err:%s' % exc
    return out


def main():
    rec = {'event': 'g11_recovery_20261111',
           'generated_at': datetime.now(LOCAL_TZ).isoformat(),
           'note': '全程只读：未驱动 workload、未改数据、未重启任何进程'}
    rec['processes'] = proc_facts()
    rec['device_drive_dates'] = drive_dates()
    rec['device_tasks'] = task_facts()

    with _ts_cursor() as cur:
        if cur is None:
            rec['error'] = 'TSDB 不可用'
        else:
            now = datetime.now(LOCAL_TZ)
            for cid in CIDS:
                cur.execute('SELECT MAX(time), COUNT(*) FROM sql_stat WHERE db_config_id=%s', [cid])
                mx, tot = cur.fetchone()
                cur.execute('SELECT COUNT(DISTINCT sql_digest) FROM sql_stat '
                            'WHERE db_config_id=%s AND time >= %s', [cid, now - timedelta(minutes=30)])
                d30 = cur.fetchone()[0]
                # 恢复后首个采样点（16:30 之后）
                cur.execute('SELECT MIN(time), COUNT(*) FROM sql_stat '
                            'WHERE db_config_id=%s AND time >= %s', [cid, now - timedelta(hours=1)])
                first, n1h = cur.fetchone()
                # 中断前最后一个采样点（16:00 之前）
                cur.execute('SELECT MAX(time), COUNT(*) FROM sql_stat '
                            'WHERE db_config_id=%s AND time < %s', [cid, now - timedelta(hours=1)])
                last_before, nb = cur.fetchone()
                gap_min = None
                if first and last_before:
                    gap_min = round((first - last_before).total_seconds() / 60.0, 1)
                rec.setdefault('sql_stat', {})[str(cid)] = {
                    'max_time_local': mx.astimezone(LOCAL_TZ).isoformat() if mx else None,
                    'rows_total': tot,
                    'distinct_digests_last_30min': d30,
                    'first_sample_after_restore_local': first.astimezone(LOCAL_TZ).isoformat() if first else None,
                    'rows_last_1h': n1h,
                    'last_sample_before_gap_local': last_before.astimezone(LOCAL_TZ).isoformat() if last_before else None,
                    'gap_minutes_between': gap_min,
                }
            # 逐小时写入分布（近 5 天）：证明"无业务负载也持续写基线行"
            for cid in CIDS:
                cur.execute("""SELECT time_bucket('1 hour', time) AS h, COUNT(*), SUM(exec_delta),
                               COUNT(DISTINCT sql_digest)
                               FROM sql_stat WHERE db_config_id=%s AND time >= %s
                               GROUP BY 1 ORDER BY 1""",
                            [cid, datetime.now(dt_tz.utc) - timedelta(days=5)])
                rows = []
                for h, c, s, d in cur.fetchall():
                    rows.append({'hour': h.astimezone(LOCAL_TZ).strftime('%m-%d %H'),
                                 'rows': int(c), 'exec_delta': int(s or 0), 'digests': int(d)})
                rec.setdefault('hourly_write_pattern', {})[str(cid)] = rows
            # 是否有 09:00-16:00 的空窗（中断量化）
            for cid in CIDS:
                cur.execute("""SELECT COUNT(*) FROM sql_stat WHERE db_config_id=%s
                               AND time >= %s AND time < %s""",
                            [cid, datetime(2026, 11, 11, 9, 0, tzinfo=LOCAL_TZ).astimezone(dt_tz.utc),
                             datetime(2026, 11, 11, 17, 0, tzinfo=LOCAL_TZ).astimezone(dt_tz.utc)])
                rec.setdefault('gap_window_20261111_0900_1700', {})[str(cid)] = cur.fetchone()[0]
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rec, ensure_ascii=False, indent=1)[:6000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
