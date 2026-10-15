# -*- coding: utf-8 -*-
"""L3 检索索引：**单接口、相似度搜索、返回 top-5**（论文 §1.3② / §6.1.1）。

论文口径：检索用 Faiss，每查询取 top-5。本项目**不引入 Faiss**（无外部二进制
依赖、可回放环境要求确定性），改用**纯 numpy** 实现余弦相似度检索：

- 论文用嵌入模型（离线 Qwen3-32B 处理文档，检索用向量库）；
- 本模块 P0 用**字符/词袋 + 哈希降维**的稀疏向量做余弦相似度。
  这是明确的简化：召回质量弱于语义嵌入，换来的是**零外部依赖、完全确定性、
  可单测**。判断口径：语料里的症状表述与记录症状高度字面重叠时，词袋足够；
  表述差异大时应升级为嵌入模型，届时只需替换 `_vectorize`/`_similarity`，
  入库/过滤/未命中信号这些真正承载工程约束的逻辑不动。

## 四条硬约束（都是本项目已经付出过代价的）

1. **方言过滤**：`KbRecord.db_types` 不含目标方言的记录不得进入上下文 ——
   REV-10 就是把 Oracle 专有等待事件发给了 MySQL。
2. **版本区间过滤**：`db_versions = {db_type: [min, max]}`，与被诊断实例真实版本
   不匹配的条目不得进入上下文 —— 论文把 "version mismatch" 列为错误来源之一。
3. **类目过滤**：工具 schema 向模型暴露了 `category` 枚举
   （`config_manual`/`fault_solution`/`best_practice`/`basic_fact`）。模型一旦指定了
   类目，过滤就必须在相似度计算**之前**生效 —— 否则被挤掉的只是 top-k 名额里的
   排序，模型会拿到"指定了类目却召回别的类"的记录，而它无从分辨这是检索质量问题
   还是自己传错了参数。
4. **未命中可观测**：零结果或最高相似度过低都要给出**明确信号**
   （`missed=True` + `miss_reason` + `top_similarity`），供上层写
   `OamRetrievalMiss`。论文部署失败原因里"检索未命中"占 12%，
   不记下来就无从改进索引与类目划分（§7 P3）。

## 相似度的确定性保证

- 特征哈希用 `zlib.crc32` 而不是 Python 内置 `hash()` —— 后者对 str 有随机化
  种子（PYTHONHASHSEED），会让每次进程启动的索引都不同，无法回归。
- 向量 L2 归一化后，余弦相似度退化为点积，`_similarity` 只做一次 `matmul`。
- 相同语料 + 相同查询 ⇒ 相同排序（并列时按 record_id 字典序，保证全序）。
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np

from monitor.oam.kb.chunk import estimate_tokens
from monitor.oam.kb.extract import KbRecord, version_in_range

# 论文口径：每查询取 top-5
DEFAULT_TOP_K = 5

# 未命中阈值：最高相似度低于它就算"没捞到有用的"。
#
# 标定依据（冒烟语料实测，2026-10）：同义/近义查询 0.17~0.42，
# 完全无关的查询 0.00。0.12 落在两簇之间的空档里，两侧都留了余量。
# 注意词袋余弦的绝对值本来就比语义嵌入低（不共享词就趋近 0），
# 阈值定高了会把正常命中也判成未命中，未命中周报就成了全噪声。
#
# 运行期可调：`appconf.get('OAM_RETRIEVAL_MISS_SIM')` 是唯一真源
# （按红线，配置项必须登记进 SPECS 才允许读取），本常量只是
# Django 未就绪时的兜底。换语义嵌入后必须重新标定这个阈值。
DEFAULT_MIN_SIMILARITY = 0.12

# 哈希降维维度（2 的幂）。2**16 足够 1 万 chunk 量级（论文口径）且内存仅 256KB。
_VECTOR_DIM = 1 << 16

MISS_EMPTY_INDEX = 'empty_index'
MISS_NO_DIALECT = 'no_dialect_match'
MISS_NO_VERSION = 'no_version_match'
MISS_NO_CATEGORY = 'no_category_match'
MISS_BELOW_THRESHOLD = 'below_similarity_threshold'

_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]')
_WORD_RE = re.compile(r'[a-z][a-z0-9_]{1,}|\d+')
# 症状里最常见、检索时毫无区分度的词（连同所有单字一起会被 IDF 式地压低权重）
_STOP_CHARS = set('的了是在和与或对为中上下有个不')


def _features(text: str) -> list[str]:
    """抽检索特征：CJK 单字 + 相邻二元组 + 英文词/数字。

    中文不做分词（无依赖），二元组兼顾"排队时间/排队"这类局部语序；
    二元组与单字都进哈希空间，靠 IDF 权重自然压制高频单字。
    """
    low = str(text or '').lower()
    feats: list[str] = []
    cjk = _CJK_RE.findall(low)
    feats.extend(c for c in cjk if c not in _STOP_CHARS)
    feats.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    feats.extend(_WORD_RE.findall(low))
    return feats


def _vectorize(text: str) -> np.ndarray:
    """文本 → L2 归一化的稠密向量（哈希技巧，无外部依赖）。

    权重取 `1 + log(tf)`（sublinear tf）：一段话里同一个词重复 20 次
    不该比出现 2 次的词相关度高三倍。
    """
    vec = np.zeros(_VECTOR_DIM, dtype=np.float32)
    feats = _features(text)
    if not feats:
        return vec
    counts: dict[int, int] = {}
    for f in feats:
        h = zlib.crc32(f.encode('utf-8')) % _VECTOR_DIM
        counts[h] = counts.get(h, 0) + 1
    for h, n in counts.items():
        vec[h] = 1.0 + np.log(float(n))
    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec /= norm
    return vec


@dataclass(frozen=True)
class KbHit:
    """一条命中。`record_id` 原样回传 —— 咨询类判定要求引用集合精确相等（§1.6⑤）。"""

    record_id: str
    similarity: float
    record: KbRecord

    def to_dict(self) -> dict:
        return {
            'record_id': self.record_id,
            'similarity': round(self.similarity, 6),
            'symptom': self.record.symptom,
            'candidate_causes': list(self.record.candidate_causes),
            'probe_actions': list(self.record.probe_actions),
            'fix_actions': list(self.record.fix_actions),
            'source_doc_id': self.record.source_doc_id,
            'version': self.record.version,
        }


@dataclass
class SearchResult:
    """检索结果。`missed` / `miss_reason` / `top_similarity` 是可观测性的载体。

    注意：**未命中时仍会返回最好的那一条**（若过滤后还有候选），而不是空列表 ——
    上层写 `OamRetrievalMiss` 时需要 `top_similarity` 才能判断"差一点就命中"
    还是"完全不相干"，前者是索引/类目问题，后者才是知识缺口。
    是否采信由 `missed` 决定，不看 `hits` 是否为空。
    """

    hits: list = field(default_factory=list)
    top_similarity: float = 0.0
    missed: bool = True
    miss_reason: str = ''
    query: str = ''
    db_type: str = ''
    db_version: str = ''
    category: str = ''
    filtered: Mapping[str, int] = field(default_factory=dict)

    @property
    def record_ids(self) -> list:
        return [h.record_id for h in self.hits]

    def to_dict(self) -> dict:
        return {
            'hits': [h.to_dict() for h in self.hits],
            'top_similarity': round(self.top_similarity, 6),
            'missed': self.missed, 'miss_reason': self.miss_reason,
            'query': self.query, 'db_type': self.db_type,
            'db_version': self.db_version, 'category': self.category,
            'filtered': dict(self.filtered),
        }

    def miss_payload(self) -> dict:
        """写 `OamRetrievalMiss` 一行所需的字段（本模块不碰 models，由上层落库）。

        按类目聚合未命中是发现"哪类知识缺口最大"的唯一途径，
        所以 `db_type` / `db_version` / 过滤计数都必须带上。
        """
        return {
            'query': self.query[:512],
            'db_type': self.db_type,
            'db_version': self.db_version,
            'category': self.category,
            'top_similarity': round(self.top_similarity, 6),
            'miss_reason': self.miss_reason,
            'candidate_count': len(self.hits),
            'filtered': dict(self.filtered),
        }


def _no_candidate_reason(counts: Mapping[str, int]) -> str:
    """候选集被清空时，指出**是哪一道过滤**干的。

    这三种未命中的处置方向完全相反：方言挡住 → KB 覆盖面偏科；版本挡住 →
    版本区间登记得太窄；类目挡住 → 该类目下压根没有记录（知识缺口）。
    混成同一个原因码，未命中周报就只剩一个无用的"未命中"总数。
    """
    for key, reason in (('category', MISS_NO_CATEGORY),
                        ('version', MISS_NO_VERSION),
                        ('dialect', MISS_NO_DIALECT)):
        if counts.get(key, 0) > 0:
            return reason
    return MISS_NO_DIALECT


def _min_similarity(override) -> float:
    """未命中阈值的唯一真源：`appconf` 优先，Django 未就绪时回落到常量。

    刻意不在模块顶层调 `appconf.get` —— 本模块要求能被裸 Python 直接 import
    做纯函数自测，顶层求值会把检索层绑死在 Django 配置上。
    """
    if override is not None:
        return float(override)
    try:
        from monitor import appconf
        return float(appconf.get('OAM_RETRIEVAL_MISS_SIM'))
    except Exception:
        return DEFAULT_MIN_SIMILARITY


class KbIndex:
    """知识库检索索引（纯 numpy）。

    `build()` 之后才可查询；`add()` 之后需要重新 `build()`。这是刻意的：
    批量化建矩阵比每次插入都重算 O(n) 快得多，而 KB 是离线批量入库的
    （论文口径：离线一次性处理，产物入库并版本化，不在推理时做）。
    """

    def __init__(self) -> None:
        self._records: list[KbRecord] = []
        self._by_id: dict[str, int] = {}
        self._matrix: np.ndarray | None = None

    # ---------------- 入库 ----------------
    def add(self, record: KbRecord) -> None:
        rid = record.record_id_or_key
        if rid in self._by_id:
            raise ValueError(f'记录 ID 重复：{rid}（§1.6⑤ 要求 ID 稳定且唯一）')
        self._by_id[rid] = len(self._records)
        self._records.append(record)
        self._matrix = None       # 脏了，需重建

    def add_all(self, records: Iterable[KbRecord]) -> int:
        n = 0
        for r in records:
            self.add(r)
            n += 1
        return n

    def build(self) -> 'KbIndex':
        """构建检索矩阵。返回 self 以便链式调用。"""
        if not self._records:
            self._matrix = np.zeros((0, _VECTOR_DIM), dtype=np.float32)
            return self
        self._matrix = np.vstack([_vectorize(self._searchable(r)) for r in self._records])
        return self

    @staticmethod
    def _searchable(record: KbRecord) -> str:
        """记录的检索文本。显式 `text` 优先（由 extract 从源片段合成）。"""
        return record.text or record.symptom

    def __len__(self) -> int:
        return len(self._records)

    @property
    def is_built(self) -> bool:
        return self._matrix is not None

    # ---------------- 过滤 ----------------
    def _admissible_mask(self, db_type: str, db_version: str,
                         category: str = '') -> tuple[np.ndarray, dict[str, int]]:
        """返回 (可进入上下文的掩码, 各原因被过滤掉的计数)。

        计数本身就是诊断信号：全是 `no_dialect` 说明 KB 覆盖面偏科，
        全是 `no_version` 说明版本区间登记得太窄，全是 `no_category` 说明
        模型点的类目下压根没有记录（属于知识缺口，不是检索质量问题）。
        """
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
            # 类目过滤**失败关闭**：模型点名了某个类目，而记录没有类目，
            # 就不能算作"属于该类目"——放行等于把"是不是这一类"这个判断
            # 悄悄转嫁给模型，而模型分辨不出来（REV-10 同一种病）。
            # 若因此清空候选集，`no_category_match` + filtered 计数会直接
            # 指出"KbRecord 没填 category"，是当场可修的数据问题，
            # 比把错类目的记录当命中喂出去好。
            if category and r.category != category:
                counts['category'] += 1
                continue
            mask[i] = True
        return mask, counts

    # ---------------- 查询 ----------------
    def search(self, query: str, db_type: str, db_version: str = '',
               top_k: int = DEFAULT_TOP_K,
               min_similarity=None,
               category: str = '') -> SearchResult:
        """相似度搜索，返回 top-k 条（论文口径 k=5）。

        过滤先于排序：**被方言/版本/类目过滤掉的记录不参与相似度计算**，
        否则它们的分数会挤占 top-k 名额，让命中结果变少（"看起来有结果，
        实际全被过滤光了"是最难查的未命中形态）。

        `min_similarity` 传 None 表示"按运行期配置"，见 `_min_similarity`。
        """
        min_similarity = _min_similarity(min_similarity)
        res = SearchResult(query=str(query or '')[:512], db_type=db_type,
                           db_version=db_version, category=category or '')
        if self._matrix is None:
            self.build()
        if not self._records:
            res.miss_reason = MISS_EMPTY_INDEX
            return res

        mask, counts = self._admissible_mask(db_type, db_version, category)
        res.filtered = counts
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            res.miss_reason = _no_candidate_reason(counts)
            return res

        q = _vectorize(query)
        if not float(np.linalg.norm(q)):
            # 空查询或纯停用词：无可比对特征，明确判未命中而不是返回任意 top-k
            res.miss_reason = MISS_BELOW_THRESHOLD
            return res

        sub = self._matrix[idx]
        sims = sub @ q                       # 向量已 L2 归一化 ⇒ 点积即余弦
        order = np.argsort(-sims, kind='stable')
        picked: list[KbHit] = []
        for oi in order:
            sim = float(sims[oi])
            if len(picked) >= top_k:
                break
            if sim < min_similarity and picked:
                break                        # 已按分数降序，低于阈值后面只会更低
            rec = self._records[int(idx[oi])]
            picked.append(KbHit(record_id=rec.record_id_or_key,
                                similarity=sim, record=rec))

        # 排序稳定化：分数并列时按 record_id 字典序（numpy argsort 不保证跨并列项全序）
        picked.sort(key=lambda h: (-h.similarity, h.record_id))
        res.hits = picked[:top_k]
        res.top_similarity = res.hits[0].similarity if res.hits else float(np.max(sims))
        if not res.hits:
            res.missed = True
            res.miss_reason = MISS_BELOW_THRESHOLD
        else:
            res.missed = res.top_similarity < min_similarity
            res.miss_reason = MISS_BELOW_THRESHOLD if res.missed else ''
        return res

    # ---------------- 可观测性 ----------------
    @property
    def record_ids(self) -> list:
        """索引里全部记录的 ID。证据审计用它判断"这条引用是不是真的存在于 KB"。"""
        return [r.record_id_or_key for r in self._records]

    def stats(self) -> dict:
        """索引概览，供管理页/新鲜度看板。"""
        by_dialect: dict[str, int] = {}
        by_category: dict[str, int] = {}
        pending = 0
        versioned = 0
        for r in self._records:
            if r.needs_review:
                pending += 1
            if r.db_versions:
                versioned += 1
            for d in r.db_types:
                by_dialect[d] = by_dialect.get(d, 0) + 1
            if r.category:
                by_category[r.category] = by_category.get(r.category, 0) + 1
        return {
            'records': len(self._records), 'vector_dim': _VECTOR_DIM,
            'built': self.is_built, 'by_dialect': by_dialect,
            'by_category': by_category, 'needs_review': pending,
            'with_db_versions': versioned,
        }


# 版本区间判定复用 extract 里的同一套口径：索引与 `KbRecord.covers()`
# 必须一致，否则会出现"记录自己说不适用、索引却放行"的裂缝。


def build_index(records: Sequence[KbRecord]) -> KbIndex:
    """便捷入口：批量入库并建好矩阵。"""
    idx = KbIndex()
    idx.add_all(records)
    return idx.build()


def records_from_orm(rows: Iterable) -> list:
    """把**落库态**的 `monitor.models.KbRecord` 适配成检索层吃的 dataclass。

    为什么必须有这一步：两个类同名不同形 —— ORM 模型有 `record_id` /
    `db_types` / `candidate_causes`，而检索层要的是 `record_id_or_key` /
    `text` / `needs_review` / `category`。没有适配时 `KbIndex.add()` 直接抛
    `AttributeError`，`oam_kb_migrate_rules` 迁出来的 39 条规则就只是躺在
    表里的行，永远进不了检索上下文（L3 形同未接线）。

    `text` 由症状 + 成因 + 动作合成，保证检索文本与迁移前的规则语义一致。
    """
    out = []
    for row in rows or []:
        symptom = str(getattr(row, 'symptom', '') or '')
        causes = [str(c) for c in (getattr(row, 'candidate_causes', None) or [])]
        probes = [str(p) for p in (getattr(row, 'probe_actions', None) or [])]
        fixes = [str(f) for f in (getattr(row, 'fix_actions', None) or [])]
        doc = getattr(row, 'source_document', None)
        db_types = [str(d) for d in (getattr(row, 'db_types', None) or [])]
        out.append(KbRecord(
            record_id=str(getattr(row, 'record_id', '') or ''),
            symptom=symptom,
            db_types=db_types,
            candidate_causes=causes,
            probe_actions=probes,
            fix_actions=fixes,
            source_doc_id=str(getattr(doc, 'doc_id', '') or ''),
            version=str(getattr(row, 'version', '') or ''),
            db_versions=dict(getattr(row, 'db_versions', None) or {}),
            category=str(getattr(doc, 'category', '') or ''),
            text=' '.join([symptom] + causes + probes + fixes).strip(),
            # 方言没判定出来的记录一律不进检索上下文（失败关闭，与 covers() 同口径）
            needs_review=not bool(db_types),
        ))
    return out


def max_text_tokens(text: str) -> int:
    """检索文本的 token 度量（复用 chunk 的确定性口径，便于上层限长）。"""
    return estimate_tokens(text)


__all__ = [
    'DEFAULT_TOP_K', 'DEFAULT_MIN_SIMILARITY', 'KbHit', 'SearchResult',
    'KbIndex', 'build_index', 'records_from_orm', 'max_text_tokens',
    'MISS_EMPTY_INDEX', 'MISS_NO_DIALECT', 'MISS_NO_VERSION',
    'MISS_NO_CATEGORY', 'MISS_BELOW_THRESHOLD',
]
