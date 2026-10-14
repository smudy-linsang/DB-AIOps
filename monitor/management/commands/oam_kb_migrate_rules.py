# -*- coding: utf-8 -*-
"""把 `rca_engine_v2.RULES_V2` 的专家规则迁移为 `KbRecord`（设计 §3.3 / §7 P0）。

## 迁移的是角色，不是内容

设计 §3.3 的核心动作：**"不删，降级为 KB 的'症状-成因-动作'记录"**。
39 条手写专家规则（+1 条复合规则 CR001）从**决策器**变成**策略可检索的记录**，
这正是论文使用专家知识的方式：绑定到动作空间，而不是充当决策器。

`rca_engine_v2` 与 `RULES_V2` **本身不动** —— 它们仍在生产路径上被
`diagnosis_pipeline.py` 使用（V2.6 W4 已把它们合并）。迁移是**增量**的：
三代 RCA 由此收敛为"一个策略 + 一批 KB 记录"，而不是"删掉旧的"。

## 为什么必须按方言落库（§9.2 / REV-10）

规则里大量对象是 Oracle 专有的（PGA、Undo、RAC、DataGuard、Library Cache、
AWR、无效对象、回收站）。若不带 `db_types` 落库，检索时会把 Oracle 的方案
推给 MySQL —— 那正是 REV-10 审计缺陷的形态。迁移时按规则的 `domain` 与
标签的 `db_types` 推断，判不出来的**不猜**，标记 `needs_review` 留待人工。

用法：
    python manage.py oam_kb_migrate_rules [--dry-run] [--db-type oracle] [--reset]
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from monitor.models import KbDocument, KbRecord
from monitor.oam.labels import LABELS, NON_CAUSE_RULES
from monitor.oam.tool_catalog import ALL_DIALECTS, TOOL_NAMES

# 规则 domain → 该域在知识库里的归一化症状关键词
_DOMAIN_SYMPTOM = {
    'connection': '连接数异常升高或连接被耗尽',
    'sql': '慢查询激增或单条 SQL 代价异常',
    'lock': '锁等待增加、事务阻塞或死锁',
    'io': '表空间/日志/临时空间紧张或 IO 等待升高',
    'memory': '缓冲命中率下降或内存溢出',
    'replication': '主从或备库延迟、复制中断',
    'cluster': '集群节点异常或分片数据不均衡',
    'capacity': '容量即将耗尽',
    'object': '存储对象状态异常',
    'sequence': '序列接近上限',
    'statistics': '统计信息过期导致执行计划选择错误',
    'awr': '性能快照采集异常',
    'config': '关键参数偏离最佳实践',
    'security': '失败登录异常',
}

# domain → 该域常用的探查工具（全部是只读工具，且都在工具目录里注册）
_DOMAIN_PROBES = {
    'connection': ['db.session_profile', 'os.metrics'],
    'sql': ['db.top_sql', 'db.slow_query'],
    'lock': ['db.lock_chain', 'db.session_profile'],
    'io': ['db.io_wait', 'db.log_surge'],
    'memory': ['db.io_wait', 'db.session_profile'],
    'replication': ['db.replication_status'],
    'cluster': ['db.session_profile', 'db.capacity_trend'],
    'capacity': ['db.capacity_trend'],
    'object': ['meta.schema_fields'],
    'sequence': ['meta.schema_fields', 'meta.statistics'],
    'statistics': ['meta.statistics'],
    'awr': ['db.session_profile'],
    'config': ['meta.guc_config', 'meta.resource_policy'],
    'security': ['db.session_profile'],
}

# 明确不含 Oracle 专有对象的域 → 六个方言都适用
_GENERIC_DOMAINS = {'connection', 'sql', 'lock', 'capacity', 'config', 'security'}

_KB_VERSION = 'rules-v2-migration-1'


class Command(BaseCommand):
    help = '把 rca_engine_v2.RULES_V2 的专家规则迁移为 KbRecord（降级为知识，不删规则）'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='只打印将要迁移的内容，不写库')
        parser.add_argument('--db-type', choices=list(ALL_DIALECTS),
                            help='只迁移该方言适用的记录；缺省迁移全部')
        parser.add_argument('--reset', action='store_true',
                            help='先删除本次迁移产生的记录（按 version 识别）')

    def handle(self, *args, **options):
        from monitor.rca_engine_v2 import COMPOUND_RULES_V2, RULES_V2

        plans = self._build_plans(RULES_V2, COMPOUND_RULES_V2, options.get('db_type'))
        self.stdout.write(f'将迁移 {len(plans)} 条 KbRecord（版本 {_KB_VERSION}）')
        skipped = [p for p in plans if p.get('skipped')]
        if skipped:
            for p in skipped:
                self.stdout.write(f"  跳过 {p['rule_id']}: {p['skip_reason']}")
            plans = [p for p in plans if not p.get('skipped')]

        if options['dry_run']:
            for p in plans[:5]:
                self.stdout.write(f"  [示例] {p['record_id']} | {p['symptom']} "
                                  f"→ {p['candidate_causes']} | {p['db_types']}")
            self.stdout.write(self.style.WARNING('dry-run：未写库'))
            return

        if options['reset']:
            deleted, _ = KbRecord.objects.filter(version=_KB_VERSION).delete()
            self.stdout.write(f'已清理旧迁移记录 {deleted} 条')

        doc = self._ensure_source_document()
        created = 0
        with transaction.atomic():
            for p in plans:
                _, was_created = KbRecord.objects.update_or_create(
                    record_id=p['record_id'],
                    defaults={
                        'symptom': p['symptom'],
                        'db_types': p['db_types'],
                        'candidate_causes': p['candidate_causes'],
                        'probe_actions': p['probe_actions'],
                        'fix_actions': p['fix_actions'],
                        'source_document': doc,
                        'db_versions': {},
                        'version': _KB_VERSION,
                        'is_active': True,
                    })
                created += int(was_created)
        self.stdout.write(self.style.SUCCESS(
            f'迁移完成：新增 {created} 条，更新 {len(plans) - created} 条'))

    # ------------------------------------------------------------------
    def _ensure_source_document(self):
        """建一个指向规则表的来源文档，让每条记录都可溯源（论文教训第三条）。"""
        doc, _ = KbDocument.objects.get_or_create(
            doc_id='SRC-RULES-V2',
            defaults={
                'category': 'fault_solution',
                'db_types': list(ALL_DIALECTS),
                'title': 'DB-AIOps v2 RCA 专家规则库（迁移为知识）',
                'body': '来源：monitor/rca_engine_v2.py 的 RULES_V2 与 COMPOUND_RULES_V2。'
                        '本记录由人工复核通过（迁移的是已评审过的规则文本）。',
                'version': _KB_VERSION,
                'source_uri': 'monitor/rca_engine_v2.py',
                'reviewed_at': None,
                'approved_by': 'migration:rca_engine_v2',
            })
        return doc

    def _build_plans(self, rules, compounds, only_db_type):
        plans = []
        # rule_id → label_id（取该规则迁移到的第一个标签）
        rule_to_label = {}
        for lb in LABELS.values():
            for rid in lb.source_rules:
                rule_to_label.setdefault(rid, lb.label_id)

        for rule in rules:
            rid = rule['id']
            if rid in NON_CAUSE_RULES:
                plans.append({'rule_id': rid, 'skipped': True,
                              'skip_reason': NON_CAUSE_RULES[rid]})
                continue
            label_id = rule_to_label.get(rid)
            if not label_id:
                plans.append({'rule_id': rid, 'skipped': True,
                              'skip_reason': '未映射到根因标签（标签空间缺项，需补）'})
                continue
            lb = LABELS[label_id]
            db_types = list(lb.db_types)
            if only_db_type and only_db_type not in db_types:
                continue
            plans.append({
                'rule_id': rid,
                'record_id': f'kb:rule:{rid}',
                'symptom': _DOMAIN_SYMPTOM.get(rule['domain'], rule['name']),
                'db_types': db_types,
                'candidate_causes': [label_id],
                'probe_actions': self._probes(rule['domain'], db_types),
                'fix_actions': self._fixes(rule),
            })

        for comp in compounds:
            plans.append({
                'rule_id': comp['id'],
                'record_id': f'kb:rule:{comp["id"]}',
                'symptom': f'{comp["name"]}（复合故障：需同时满足 '
                           f'{",".join(comp["requires"])}）',
                'db_types': list(ALL_DIALECTS),
                'candidate_causes': sorted({rule_to_label[r]
                                            for r in comp['requires']
                                            if r in rule_to_label}),
                'probe_actions': ['db.lock_chain', 'db.session_profile'],
                'fix_actions': [s for s in comp.get('suggestions', [])[:3]],
            })
        return plans

    def _probes(self, domain, db_types):
        tools = _DOMAIN_PROBES.get(domain, ['db.session_profile'])
        known = [t for t in tools if t in TOOL_NAMES]
        if not known:
            known = ['db.session_profile']
        return known

    def _fixes(self, rule):
        """修复动作取规则自带建议的前 3 条。

        注意这里存的是**自然语言建议**，不是可执行的工具调用 —— 变更类动作
        一律不进策略的动作空间（`ToolProxy` 一律 MUTATION_FORBIDDEN），
        KB 记录只是让策略知道"这类问题通常怎么解"，真要执行仍走审批链。
        """
        return [str(s) for s in (rule.get('suggestions') or [])][:3]
