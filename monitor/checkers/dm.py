# -*- coding: utf-8 -*-
"""
达梦数据库 (DM8) 检查器
支持 DW 集群模式（主备）和 DSC 集群模式（共享存储）
采集 22 大类指标：基础信息、会话、空间、性能、锁等待、会话详情、
SQL统计、缓冲池、事务统计、复制集群、DW集群、DSC集群、配置参数、
日志统计、安全审计、资源限制、长事务、死锁记录、Redo日志、
对象统计、数据字典、会话按状态统计、高可用状态汇总、配置参数扩展。
"""

import dmPython
from monitor.checkers.base import BaseDBChecker


class DamengChecker(BaseDBChecker):
    """达梦数据库 (DM8) 监控检查器"""

    def get_connection(self, config):
        """达梦 dmPython 连接"""
        return dmPython.connect(
            user=config.username,
            password=config.get_password(),
            server=config.host,
            port=config.port,
        )

    def collect_metrics(self, config, conn):
        cur = conn.cursor()

        # =============================================
        # 1. 基础信息 (basic)
        # =============================================
        cur.execute("SELECT banner FROM v$version WHERE ROWNUM=1")
        version = cur.fetchone()[0]

        try:
            cur.execute(
                "SELECT INSTANCE_NAME, HOST_NAME, TO_CHAR(START_TIME, 'YYYY-MM-DD HH24:MI:SS') FROM V$INSTANCE"
            )
            inst_row = cur.fetchone()
            instance_name = inst_row[0]
            host_name = inst_row[1]
            startup_time = inst_row[2]
        except Exception:
            instance_name = 'N/A'
            host_name = 'N/A'
            startup_time = 'N/A'

        try:
            cur.execute("SELECT MODE$ FROM V$INSTANCE")
            db_mode = cur.fetchone()[0]
        except Exception:
            db_mode = 'N/A'

        try:
            cur.execute("SELECT (SYSDATE-START_TIME)*86400 FROM v$instance")
            uptime = int(cur.fetchone()[0])
        except Exception:
            uptime = 0

        try:
            cur.execute("SELECT ARCH_MODE FROM V$DATABASE")
            arch_mode = cur.fetchone()[0]
        except Exception:
            arch_mode = 'N/A'

        # =============================================
        # 2. 连接与会话 (session)
        # =============================================
        cur.execute("SELECT count(*) FROM v$sessions WHERE STATE='ACTIVE'")
        active_sessions = int(cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM v$sessions")
        total_sessions = int(cur.fetchone()[0])

        cur.execute("SELECT VALUE FROM v$parameter WHERE name='MAX_SESSIONS'")
        max_sessions = int(cur.fetchone()[0])
        conn_usage_pct = round(
            (active_sessions / max_sessions) * 100, 2
        ) if max_sessions > 0 else 0

        try:
            # DM8 无 V$SESSION_WAIT 视图（DB-009 实测 -2106）→ 改用锁等待口径。
            # 该指标在告警模板里的语义是「等待会话数」（init_alert_templates:
            # '达梦 等待会话数'），所以按 **等待事务** 去重，而不是数锁条数 ——
            # 同一会话可能同时持有多把被阻塞的锁，按锁条数会把等待会话数放大。
            # V$LOCK 没有 SESS_ID 列，关联键是 TRX_ID（DB-009 实测列结构）。
            cur.execute("SELECT COUNT(DISTINCT TRX_ID) FROM V$LOCK WHERE BLOCKED = 1")
            session_wait_count = int(cur.fetchone()[0])
        except Exception:
            session_wait_count = 0

        # =============================================
        # 3. 空间使用 (space)
        # =============================================
        # 表空间
        try:
            cur.execute("""
                SELECT 
                    NAME,
                    ROUND(TOTAL_SIZE * PAGE / 1024.0 / 1024.0, 2) as TOTAL_MB,
                    ROUND(USED_SIZE * PAGE / 1024.0 / 1024.0, 2) as USED_MB,
                    CASE WHEN TOTAL_SIZE > 0 
                         THEN ROUND(USED_SIZE * 100.0 / TOTAL_SIZE, 2)
                         ELSE 0 END as USED_PCT
                FROM (
                    SELECT 
                        t.NAME,
                        t.TOTAL_SIZE,
                        t.USED_SIZE,
                        (SELECT CAST(VALUE AS INT) FROM V$PARAMETER WHERE NAME = 'GLOBAL_PAGE_SIZE') as PAGE
                    FROM V$TABLESPACE t
                )
            """)
            tablespaces = []
            for row in cur.fetchall():
                tablespaces.append({
                    "name": row[0],
                    "total_mb": float(row[1]),
                    "used_mb": float(row[2]),
                    "used_pct": float(row[3])
                })
        except Exception:
            tablespaces = []

        # 临时表空间
        try:
            cur.execute("""
                SELECT 
                    NAME,
                    ROUND(TOTAL_SIZE * PAGE / 1024.0 / 1024.0, 2) as TOTAL_MB
                FROM (
                    SELECT 
                        t.NAME,
                        t.TOTAL_SIZE,
                        (SELECT CAST(VALUE AS INT) FROM V$PARAMETER WHERE NAME = 'GLOBAL_PAGE_SIZE') as PAGE
                    FROM V$TABLESPACE t WHERE t.TYPE$ = 2
                )
            """)
            temp_tablespaces = []
            for row in cur.fetchall():
                temp_tablespaces.append({
                    "name": row[0],
                    "size_mb": float(row[1])
                })
        except Exception:
            temp_tablespaces = []

        # 数据文件统计
        try:
            cur.execute(
                "SELECT count(*), SUM(TOTAL_SIZE * PAGE) / 1024 / 1024 / 1024 FROM V$TABLESPACE"
            )
            tbs_row = cur.fetchone()
            datafile_count = tbs_row[0]
            datafile_size_total_gb = float(tbs_row[1])
        except Exception:
            datafile_count = 0
            datafile_size_total_gb = 0

        # =============================================
        # 4. 性能指标 (performance)
        # =============================================
        try:
            cur.execute("SELECT VALUE FROM V$PARAMETER WHERE NAME='BUFFER'")
            buffer_size = int(cur.fetchone()[0])
            buffer_size_mb = round(buffer_size / 1024.0, 2)
        except Exception:
            buffer_size_mb = 0

        try:
            cur.execute(
                "SELECT STAT_VAL FROM V$SYSSTAT WHERE NAME='sql executed count'"
            )
            sql_count = int(cur.fetchone()[0] or 0)
        except Exception:
            sql_count = 0

        try:
            cur.execute(
                "SELECT STAT_VAL FROM V$SYSSTAT WHERE NAME='transaction total count'"
            )
            tran_count = int(cur.fetchone()[0] or 0)
        except Exception:
            tran_count = 0

        qps = round(sql_count / uptime, 2) if uptime > 0 else 0
        tps = round(tran_count / uptime, 2) if uptime > 0 else 0

        # 缓冲池命中率
        try:
            # DM8 的 V$BUFFER 无 REQUEST_R_SORT/CACHE_R_SORT 列（DB-009 实测 -2111）
            # → 口径不变：仍是「缓冲池命中率」，改用 DM8 的缓冲池视图 V$BUFFERPOOL
            #   （逻辑读 N_LOGIC_READS / 物理读 N_PHY_READS），乘 1.0 防整除截断
            cur.execute(
                """
                SELECT CASE WHEN SUM(N_LOGIC_READS + N_PHY_READS) > 0
                            THEN ROUND((1 - SUM(N_PHY_READS) * 1.0
                                        / SUM(N_LOGIC_READS + N_PHY_READS)) * 100, 2)
                            ELSE 0 END as CACHE_HIT_RATIO
                FROM V$BUFFERPOOL
                """
            )
            cache_hit_ratio = float(cur.fetchone()[0])
        except Exception:
            cache_hit_ratio = 0

        # =============================================
        # 5. 锁等待 (wait) - 增强
        # =============================================
        lock_list = []
        lock_wait_count = 0
        try:
            cur.execute("""
                SELECT 
                    t.SESS_ID,
                    s.USER_NAME,
                    t.ID as TRANSACTION_ID,
                    SUBSTR(s.SQL_TEXT, 1, 200) as SQL_TEXT,
                    l.LMODE,
                    l.BLOCKED
                FROM V$LOCK l
                LEFT JOIN V$TRX t ON l.TRX_ID = t.ID
                LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID
                WHERE l.BLOCKED = 1
            """)
            for row in cur.fetchall():
                lock_wait_count += 1
                lock_list.append({
                    "sess_id": str(row[0]),
                    "user_name": row[1] or 'N/A',
                    "transaction_id": str(row[2]) if row[2] else 'N/A',
                    "sql_text": (row[3] or 'N/A')[:100],
                    "lmode": str(row[4]),
                    "blocked": row[5]
                })
        except Exception:
            pass

        # 等待事件
        try:
            # DM8 无 V$SESSION_WAIT（实测 -2106）→ 等待事件口径取 V$WAIT_HISTORY(WAIT_CLASS)
            cur.execute("""
                SELECT WAIT_CLASS, COUNT(*) as CNT
                FROM V$WAIT_HISTORY
                WHERE WAIT_CLASS != 'Idle'
                GROUP BY WAIT_CLASS
                ORDER BY CNT DESC
                LIMIT 10
            """)
            wait_events = []
            for row in cur.fetchall():
                wait_events.append({
                    "event": row[0],
                    "count": int(row[1])
                })
        except Exception:
            wait_events = []

        # =============================================
        # 6. 会话详情 (session_detail) - P0新增
        # =============================================
        session_list = []
        try:
            cur.execute("""
                SELECT 
                    SESS_ID, USER_NAME, STATE, CLNT_IP, CLNT_HOST, APPNAME,
                    SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT,
                    TRX_ID
                FROM V$SESSIONS
                WHERE USER_NAME IS NOT NULL
                ORDER BY STATE, SESS_ID
                LIMIT 100
            """)
            for row in cur.fetchall():
                session_list.append({
                    "sess_id": str(row[0]),
                    "user_name": row[1] or 'N/A',
                    "state": row[2] or 'N/A',
                    "program": row[5] or 'N/A',
                    "ip": row[3] or 'N/A',
                    "host": row[4] or 'N/A',
                    "sql_text": (row[6] or 'N/A')[:200],
                    "trx_id": str(row[7]) if row[7] else 'N/A',
                    "transaction_id": str(row[7]) if row[7] else 'N/A'
                })
        except Exception:
            pass

        # =============================================
        # 7. SQL统计 (sql) - P0新增
        # =============================================
        slow_queries = []
        try:
            cur.execute("""
                SELECT * FROM (
                    SELECT SQL_ID, SUBSTR(TOP_SQL_TEXT, 1, 200) as SQL_TEXT,
                           COUNT(*) as EXECUTE_COUNT, AVG(TIME_USED) as AVG_TIME
                    FROM V$SQL_HISTORY
                    GROUP BY SQL_ID, TOP_SQL_TEXT
                    ORDER BY EXECUTE_COUNT DESC
                )
                WHERE ROWNUM <= 10
            """)
            for row in cur.fetchall():
                slow_queries.append({
                    "sql_id": str(row[0]),
                    "sql_text": (row[1] or 'N/A')[:200],
                    "execute_count": int(row[2] or 0),
                    "avg_time": int(row[3]) if row[3] else 0
                })
        except Exception:
            pass

        top_sql = []
        try:
            cur.execute("""
                SELECT * FROM (
                    SELECT SQL_ID, SUBSTR(TOP_SQL_TEXT, 1, 200) as SQL_TEXT,
                           COUNT(*) as EXECUTE_COUNT
                    FROM V$SQL_HISTORY
                    GROUP BY SQL_ID, TOP_SQL_TEXT
                    ORDER BY EXECUTE_COUNT DESC
                )
                WHERE ROWNUM <= 10
            """)
            for row in cur.fetchall():
                top_sql.append({
                    "sql_id": str(row[0]),
                    "sql_text": (row[1] or 'N/A')[:200],
                    "execute_count": int(row[2] or 0)
                })
        except Exception:
            pass

        # =============================================
        # 8. 缓冲池 (buffer) - P0
        # =============================================
        buffer_pools = []
        try:
            cur.execute("""
                SELECT ID, NAME, PAGE_SIZE, N_TOTAL_PAGES,
                       FREE, N_DIRTY, N_PHY_READS
                FROM V$BUFFERPOOL
            """)
            for row in cur.fetchall():
                buffer_pools.append({
                    "group_id": int(row[0] or 0),
                    "group_name": str(row[1] or 'N/A'),
                    # DM8 池视图无 node 概念（DSC 才有）；buffer_size 以字节计
                    "node_num": 0,
                    "buffer_size": int((row[2] or 0) * (row[3] or 0)),
                    "buffer_count": int(row[3] or 0),
                    "free_count": int(row[4] or 0),
                    # DM8 的 V$BUFFERPOOL 没有「活跃页」列 —— 老实现用的
                    # V$BUFFER.ACTIVE_COUNT 在 DM8 根本不存在（实测 -2111）。
                    # N_LOGIC_READS 是**逻辑读次数**，拿它冒充活跃页数属于
                    # 指标含义偷换（冻结输入语义纪律），故置 0 并在此留痕。
                    "active_count": 0,
                    "dirty_count": int(row[5] or 0),
                    "phy_read_count": int(row[6] or 0)
                })
        except Exception:
            pass

        # =============================================
        # 9. 事务统计 (transaction) - P1
        # =============================================
        try:
            cur.execute("SELECT count(*) FROM V$TRX")
            active_transactions = int(cur.fetchone()[0])
        except Exception:
            active_transactions = 0

        try:
            cur.execute("""
                SELECT count(*) FROM V$SESSIONS 
                WHERE STATE='INACTIVE' AND TRX_ID != 0
            """)
            idle_transactions = int(cur.fetchone()[0])
        except Exception:
            idle_transactions = 0

        # =============================================
        # 10. 复制与集群 (replication) - P1 增强
        # =============================================
        try:
            cur.execute("SELECT ARCH_MODE FROM V$DATABASE")
            archive_mode = cur.fetchone()[0]
        except Exception:
            archive_mode = 'N/A'

        try:
            # DM8 无 V$ARCH_DEST（实测 -2106）→ 归档目的地在 V$ARCH_STATUS
            cur.execute(
                "SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC FROM V$ARCH_STATUS"
            )
            archive_dest = []
            for row in cur.fetchall():
                archive_dest.append({
                    "dest_id": 0,
                    "status": row[2] or 'N/A',
                    "dest_name": row[1] or 'N/A',
                    "arch_type": row[0] or 'N/A',
                    "arch_src": row[3] or 'N/A'
                })
        except Exception:
            archive_dest = []

        try:
            cur.execute("SELECT count(*) FROM V$ARCH_FILE")
            archive_file_count = int(cur.fetchone()[0])
        except Exception:
            archive_file_count = 0

        # =============================================
        # 10.5 DW 集群（主备）监控 - P0 新增
        # =============================================
        # 数据库角色和模式
        try:
            cur.execute("SELECT MODE$ FROM V$INSTANCE")
            dm_instance_mode = cur.fetchone()[0]
        except Exception:
            dm_instance_mode = 'N/A'

        try:
            cur.execute("SELECT ROLE$ FROM V$DATABASE")
            _db_role = cur.fetchone()[0]
            # DM8 用 ROLE$ 表达库角色（0 = PRIMARY），V$DATABASE 无 MODE$ 列
            dm_database_mode = ('PRIMARY' if str(_db_role) in ('0', 'PRIMARY')
                                else str(_db_role))
        except Exception:
            dm_database_mode = 'N/A'

        # 实时归档状态
        try:
            # 归档目的地口径统一到 V$ARCH_STATUS（ARCH_TYPE/ARCH_DEST/ARCH_STATUS/ARCH_SRC）
            cur.execute("""
                SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC
                FROM V$ARCH_STATUS
            """)
            realtime_archive_dest = []
            for row in cur.fetchall():
                realtime_archive_dest.append({
                    "dest_id": 0,
                    "dest_name": row[1] or 'N/A',
                    "status": row[2] or 'N/A',
                    "arch_type": row[0] or 'N/A',
                    "arch_seq": 0,
                    "arch_space": 0,
                    "arch_file": row[1] or 'N/A',
                    "arch_timely": 'N/A',
                    "is_logical": 'N/A',
                    "synchronized": row[3] or 'N/A'
                })
        except Exception:
            realtime_archive_dest = []

        # 实时日志同步状态
        try:
            # DB-009 桌面端复核订正：老实现在这里查 V$RLOG，然后把 CUR_OFFSET
            # 塞进 apply_seq、把 ALMOST_FULL_STATUS 塞进 sync_status —— 前者是
            # **归档日志字节偏移**、后者是**日志空间水位**，都不是"主备同步进度"，
            # 属于指标含义偷换。DM8 单实例本身没有主备同步真值，
            # 但归档同步状态在 V$ARCH_STATUS.ARCH_STATUS 上有真值列（实测 4 列）。
            cur.execute("""
                SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC
                FROM V$ARCH_STATUS
            """)
            rlog_sync_status = []
            for row in cur.fetchall():
                rlog_sync_status.append({
                    "src_instance": str(row[3] or 'N/A'),
                    "dest_instance": str(row[1] or 'N/A'),
                    "arch_dest": str(row[1] or 'N/A'),
                    # DM8 单实例无归档序号/回放序号真值列，不伪造
                    "arch_seq": 0,
                    "arch_seq_all": 0,
                    "apply_seq": 0,
                    "apply_seq_all": 0,
                    "sync_status": row[2] or 'N/A'
                })
        except Exception:
            rlog_sync_status = []

        # 主备延迟
        try:
            # DM8 单实例无 V$DEST_PENDING（DW 主备专用，实测 -2106）
            # → 合法空集查询占位，不伪造备库延迟数据
            cur.execute("""
                SELECT PATH, STATUS, CREATE_TIME, CLOSE_TIME, ARCH_SEQ, DB_MAGIC
                FROM V$ARCH_FILE WHERE 1=0
            """)
            dest_pending = []
            apply_delay_total = 0
            for row in cur.fetchall():
                delay = int(row[2]) if row[2] else 0
                apply_delay_total += delay
                dest_pending.append({
                    "dst_inst_name": row[0] or 'N/A',
                    "apply_status": row[1] or 'N/A',
                    "apply_delay": delay,
                    "apply_rst_seq": int(row[3]) if row[3] else 0,
                    "apply_rst_time": str(row[4]) if row[4] else 'N/A',
                    "last_arch_seq": int(row[5]) if row[5] else 0
                })
        except Exception:
            dest_pending = []
            apply_delay_total = 0

        # DW 复制状态汇总
        dw_replication_health = 'UNKNOWN'
        dw_replication_issues = []
        if dm_instance_mode == 'PRIMARY' or dm_database_mode == 'PRIMARY':
            dw_replication_health = 'HEALTHY'
        elif dm_instance_mode == 'STANDBY' or dm_database_mode == 'STANDBY':
            if apply_delay_total > 1000:
                dw_replication_health = 'DEGRADED'
                dw_replication_issues.append(
                    f"备库延迟过高: {apply_delay_total}"
                )
            else:
                dw_replication_health = 'HEALTHY'
        else:
            if dm_instance_mode != 'N/A':
                dw_replication_health = 'UNKNOWN'

        # =============================================
        # 10.6 DSC 集群（共享存储）监控 - P0 新增
        # =============================================
        # DSC 集群信息
        try:
            cur.execute("""
                SELECT INSTANCE_NAME, INSTANCE_NUMBER, HOST_NAME, NULL as PORT_NUM,
                       STATUS$, NULL as IS_PRIMARY, NULL as UPTIME
                FROM V$INSTANCE
            """)
            dsc_cluster_info = []
            dsc_node_count = 0
            dsc_primary_node = 'N/A'
            for row in cur.fetchall():
                if row[5] == 1:  # IS_PRIMARY
                    dsc_primary_node = row[0]
                dsc_cluster_info.append({
                    "instance_name": row[0] or 'N/A',
                    "instance_id": int(row[1]) if row[1] else 0,
                    "host_name": row[2] or 'N/A',
                    "port_num": int(row[3]) if row[3] else 0,
                    "status": row[4] or 'N/A',
                    "is_primary": int(row[5]) if row[5] else 0,
                    "uptime": int(row[6]) if row[6] else 0
                })
            # 节点数取 V$DATABASE.DSC_NODES 真值（DB-009 实测列存在，
            # DM8 单实例为 0）。不能用 len(dsc_cluster_info)：V$INSTANCE 恒有
            # 本实例一行，按行数算会把单机报成「DSC 1 节点集群」。
            cur.execute("SELECT DSC_NODES FROM V$DATABASE")
            _dsn_row = cur.fetchone()
            dsc_node_count = int(_dsn_row[0] or 0) if _dsn_row else 0
        except Exception:
            dsc_cluster_info = []
            dsc_node_count = 0
            dsc_primary_node = 'N/A'

        # DSC 实例详情
        try:
            # DM8 无 V$DSC_INSTANCES（实测 -2106）→ 合法空集，不伪造节点明细
            cur.execute("""
                SELECT ARCH_SEQ, PATH, STATUS, CREATE_TIME, CLOSE_TIME, DB_MAGIC
                FROM V$ARCH_FILE WHERE 1=0
            """)
            dsc_instances = []
            for row in cur.fetchall():
                dsc_instances.append({
                    "inst_id": int(row[0]) if row[0] else 0,
                    "inst_name": row[1] or 'N/A',
                    "cluster_state": row[2] or 'N/A',
                    "db_magic": int(row[3]) if row[3] else 0,
                    "rlog_send_offset": str(row[4]) if row[4] else 'N/A',
                    "rlog_pkg_snd_count": int(row[5]) if row[5] else 0
                })
        except Exception:
            dsc_instances = []

        # DSC 全局锁
        try:
            # DM8 无 V$GLOBAL_LATCH（实测 -2106）→ 合法空集
            cur.execute("""
                SELECT PATH, STATUS, CREATE_TIME, CLOSE_TIME, ARCH_SEQ, DB_MAGIC
                FROM V$ARCH_FILE WHERE 1=0
            """)
            dsc_global_latches = []
            for row in cur.fetchall():
                dsc_global_latches.append({
                    "lock_id": str(row[0]) if row[0] else 'N/A',
                    "lock_type": row[1] or 'N/A',
                    "owner_inst": int(row[2]) if row[2] else 0,
                    "owner_lru": int(row[3]) if row[3] else 0,
                    "blocking_lru": int(row[4]) if row[4] else 0,
                    "blocking_inst": int(row[5]) if row[5] else 0
                })
        except Exception:
            dsc_global_latches = []

        # DSC 锁状态统计
        try:
            cur.execute(
                "SELECT COUNT(*) FROM V$LOCK WHERE BLOCKED = 1"
            )
            dsc_lock_contention_count = int(cur.fetchone()[0])
        except Exception:
            dsc_lock_contention_count = 0

        # DSC 健康状态判断
        dsc_cluster_health = 'UNKNOWN'
        dsc_cluster_issues = []
        if dsc_node_count > 0:
            dsc_cluster_health = 'HEALTHY'
            for node in dsc_cluster_info:
                if node['status'] != 'OPEN':
                    dsc_cluster_health = 'DEGRADED'
                    dsc_cluster_issues.append(
                        f"节点 {node['instance_name']} 状态异常: {node['status']}"
                    )
            if dsc_lock_contention_count > 100:
                dsc_cluster_health = 'DEGRADED'
                dsc_cluster_issues.append(
                    f"全局锁竞争过多: {dsc_lock_contention_count}"
                )
        else:
            dsc_cluster_health = 'NOT_CLUSTER'

        # =============================================
        # 11. 配置参数 (config) - P1
        # =============================================
        config_params = {}
        config_keys = [
            'BUFFER', 'SORT_BUF_SIZE', 'MLOG_BUF_SIZE', 'MAX_SESSIONS',
            'MAX_TRX'
        ]
        for key in config_keys:
            try:
                cur.execute(f"SELECT VALUE FROM V$PARAMETER WHERE NAME='{key}'")
                config_params[key] = cur.fetchone()[0]
            except Exception:
                pass

        # =============================================
        # 12. 日志统计 (log) - P1
        # =============================================
        try:
            # DM8 无 V$LOG 视图（实测 -2106）→ 归档日志文件数口径
            cur.execute("SELECT count(*) FROM V$ARCH_FILE")
            log_count = int(cur.fetchone()[0])
        except Exception:
            log_count = 0

        try:
            cur.execute("SELECT TOTAL_SPACE FROM V$RLOG")
            log_size = int(cur.fetchone()[0] or 0)
        except Exception:
            log_size = 0

        # =============================================
        # 13. 安全审计 (security) - P2
        # =============================================
        # DM8 无 V$LOGIN 视图（DB-009 实测 -2106）→ 登录计数不可得。
        # 给 None（不可得）而不是 0（真的没有登录过），避免把"查不到"
        # 渲染成"登录数为 0"。V$SESSION_HISTORY 虽有登录记录，但那是历史
        # 会话流水，不能等同"当前登录数"，口径不同，不拿它顶替。
        login_count = None

        try:
            # DM8 的 V$SESSIONS.STATE 实测取值只有 ACTIVE，没有 FAILED
            # （失败登录不落在会话视图里），所以这里恒为空 —— 是"该维度
            # 不可得"，不是"没有失败登录"。
            cur.execute("""
                SELECT USER_NAME, COUNT(*) as CNT
                FROM V$SESSIONS
                WHERE STATE = 'FAILED'
                GROUP BY USER_NAME
            """)
            failed_logins = []
            for row in cur.fetchall():
                failed_logins.append({
                    "user_name": row[0] or 'N/A',
                    "count": int(row[1])
                })
        except Exception:
            failed_logins = []

        # =============================================
        # 14. 资源限制 (resource) - P2
        # =============================================
        resource_limits = []
        for resource in ['MAX_SESSIONS', 'MAX_TRX', 'MAX_LOCK_NUM']:
            try:
                cur.execute(
                    f"SELECT VALUE FROM V$PARAMETER WHERE NAME='{resource}'"
                )
                value = cur.fetchone()[0]
                resource_limits.append({
                    "resource_name": resource,
                    "value": value
                })
            except Exception:
                pass

        # =============================================
        # 15. 长时间事务 (long_trx) - P0
        # =============================================
        long_transactions = []
        try:
            # DB-009 桌面端复核订正：
            # ① DM8 的 V$TRX 实测 25 列里**没有事务开始时间**（只有 START_LSN，
            #    是日志序列号不是时间戳），所以 DURATION_SEC 只能用会话
            #    CREATE_TIME 近似 —— 它是**会话年龄**而非事务年龄，此处明确留痕。
            # ② 必须按 t.STATUS='ACTIVE' 过滤：V$TRX.STATUS 实测取值含 ACTIVE，
            #    不加这个条件会把所有跑超过 60 秒的**空闲会话**当成"长事务"。
            cur.execute("""
                SELECT t.ID as TRX_ID, t.SESS_ID, t.STATUS,
                       (SYSDATE - s.CREATE_TIME) * 86400 as DURATION_SEC,
                       SUBSTR(s.SQL_TEXT, 1, 200) as SQL_TEXT
                FROM V$TRX t LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID
                WHERE t.STATUS = 'ACTIVE'
                  AND (SYSDATE - s.CREATE_TIME) * 86400 > 60
                ORDER BY DURATION_SEC DESC
            """)
            for row in cur.fetchall():
                long_transactions.append({
                    "trx_id": str(row[0] or ''),
                    "sess_id": str(row[1] or ''),
                    "state": row[2] or 'N/A',
                    "duration_sec": int(row[3] or 0),
                    "sql_text": (row[4] or 'N/A')[:200]
                })
        except Exception:
            pass

        # =============================================
        # 16. 死锁记录 (deadlock) - P0
        # =============================================
        last_deadlock = {}
        try:
            cur.execute("""
                SELECT * FROM (
                    SELECT SESS_ID, TRX_ID, SQL_TEXT, HAPPEN_TIME
                    FROM V$DEADLOCK_HISTORY
                    ORDER BY HAPPEN_TIME DESC
                ) WHERE ROWNUM <= 5
            """)
            deadlock_rows = []
            for row in cur.fetchall():
                deadlock_rows.append({
                    "sess_id": str(row[0] or ''),
                    "trx_id": str(row[1] or ''),
                    "sql_text": (row[2] or 'N/A')[:200],
                    "deadlock_time": str(row[3] or 'N/A')
                })
            last_deadlock = {"found": len(deadlock_rows) > 0, "records": deadlock_rows}
        except Exception:
            last_deadlock = {"found": False, "records": []}

        # =============================================
        # 17. Redo 日志统计 (redo_log) - P0
        # =============================================
        redo_log_stats = {}
        try:
            cur.execute("SELECT COUNT(*) FROM V$RLOG")
            redo_log_stats['rlog_count'] = int(cur.fetchone()[0] or 0)
        except Exception:
            redo_log_stats['rlog_count'] = 0
        try:
            cur.execute("SELECT COUNT(*) FROM V$RLOG WHERE ALMOST_FULL_STATUS <> 'SAFE'")
            redo_log_stats['active_rlog_count'] = int(cur.fetchone()[0] or 0)
        except Exception:
            redo_log_stats['active_rlog_count'] = 0
        try:
            cur.execute("SELECT VALUE FROM V$PARAMETER WHERE NAME='RLOG_BUF_SIZE'")
            redo_log_stats['rlog_buf_size'] = cur.fetchone()[0]
        except Exception:
            redo_log_stats['rlog_buf_size'] = 'N/A'
        try:
            cur.execute("SELECT VALUE FROM V$PARAMETER WHERE NAME='RLOG_POOL_SIZE'")
            redo_log_stats['rlog_pool_size'] = cur.fetchone()[0]
        except Exception:
            redo_log_stats['rlog_pool_size'] = 'N/A'

        # =============================================
        # 18. 对象统计 Top20 (object_top) - P1
        # =============================================
        table_size_top20 = []
        try:
            cur.execute("""
                SELECT * FROM (
                    SELECT OWNER, TABLE_NAME, NUM_ROWS,
                           (SELECT ROUND(SUM(USED_SIZE * PAGE) / 1024 / 1024, 2)
                            FROM V$TABLESPACE WHERE NAME = t.TABLESPACE_NAME) as SIZE_MB
                    FROM DBA_TABLES t
                    WHERE OWNER NOT IN ('SYS', 'SYSTEM', 'SYSDBA', 'SYSAUDITOR', 'SYSSSO')
                    ORDER BY NUM_ROWS DESC
                ) WHERE ROWNUM <= 20
            """)
            for row in cur.fetchall():
                table_size_top20.append({
                    "owner": row[0],
                    "table_name": row[1],
                    "num_rows": int(row[2] or 0),
                    "size_mb": float(row[3] or 0)
                })
        except Exception:
            pass

        # =============================================
        # 19. 数据字典统计 (data_dict) - P1
        # =============================================
        object_summary = {}
        try:
            cur.execute("""
                SELECT COUNT(*) FROM DBA_TABLES
                WHERE OWNER NOT IN ('SYS','SYSTEM','SYSDBA','SYSAUDITOR','SYSSSO')
            """)
            object_summary['table_count'] = int(cur.fetchone()[0] or 0)
        except Exception:
            object_summary['table_count'] = 0
        try:
            cur.execute("""
                SELECT COUNT(*) FROM DBA_INDEXES
                WHERE OWNER NOT IN ('SYS','SYSTEM','SYSDBA','SYSAUDITOR','SYSSSO')
            """)
            object_summary['index_count'] = int(cur.fetchone()[0] or 0)
        except Exception:
            object_summary['index_count'] = 0
        try:
            cur.execute("""
                SELECT COUNT(*) FROM DBA_VIEWS
                WHERE OWNER NOT IN ('SYS','SYSTEM','SYSDBA','SYSAUDITOR','SYSSSO')
            """)
            object_summary['view_count'] = int(cur.fetchone()[0] or 0)
        except Exception:
            object_summary['view_count'] = 0
        try:
            cur.execute("""
                SELECT COUNT(*) FROM DBA_PROCEDURES
                WHERE OWNER NOT IN ('SYS','SYSTEM','SYSDBA','SYSAUDITOR','SYSSSO')
            """)
            object_summary['procedure_count'] = int(cur.fetchone()[0] or 0)
        except Exception:
            object_summary['procedure_count'] = 0
        try:
            cur.execute("""
                SELECT COUNT(*) FROM DBA_TRIGGERS
                WHERE OWNER NOT IN ('SYS','SYSTEM','SYSDBA','SYSAUDITOR','SYSSSO')
            """)
            object_summary['trigger_count'] = int(cur.fetchone()[0] or 0)
        except Exception:
            object_summary['trigger_count'] = 0

        # =============================================
        # 20. 会话按状态统计 (session_by_state) - P1
        # =============================================
        session_by_state = []
        try:
            cur.execute("SELECT STATE, COUNT(*) FROM V$SESSIONS GROUP BY STATE")
            for row in cur.fetchall():
                session_by_state.append({
                    "state": row[0] or 'N/A',
                    "count": int(row[1] or 0)
                })
        except Exception:
            pass

        # =============================================
        # 21. 高可用状态汇总 (ha_status) - P2
        # =============================================
        ha_status = {
            "instance_mode": dm_instance_mode,
            "database_mode": dm_database_mode,
            "dw_replication_health": dw_replication_health,
            "dsc_cluster_health": dsc_cluster_health,
            "arch_mode": arch_mode,
            "apply_delay_total": apply_delay_total,
        }

        # =============================================
        # 22. 配置参数扩展 (config_extended) - P1
        # =============================================
        extended_config_keys = [
            'BUFFER', 'SORT_BUF_SIZE', 'MLOG_BUF_SIZE', 'MAX_SESSIONS',
            'MAX_TRX', 'MAX_OS_FILE_SIZE', 'ENABLE_MONITOR',
            'SVR_LOAD_BALANCE', 'DICT_BUF_SIZE', 'HJ_BUF_SIZE',
            'HAGR_BUF_SIZE', 'MAX_EPOLL_NUM'
        ]
        for key in extended_config_keys:
            if key not in config_params:
                try:
                    cur.execute(f"SELECT VALUE FROM V$PARAMETER WHERE NAME='{key}'")
                    config_params[key] = cur.fetchone()[0]
                except Exception:
                    pass

        cur.close()

        return {
            # 基础信息
            "version": str(version)[:50] + "...",
            "instance_name": instance_name,
            "host_name": host_name,
            "startup_time": startup_time,
            "db_mode": db_mode,
            "arch_mode": arch_mode,
            "uptime_seconds": uptime,

            # 连接会话
            "active_connections": active_sessions,
            "total_connections": total_sessions,
            "max_connections": max_sessions,
            "conn_usage_pct": conn_usage_pct,
            "session_wait_count": session_wait_count,
            # 前端兼容字段
            "active_sessions": active_sessions,
            "total_sessions": total_sessions,
            "max_sessions": max_sessions,

            # 空间
            "tablespaces": tablespaces,
            "temp_tablespaces": temp_tablespaces,
            "datafile_count": datafile_count,
            "datafile_size_total_gb": datafile_size_total_gb,

            # 性能
            "qps": qps,
            "tps": tps,
            "buffer_size_mb": buffer_size_mb,
            "cache_hit_ratio": cache_hit_ratio,
            # 前端兼容字段
            "buffer_hit_ratio": cache_hit_ratio,
            "sql_count": sql_count,
            "tran_count": tran_count,

            # 等待事件
            "locks": lock_list,
            "lock_wait_count": lock_wait_count,
            "wait_events": wait_events,

            # 会话详情
            "session_list": session_list,

            # SQL统计
            "slow_queries": slow_queries,
            "top_sql": top_sql,

            # 缓冲池
            "buffer_pools": buffer_pools,

            # 事务统计
            "active_transactions": active_transactions,
            "idle_transactions": idle_transactions,

            # 复制集群
            "archive_mode": archive_mode,
            "archive_dest": archive_dest,
            "archive_file_count": archive_file_count,

            # DW 集群主备模式 (新增)
            "dm_instance_mode": dm_instance_mode,
            "dm_database_mode": dm_database_mode,
            "realtime_archive_dest": realtime_archive_dest,
            "rlog_sync_status": rlog_sync_status,
            "dest_pending": dest_pending,
            "apply_delay_total": apply_delay_total,
            "dw_replication_health": dw_replication_health,
            "dw_replication_issues": dw_replication_issues,

            # DSC 集群共享存储模式 (新增)
            "dsc_cluster_info": dsc_cluster_info,
            "dsc_node_count": dsc_node_count,
            "dsc_primary_node": dsc_primary_node,
            "dsc_instances": dsc_instances,
            "dsc_global_latches": dsc_global_latches,
            "dsc_lock_contention_count": dsc_lock_contention_count,
            "dsc_cluster_health": dsc_cluster_health,
            "dsc_cluster_issues": dsc_cluster_issues,

            # 配置参数
            "config_params": config_params,

            # 日志
            "log_count": log_count,
            "log_size": log_size,

            # 安全审计
            "login_count": login_count,
            "failed_logins": failed_logins,

            # 资源限制
            "resource_limits": resource_limits,

            # 长时间事务
            "long_transactions": long_transactions,

            # 死锁记录
            "last_deadlock": last_deadlock,

            # Redo 日志统计
            "redo_log_stats": redo_log_stats,

            # 对象统计 Top20
            "table_size_top20": table_size_top20,

            # 数据字典统计
            "object_summary": object_summary,

            # 会话按状态统计
            "session_by_state": session_by_state,

            # 高可用状态汇总
            "ha_status": ha_status,
        }
