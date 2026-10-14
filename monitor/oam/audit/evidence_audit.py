# -*- coding: utf-8 -*-
"""L5 独立证据审计（论文 §5.5 的生产做法，设计 §4.5）。

论文原文：*"报告呈现给 DBA 之前，独立的 LLM 审计模块依固定评分提示审查诊断轨迹，
检查每一条根因主张与修复**是否由工具观测或知识库证据支撑**，标记无支撑推理或
证据缺失，低置信/高风险转人工。"*

## 本实现的取舍：确定性优先，LLM 审计是增强而非前提

论文用**另一个更大的模型**（Qwen3-235B）+ 固定评分提示做审计，并强调
*"审计模块必须独立于产出报告的策略"*（设计 §4.5 原话："用同一个模型自审等于没审"）。

本项目 P0 阶段**先用确定性检查**，理由是它对目标缺陷的覆盖更直接：

> R25-02 的编造（编造 ASH 会话、编造表空间 90.3%、编造执行计划）的本质是
> **"事实没有对应的观测 ID"**。确定性检查直接核对这一点：
> 主张引用的 evidence_ref 必须能在轨迹的观测证据或 KB 记录里找到。
> 编造出的"事实"没有来源 ID，**必然**被标记 —— 这比让另一个模型去"读一遍"
> 是否像真的更可靠，也不受提示词漂移影响。

LLM 语义审计（查"结论虽有条证据但推不出来"）留作后续增强，接口已留。

## 审计结论与评测口径的衔接

`AuditResult.passed=False` ⇒ 轨迹标记 `hallucinated=True`，
进幻觉率分子。但**证据不足的合法终止不参与幻觉率**
（见 `verifier.hallucination_rate`）—— 否则会逼着策略硬编根因。
"""
import logging
from dataclasses import dataclass, field

from monitor import appconf
from monitor.oam.verifier import STATUS_EVIDENCE_INSUFFICIENT

logger = logging.getLogger('monitor.oam')


@dataclass
class Claim:
    """一条需要证据支撑的主张。"""

    kind: str              # root_cause / fix / fact
    text: str
    evidence_refs: list = field(default_factory=list)

    def __str__(self):
        return f'[{self.kind}] {self.text}'


@dataclass
class AuditResult:
    passed: bool
    unsupported: list = field(default_factory=list)
    route_to_human: bool = False
    reason: str = ''
    checked_refs: set = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            'passed': self.passed,
            'route_to_human': self.route_to_human,
            'reason': self.reason,
            'unsupported': [{'kind': c.kind, 'text': c.text,
                             'evidence_refs': c.evidence_refs}
                            for c in self.unsupported],
            'unsupported_count': len(self.unsupported),
        }


def extract_claims(report: dict) -> list:
    """从报告里抽出所有需要证据支撑的主张。

    抽三类：
      · `root_cause` —— 根因主张（论文的"每一条根因主张"）；
      · `fix`        —— 修复主张（"修复是否由…证据支撑"）；
      · `claims[]`   —— 报告自陈的附加事实性论断（如"该表 1200 万行"）。
    """
    report = report or {}
    out = []
    if report.get('root_cause'):
        out.append(Claim('root_cause', str(report['root_cause']),
                         list(report.get('evidence_refs') or [])))
    fix = report.get('fix')
    if fix:
        out.append(Claim('fix', str(fix),
                         list((report.get('fix_evidence_refs')
                               or report.get('evidence_refs') or []))))
    for c in report.get('claims') or []:
        if isinstance(c, dict):
            out.append(Claim(str(c.get('kind') or 'fact'), str(c.get('text') or ''),
                             list(c.get('evidence_refs') or [])))
    return out


def _known_refs(trajectory, kb_refs=None) -> set:
    """轨迹中出现过的全部证据引用 + 知识库已知记录 ID。"""
    refs = set()
    for step in getattr(trajectory, 'steps', []) or []:
        obs = getattr(step, 'observation', None)
        if obs and obs.evidence:
            refs.update(str(r) for r in (obs.evidence.get('refs') or []))
        # 元数据/参数类工具也会留一个 tool:<name> 引用
        name = getattr(step.action, 'tool', '') if getattr(step, 'action', None) else ''
        if name and not getattr(step, 'tool_error_code', ''):
            refs.add(f'tool:{name}')
    refs.update(str(r) for r in (kb_refs or []))
    return refs


def audit(trajectory, report: dict, kb_refs=None) -> AuditResult:
    """逐条核对根因主张与修复是否有工具观测或 KB 证据支撑。

    `kb_refs` 是知识库中真实存在的记录 ID 集合（由 L3 的索引提供）。
    传入空集合意味着"KB 未接入" —— 此时只认轨迹内的观测引用。
    """
    report = report or {}

    # 证据不足的合法终止：不是幻觉，但也**不允许**它顺手塞一个根因进来。
    if str(report.get('status') or '').lower() == STATUS_EVIDENCE_INSUFFICIENT:
        if report.get('root_cause'):
            return AuditResult(
                passed=False, route_to_human=True,
                unsupported=[Claim('root_cause', str(report['root_cause']),
                                    list(report.get('evidence_refs') or []))],
                reason='报告声明证据不足却仍给出了根因 —— 这是编造，不是诚实')
        return AuditResult(passed=True, route_to_human=True,
                           reason='策略声明证据不足并转人工（合法终止，不记幻觉）')

    known = _known_refs(trajectory, kb_refs)
    min_refs = appconf.get('OAM_EVIDENCE_MIN_REFS')

    claims = extract_claims(report)
    unsupported = []
    for claim in claims:
        refs = [str(r) for r in claim.evidence_refs]
        hit = [r for r in refs if r in known]
        if len(hit) < min_refs:
            unsupported.append(claim)

    # 无支撑的主张一律转人工 —— 论文的"低置信/高风险转人工"
    low_conf = bool(report.get('confidence_low'))
    route = bool(unsupported) or low_conf
    reason = ('全部主张均有观测或 KB 证据支撑' if not unsupported
              else f'{len(unsupported)} 条主张缺少证据支撑：'
                   + '；'.join(f'{c.kind}={c.text}' for c in unsupported[:3]))
    if low_conf:
        reason += '；策略自报低置信'

    return AuditResult(passed=not unsupported, unsupported=unsupported,
                       route_to_human=route, reason=reason, checked_refs=known)


def persist_miss(trajectory_id: str, kb_category: str, query_text: str,
                 db_type: str, top_similarity):
    """写检索未命中留痕（论文部署失败原因里占 12%，不记就无从改进索引）。

    任何异常都必须留痕（`degrade.note`），不静默吞掉 —— 红线要求。
    """
    from monitor.models import OamRetrievalMiss
    try:
        OamRetrievalMiss.objects.create(
            trajectory_id=trajectory_id or '', kb_category=kb_category or '',
            query_text=(query_text or '')[:4000], db_type=db_type or '',
            top_similarity=top_similarity)
    except Exception as e:
        from monitor import degrade
        degrade.note('oam.kb.retrieval_miss', '检索未命中留痕写入失败', e)


__all__ = ['audit', 'AuditResult', 'Claim', 'extract_claims', 'persist_miss']
