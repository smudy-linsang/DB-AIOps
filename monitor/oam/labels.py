# -*- coding: utf-8 -*-
"""根因标签空间（封闭枚举，**按方言分表**）。

设计 §9.1 的判断：*"P0 的第一项工作其实是定义根因标签空间 + 建标注流程，
而不是写代码。标签空间定不下来，验证器就无从判定，整条链都是空转。"*
本文件就是那件事。

## 为什么必须按方言分表（§9.2）

论文只面向 DWS（PostgreSQL 系），单方言。本项目纳管 6 个方言，于是
"同一症状在不同方言的根因集合不同"：

- Oracle 的 `enq: TX - row lock contention` 在 MySQL **没有对应物**；
- Oracle 的 PGA / Undo / RAC / DataGuard / Library Cache 在 PostgreSQL 全部不存在；
- 反过来 MySQL 的 `idle in transaction` 在 Oracle 也不成立。

**REV-10 审计缺陷正是这个坑的实例** —— 把 Oracle 专有等待事件发给 MySQL/PG 事故。
V3.0 若不把方言做成一等维度，会在更大的面上重犯。所以：
`is_valid_label('RAC_NODE_EVICTED', 'mysql')` 必须返回 False。

## 标签空间的用途边界（§9.1，非常容易抄错）

封闭标签空间**只用于自动验证与离线评测，不是生产约束**。论文 §3.3 Limitation 原文：
*"In production, the closed label spaces are used only for automatic verification and
offline evaluation. DBAgent is **not forced to choose a known cause** when evidence
is insufficient."*

所以：
- `verify()` 用它判定"对不对"；
- 生产诊断**不**用它过滤候选根因，策略被允许说"证据不足"；
- 标签集**不是**策略的可选答案清单。

## 匹配口径

论文 §3.3：根因匹配是**轻度归一化后的精确字符串相等**（lowercasing、空白与标点清理），
**不是语义相似度**。这正是标签必须是枚举而不能是自由文本的原因。
"""
from dataclasses import dataclass, field

# 本项目实际纳管的 6 个方言（对齐 models.DB_TYPES，redis/mongo 是预留占位，不算）
ALL_DIALECTS = ('oracle', 'mysql', 'pgsql', 'dm', 'gbase', 'tdsql')

# 任务类别（对齐 OamCase.CATEGORY，论文 §5.1.1 的三类 + 我们的日常巡检）
CAT_INSPECTION = 'inspection'
CAT_TUNING = 'tuning'
CAT_OVERLOAD = 'overload'
CAT_CONSULTATION = 'consultation'
ALL_CATEGORIES = (CAT_INSPECTION, CAT_TUNING, CAT_OVERLOAD, CAT_CONSULTATION)


@dataclass(frozen=True)
class RootCauseLabel:
    """一个根因标签。

    `source_rules` 记录它由 `rca_engine_v2.RULES_V2` 的哪些 rule_id 迁移而来
    —— 设计 §3.3 的核心动作是"39 条专家规则降级为 KB 记录"，
    保留这条血缘才能在迁移后追溯"这条知识当初是从哪条规则来的"。
    """

    label_id: str
    name: str
    db_types: tuple = ALL_DIALECTS
    categories: tuple = ALL_CATEGORIES
    evidence_hint: str = ''
    source_rules: tuple = ()

    def valid_for(self, db_type: str) -> bool:
        return db_type in self.db_types


def _L(label_id, name, db_types=ALL_DIALECTS, categories=ALL_CATEGORIES,
       evidence_hint='', source_rules=()):
    return RootCauseLabel(label_id, name, db_types, categories,
                          evidence_hint, source_rules)


# 只有 Oracle 有的概念集合。集中声明，便于"新增 Oracle 标签时顺手想一遍"
# 其他方言到底有没有对应物 —— 这是防 REV-10 复发的第一道闸。
_ORACLE = ('oracle',)
# PG 系的三个：Oracle 同样具备 buffer cache 语义，但统计对象名不同，这里按
# "诊断口径可复用"而非"参数同名"来判定。
_PG_FAMILY = ('oracle', 'pgsql', 'dm', 'tdsql')
# 有分片/分布式的：TDSQL、GBase 8a
_SHARDED = ('tdsql', 'gbase')


# ==========================================================================
# 标签表。顺序按对象域排列，与 RULES_V2 的 domain 对齐，便于迁移时逐条核对。
# ==========================================================================
_LABELS = (
    # ---- 连接域（6 条：R001-R004, R083, R084）----
    _L('CONN_LEAK', '连接数泄漏', evidence_hint='连接使用率高但 QPS 极低、session_by_state 中 idle 占比高',
       source_rules=('R001',)),
    _L('CONN_STORM', '连接风暴', evidence_hint='aborted_connects / 失败登录激增',
       source_rules=('R002',)),
    # idle in transaction 是 PG/MySQL 系的概念，Oracle 无此状态
    _L('IDLE_IN_TXN', '空闲长事务',
       db_types=('mysql', 'pgsql', 'dm', 'gbase', 'tdsql'),
       evidence_hint='idle_in_transaction 数量与持续时长、长事务持有锁快照',
       source_rules=('R003',)),
    _L('CONN_EXHAUSTED', '连接数接近上限', evidence_hint='conn_usage_pct > 90 且伴随连接失败',
       source_rules=('R004',)),
    _L('INSTANCE_STATE_ABNORMAL', '实例状态异常', evidence_hint='实例 DOWN / 主从角色异常',
       source_rules=('R083',)),
    _L('CONN_SOURCE_CONCENTRATION', '连接来源集中度过高',
       evidence_hint='连接集中于少数来源 IP 且使用率偏高', source_rules=('R084',)),

    # ---- SQL 域（5 条：R011-R014, R080）+ 计划域新增 ----
    _L('SLOW_QUERY_SURGE', '慢查询激增', categories=(CAT_TUNING, CAT_OVERLOAD),
       evidence_hint='慢查询 Top 列表与查询耗时分布', source_rules=('R011',)),
    _L('FULL_TABLE_SCAN', '高 cost 全表扫描', categories=(CAT_TUNING,),
       evidence_hint='执行计划中出现全表扫描算子且表行数大', source_rules=('R012', 'R080')),
    _L('HARD_PARSE_STORM', '解析过载', db_types=_ORACLE,
       evidence_hint='硬解析比例与 Library Cache 压力', source_rules=('R013',)),
    _L('LIBRARY_CACHE_MISS', '库缓存命中率突降', db_types=_ORACLE,
       evidence_hint='Library Cache Hit 比率下降', source_rules=('R014',)),
    # 由 L2 计划规则 MISSING_INDEX 直接支撑（论文 §5.2 启发式之二）
    _L('MISSING_INDEX', '大表低选择率扫描缺索引', categories=(CAT_TUNING,),
       evidence_hint='执行计划：大表 + 选择率 < 5% + 顺序扫描',
       source_rules=()),
    # 由 L2 计划规则 CARD_EST_ERROR 直接支撑（论文 §5.2 启发式之一）
    _L('CARDINALITY_MISESTIMATE', '基数估计错误', categories=(CAT_TUNING,),
       evidence_hint='执行计划：估算行数与实际行数相差一个数量级以上',
       source_rules=('R075',)),

    # ---- 锁域（4 条：R021-R024）----
    _L('LOCK_WAIT', '锁等待激增', evidence_hint='阻塞链长度与等待会话数', source_rules=('R021',)),
    _L('HOT_ROW_CONTENTION', '行锁升级为热点行竞争', evidence_hint='同一对象上行锁等待集中',
       source_rules=('R022',)),
    _L('DEADLOCK', '死锁频发', evidence_hint='24h 死锁次数与涉及对象', source_rules=('R023',)),
    _L('TABLE_LOCK_BLOCK', '表级锁阻塞', evidence_hint='表级锁阻塞全部 DML', source_rules=('R024',)),

    # ---- IO / 空间域（7 条：R031-R035, R082, R085）----
    _L('TABLESPACE_NEAR_FULL', '表空间容量不足',
       evidence_hint='表空间使用率 > 90%', source_rules=('R031',)),
    _L('LOG_SWITCH_FREQUENT', '日志切换频率过高', evidence_hint='redo/binlog 切换频次',
       source_rules=('R032',)),
    _L('ARCHIVE_GAP', '主备归档间隙', db_types=_ORACLE,
       evidence_hint='归档目的地缺口，可能丢失数据', source_rules=('R033',)),
    _L('TEMP_TS_EXHAUSTED', '临时表空间撑爆', evidence_hint='临时表空间使用率与落盘会话',
       source_rules=('R034',)),
    _L('UNDO_TS_TIGHT', 'Undo 段紧张', db_types=_ORACLE,
       evidence_hint='Undo 表空间使用率与长事务', source_rules=('R035',)),
    _L('LOG_WRITE_SURGE', '日志生成速率突增',
       evidence_hint='redo/binlog 生成速率与计划外批量 DML', source_rules=('R082',)),
    _L('LOG_WRITE_BOTTLENECK', '日志写入瓶颈', db_types=_ORACLE,
       evidence_hint='log file sync 等待占比高', source_rules=('R085',)),

    # ---- 内存域（2 条：R041, R042）----
    _L('BUFFER_CACHE_LOW', 'Buffer Cache 命中率突降', db_types=_PG_FAMILY,
       evidence_hint='物理读激增、命中率下降', source_rules=('R041',)),
    _L('PGA_EXHAUSTED', 'PGA 溢出', db_types=_ORACLE,
       evidence_hint='PGA 使用率过高导致排序落盘', source_rules=('R042',)),

    # ---- 复制域（3 条：R051-R053）----
    _L('REPLICATION_LAG', '主从/备库延迟严重',
       evidence_hint='延迟秒数与积压字节数', source_rules=('R051',)),
    _L('REPLICATION_BROKEN', '复制中断', evidence_hint='复制线程停止',
       source_rules=('R052',)),
    _L('STANDBY_APPLY_LAG', '备库 Apply 延迟', db_types=_ORACLE,
       evidence_hint='DataGuard Apply Lag', source_rules=('R053',)),

    # ---- 集群域（3 条：R061, R062, R081）----
    _L('CLUSTER_NODE_EVICTED', '集群节点被驱逐', db_types=_ORACLE,
       evidence_hint='节点驱逐事件', source_rules=('R061',)),
    _L('SPLIT_BRAIN_RISK', '脑裂风险', db_types=_ORACLE,
       evidence_hint='集群分裂风险指示', source_rules=('R062',)),
    # 由 L2 计划规则 DATA_SKEW 支撑；分片失衡是 TDSQL/GBase 的典型
    _L('SHARD_SKEW', '分片数据不均衡', db_types=_SHARDED,
       evidence_hint='分片间数据量差异比', source_rules=('R081',)),

    # ---- 容量 / 对象 / 序列 / 统计 / 配置 / 安全 ----
    _L('DISK_WILL_EXHAUST', '容量 7 天内将耗尽', categories=(CAT_OVERLOAD, CAT_INSPECTION),
       evidence_hint='历史增长趋势外推', source_rules=('R071',)),
    _L('INVALID_OBJECT', '存在无效对象', db_types=_ORACLE,
       evidence_hint='对象状态为 INVALID', source_rules=('R072',)),
    _L('RECYCLEBIN_NEAR_FULL', '回收站对象过多', db_types=_ORACLE,
       evidence_hint='回收站对象数与占用空间', source_rules=('R073',)),
    _L('SEQUENCE_NEAR_MAX', '序列接近 MAX', db_types=('oracle', 'pgsql', 'dm'),
       evidence_hint='序列使用率 > 80%', source_rules=('R074',)),
    _L('STATISTICS_STALE', '统计信息过期',
       evidence_hint='统计信息采集时间超期、计划估算与实际行数偏差',
       source_rules=('R075',)),
    _L('SCN_HEADROOM_LOW', 'SCN Headroom 不足', db_types=_ORACLE,
       evidence_hint='SCN Headroom 余量', source_rules=('R077',)),
    _L('CONFIG_DRIFT', '参数偏离最佳实践',
       evidence_hint='关键参数与基线不一致', source_rules=('R078',)),
    _L('AUTH_FAILURE_SPIKE', '失败登录异常', categories=(CAT_OVERLOAD, CAT_INSPECTION),
       evidence_hint='失败登录次数激增', source_rules=('R079',)),
)

# label_id → RootCauseLabel
LABELS = {lb.label_id: lb for lb in _LABELS}


# ==========================================================================
# 查询接口
# ==========================================================================
def labels_for(db_type: str, category: str | None = None) -> tuple:
    """某方言（可选叠加任务类别）的封闭标签集合。"""
    out = [lb for lb in _LABELS if lb.valid_for(db_type)]
    if category:
        out = [lb for lb in out if category in lb.categories]
    return tuple(out)


def is_valid_label(label_id: str, db_type: str) -> bool:
    """标签在该方言下是否合法。

    这是 §9.2 的闸门：Oracle 专有标签对 MySQL 必须返回 False。
    大小写不敏感（策略输出经轻度归一化后再校验）。
    """
    lb = LABELS.get(str(label_id or '').strip().upper())
    return bool(lb and lb.valid_for(db_type))


def assert_label(label_id: str, db_type: str) -> str:
    """返回归一化后的 label_id；非法则抛 ValueError。

    供 `OamCase.gt_root_cause` 保存前校验 —— 真值根因必须在封闭标签空间内
    且属于该方言，否则基准集就是拿造出来的用例自证（设计 §8）。
    """
    norm = str(label_id or '').strip().upper()
    lb = LABELS.get(norm)
    if lb is None:
        raise ValueError(f'未知根因标签 {label_id!r}；封闭标签空间共 {len(LABELS)} 个标签')
    if not lb.valid_for(db_type):
        raise ValueError(
            f'根因标签 {norm}({lb.name}) 不适用于方言 {db_type}；'
            f'该标签仅适用于 {",".join(lb.db_types)}'
        )
    return norm


def label_space_signature(db_type: str) -> str:
    """该方言标签集的稳定指纹，用于评测报告记录"这次跑的是哪套标签"。"""
    import hashlib

    payload = '|'.join(sorted(lb.label_id for lb in labels_for(db_type)))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]


def oracle_only_labels() -> tuple:
    """仅 Oracle 可用的标签 ID —— REV-10 的回归测试直接用这个集合。"""
    return tuple(sorted(lb.label_id for lb in _LABELS if lb.db_types == _ORACLE))


# 明确**不是根因**的专家规则：它们描述的是"诊断覆盖度不足"而非"数据库故障"。
#
# 放在这里显式列出，而不是让它们悄悄缺席 —— 否则 §9.1 的"标签空间完整"就没法自检，
# 下一个人会以为"39 条规则都有标签"而实际上少了一条、且不知道为什么少。
#
#   R076「AWR 快照间隔异常」：快照间隔过长意味着**问题现场没被采到**，
#        这是观测能力问题，不是数据库的故障原因。把它当根因会让策略把
#        "没测到" 讲成 "数据库慢"。
#
# 注意 R077「SCN Headroom 不足」**是**根因（有 ORA-19706 的真实后果），故不在此列。
NON_CAUSE_RULES = {
    'R076': 'AWR 快照间隔过长属观测覆盖度问题，不是数据库故障原因',
}


def migrated_rule_ids() -> tuple:
    """已迁移为标签的 RULES_V2 规则 ID（含显式豁免项）。

    供迁移命令与测试自检：保证 39 条规则**没有一条在迁移中丢标签**，
    也没有哪条被静默遗忘 —— 豁免必须写明理由。
    """
    out = set(NON_CAUSE_RULES)
    for lb in _LABELS:
        out.update(lb.source_rules)
    return tuple(sorted(out))


__all__ = [
    'ALL_DIALECTS', 'ALL_CATEGORIES', 'RootCauseLabel', 'LABELS',
    'CAT_INSPECTION', 'CAT_TUNING', 'CAT_OVERLOAD', 'CAT_CONSULTATION',
    'labels_for', 'is_valid_label', 'assert_label', 'label_space_signature',
    'oracle_only_labels', 'migrated_rule_ids', 'NON_CAUSE_RULES',
]
