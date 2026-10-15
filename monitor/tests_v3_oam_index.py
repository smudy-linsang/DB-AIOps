# -*- coding: utf-8 -*-
"""L3 检索索引（`monitor/oam/kb/index.py`）稀疏化改造的验收测试。

## 为什么要这份测试

索引从"每条 65536 维稠密 float32"改成稀疏倒排之后，**唯一不可让步的验收
条件是检索结果不变**。理由不在性能，在可复现性：设计文档把"同一用例同策略
重跑结果一致"当作硬不变量（OAM 可回放环境的全部意义就在这），而检索结果是
引用审计（`kb_refs` 与 KB 实际内容逐条比对）的输入 —— 排序或分数一变，
审计结论就会在两次重跑之间漂移，而这种漂移事后极难归因。

所以本文件里放了一份**参照用的稠密实现** `_DenseReferenceIndex`，逐字复刻改造
前的算法（`np.vstack([_vectorize(...)]) @ _vectorize(query)` + 同一套过滤与
排序），把它当唯一的裁判：新实现必须在 `record_ids` 顺序、每条 similarity、
`missed` / `miss_reason`、`filtered` 计数上与它完全一致。

## 相似度为什么是 `places=6` 而不是逐位相等

稠密路径的点积走 BLAS sgemv 的 float32 分块累加，稀疏路径走 float64 累加后
转回 float32 —— 两者**累加顺序不同**，逐位相等在数学上就不成立（而且稠密
路径自身的位模式还随 CPU/BLAS 实现变，本就不是逐位稳定的契约）。

实测（12 条固定语料 × 10 个查询 × 10 组过滤）两者最大偏差 **5.96e-08**，
恰好是 0.5 处的**一个 float32 ULP**。`places=6` 的判定门槛是 5e-7，留了约
8 倍余量，因此这里是**有意义的严格断言**，不是为了让它绿而放宽。

## 内存口径

`KbIndex.nbytes` 只统计倒排索引平铺数组的字节数（int64 特征 id + int64
段指针 + int32 文档下标 + float32 权重，每条 posting 8 字节）。稠密等价物
按 `记录数 × _VECTOR_DIM × 4` 算。

全部用例均为 `@tag('unit')`，零外部依赖，不触网不连库。
"""
import numpy as np
from django.test import SimpleTestCase, tag

from monitor.oam.kb.extract import KbRecord, version_in_range
from monitor.oam.kb.index import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_TOP_K,
    MISS_BELOW_THRESHOLD,
    MISS_EMPTY_INDEX,
    MISS_NO_CATEGORY,
    MISS_NO_DIALECT,
    MISS_NO_VERSION,
    KbIndex,
    _VECTOR_DIM,
    _vectorize,
    build_index,
)

# ---------------------------------------------------------------------------
# 参照实现：改造前的稠密检索，逐字复刻
# ---------------------------------------------------------------------------
class _DenseReferenceIndex:
    """**裁判**。稠密矩阵 + 同一套过滤/排序/阈值逻辑。

    这里刻意**不调用** `KbIndex` 的任何方法（包括 `_admissible_mask`）：
    复刻一份独立实现，裁判才有意义，否则"被测对象和裁判一起改错"会得到假绿。

    复刻自改造前的 `monitor/oam/kb/index.py`：
    `np.vstack([_vectorize(_searchable(r)) for r in records])` 建矩阵，
    查询走 `self._matrix[idx] @ q`。
    """

    def __init__(self, records):
        self._records = list(records)
        self._matrix = np.vstack(
            [_vectorize(r.text or r.symptom) for r in self._records])

    def _admissible_mask(self, db_type, db_version, category=''):
        counts = {'needs_review': 0, 'dialect': 0, 'version': 0,
                  'category': 0, 'total': len(self._records)}
        mask = np.zeros(len(self._records), dtype=bool)
        for i, r in enumerate(self._records):
            if r.needs_review:
                counts['needs_review'] += 1
                continue
            if not r.db_types or db_type not in r.db_types:
                counts['dialect'] += 1
                continue
            rng = (r.db_versions or {}).get(db_type)
            if rng and not version_in_range(db_version, rng):
                counts['version'] += 1
                continue
            if category and r.category != category:
                counts['category'] += 1
                continue
            mask[i] = True
        return mask, counts

    def search(self, query, db_type, db_version='', top_k=DEFAULT_TOP_K,
               min_similarity=DEFAULT_MIN_SIMILARITY, category=''):
        """返回可整体比较的快照 dict（顺序/分数/未命中信号/过滤计数）。"""
        snap = {'record_ids': [], 'similarities': [], 'top_similarity': 0.0,
                'missed': True, 'miss_reason': '', 'filtered': {},
                'filtered_ok': False}
        if not self._records:
            snap['miss_reason'] = MISS_EMPTY_INDEX
            return snap

        mask, counts = self._admissible_mask(db_type, db_version, category)
        snap['filtered'] = counts
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            for key, reason in (('category', MISS_NO_CATEGORY),
                                ('version', MISS_NO_VERSION),
                                ('dialect', MISS_NO_DIALECT)):
                if counts.get(key, 0) > 0:
                    snap['miss_reason'] = reason
                    break
            else:
                snap['miss_reason'] = MISS_NO_DIALECT
            snap['filtered_ok'] = True
            return snap

        q = _vectorize(query)
        if not float(np.linalg.norm(q)):
            snap['miss_reason'] = MISS_BELOW_THRESHOLD
            snap['filtered_ok'] = True
            return snap

        sub = self._matrix[idx]
        sims = sub @ q                       # 向量已 L2 归一化 ⇒ 点积即余弦
        order = np.argsort(-sims, kind='stable')
        picked = []
        for oi in order:
            sim = float(sims[oi])
            if len(picked) >= top_k:
                break
            if sim < min_similarity and picked:
                break
            rec = self._records[int(idx[oi])]
            picked.append((rec.record_id_or_key, sim))
        picked.sort(key=lambda h: (-h[1], h[0]))
        picked = picked[:top_k]

        snap['record_ids'] = [p[0] for p in picked]
        snap['similarities'] = [p[1] for p in picked]
        snap['top_similarity'] = picked[0][1] if picked else float(np.max(sims))
        if not picked:
            snap['missed'] = True
            snap['miss_reason'] = MISS_BELOW_THRESHOLD
        else:
            snap['missed'] = snap['top_similarity'] < min_similarity
            snap['miss_reason'] = MISS_BELOW_THRESHOLD if snap['missed'] else ''
        snap['filtered_ok'] = True
        return snap


def _live_snapshot(result):
    """把 `SearchResult` 压成与 `_DenseReferenceIndex.search()` 同形状的快照。"""
    return {
        'record_ids': list(result.record_ids),
        'similarities': [h.similarity for h in result.hits],
        'top_similarity': result.top_similarity,
        'missed': result.missed,
        'miss_reason': result.miss_reason,
        'filtered': dict(result.filtered),
    }


# ---------------------------------------------------------------------------
# 固定语料 / 查询 / 过滤组合（全部硬编码，保证跨进程、跨机器可复现）
# ---------------------------------------------------------------------------
# (record_id, 症状, db_types, db_versions, category)
CORPUS = [
    ('KB-001', 'InnoDB 行锁等待超时 innodb_lock_wait_timeout 升高',
     ['mysql'], {}, 'fault_solution'),
    ('KB-002', 'Oracle 等待事件 enq TX row lock contention 争用',
     ['oracle'], {'oracle': ['11g', '19c']}, 'fault_solution'),
    ('KB-003', '慢查询未走索引导致全表扫描 explain type ALL',
     ['mysql', 'postgresql'], {}, 'best_practice'),
    ('KB-004', '连接池耗尽 connection pool exhausted 大量等待',
     ['mysql', 'postgresql', 'oracle'], {}, 'fault_solution'),
    ('KB-005', '主从复制延迟 replication lag seconds 过大',
     ['mysql'], {'mysql': ['5.7', '8.0']}, 'fault_solution'),
    ('KB-006', 'redo log 刷盘压力 log_flush 耗时升高',
     ['mysql'], {}, 'best_practice'),
    ('KB-007', '达梦数据库锁等待 deadlock 超时统计',
     ['dm'], {}, 'fault_solution'),
    ('KB-008', 'GBase 8a 节点间网络抖动 heartbeat timeout',
     ['gbase'], {}, 'fault_solution'),
    ('KB-009', 'SGA_TARGET 参数配置手册 shared pool 调整',
     ['oracle'], {}, 'config_manual'),
    ('KB-010', '',                                  # 检索文本为空 ⇒ 零向量
     ['tdsql'], {}, 'basic_fact'),
    ('KB-011', '待复核条目 方言判定不可靠 人工确认',  # needs_review ⇒ 永远不进上下文
     [], {}, 'basic_fact'),
    ('KB-012', 'tdsql 分布式事务 2pc 长时间挂起',
     ['tdsql'], {}, 'fault_solution'),
]

# 中文 / 英文 / 纯数字 / 完全无关 / 空串 / 纯停用词
QUERIES = [
    'innodb 行锁等待超时怎么排查',
    'row lock wait timeout innodb',
    '连接池耗尽 connection pool exhausted',
    '慢查询 全表扫描 索引 explain',
    '12345 67890 42',
    '天文观测与地质年代完全不相关',
    'replication lag 复制延迟主从',
    'sga_target shared pool 配置手册',
    '',
    '的死在了',
]

# (db_type, db_version, category) —— 覆盖不过滤 / 只过方言 / 只过版本 / 只过类目 / 全空
FILTER_CASES = [
    ('mysql', '8.0', ''),
    ('mysql', '5.7', ''),
    ('mysql', '5.6', ''),          # 命中 KB-005 的版本区间之外
    ('oracle', '19c', ''),
    ('oracle', '11g', ''),
    ('postgresql', '14', ''),
    ('mysql', '8.0', 'config_manual'),    # 只有 KB-009 是 config_manual，但它是 oracle
    ('oracle', '19c', 'config_manual'),   # 方言 ∩ 类目 恰好一条
    ('mysql', '8.0', 'basic_fact'),       # 无交集 ⇒ 候选清空
    ('dm', '', ''),
    ('tdsql', '', ''),
    ('gbase', '', ''),
    ('unknown_db', '', ''),               # 全部方言不匹配
]


def _make_records():
    """把 `CORPUS` 变成 `KbRecord`（`text` 合成口径与 `records_from_orm` 一致）。"""
    out = []
    for rid, symptom, db_types, db_versions, category in CORPUS:
        text = ' '.join([symptom, '候选成因', '排查动作', '修复动作']).strip()
        out.append(KbRecord(
            record_id=rid, symptom=symptom, db_types=list(db_types),
            candidate_causes=['候选成因'], probe_actions=['排查动作'],
            fix_actions=['修复动作'], source_doc_id=f'DOC-{rid}',
            db_versions=dict(db_versions), category=category, text=text,
            needs_review=not bool(db_types),
        ))
    return out


# ---------------------------------------------------------------------------
# 1. 等价性（最重要）
# ---------------------------------------------------------------------------
@tag('unit')
class DenseEquivalenceTests(SimpleTestCase):
    """稀疏检索必须与稠密参照实现逐条等价。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.records = _make_records()
        cls.index = build_index(cls.records)
        cls.reference = _DenseReferenceIndex(cls.records)

    def _assert_equivalent(self, query, db_type, db_version, category):
        want = self.reference.search(query, db_type, db_version, category=category)
        got = _live_snapshot(
            self.index.search(query, db_type, db_version, category=category))
        ctx = (f'db={db_type} ver={db_version} cat={category} '
               f'query={query!r}')

        # 过滤计数（键集合与值都要一致）
        self.assertEqual(sorted(want['filtered']), sorted(got['filtered']), ctx)
        self.assertEqual(want['filtered'], got['filtered'], ctx)
        # 顺序与 ID
        self.assertEqual(want['record_ids'], got['record_ids'], ctx)
        # 未命中信号
        self.assertEqual(want['missed'], got['missed'], ctx)
        self.assertEqual(want['miss_reason'], got['miss_reason'], ctx)
        # top_similarity
        self.assertAlmostEqual(want['top_similarity'], got['top_similarity'],
                               places=6, msg=ctx)
        # 每条 similarity，places=6（门槛 5e-7，实测最大偏差 5.96e-08）
        self.assertEqual(len(want['similarities']), len(got['similarities']), ctx)
        for i, (w, g) in enumerate(zip(want['similarities'], got['similarities'])):
            self.assertAlmostEqual(w, g, places=6, msg=f'{ctx} hit#{i}')

    def test_matches_dense_reference_all_queries_and_filters(self):
        """12 个查询 × 13 组过滤 = 156 组，逐组全字段比对。"""
        for db_type, db_version, category in FILTER_CASES:
            for query in QUERIES:
                with self.subTest(db=db_type, ver=db_version, cat=category,
                                  query=query):
                    self._assert_equivalent(query, db_type, db_version, category)

    def test_equivalence_is_not_vacuous(self):
        """防"两边都返回空"式的假绿：至少要有真实命中与非零分数。"""
        hit_cases = 0
        nonzero = 0
        for db_type, db_version, category in FILTER_CASES:
            for query in QUERIES:
                got = _live_snapshot(self.index.search(
                    query, db_type, db_version, category=category))
                if got['record_ids']:
                    hit_cases += 1
                    nonzero += sum(1 for s in got['similarities'] if s > 0)
        self.assertGreater(hit_cases, 0, '语料/查询组合一个都没命中，等价性没被验到')
        self.assertGreater(nonzero, 0, '命中相似度全是 0，点积路径没被验到')
        # 阈值两侧都要覆盖到：既有命中也有未命中
        missed = sum(
            1 for db_type, db_version, category in FILTER_CASES
            for query in QUERIES
            if self.index.search(query, db_type, db_version,
                                 category=category).missed)
        self.assertGreater(missed, 0, '未命中分支没被覆盖')
        self.assertLess(missed, len(FILTER_CASES) * len(QUERIES), '命中分支没被覆盖')

    def test_english_and_numeric_queries_match(self):
        """英文词 / 纯数字：`_WORD_RE` 路径与稀疏查找都要覆盖到。"""
        for query in ('row lock wait timeout innodb',
                      'innodb_buffer_pool_wait_free',
                      '12345 67890 42',
                      '8.0 19c 11g'):
            for db_type, db_version, category in FILTER_CASES[:6]:
                with self.subTest(query=query, db=db_type):
                    self._assert_equivalent(query, db_type, db_version, category)

    def test_top_k_variants_match(self):
        """`top_k` 改变只应改变取几条，不应改变排序口径。"""
        for top_k in (1, 2, 3, 5, 20):
            for query in QUERIES:
                want = self.reference.search(query, 'mysql', '8.0', top_k=top_k)
                got = _live_snapshot(self.index.search(
                    query, 'mysql', '8.0', top_k=top_k))
                self.assertEqual(want['record_ids'], got['record_ids'],
                                 f'top_k={top_k} query={query!r}')
                self.assertEqual(want['missed'], got['missed'], f'top_k={top_k}')

    def test_min_similarity_override_matches(self):
        """`min_similarity` 显式传值时行为也一致。"""
        for ms in (0.0, 0.12, 0.3, 0.9):
            for query in QUERIES:
                want = self.reference.search(query, 'mysql', '8.0',
                                             min_similarity=ms)
                got = _live_snapshot(self.index.search(
                    query, 'mysql', '8.0', min_similarity=ms))
                self.assertEqual(want['record_ids'], got['record_ids'],
                                 f'ms={ms} query={query!r}')
                self.assertEqual(want['missed'], got['missed'], f'ms={ms}')
                self.assertEqual(want['miss_reason'], got['miss_reason'],
                                 f'ms={ms}')


# ---------------------------------------------------------------------------
# 2. 内存
# ---------------------------------------------------------------------------
def _synthetic_records(count):
    """确定性合成语料：按索引轮转词表，不依赖 `random` 全局状态。"""
    words = ['等待', '事件', '队列', '锁', '超时', '慢查询', '索引', '全表', '扫描',
             '连接', '耗尽', '复制', '延迟', '主从', '切换', '刷盘', '日志', '提交',
             'innodb', 'row_lock', 'log_flush', 'redo', 'buffer', 'pool', 'wait',
             'mysql', 'oracle', 'postgres', 'tdsql', 'gbase', 'thread', 'timeout']
    out = []
    for i in range(count):
        chunk = [words[(i * 7 + k * 13) % len(words)] for k in range(24)]
        symptom = ''.join(chunk) if i % 2 == 0 else ' '.join(chunk)
        out.append(KbRecord(
            record_id=f'SYN-{i:05d}', symptom=symptom,
            db_types=['mysql'], candidate_causes=[], probe_actions=[],
            fix_actions=[], text=symptom,
        ))
    return out


@tag('unit')
class SparseMemoryTests(SimpleTestCase):
    """索引存储必须是稀疏的：字节数远低于稠密等价物。"""

    N = 2000

    def test_nbytes_is_under_five_percent_of_dense(self):
        records = _synthetic_records(self.N)
        index = build_index(records)
        dense_bytes = self.N * _VECTOR_DIM * 4       # 稠密 float32 (n, 65536)
        self.assertEqual(len(index), self.N)
        self.assertGreater(index.nbytes, 0, 'nbytes 未统计到实际存储')
        self.assertLessEqual(
            index.nbytes, dense_bytes * 0.05,
            f'稀疏索引 {index.nbytes}B 超过稠密等价物 {dense_bytes}B 的 5%')
        # 收益量级：实测约 0.3%，这里只钉住"至少 20 倍"这条保守线
        self.assertGreater(dense_bytes / index.nbytes, 20.0)

    def test_dense_equivalent_would_be_multi_gig_at_paper_scale(self):
        """论文口径 1 万条语料：稠密要 2.5GB，稀疏应是 MB 量级。"""
        records = _synthetic_records(2000)
        index = build_index(records)
        per_record = index.nbytes / len(index)
        dense_per_record = _VECTOR_DIM * 4
        projected_10k_sparse = per_record * 10000
        self.assertLess(projected_10k_sparse, 200 * 1024 * 1024,
                        f'1 万条推算 {projected_10k_sparse / 1048576:.1f}MB，超出预期')
        self.assertLess(projected_10k_sparse,
                        dense_per_record * 10000 * 0.05)

    def test_nbytes_zero_before_build_and_reset_by_add(self):
        index = KbIndex()
        self.assertEqual(index.nbytes, 0)
        self.assertFalse(index.is_built)
        records = _make_records()
        index.add_all(records)
        self.assertFalse(index.is_built, 'add() 后索引应失效，需重新 build()')
        index.build()
        self.assertTrue(index.is_built)
        self.assertGreater(index.nbytes, 0)
        index.add(KbRecord(record_id='KB-999', symptom='新增记录'))
        self.assertFalse(index.is_built)
        self.assertEqual(index.nbytes, 0)


# ---------------------------------------------------------------------------
# 3. 确定性
# ---------------------------------------------------------------------------
@tag('unit')
class DeterminismTests(SimpleTestCase):
    """同一语料 + 同一查询 ⇒ 逐次、逐实例结果完全一致。"""

    def setUp(self):
        self.records = _make_records()
        self.index = build_index(self.records)

    def test_repeated_search_identical(self):
        for query in QUERIES:
            with self.subTest(query=query):
                a = _live_snapshot(self.index.search(query, 'mysql', '8.0'))
                b = _live_snapshot(self.index.search(query, 'mysql', '8.0'))
                self.assertEqual(a, b)

    def test_two_index_instances_identical(self):
        other = build_index(_make_records())
        for query in QUERIES:
            for db_type, db_version, category in FILTER_CASES:
                with self.subTest(query=query, db=db_type):
                    a = _live_snapshot(self.index.search(
                        query, db_type, db_version, category=category))
                    b = _live_snapshot(other.search(
                        query, db_type, db_version, category=category))
                    self.assertEqual(a, b)

    def test_record_order_follows_insertion_order(self):
        """倒排内部按特征 id 排序，但文档下标仍按入库顺序 —— `record_ids` 不许变。"""
        self.assertEqual(self.index.record_ids, [r[0] for r in CORPUS])

    def test_similarities_are_bit_stable(self):
        """不只是 places=6 一致，而是逐位一致（同一索引实例内重复计算）。"""
        q = _vectorize('innodb 行锁等待超时')
        first = self.index._postings.dot(q)
        for _ in range(3):
            self.assertTrue(np.array_equal(first, self.index._postings.dot(q)))


# ---------------------------------------------------------------------------
# 4. 过滤路径
# ---------------------------------------------------------------------------
@tag('unit')
class FilterPathTests(SimpleTestCase):
    """方言 / 版本 / 类目过滤在稀疏路径下仍然生效。"""

    def setUp(self):
        self.index = build_index(_make_records())

    def _ids(self, query, **kw):
        return self.index.search(query, **kw).record_ids

    def test_dialect_filter_excludes_other_dialects(self):
        ids = self._ids('锁等待 row lock contention 争用', db_type='mysql')
        self.assertNotIn('KB-002', ids, 'oracle 记录不得进入 mysql 上下文')
        self.assertNotIn('KB-009', ids)
        self.assertIn('KB-001', ids)

    def test_version_filter_excludes_out_of_range(self):
        """KB-005 登记 `mysql: [5.7, 8.0]`，5.6 必须被版本过滤挡掉。"""
        r56 = self.index.search('复制延迟 replication lag', 'mysql', '5.6')
        r58 = self.index.search('复制延迟 replication lag', 'mysql', '5.8')
        self.assertNotIn('KB-005', r56.record_ids)
        self.assertEqual(r56.filtered['version'], 1)
        self.assertIn('KB-005', r58.record_ids)
        self.assertEqual(r58.filtered['version'], 0)

    def test_category_filter_excludes_other_categories(self):
        ids = self._ids('sga_target shared pool 配置手册',
                        db_type='oracle', db_version='19c',
                        category='config_manual')
        self.assertEqual(ids, ['KB-009'])
        r = self.index.search('锁等待', 'oracle', '19c', category='fault_solution')
        self.assertNotIn('KB-009', r.record_ids)
        self.assertGreaterEqual(r.filtered['category'], 1)

    def test_empty_candidate_reports_dialect(self):
        """全部方言不匹配 ⇒ `no_dialect_match`。"""
        r = self.index.search('锁等待', 'unknown_db', '')
        self.assertEqual(r.record_ids, [])
        self.assertEqual(r.miss_reason, MISS_NO_DIALECT)
        self.assertTrue(r.missed)
        self.assertEqual(r.filtered['total'], len(CORPUS))
        # 候选清空 ⇒ 每条记录恰好落进一个过滤原因
        self._assert_filtered_partitions(r)

    def _assert_filtered_partitions(self, result):
        """过滤计数是**划分**：四类之和必须等于 total（候选清空时无幸存者）。"""
        f = result.filtered
        self.assertEqual(
            f['needs_review'] + f['dialect'] + f['version'] + f['category'],
            f['total'], f'过滤计数不是划分：{dict(f)}')

    def test_empty_candidate_reports_version(self):
        """只有方言匹配、但版本全被挡 ⇒ `no_version_match`。

        `unknown_version_db` 不在语料里会让方言先挡掉，所以造一个方言匹配
        而版本区间全覆盖不到的目标：`mysql` 配 5.6 时只剩 KB-005 被版本挡，
        因此这里用一条 `db_types` 全覆盖的临时记录来把候选清空。
        """
        records = _make_records()
        versioned = KbRecord(
            record_id='KB-ONLY-59', symptom='仅登记 mysql 57 的记录',
            db_types=['mysql'], db_versions={'mysql': ['5.7', '5.7']},
            category='fault_solution', text='仅登记 mysql 57 的记录')
        records = [r for r in records if r.db_types == ['mysql']
                   and r.db_versions] + [versioned]
        index = build_index(records)
        r = index.search('登记 mysql 57', 'mysql', '5.6')
        self.assertEqual(r.record_ids, [])
        self.assertEqual(r.miss_reason, MISS_NO_VERSION)
        self.assertTrue(r.missed)
        self.assertGreaterEqual(r.filtered['version'], 1)

    def test_empty_candidate_reports_category(self):
        """类目把候选清空 ⇒ `no_category_match`。"""
        r = self.index.search('锁等待', 'mysql', '8.0', category='config_manual')
        self.assertEqual(r.record_ids, [])
        self.assertEqual(r.miss_reason, MISS_NO_CATEGORY)
        self.assertTrue(r.missed)
        self.assertGreaterEqual(r.filtered['category'], 1)
        self._assert_filtered_partitions(r)

    def test_needs_review_never_enters_context(self):
        """KB-011 方言为空 ⇒ needs_review，任何查询都不得召回。"""
        for db_type in ('mysql', 'oracle', 'dm', 'unknown_db'):
            with self.subTest(db=db_type):
                r = self.index.search('待复核条目 方言判定不可靠', db_type)
                self.assertNotIn('KB-011', r.record_ids)
        r = self.index.search('待复核条目', 'mysql')
        self.assertGreaterEqual(r.filtered['needs_review'], 1)

    def test_filter_counts_keys_unchanged(self):
        """`filtered` 的键集合是 `SearchResult.miss_payload` 的契约，不能变。"""
        r = self.index.search('锁等待', 'mysql', '8.0', category='fault_solution')
        self.assertEqual(sorted(r.filtered),
                         ['category', 'dialect', 'needs_review', 'total', 'version'])
        self.assertEqual(sorted(r.miss_payload()['filtered']),
                         ['category', 'dialect', 'needs_review', 'total', 'version'])


# ---------------------------------------------------------------------------
# 5. 边界
# ---------------------------------------------------------------------------
@tag('unit')
class EdgeCaseTests(SimpleTestCase):
    """空语料 / 空查询 / 全部被过滤。"""

    def test_empty_corpus(self):
        index = build_index([])
        self.assertEqual(len(index), 0)
        self.assertTrue(index.is_built, '空语料 build() 后仍是"已构建"状态')
        # 空索引只剩段指针数组（8 字节），不应有任何 posting
        self.assertLess(index.nbytes, 1024)
        self.assertEqual(index.nbytes, index._postings.feat_ids.nbytes
                         + index._postings.feat_ptr.nbytes
                         + index._postings.post_doc.nbytes
                         + index._postings.post_val.nbytes)
        self.assertEqual(index.record_ids, [])
        r = index.search('锁等待', 'mysql')
        self.assertEqual(r.record_ids, [])
        self.assertTrue(r.missed)
        self.assertEqual(r.miss_reason, MISS_EMPTY_INDEX)
        # `empty_index` 在设置 `filtered` 之前就返回（沿用改造前的行为）：
        # 空语料没有任何东西被"过滤掉"，所以是空字典而不是全 0 计数
        self.assertEqual(dict(r.filtered), {})

    def test_empty_corpus_search_without_build(self):
        """不 build 直接 search 也必须自愈并给出 `empty_index`。"""
        index = KbIndex()
        r = index.search('锁等待', 'mysql')
        self.assertEqual(r.miss_reason, MISS_EMPTY_INDEX)

    def test_empty_query_has_no_comparable_features(self):
        index = build_index(_make_records())
        # 注意只能用**单字**停用词：`_features` 会把 CJK 相邻二元组也算特征，
        # "的了是在..." 这样的多字串仍会产出二元组，不算"无可比特征"。
        for query in ('', '   ', '的', '了', '不'):
            with self.subTest(query=query):
                r = index.search(query, 'mysql', '8.0')
                self.assertEqual(r.record_ids, [])
                self.assertTrue(r.missed)
                self.assertEqual(r.miss_reason, MISS_BELOW_THRESHOLD)
                self.assertEqual(r.top_similarity, 0.0)

    def test_all_records_filtered_out(self):
        """整语料被方言挡 ⇒ 候选清空，且不得返回任何 top-k。"""
        index = build_index(_make_records())
        r = index.search('innodb 行锁等待超时', 'sqlite', '')
        self.assertEqual(r.record_ids, [])
        self.assertEqual(r.top_similarity, 0.0)
        self.assertTrue(r.missed)
        self.assertEqual(r.miss_reason, MISS_NO_DIALECT)

    def test_record_with_empty_text_yields_zero_similarity(self):
        """检索文本为空的记录 ⇒ 零向量，任何查询都是 0 分，永不进入 top-k。

        单独造一个小语料：`_make_records()` 里的 KB-010 虽然症状为空，但
        `text` 是症状 + 成因 + 动作合成出来的，并不为空。
        """
        empty = KbRecord(record_id='EMPTY-001', symptom='', text='',
                         db_types=['mysql'], category='fault_solution')
        real = KbRecord(record_id='REAL-001', symptom='行锁等待超时',
                        text='行锁等待超时 innodb', db_types=['mysql'],
                        category='fault_solution')
        index = build_index([empty, real])
        r = index.search('行锁等待超时 innodb', 'mysql', '8.0')
        self.assertEqual(r.record_ids, ['REAL-001'])
        self.assertNotIn('EMPTY-001', r.record_ids)
        # 零向量记录在任何查询下的相似度都恰好是 0
        q = _vectorize('任意查询 行锁')
        self.assertEqual(float(index._postings.dot(q)[0]), 0.0)
        self.assertGreater(float(index._postings.dot(q)[1]), 0.0)

    def test_single_feature_collision_query(self):
        """查询特征在哈希空间里撞到别的文档的特征 ⇒ 结果仍与稠密一致。"""
        index = build_index(_make_records())
        reference = _DenseReferenceIndex(_make_records())
        for query in ('z', 'zz', 'q', 'qqq', 'x1 x2 x3'):
            with self.subTest(query=query):
                want = reference.search(query, 'mysql', '8.0')
                got = _live_snapshot(index.search(query, 'mysql', '8.0'))
                self.assertEqual(want['record_ids'], got['record_ids'])
                self.assertEqual(want['miss_reason'], got['miss_reason'])

    def test_duplicate_record_id_rejected(self):
        index = KbIndex()
        index.add(KbRecord(record_id='DUP', symptom='甲', db_types=['mysql']))
        with self.assertRaises(ValueError):
            index.add(KbRecord(record_id='DUP', symptom='乙', db_types=['mysql']))

    def test_stats_keeps_vector_dim_key(self):
        """`stats()` 的对外契约（管理页在读）不能因稀疏化丢字段。"""
        index = build_index(_make_records())
        st = index.stats()
        self.assertEqual(st['vector_dim'], _VECTOR_DIM)
        self.assertEqual(st['records'], len(CORPUS))
        self.assertTrue(st['built'])
        self.assertEqual(st['by_dialect']['mysql'], 5)
        self.assertEqual(st['by_category']['fault_solution'], 7)
