# -*- coding: utf-8 -*-
"""L2 感知层 · 模态三：KB 检索结果的凝练。

对应设计 §4.2 MMA 表格第 3 行「KB 检索结果 → 把多个分散事实/冗长段落综合 →
连贯凝练摘要」。也对应 §1.3② 的工具语义增强：原始数据源暴露的是面向模式的输出，
需要一段简洁的自然语言描述供策略使用。

## refs 必须原样透传（设计 §1.6⑤，硬约束）

咨询类任务（`CAT_CONSULTATION`）的判定要求：**引用的文档集合与规格里的期望集合
完全相等**。因此 `evidence['refs']` 里的 ID 必须与 KB 侧使用的 ID **一字不差** ——
不允许重编号、不允许截断、不允许"只留前 3 条"。
为了能做集合相等，refs 统一带 `kb:` 前缀（`monitor.oam.types.Trajectory.kb_refs`
正是按这个前缀挑出知识库引用的）。

**没有 ID 的片段一律不进摘要**，只记进 `evidence['skipped']`：
进了摘要却引不出来源，等于给审计留一个对不上的窟窿。

## 为什么要留 `llm_renderer` 接口（但不在推理时调用）

论文对知识描述的做法是**离线生成一次、入库版本化**，不在推理时现生成 ——
一来推理期延迟可控，二来版本化之后"同一条知识在不同轮次说不一样"的问题从根上消失。

所以本模块的分工是：

- `digest()` —— **纯离线、确定性、零 LLM 依赖**，用规则拼装连贯摘要。
  这是默认路径，也是唯一会在推理路径上跑的路径。
- `render_digest_with_llm()` —— 构建期入口，调用方把 `llm_renderer` 传进来，
  用 LLM 把同样的结构化要点重写成更自然的文字，产出同样带 refs 的 `Observation`，
  由 KB 侧**入库版本化**。

两个入口产出同一套 `evidence` 结构，所以审计规则只有一份。

## 已知限制

- 规则拼装能做到的是"分组 + 去重 + 排序"，做不到真正的语义综合。
  设计 §1.4 第 2 条也承认了这一点（计划分析里"仅规则"不是处处占优），
  所以 `evidence['points']` 留了结构化要点，供离线 LLM 重写、也供上层二次加工；
- 同义但措辞不同的片段只能按归一化后的文本去重，语义级去重要靠离线 LLM 那一步。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable, Sequence

from monitor.oam.types import Observation

logger = logging.getLogger('monitor.oam.perception')

__all__ = [
    'digest', 'render_digest_with_llm', 'KB_KIND_LABELS', 'FRAGMENT_ID_KEYS',
    'MAX_PER_GROUP',
]

# 知识库分类（§1.3① 的四分类）。片段用哪个分类由 KB 侧标，这里只做展示顺序与文案。
KB_KIND_LABELS = {
    'config_manual': '配置参数手册',
    'troubleshooting': '故障处理方案',
    'best_practice': '最佳实践指南',
    'fact': '基础事实知识',
    'other': '其他知识',
}
_KIND_ALIASES = {
    '配置参数手册': 'config_manual', '配置': 'config_manual', 'config': 'config_manual',
    '故障处理方案': 'troubleshooting', '处理方案': 'troubleshooting',
    'trouble': 'troubleshooting', '故障': 'troubleshooting',
    '最佳实践指南': 'best_practice', '最佳实践': 'best_practice',
    'practice': 'best_practice',
    '基础事实知识': 'fact', '事实': 'fact',
}
_KIND_ORDER = tuple(k for k in KB_KIND_LABELS if k != 'other') + ('other',)

#: 片段 ID 的候选字段名（按优先级）。**都取不到就跳过该片段**，不进摘要。
FRAGMENT_ID_KEYS = ('id', 'chunk_id', 'doc_id', 'document_id')
_FRAGMENT_TEXT_KEYS = ('text', 'content', 'chunk', 'snippet', 'body')

REF_PREFIX = 'kb:'
MAX_PER_GROUP = 6      # 论文未给值，按需调整：每类最多展开几条（更多会挤爆策略的上下文）
_WS_RE = re.compile(r'\s+')


def _fragment_id(fragment: dict) -> str | None:
    for key in FRAGMENT_ID_KEYS:
        value = fragment.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _fragment_text(fragment: dict) -> str:
    for key in _FRAGMENT_TEXT_KEYS:
        value = fragment.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _fragment_kind(fragment: dict) -> str:
    raw = str(fragment.get('kind') or fragment.get('category') or '').strip()
    key = raw.lower().replace('-', '_').replace(' ', '_')
    key = _KIND_ALIASES.get(raw, _KIND_ALIASES.get(key, key))
    return key if key in KB_KIND_LABELS else 'other'


def _normalize(text: str) -> str:
    """去重用的归一化：压空白、去标点、转小写。只做字面层，不做语义层。"""
    return _WS_RE.sub(' ', re.sub(r'[，。、；：,.;:!?！？"\'()（）]', '', text)).strip().lower()


def _summarize_point(text: str, limit: int) -> str:
    """把一条过长的事实压成单句要点（截断必须留痕，故显式加省略号）。"""
    text = _WS_RE.sub(' ', text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip('，,;；。 ') + '……'


def _collect(fragments: Iterable[dict], max_per_group: int) -> tuple[dict, list, list]:
    """按分类分组、字面去重、每类截断。返回 (分组结果, 用到的片段, 被跳过的片段)。"""
    groups: dict[str, list[dict]] = {}
    used: list[dict] = []
    skipped: list[dict] = []
    seen: dict[str, set[str]] = {}
    for index, fragment in enumerate(fragments or []):
        if not isinstance(fragment, dict):
            skipped.append({'index': index, 'reason': 'not_a_dict'})
            continue
        fid = _fragment_id(fragment)
        text = _fragment_text(fragment)
        if fid is None:
            skipped.append({'index': index, 'reason': 'missing_id'})
            continue
        if not text:
            skipped.append({'index': index, 'id': fid, 'reason': 'empty_text'})
            continue
        kind = _fragment_kind(fragment)
        bucket = groups.setdefault(kind, [])
        seen.setdefault(kind, set())
        key = _normalize(text)
        if key in seen[kind]:
            # 同一条知识被检索到两次：只引一次，但记下来（refs 要与"实际咨询过的
            # 文档集合"相等，重复引用不影响集合相等，省得白占上下文）
            skipped.append({'index': index, 'id': fid, 'reason': 'duplicate'})
            continue
        if len(bucket) >= max_per_group:
            skipped.append({'index': index, 'id': fid, 'reason': 'over_group_limit'})
            continue
        seen[kind].add(key)
        entry = {'id': fid, 'kind': kind, 'text': text,
                 'score': fragment.get('score'), 'title': fragment.get('title')}
        bucket.append(entry)
        used.append(fragment)
    return groups, used, skipped


def _ref_of(record_id: str) -> str:
    """统一的引用写法：`kb:<id>`。

    幂等 —— 记录 ID 本身已经带 `kb:` 前缀时（`oam_kb_migrate_rules` 用的是
    `kb:rule:R001`）不再叠加，否则会产出 `kb:kb:rule:R001`，
    与用例侧 `required_evidence` 的写法永远对不上（咨询类判定按集合精确相等）。
    """
    rid = str(record_id).strip()
    return rid if rid.startswith(REF_PREFIX) else f'{REF_PREFIX}{rid}'


def _assemble(groups: dict, used: list, db_type: str, skipped: list,
              max_per_group: int) -> tuple[str, list]:
    """规则拼装：分组小标题 + 要点 + 收尾。纯确定性，无 LLM。"""
    points: list[dict] = []
    lines: list[str] = []
    kind_counts = {kind: len(items) for kind, items in groups.items()}
    header = (f'关于 {db_type} 的知识凝练：本次引用 {len(used)} 个片段'
              f'（{"、".join(f"{KB_KIND_LABELS[k]} {v} 条" for k, v in sorted(kind_counts.items()))}）。')
    lines.append(header)
    for kind in _KIND_ORDER:
        items = groups.get(kind)
        if not items:
            continue
        label = KB_KIND_LABELS[kind]
        lines.append(f'{label}：')
        for item in items:
            summary = _summarize_point(item['text'], 160)
            ref = f'[{_ref_of(item["id"])}]'
            points.append({'kind': kind, 'ref': _ref_of(item['id']),
                           'id': item['id'], 'title': item.get('title'),
                           'score': item.get('score'), 'summary': summary})
            lines.append(f'- {summary} {ref}')
        if len(items) >= max(int(max_per_group), 1):
            dropped = sum(1 for s in skipped if s.get('reason') == 'over_group_limit')
            if dropped:
                lines.append(f'  （同类另有 {dropped} 条未展开）')
    if not groups:
        lines.append('未检索到可引用的知识片段。')
    text = '\n'.join(lines)
    return text, points


def digest(fragments: Sequence[dict], db_type: str, *,
           max_per_group: int = MAX_PER_GROUP) -> Observation:
    """把多个分散事实/冗长段落综合成连贯凝练摘要，返回 `Observation`。

    `fragments` 的字段契约（键名可替换，取值必须原样透传）：
        id/chunk_id/doc_id/document_id —— 来源 ID，**缺失则该片段不进摘要**
        text/content/chunk/snippet/body —— 正文
        kind/category —— 知识分类（§1.3① 四分类），缺失归入"其他知识"
        score/title —— 可选，仅用于 evidence 留痕，不参与拼装

    `evidence` 必填：`refs` 是 `['kb:<id>', ...]`，顺序与摘要里的引用顺序一致；
    另外给 `doc_ids`（去重后的原始 ID，供集合相等判定）、`points`（结构化要点，
    供离线 LLM 重写或上层二次加工）、`skipped`（没进摘要的片段及原因）、
    `counts`。**无片段时也返回 Observation**（`refs` 为空、`empty=True`）——
    上层应把空 refs 当作"证据不足"，而不是一条被知识库支撑的结论。
    """
    fragments = list(fragments or [])
    groups, used, skipped = _collect(fragments, max_per_group)
    text, points = _assemble(groups, used, db_type, skipped, max_per_group)
    refs = [p['ref'] for p in points]
    return Observation(text=text, evidence={
        'db_type': db_type,
        'refs': refs,
        'doc_ids': sorted({p['id'] for p in points}),
        'points': points,
        'counts': {
            'fragment_count': len(fragments),
            'used_count': len(used),
            'skipped_count': len(skipped),
            'per_kind': {k: len(v) for k, v in groups.items()},
        },
        'skipped': skipped,
        'empty': not used,
        'renderer': 'offline-rule',
    })


def render_digest_with_llm(fragments: Sequence[dict], db_type: str,
                           llm_renderer: Callable[[list[dict], str], str], *,
                           max_per_group: int = MAX_PER_GROUP) -> Observation:
    """**构建期**用 LLM 重写凝练摘要（论文做法：离线生成一次、入库版本化）。

    `llm_renderer(fragments, db_type) -> str`：接收 `digest()` 已经挑好的结构化要点
    与方言，返回自然语言正文。它拿到的是**已去重、已带 ID 的要点**，拿不到
    原始长文档 —— 这样才能保证 refs 集合不被 LLM 改写。

    刻意**不在推理路径上调用**：本函数是 KB 构建入库时跑的，跑完的结果连同
    `evidence` 一起版本化存下来；推理期直接读存好的 `Observation`。
    LLM 调用失败一律抛出（`llm_renderer` 不是可忽略的旁路），但**不会**悄悄
    回退成规则版 —— 需要规则版就直接调 `digest()`，让调用方明确知道用的是哪个。
    """
    base = digest(fragments, db_type, max_per_group=max_per_group)
    points = list(base.evidence.get('points') or [])
    if not callable(llm_renderer):
        raise TypeError('llm_renderer 必须是可调用对象 (points, db_type) -> str')
    text = llm_renderer(points, db_type)
    if not isinstance(text, str) or not text.strip():
        raise ValueError('llm_renderer 必须返回非空字符串')
    return Observation(text=text.strip(), evidence={**base.evidence, 'renderer': 'llm'})
