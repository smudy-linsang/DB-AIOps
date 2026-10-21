# -*- coding: utf-8 -*-
"""L3 工具目录：统一函数调用范式 + **按方言分别生成的语义增强描述**。

## 为什么描述必须按方言各生成一份

论文的做法（§4.3）：原始数据源暴露的是面向模式的输出（如字段名
`delta_tup_fetched`），缺乏语言上下文；做法是让 LLM 读手册/视图模式的
形式化规格 + KB 的事实与配置笔记，**合成一段供策略使用的自然语言描述**
（用途、接口、字段含义），离线一次性生成、入库并版本化。

**本项目相对论文最大的增量难点是六方言**（§9.2）：同一个逻辑工具，在
Oracle 要查 `V$`/`DBA_` 视图、在 MySQL 要查 `information_schema`/
`performance_schema`、在 PG 要查 `pg_catalog`/`pg_stat_*`。
只写一份通用描述 = 把 Oracle 的东西讲给 MySQL 用。

> 这不是理论风险。**REV-10 审计缺陷就是这个坑的实例**：把 Oracle 专有等待事件
> `enq: TX - row lock contention` 发给了 MySQL/PG 事故。所以本文件里
> 每个工具**每个方言各有一份描述**，且方言差异写进描述正文而不是注释里。

## 安全约束（论文 §5.5 + 本项目既有资产）

- 工具一律**只读**：目标库连接必须走 `DbConnector.get_connection(..., readonly=True)`。
- `mutating=True` 的工具**注册但不放开**：变更类操作不由智能体直接执行，
  `ToolProxy` 一律返回 `MUTATION_FORBIDDEN`，改走风险标签 + 人工审批链
  （既有 `approval_engine` / `autonomy_policy` / `playbook_authz`）。
- 全部**无自由 SQL 参数**：参数只允许枚举/数值/短字符串，见 `params_schema`。
"""
from dataclasses import dataclass, field
from typing import Callable

# 工具种类（对齐 OamToolSpec.kind 与论文的 1 + 1 + 7 + 4 编制）
KIND_KB = 'kb'          # 知识检索：单接口，相似度搜索 top-5
KIND_OS = 'os'          # OS 级指标：CPU/内存/磁盘/网络
KIND_DB = 'db'          # DB 级工作负载指标
KIND_META = 'meta'      # 元数据或配置
KIND_FIX = 'fix'        # 修复类（全部 mutating，策略不得直接调用）

ALL_DIALECTS = ('oracle', 'mysql', 'pgsql', 'dm', 'gbase', 'tdsql')


@dataclass(frozen=True)
class ToolSpec:
    """一个工具在某方言下的规格。

    `fn` 为回放环境之外的真实实现；回放环境（`env/replay.py`）不使用它，
    而是从冻结快照取数 —— 这样评测台与生产走同一套参数契约。
    """

    name: str
    kind: str
    db_type: str                      # '' 表示通用
    params_schema: dict = field(default_factory=dict)
    enriched_description: str = ''
    mutating: bool = False
    replayable: bool = True
    fn: Callable | None = None

    @property
    def key(self):
        return (self.name, self.db_type)

    def desc_stub(self) -> str:
        """描述首句（不含方言取数说明），供不需要完整增强描述的场景。"""
        return self.enriched_description.split('【')[0].strip()

    def validate(self, params: dict) -> tuple:
        """返回 (ok, reason)。只校验枚举与数值域，不接受自由 SQL。"""
        props = self.params_schema.get('properties', {})
        required = self.params_schema.get('required', [])
        if not isinstance(params, dict):
            return False, f'{self.name} 的参数必须是对象'
        for key in required:
            if params.get(key) in (None, ''):
                return False, f'缺少必填参数 {key}'
        for key, value in params.items():
            spec = props.get(key)
            if spec is None:
                return False, f'参数 {key} 不在 {self.name} 的契约内（禁止自由参数）'
            typ = spec.get('type')
            if typ == 'enum' and value not in spec.get('values', []):
                return False, f'参数 {key}={value!r} 不在允许值 {spec.get("values")} 内'
            if typ == 'integer':
                if not isinstance(value, int) or isinstance(value, bool):
                    return False, f'参数 {key} 必须是整数'
                if 'min' in spec and value < spec['min']:
                    return False, f'参数 {key}={value} 小于下限 {spec["min"]}'
                if 'max' in spec and value > spec['max']:
                    return False, f'参数 {key}={value} 大于上限 {spec["max"]}'
            if typ == 'string':
                if not isinstance(value, str):
                    return False, f'参数 {key} 必须是字符串'
                if len(value) > spec.get('maxLength', 500):
                    return False, f'参数 {key} 超过 {spec.get("maxLength")} 字上限'
        return True, ''


# ==========================================================================
# 基础规格：kind / 参数契约 / 通用描述 / 方言差异说明
# ==========================================================================
# 每个方言一句"这个工具在**这个库**上到底查什么"。这是语义增强的核心价值，
# 也是 REV-10 的防线 —— 策略读到的就是这段文字。
_DIALECT_NOTES = {
    'db.session_profile': {
        'oracle': '查 V$SESSION / V$SESSIONS_ACTIVITY 的活动会话与等待类、'
                  '并按 DBID 区分 RAC 节点；等待事件形如 enq: TX - row lock contention。',
        'mysql': '查 information_schema.PROCESSLIST 与 performance_schema.events_waits_summary_global_event 汇总；'
                 '等待以 event_name 呈现（如 lock wait）。'
                 '注意：MySQL 的等待事件体系与 Oracle 不通用，切勿套用其他方言的等待事件名。',
        'pgsql': '查 pg_stat_activity 与 pg_locks；等待态由 wait_event_type/wait_event 给出'
                 '（Lock、IO、LWLock），并可用 pg_stat_activity 判定 idle in transaction。',
        'dm': '达梦兼容 Oracle 的 V$ 视图命名，但等待事件集合不同；'
              '取不到专有等待事件时应如实返回不支持，不得套用 Oracle 事件名。',
        'gbase': 'GBase 8a 取自 information_schema 与集群节点指标；'
                 '分片间会话需按分片(shard)维度聚合。',
        'tdsql': 'TDSQL（proxy + set 架构）经 /*sets:allsets*/ 透传按 set 取 processlist / innodb_trx，'
                 '结果带 info 列标明 set；不带透传时 proxy 只路由到某一个 set，看到的会话不完整。'
                 '网关层会话用 /*proxy*/show processlist（小写）。',
    },
    'db.top_sql': {
        'oracle': '基于 V$SQL / DBA_HIST_SQLSTAT，按 elapsed_time 排序并带 plan_hash_value。',
        'mysql': '基于 performance_schema.events_statements_summary_by_digest，'
                 '按 SUM_TIMER_WAIT 排序并带 DIGEST。',
        'pgsql': '基于 pg_stat_statements，按 total_exec_time 排序并带 queryid。',
        'dm': '达梦取 V$SQL 系列；digest 体系与 Oracle 不完全一致，跨库对比时需注明。',
        'gbase': 'GBase 取采集器上报的 SQL 摘要；分片间需合并后排序。',
        'tdsql': 'TDSQL 经 /*sets:allsets*/ 透传按 set 取 events_statements_summary_by_digest，'
                 '先按 set 差分再跨 set 求和（平台 sql_stat 已按 set_name 分行存储）；'
                 '透传不可用时数据只来自一个 set，必须标注不可信。',
    },
    'db.lock_chain': {
        'oracle': '由 V$SESSION 的 blocking_session 关系构建阻塞链，'
                  '典型为 enq: TX 行锁竞争；RAC 下需区分节点间（gc buffer busy）。',
        'mysql': '由 information_schema.INNODB_TRX 与 innodb_lock_waits 构建；'
                 '等待以事务与锁对象（表/行/间隙锁）标识，'
                 '不得套用其他方言的等待事件名。',
        'pgsql': '由 pg_locks 与 pg_stat_activity 构建；'
                 '区分 heavyweight lock 与 tuple lock，长事务是常见根因。',
        'dm': '达梦锁机制与 Oracle 接近但视图不同，取不到时应返回不支持。',
        'gbase': 'GBase 分布式下的行锁需跨节点归集，链可能不完整，结论置信度须下调。',
        'tdsql': 'TDSQL 的行锁在各 set 内部，经透传按 set 取 data_lock_waits；'
                 '跨 set 的分布式事务锁关系在 SQL 层不可见，缺失时如实标注。',
    },
    'db.io_wait': {
        'oracle': '基于 V$SYSSTAT 的 redo/log file sync、physical read 等待，'
                  '以及 DBA_DATA_FILES 的 IOPS 统计。',
        'mysql': '基于 performance_schema 的 file_summary_by_instance 与 innodb_io_stats。',
        'pgsql': '基于 pg_stat_database 的 blks_read/blks_hit 与 pg_stat_bgwriter。',
        'dm': '达梦取 V$ 视图的 IO 统计，口径与 Oracle 有差异，跨库对比须注明。',
        'gbase': 'GBase 取存储层与节点 IO 指标。',
        'tdsql': 'TDSQL 的 IO 指标按 set 主节点分别取（透传 SHOW GLOBAL STATUS 的 Innodb_data_*），不得只看一个 set。',
    },
    'db.replication_status': {
        'oracle': 'DataGuard：V$DATAGUARD_STATS 的 apply lag / transport lag，'
                  '以及 V$ARCHIVED_LOG 的归档缺口。',
        'mysql': '从库以 SHOW SLAVE STATUS / performance_schema.replication_* 的 '
                 'Seconds_Behind_Master 与 IO 线程状态为准。',
        'pgsql': 'pg_stat_replication 的 write_lag/flush_lag/replay_lag 与 pg_stat_wal_receiver。',
        'dm': '达梦备库状态取其自有的守护进程视图。',
        'gbase': 'GBase 主从以复制延迟指标为准。',
        'tdsql': 'TDSQL 以 /*proxy*/show routes 的各 set 主备与状态为准；'
                 '区分 proxy 网关层与 set 内主备复制两层。',
    },
    'db.capacity_trend': {
        'oracle': '基于 DBA_DATA_FILES / DBA_SEGMENTS 与历史增长外推，含表空间与数据文件。',
        'mysql': '基于 information_schema.TABLES 的 data_length/index_length 与 ibd 文件。',
        'pgsql': '基于 pg_database_size 与 pg_total_relation_size。',
        'dm': '达梦取表空间与数据文件使用率。',
        'gbase': 'GBase 取表空间使用率（分片库需按分片归集）。',
        'tdsql': 'TDSQL 的容量按 set 看：分表数据按 shardkey 哈希分布到各 set，单表只在第一个 set，'
                 '广播表每个 set 全量；任一 set 写满即影响全局。',
    },
    'meta.guc_config': {
        'oracle': '查 V$PARAMETER / SPFILE 的参数当前值，'
                  '参数命名如 db_block_size、sga_target、open_cursors。',
        'mysql': '查 @@GLOBAL / @@SESSION 变量，'
                 '命名为 innodb_buffer_pool_size、sort_buffer_size、max_connections。',
        'pgsql': '查 pg_settings，命名为 work_mem、shared_buffers、max_connections。',
        'dm': '达梦参数命名对齐 Oracle 的子集，但部分参数不生效，须以实际值为准。',
        'gbase': 'GBase 参数体系接近 MySQL 系。',
        'tdsql': 'TDSQL 的参数分 proxy 网关（/*proxy*/show config）与 set 内 MySQL（SHOW VARIABLES）两层，必须标明是哪一层。',
    },
    'meta.statistics': {
        'oracle': '查 DBA_TAB_STATISTICS / DBA_TAB_COL_STATISTICS 的 last_analyzed 与 stale 标记；'
                  'statistics_level 决定自动收集策略。',
        'mysql': '查 information_schema.TABLES 的 TABLE_ROWS/UPDATE_TIME 与 '
                 'innodb_stats_persistent（持久化统计信息）。',
        'pgsql': '查 pg_statistic 与 pg_class 的 reltuples/reltuples，'
                 '以及 pg_stats.last_analyzed 与 autovacuum 配置。',
        'dm': '达梦取其统计信息管理视图。',
        'gbase': 'GBase 取统计信息收集状态。',
        'tdsql': 'TDSQL 统计信息在各 set 内独立收集，set 间数据量不同会导致估算差异。',
    },
    'meta.schema_fields': {
        'oracle': '查 ALL_TAB_COLUMNS / ALL_INDEXES / ALL_IND_COLUMNS。',
        'mysql': '查 information_schema.COLUMNS / STATISTICS。',
        'pgsql': '查 information_schema.columns 与 pg_indexes。',
        'dm': '达梦取 ALL_TAB_COLUMNS 等兼容视图。',
        'gbase': 'GBase 取 information_schema。',
        'tdsql': 'TDSQL 分布式实例用 /*proxy*/show shardkey 取各表 shardkey（noshardkey_allset 为广播表）；'
                 'SQL 不带 shardkey 会路由到全部 set。',
    },
    'meta.resource_policy': {
        'oracle': '查资源计划（DBA_RESOURCE_PLANS / WLM），'
                  '注意 RESOURCE_PLAN 与 RESOURCE_PLAN_CPU 可能是 NULL。',
        'mysql': '查资源组与线程池（information_schema.USER_RESOURCES 等）。',
        'pgsql': '查 pg_cgroup / 扩展的 WLM 配置；PGXC_WLM_SESSION_INFO 只存在于 DWS / GaussDB 等 PG-XC 系产品。',
        'dm': '达梦取其资源管理配置。',
        'gbase': 'GBase 取资源与线程池配置。',
        'tdsql': 'TDSQL MySQL 版没有 PGXC_* 视图；资源限制看各 set 的 max_connections、'
                 'thread pool 配置与 proxy 的连接限制（/*proxy*/show config）。',
    },
    'os.metrics': {
        'oracle': 'Oracle 主机侧指标，与数据库实例指标配对解读'
                  '（例如进程数高 + 连接数高提示实例与主机资源同时吃紧）。',
        'mysql': '同左，MySQL 主机指标；需结合 innodb 线程池与主机线程数判断。',
        'pgsql': '同左，PG 主机指标；需结合 shared_buffers 与主机内存判断。',
        'dm': '同左，达梦主机指标。',
        'gbase': '同左，GBase 主机指标，分片部署下按节点归集。',
        'tdsql': 'TDSQL 主机指标须区分 proxy 网关主机与各 set 的 DB 主机两组。',
    },
    'kb.search': {
        'oracle': '检索知识库时按 Oracle 方言过滤，避免召回 MySQL/PG 的方案。',
        'mysql': '检索知识库时按 MySQL 方言过滤。',
        'pgsql': '检索知识库时按 PostgreSQL/DWS 方言过滤。',
        'dm': '检索知识库时按达梦方言过滤；达梦兼容 Oracle 但参数名与等待事件不同。',
        'gbase': '检索知识库时按 GBase 方言过滤。',
        'tdsql': '检索知识库时按 TDSQL 方言过滤；分布式专属根因（广播查询、跨 set JOIN、set 倾斜）仅在此适用。',
    },
    'db.slow_query': {
        'oracle': '基于 DBA_HIST_SQLSTAT 的 elapsed_time 与执行次数。',
        'mysql': '基于慢查询日志与 performance_schema。',
        'pgsql': '基于 pg_stat_statements 与 auto_explain。',
        'dm': '达梦取慢 SQL 视图。',
        'gbase': 'GBase 取慢 SQL 采集结果。',
        'tdsql': 'TDSQL 慢 SQL 来自按 set 采集的 digest 增量（sql_stat，含热点 set 与倾斜度）；'
                 '分布式实例同时检查是否带 shardkey。',
    },
    'db.log_surge': {
        'oracle': '基于 V$LOGHIST 的 redo 生成速率与归档切换频次。',
        'mysql': '基于 binlog 文件增长速率。',
        'pgsql': '基于 pg_stat_database 的 wal_bytes 与 WAL 段切换。',
        'dm': '达梦取归档与日志切换视图。',
        'gbase': 'GBase 取日志增长指标。',
        'tdsql': 'TDSQL 的 binlog 与 redo 都在各 set 内部，按 set 分别看；proxy 不产生日志量。',
    },
}

_BASE_TOOLS = (
    # ---- 1 个知识检索（论文：单接口，相似度搜索 top-5）----
    dict(name='kb.search', kind=KIND_KB,
         schema={'type': 'object',
                 'properties': {'query': {'type': 'string', 'maxLength': 500},
                                'category': {'type': 'enum',
                                             'values': ['config_manual', 'fault_solution',
                                                        'best_practice', 'basic_fact']}},
                 'required': ['query']},
         desc='在整理后的知识库中做相似度搜索，返回 top-5 片段。'
              '检索内容已由三步构建管线（分类/切分/抽取）整理为"症状-成因-动作"记录，'
              '不是原始文档片段；结果原样回传记录 ID 与来源文档 ID，'
              '这些 ID 是后续证据审计的比对依据。',
         note_key='kb.search'),
    # ---- 1 个 OS 级指标 ----
    dict(name='os.metrics', kind=KIND_OS,
         schema={'type': 'object',
                 'properties': {'group': {'type': 'enum',
                                          'values': ['cpu', 'memory', 'disk', 'network']},
                                'minutes': {'type': 'integer', 'min': 5, 'max': 1440}},
                 'required': []},
         desc='读取主机的 OS 级指标（CPU / 内存 / 磁盘 / 网络）。'
              '用途：区分"数据库自身问题"与"主机资源瓶颈"，'
              '并为数据库指标异常提供旁证。本工具只读，不做任何变更。',
         note_key='os.metrics'),
    # ---- 7 个 DB 级工作负载工具 ----
    dict(name='db.session_profile', kind=KIND_DB,
         schema={'type': 'object',
                 'properties': {'window_min': {'type': 'integer', 'min': 5, 'max': 120}},
                 'required': []},
         desc='取活动会话画像：等待事件/等待态分布、Top SQL、活跃会话趋势。'
              '用途：定位会话在等什么。',
         note_key='db.session_profile'),
    dict(name='db.top_sql', kind=KIND_DB,
         schema={'type': 'object',
                 'properties': {'limit': {'type': 'integer', 'min': 1, 'max': 20}},
                 'required': []},
         desc='取耗时最长的 SQL 列表（含执行次数、平均时延、计划指纹）。'
              '用途：把"慢"落到具体语句上，进而触发执行计划分析。',
         note_key='db.top_sql'),
    dict(name='db.slow_query', kind=KIND_DB,
         schema={'type': 'object',
                 'properties': {'limit': {'type': 'integer', 'min': 1, 'max': 20}},
                 'required': []},
         desc='取近期慢查询记录（含 SQL 文本截断、耗时、发生时间）。'
              '用途：为查询调优类任务提供证据入口。',
         note_key='db.slow_query'),
    dict(name='db.lock_chain', kind=KIND_DB,
         schema={'type': 'object',
                 'properties': {'window_min': {'type': 'integer', 'min': 5, 'max': 120}},
                 'required': []},
         desc='构建当前阻塞链：阻塞者 → 被阻塞者的有向关系。'
              '用途：判断锁问题是否由长事务引起（决定是治锁还是治事务）。',
         note_key='db.lock_chain'),
    dict(name='db.io_wait', kind=KIND_DB,
         schema={'type': 'object',
                 'properties': {'minutes': {'type': 'integer', 'min': 5, 'max': 1440}},
                 'required': []},
         desc='取 IO 与日志相关的等待与吞吐指标（物理读、redo/binlog 写入、落盘）。'
              '用途：区分"CPU 算不完"与"IO 写不动"。',
         note_key='db.io_wait'),
    dict(name='db.replication_status', kind=KIND_DB,
         schema={'type': 'object', 'properties': {}, 'required': []},
         desc='取主从/备库复制状态（延迟、线程状态、归档缺口）。'
              '用途：确认延迟是否达到不可接受区间。',
         note_key='db.replication_status'),
    dict(name='db.log_surge', kind=KIND_DB,
         schema={'type': 'object',
                 'properties': {'minutes': {'type': 'integer', 'min': 5, 'max': 1440}},
                 'required': []},
         desc='取日志生成速率与切换频次的趋势。'
              '用途：定位计划外批量 DML，以及归档/日志空间紧张。',
         note_key='db.log_surge'),
    # ---- 4 个元数据/配置工具（论文：元数据与参数这类简单结果跳过 MMA）----
    dict(name='meta.schema_fields', kind=KIND_META,
         schema={'type': 'object',
                 'properties': {'table': {'type': 'string', 'maxLength': 128}},
                 'required': []},
         desc='取模式字段与索引定义。用途：确认索引是否覆盖查询谓词、'
              '判断选择率。',
         note_key='meta.schema_fields'),
    dict(name='meta.statistics', kind=KIND_META,
         schema={'type': 'object',
                 'properties': {'table': {'type': 'string', 'maxLength': 128}},
                 'required': []},
         desc='取统计信息的采集时间与新鲜度标记。'
              '用途：基数估计错误的第一嫌疑（统计信息过期）。',
         note_key='meta.statistics'),
    dict(name='meta.guc_config', kind=KIND_META,
         schema={'type': 'object',
                 'properties': {'name': {'type': 'string', 'maxLength': 64}},
                 'required': []},
         desc='取参数当前值。用途：发现参数偏离最佳实践、'
              '以及换算修复规格的单位（如内存类参数的 MB/kB）。',
         note_key='meta.guc_config'),
    dict(name='meta.resource_policy', kind=KIND_META,
         schema={'type': 'object',
                 'properties': {'pool': {'type': 'string', 'maxLength': 64}},
                 'required': []},
         desc='取资源配置（资源组/资源池/WLM/线程池）。'
              '用途：判断排队是否由资源池槽位不足引起。',
         note_key='meta.resource_policy'),
    # ---- 变更类工具：注册但**禁止策略直接调用**（论文 §5.5 的安全闸门）----
    dict(name='fix.kill_session', kind=KIND_FIX,
         schema={'type': 'object',
                 'properties': {'sid': {'type': 'integer', 'min': 0}},
                 'required': ['sid']},
         desc='终止指定会话。**变更类操作**：不经风险标签与人工审批不得执行。',
         mutating=True, replayable=False, note_key=None),
    dict(name='fix.alter_parameter', kind=KIND_FIX,
         schema={'type': 'object',
                 'properties': {'name': {'type': 'string', 'maxLength': 64},
                                'value': {'type': 'string', 'maxLength': 64}},
                 'required': ['name', 'value']},
         desc='修改参数。**变更类操作**：需走审批链并支持回滚。',
         mutating=True, replayable=False, note_key=None),
    dict(name='fix.create_index', kind=KIND_FIX,
         schema={'type': 'object',
                 'properties': {'table': {'type': 'string', 'maxLength': 128},
                                'columns': {'type': 'string', 'maxLength': 256}},
                 'required': ['table', 'columns']},
         desc='建索引。**变更类操作**：会拖慢写入，须评估后再执行。',
         mutating=True, replayable=False, note_key=None),
    dict(name='fix.extend_space', kind=KIND_FIX,
         schema={'type': 'object',
                 'properties': {'tablespace': {'type': 'string', 'maxLength': 128},
                                'size': {'type': 'string', 'maxLength': 32}},
                 'required': ['tablespace', 'size']},
         desc='扩展表空间/数据文件。**变更类操作**：需容量评估与审批。',
         mutating=True, replayable=False, note_key=None),
)


def _build_registry() -> dict:
    """展开为 (name, db_type) → ToolSpec。

    每个工具为**每个方言各出一条**记录，`enriched_description` 拼入该方言的
    取数说明。`db_type=''` 的通用条目保留，用于跨方言工具。
    """
    registry = {}
    for base in _BASE_TOOLS:
        note_key = base.get('note_key')
        for db_type in ALL_DIALECTS:
            desc = base['desc']
            if note_key:
                note = _DIALECT_NOTES.get(note_key, {}).get(db_type)
                if note:
                    desc = f'{desc}【{db_type} 取数说明】{note}'
            spec = ToolSpec(
                name=base['name'], kind=base['kind'], db_type=db_type,
                params_schema=base['schema'], enriched_description=desc,
                mutating=base.get('mutating', False),
                replayable=base.get('replayable', True),
            )
            registry[spec.key] = spec
    return registry


# (name, db_type) → ToolSpec
TOOLS = _build_registry()

# 工具名 → 各方言规格，供人工查阅
BY_NAME = {}
for _spec in TOOLS.values():
    BY_NAME.setdefault(_spec.name, {})[_spec.db_type] = _spec

TOOL_NAMES = tuple(sorted({spec.name for spec in TOOLS.values()}))


def get_tool(name: str, db_type: str) -> ToolSpec | None:
    """取指定方言下的工具规格；不存在返回 None（由调用方转成确定性错误）。"""
    return TOOLS.get((name, db_type)) or TOOLS.get((name, ''))


def tools_for(db_type: str) -> list:
    """该方言下可用的工具清单（不含 mutating 的，变更类不进策略的动作空间）。"""
    out = [spec for spec in TOOLS.values()
           if spec.db_type == db_type and not spec.mutating]
    return sorted(out, key=lambda s: (s.kind, s.name))


def mutating_tools(db_type: str) -> list:
    """该方言下的变更类工具。**仅供 ToolProxy 拦截与人工审批链路使用**。"""
    return sorted((spec for spec in TOOLS.values()
                   if spec.db_type == db_type and spec.mutating),
                  key=lambda s: s.name)


def catalog_for_prompt(db_type: str, include_enriched: bool = True) -> str:
    """渲染给策略的工具目录。变更类**不出现在这里** —— 它们不在策略的动作空间内。"""
    lines = []
    for spec in tools_for(db_type):
        params = spec.params_schema.get('properties', {})
        pdesc = ', '.join(
            f"{k}:{v.get('type')}{'=' + str(v['values']) if v.get('type') == 'enum' else ''}"
            for k, v in sorted(params.items())) or '无'
        body = spec.enriched_description if include_enriched else spec.desc_stub()
        lines.append(f"- {spec.name} [{spec.kind}] 参数({pdesc})\n    {body}")
    return '\n'.join(lines)


__all__ = [
    'ToolSpec', 'TOOLS', 'BY_NAME', 'TOOL_NAMES', 'ALL_DIALECTS',
    'KIND_KB', 'KIND_OS', 'KIND_DB', 'KIND_META', 'KIND_FIX',
    'get_tool', 'tools_for', 'mutating_tools', 'catalog_for_prompt',
]
