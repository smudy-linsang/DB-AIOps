# -*- coding: utf-8 -*-
"""L1 验证器：判定一份诊断报告"对不对"。

**这是整个 V3.0 的地基。** 设计 §4.1 说得很直白：*"它是 RL 的训练场，但不依赖 RL
—— 它本身就是这个项目三轮复审都缺的那个东西：一个能判断'诊断结论对不对'的
离线评测台。没有它，任何 AI 能力的改进都只能靠人看，这就是编造数据能活三个版本的原因。"*

论文原文（§3.3 Eq.1）：

```
H(Ŷ; Σ) = 1 ,  当且仅当  ĉ = c*  且  H_rem(m̂; Σ) = 1
        = 0 ,  其他
```

## 判定顺序（照原文，不调换）

    1) 确定性抽取 → **解析失败判 0**
    2) 根因匹配   → 轻度归一化后精确相等
    3) 修复验证   → 可执行：Lat ≤ t_ref 且 Out 相等
                     不可执行：修复规格 ≡ 期望规格（归一化后）
                     咨询类：引用文档集合与规格**完全相等**

## "证据不足"是合法终止，不是失败

论文 §3.3 Limitation 原文：*"In production, the closed label spaces are used only for
automatic verification and offline evaluation. DBAgent is **not forced to choose a known
cause** when evidence is insufficient."*

所以 `Verdict.escalated=True` 与 `passed=False` 是**正交**的两件事：
策略输出"证据不足"并要求转人工时，**不计成功，但也不计幻觉**
（评测口径见 `bench/runner.py`）。

这条不是宽容，是防策略被奖励推着强行归类 —— 与 §9.3 的频率偏差叠加后会
让智能体专门去确认常见根因。V3.0 的策略必须被允许说"我不知道"。
"""
import json
import logging

from monitor.oam.labels import CAT_CONSULTATION
from monitor.oam.types import Verdict
from monitor.oam.verifier.cause_match import match_cause
from monitor.oam.verifier.fix_verify import (
    verify_by_execution, verify_by_spec, verify_consultation,
)

logger = logging.getLogger('monitor.oam')

# 报告状态里表示"证据不足"的取值。与 API 层的 evidence_insufficient 对齐。
STATUS_EVIDENCE_INSUFFICIENT = 'evidence_insufficient'


def extract_report(report) -> tuple:
    """确定性抽取器：把报告解析成 {root_cause, fix, evidence_refs, status}。

    论文原文：*"报告在 answer 标签内，确定性抽取器解析，**解析失败判 0**"*。
    返回 (parsed, err)；err 非空即判 0，不做任何"尽力猜一下"的补救。
    """
    if report is None:
        return None, '报告为空'
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except (ValueError, TypeError) as e:
            return None, f'报告不是合法 JSON: {e}'
    if not isinstance(report, dict):
        return None, f'报告类型非法（应为对象，实为 {type(report).__name__}）'

    # 证据不足是合法的终态，先于任何判定识别出来
    status = str(report.get('status') or '').strip().lower()
    parsed = {
        'root_cause': report.get('root_cause', ''),
        'fix': report.get('fix', report.get('fix_spec', {})),
        'evidence_refs': report.get('evidence_refs') or [],
        'status': status,
        'confidence': report.get('confidence'),
    }
    if status == STATUS_EVIDENCE_INSUFFICIENT:
        return parsed, None
    if not parsed['root_cause']:
        return None, '报告缺少 root_cause 字段'
    return parsed, None


def is_evidence_insufficient(parsed: dict) -> bool:
    return str((parsed or {}).get('status') or '').strip().lower() == STATUS_EVIDENCE_INSUFFICIENT


def verify(case, report, executor=None) -> Verdict:
    """判定一份报告。`case` 需具备 OamCase 的字段。

    `executor` 仅可执行案例用：形如 `fn(case, fix) -> {'latency','output'}`，
    在集群内真正施加修复并测量。离线评测通常传 None，走 fix_spec 里录制好的
    测量值；两者都没有时**判失败**（不可判定不等于通过）。
    """
    parsed, err = extract_report(report)
    if err:
        return Verdict(passed=False, reason=f'报告解析失败: {err}', score=0.0,
                       detail={'extract_error': err})

    # ---- 合法终止：证据不足，转人工 ----
    if is_evidence_insufficient(parsed):
        return Verdict(
            passed=False, escalated=True, score=0.0,
            reason='策略判定证据不足并请求转人工（合法终止：不计成功，也不计幻觉）',
            detail={'root_cause': parsed.get('root_cause') or '',
                    'evidence_refs': parsed.get('evidence_refs') or []})

    db_type = getattr(case, 'db_type', '')

    # ---- 2) 根因匹配 ----
    ok, reason = match_cause(parsed['root_cause'], getattr(case, 'gt_root_cause', ''), db_type)
    if not ok:
        return Verdict(passed=False, reason=f'根因不匹配：{reason}', score=0.0,
                       detail={'predicted': parsed['root_cause'],
                               'expected': getattr(case, 'gt_root_cause', '')})

    # ---- 3) 修复验证 ----
    if getattr(case, 'category', '') == CAT_CONSULTATION:
        ok, reason, detail = verify_consultation(case, parsed.get('evidence_refs'))
        branch = 'consultation'
    elif getattr(case, 'fix_executable', False):
        ok, reason, detail = verify_by_execution(case, parsed['fix'], executor=executor)
        branch = 'executable'
    else:
        ok, reason, detail = verify_by_spec(case, parsed['fix'])
        branch = 'spec'

    if not ok:
        return Verdict(passed=False, cause_matched=True, fix_ok=False, score=0.0,
                       reason=reason, detail=detail)

    return Verdict(passed=True, cause_matched=True, fix_ok=True, score=1.0,
                   reason=f'{branch} 验证通过：{reason}', detail=detail)


def success_rate(verdicts) -> float:
    """总体成功率 SR = (1/|D|) Σ H(Ŷ;Σ)（论文 Eq.2）。

    **证据不足的合法终止计入分母、不计入分子** —— 它既不是成功，
    也不是"没做完"，论文没有为它单列口径；但把它排除在分母外会让
    "总是说不知道"的策略刷出满分。
    """
    items = list(verdicts or [])
    if not items:
        return 0.0
    return sum(1 for v in items if v.passed) / len(items)


def macro_success_rate(by_category) -> dict:
    """按类别宏平均后再汇总（论文 Eq.(2) 下一行）。

    论文原文：*"reported overall and per-category (macro-averaged across K)"*。
    宏平均防止"某个大类用例多就把总分抬上去"。
    """
    rates = []
    per_cat = {}
    for category, verdicts in sorted((by_category or {}).items()):
        r = success_rate(verdicts)
        per_cat[category] = r
        rates.append(r)
    return {'per_category': per_cat,
            'overall': round(sum(rates) / len(rates), 4) if rates else 0.0}


def hallucination_rate(trajectories) -> float:
    """幻觉率 = 证据审计判定"存在无支撑主张"的轨迹占比。

    分母**排除**证据不足的合法终止 —— 那不是幻觉，是策略正确地承认不知道
    （设计 §9.1）。把合法终止算成幻觉会逼着策略硬编一个根因。
    """
    auditable = [t for t in (trajectories or []) if not getattr(t, 'escalated', False)]
    if not auditable:
        return 0.0
    flagged = sum(1 for t in auditable if getattr(t, 'hallucinated', False))
    return flagged / len(auditable)


__all__ = [
    'verify', 'extract_report', 'is_evidence_insufficient',
    'success_rate', 'macro_success_rate', 'hallucination_rate',
    'STATUS_EVIDENCE_INSUFFICIENT',
]
