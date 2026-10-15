# -*- coding: utf-8 -*-
"""L3 知识与工具层 —— **知识库三步构建**（论文 §1.3①）。

论文把知识库构建拆成三步，本包按同构拆成三个模块，顺序不可交换：

| 步骤 | 模块 | 论文口径 | 本项目的额外硬约束 |
|------|------|----------|--------------------|
| ① 分类 | `classify.py` | 按文档在诊断流程中的功能归类（4 类），使策略能在推理不同阶段做针对性检索 | 判定必须可解释（带信号），人工标注优先 |
| ② 语义精炼 | `chunk.py` | 自适应切分：叙述性→递归切分；结构化→结构感知切分，**一个参数的描述/默认值/取值范围同单元**；每片段 <1000 token | token 计数无外部依赖且确定；字段完整性优先于长度上限 |
| ③ 因果与事实抽取 | `extract.py` | 症状→候选成因→绑定动作空间，做成循环可直接消费的记录 | 成因必须落在**封闭根因标签空间**；动作必须是**真实工具名**；方言必填 |
| 检索 | `index.py` | 单接口、相似度搜索、top-5（Faiss） | 纯 numpy；按方言与版本区间过滤；未命中必须可观测 |

为什么顺序不可交换：② 依赖 ① 的类目决定切分策略（把配置手册按叙述性硬切，
参数就会被劈开）；③ 依赖 ② 的片段边界决定一条记录引用哪些源片段；
检索依赖 ③ 的 `KbRecord`。

贯穿四步的一条红线：**方言与版本是一等维度**。分类阶段允许"判定不出来"
（标 `needs_review`、失败关闭），抽取与检索阶段一律按方言/版本过滤 ——
把 Oracle 的方案推给 MySQL 是本项目已经付出过代价的 REV-10 缺陷。

用法最小示例::

    from monitor.oam.kb import classify, chunk, extract, KbIndex

    doc = {'doc_id': 'ora_sga_manual', 'title': 'SGA_TARGET 参数手册', 'body': ...}
    doc['category'] = classify(doc)          # ① 分类（可带人工标注覆盖）
    chunks = chunk(doc)                       # ② 切分
    records = extract(doc, chunks)            # ③ 抽取
    result = KbIndex().add_all(records).build().search(
        '查询排队时间过高', db_type='oracle', db_version='19c')
    if result.missed:
        ...   # 上层据此写一行 OamRetrievalMiss（检索未命中占失败的 12%）

已知限制集中在各模块 docstring：抽取暂用确定性规则而非论文的离线 Qwen3-32B，
检索用词袋余弦而非嵌入模型 —— 两处都是为 P0 零外部依赖做的显式简化，
替换点已在对应模块标注。
"""
from monitor.oam.kb.chunk import (
    MAX_CHUNK_TOKENS,
    Chunk,
    chunk,
    estimate_tokens,
    recursive_split,
    structure_aware_split,
)
from monitor.oam.kb.classify import (
    KB_CATEGORIES,
    Classification,
    classify,
    classify_detailed,
)
from monitor.oam.kb.extract import (
    KbRecord,
    detect_db_types,
    extract,
    normalize_symptom,
    version_in_range,
)
from monitor.oam.kb.index import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_TOP_K,
    KbHit,
    KbIndex,
    SearchResult,
    build_index,
    records_from_orm,
)

__all__ = [
    # 三步
    'KB_CATEGORIES', 'Classification', 'classify', 'classify_detailed',
    'MAX_CHUNK_TOKENS', 'Chunk', 'chunk', 'recursive_split',
    'structure_aware_split', 'estimate_tokens',
    'KbRecord', 'extract', 'detect_db_types', 'normalize_symptom', 'version_in_range',
    # 检索
    'KbIndex', 'KbHit', 'SearchResult', 'build_index', 'records_from_orm',
    'DEFAULT_TOP_K', 'DEFAULT_MIN_SIMILARITY',
]
