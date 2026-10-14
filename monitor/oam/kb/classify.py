# -*- coding: utf-8 -*-
"""知识库三步构建 · 第一步：**分类**（论文 §1.3① 第 1 项）。

论文原文的分类口径是"按文档在典型诊断流程中的功能"分为四类，使智能体能在
推理的不同阶段做针对性检索 —— 也就是说，分类不是为了归档好看，而是为了让
策略在不同推理阶段**只检索该阶段用得上的那类知识**：

    config_manual    配置参数手册 —— 取证/配置核对阶段用（"这个参数默认值多少"）
    fault_solution   故障处理方案 —— 定位阶段用（"这个症状怎么查、怎么修"）
    best_practice   最佳实践指南 —— 决策/调优阶段用（"应该配成什么样"）
    basic_fact      基础事实知识 —— 理解阶段用（"这个词/这个概念指什么"）

## 为什么判定必须可解释

分类结果是后续切分策略的**输入**（`chunk.py` 靠它决定用递归切分还是结构感知
切分），判错了会静默地破坏论文对结构化内容的硬要求（参数描述/默认值/取值范围
必须同单元）。所以本模块不输出一个黑箱标签，而是同时输出：

- `category`     最终类别；
- `signals`      命中了哪些关键词/结构特征（逐条可读）；
- `confidence`   top 与次选的间距（0~1），低置信应触发人工复核；
- `source`       `manual`（人工标注优先）或 `auto`。

## 已知限制

1. 判定是**确定性关键词 + 结构特征打分**，不是分类器。好处是可复现、零依赖、
   可单测；代价是对没见过的表述会有误判。`confidence` 就是为人工复核留的口子。
2. 人工标注优先，但人工标注的类别若不在 `KB_CATEGORIES` 内**直接报错**而不是
   静默兜底 —— 一个拼错的类目一旦静默入库，后面按类目聚合的未命中周报就会失真
   （论文失败原因里"检索未命中"占 12%，靠的正是这些聚合数字可下钻）。
3. 本模块不做方言判定。那是 `extract.py` 的职责（§7 P3 的"版本/方言绑定"）。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping

# 论文的四个类目。顺序即"平票时的优先级"，也是 models.KbDocument.category 的取值域。
KB_CATEGORIES: tuple[str, ...] = (
    'config_manual', 'fault_solution', 'best_practice', 'basic_fact',
)

CAT_CONFIG_MANUAL = 'config_manual'
CAT_FAULT_SOLUTION = 'fault_solution'
CAT_BEST_PRACTICE = 'best_practice'
CAT_BASIC_FACT = 'basic_fact'


@dataclass(frozen=True)
class Classification:
    """一次分类的完整结论（含判定信号），供人工复核与回归测试使用。"""

    category: str
    confidence: float
    signals: tuple[str, ...] = ()
    source: str = 'auto'
    scores: Mapping[str, float] = field(default_factory=dict)

    def explain(self) -> str:
        """一行人类可读的解释，直接进日志/管理页。"""
        head = f'{self.category}(置信度={self.confidence:.2f}, 来源={self.source})'
        return f'{head} 信号: {"; ".join(self.signals) or "无"}'


# --------------------------------------------------------------------------
# 判定信号表。刻意做成"看得见的词表"而不是黑箱：词表改动 = 分类口径改动，
# 需要在评审里说清楚。词表只收运维手册里真会出现的表述，不收泛义词
# （如"问题""说明"这类词几乎每篇都有，收进来等于噪声）。
# --------------------------------------------------------------------------
_TITLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    CAT_CONFIG_MANUAL: (
        '参数手册', '参数说明', '参数列表', '配置手册', '配置参数', '参数参考',
        '初始化参数', '动态参数', '系统参数', '可调参数', '参数说明表',
        'init.ora', 'spfile', '参数配置',
    ),
    CAT_FAULT_SOLUTION: (
        '故障处理', '故障排查', '排查手册', '处理方案', '解决方案', '应急处理',
        '错误码', '报错', '异常处理', '故障案例', '定位步骤', '恢复步骤',
        'troubleshooting', '故障',
    ),
    CAT_BEST_PRACTICE: (
        '最佳实践', '实践指南', '调优指南', '调优建议', '优化建议', '推荐配置',
        '参考实践', '运维规范', '指南', '建议', '原则', '规范',
    ),
    CAT_BASIC_FACT: (
        '概述', '简介', '概念', '术语', '定义', '原理', '是什么',
        '基础知识', '说明', '介绍',
    ),
}

_BODY_KEYWORDS: dict[str, tuple[str, ...]] = {
    CAT_CONFIG_MANUAL: (
        '默认值', '取值范围', '最小值', '最大值', '作用域', '动态修改', '静态参数',
        '会话级', '全局级', '参数说明', '参数表', '单位', '参数值',
        'alter system set', 'alter session set', 'pfile', 'spfile', 'scope',
    ),
    CAT_FAULT_SOLUTION: (
        '排查步骤', '处理步骤', '定位步骤', '根因', '故障现象', '报错信息',
        '错误码', '日志分析', '诊断', '应急', '恢复', '复现', '规避',
        '根因分析', '故障处理',
    ),
    CAT_BEST_PRACTICE: (
        '建议', '推荐', '最佳实践', '应当', '不宜', '尽量避免', '权衡',
        '适用场景', '调优', '原则', '参考值', '经验值',
    ),
    CAT_BASIC_FACT: (
        '是指', '指的是', '定义为', '称为', '由', '组成', '含义', '概念',
        '术语', '区别于', '说明',
    ),
}

# 同一关键词在正文里重复出现的信息量是递减的，封顶避免"通篇谈默认值"刷分
_BODY_KEY_CAP = 3
_TITLE_WEIGHT = 3.0
_BODY_WEIGHT = 1.0
_FEATURE_WEIGHT = 2.0


def _kw_hits(haystack: str, words: tuple[str, ...]) -> list[str]:
    low = haystack.lower()
    return [w for w in words if w in low]


def _structural_features(title: str, body: str) -> dict[str, list[str]]:
    """从版式/编号里抽信号 —— 手册与方案的差别往往不在用词而在结构。"""
    lines = [ln.strip() for ln in body.splitlines()]
    non_empty = [ln for ln in lines if ln]
    feats: dict[str, list[str]] = {c: [] for c in KB_CATEGORIES}

    # 配置手册：大量"参数名：值"与"默认值/取值范围"字段块
    field_lines = [
        ln for ln in non_empty
        if _FIELD_WORDS_RE.search(ln) and not ln.startswith('#')
    ]
    if len(field_lines) >= 4:
        feats[CAT_CONFIG_MANUAL].append(f'参数字段块 {len(field_lines)} 处')
    if any(ln.startswith('|') and ('参数' in ln or '取值' in ln or '默认' in ln) for ln in lines):
        feats[CAT_CONFIG_MANUAL].append('含参数表格（Markdown 表）')

    # 故障方案：编号步骤 + 错误码形态
    step_lines = [ln for ln in non_empty if _STEP_RE.match(ln)]
    if len(step_lines) >= 3:
        feats[CAT_FAULT_SOLUTION].append(f'编号步骤 {len(step_lines)} 步')
    errs = _ERROR_CODE_RE.findall(body)
    if errs:
        uniq = sorted(set(errs))[:3]
        feats[CAT_FAULT_SOLUTION].append('含错误码 ' + ','.join(uniq))

    # 最佳实践：建议/推荐出现在小节标题或行首
    rec_heads = [ln for ln in lines if ln.startswith('#') and _kw_hits(ln, ('建议', '推荐', '最佳实践', '调优', '规范'))]
    if rec_heads:
        feats[CAT_BEST_PRACTICE].append('小节标题为建议/推荐类')
    if any(ln.startswith(('- ', '* ')) and _kw_hits(ln, ('建议', '应当', '不宜', '推荐')) for ln in non_empty):
        feats[CAT_BEST_PRACTICE].append('列表项为建议句式')

    # 基础事实：有实质内容但既无结构块也无步骤
    if non_empty and not any(feats[c] for c in (CAT_CONFIG_MANUAL, CAT_FAULT_SOLUTION, CAT_BEST_PRACTICE)):
        feats[CAT_BASIC_FACT].append('无步骤/无字段块/无建议句式的说明性正文')
    return feats


# 正则集中声明，便于与上方的词表对照阅读
_STEP_RE = re.compile(r'^(?:步骤\s*)?\d+[.、)]|^(?:Step|STEP)\s*\d+', re.IGNORECASE)
_ERROR_CODE_RE = re.compile(r'\bORA-\d{4,6}\b|\bERROR\s+\d+\b|\bMySQL\s+\[\w+\]|错误码\s*[:：]\s*\S+')
_FIELD_WORDS_RE = re.compile(
    r'(默认值|取值范围|最小值|最大值|缺省值|作用域|单位|取值|范围|参数名|参数值|'
    r'default|range|min|max|unit|scope)',
    re.IGNORECASE,
)


def _doc_field(doc: Any, name: str, default: Any = '') -> Any:
    """文档入参既支持 dict 也支持对象（models.KbDocument / 离线 JSON 都传得进来）。"""
    if isinstance(doc, Mapping):
        return doc.get(name, default)
    return getattr(doc, name, default)


def _score_doc(title: str, body: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores: dict[str, float] = {c: 0.0 for c in KB_CATEGORIES}
    signals: dict[str, list[str]] = {c: [] for c in KB_CATEGORIES}

    for cat, words in _TITLE_KEYWORDS.items():
        hits = _kw_hits(title, words)
        if hits:
            scores[cat] += _TITLE_WEIGHT * len(hits)
            signals[cat].append('标题命中 ' + '/'.join(hits[:4]))

    body_hits: dict[str, Counter] = {c: Counter() for c in KB_CATEGORIES}
    for cat, words in _BODY_KEYWORDS.items():
        low = body.lower()
        for w in words:
            n = low.count(w.lower())
            if n:
                body_hits[cat][w] = n
    for cat, counter in body_hits.items():
        # 封顶计数：出现 ≥1 次即给分，最多按 _BODY_KEY_CAP 次累加
        for w, n in counter.items():
            scores[cat] += _BODY_WEIGHT * min(n, _BODY_KEY_CAP)
        if counter:
            top = ', '.join(f'{w}×{n}' for w, n in counter.most_common(4))
            signals[cat].append('正文命中 ' + top)

    for cat, feats in _structural_features(title, body).items():
        if feats:
            scores[cat] += _FEATURE_WEIGHT * len(feats)
            signals[cat].extend(feats)

    return scores, signals


def classify_detailed(doc: Any) -> Classification:
    """分类并返回带判定信号的完整结论。

    入参至少要有 `title` / `body`；`category` 是可选的人工标注，**优先于自动判定**。
    """
    title = str(_doc_field(doc, 'title', '') or '')
    body = str(_doc_field(doc, 'body', '') or '')
    manual = _doc_field(doc, 'category', None)

    if manual:
        manual_norm = str(manual).strip().lower()
        if manual_norm not in KB_CATEGORIES:
            raise ValueError(
                f'人工标注的 category={manual!r} 不在 KB_CATEGORIES 内；'
                f'合法取值={KB_CATEGORIES}'
            )
        return Classification(
            category=manual_norm, confidence=1.0,
            signals=(f'人工标注 category={manual_norm}',),
            source='manual', scores={manual_norm: 1.0},
        )

    if not (title or body):
        raise ValueError('分类入参为空：至少需要 title 或 body')

    scores, signals = _score_doc(title, body)
    ranked = sorted(KB_CATEGORIES, key=lambda c: (-scores[c], KB_CATEGORIES.index(c)))
    top, second = ranked[0], ranked[1]
    top_s, second_s = scores[top], scores[second]

    if top_s <= 0.0:
        # 一个信号都没命中：按基础事实兜底，置信度 0，让上层知道这是兜底结果
        return Classification(
            category=CAT_BASIC_FACT, confidence=0.0,
            signals=('未命中任何分类信号，按基础事实兜底',),
            source='auto', scores=dict(scores),
        )

    if second_s > 0:
        confidence = top_s / (top_s + second_s)
    else:
        # 无竞争类目时按绝对分给分，3 分（一处标题命中）即视为较可信
        confidence = min(1.0, top_s / 3.0)
    return Classification(
        category=top, confidence=round(confidence, 4),
        signals=tuple(signals[top]), source='auto', scores=dict(scores),
    )


def classify(doc: Any) -> str:
    """论文口径的分类接口：只回类别。

    需要判定信号时改用 `classify_detailed()`。
    """
    return classify_detailed(doc).category


__all__ = [
    'KB_CATEGORIES', 'Classification', 'classify', 'classify_detailed',
    'CAT_CONFIG_MANUAL', 'CAT_FAULT_SOLUTION', 'CAT_BEST_PRACTICE', 'CAT_BASIC_FACT',
]
