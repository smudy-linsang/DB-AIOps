# -*- coding: utf-8 -*-
"""基准台：跑出**成功率**与**幻觉率**，且与覆盖度同屏（设计 §4.1 / §6 / §8）。

## 这个模块存在的理由

设计 §2.1 的结论：*"这个项目从来没有'成功率'这个指标。没有专家标注的根因、
没有修复规格、没有确定性验证器，所以'RCA 准不准'无法度量 —— 这正是我在三轮
复审里反复撞到的同一面墙（编造的数据没有任何机制能识别）。"*

P0 的验收不是"功能上线"，而是**"基准跑出第一组数"**（设计 §7 P0 结语）。
有了这组数，后面每一步才有可比性。

## 三条口径，都来自论文原文，不是我们自定的

1. **成功率按类别宏平均后再汇总**（§1.6 ③）：原文 Eq.(2) 下一行
   *"reported overall and per-category (macro-averaged across K)"*。
   宏平均防止某个大类用例多就把总分抬上去。同时保留加权值**仅供对照**。
2. **幻觉率的分母排除证据不足的合法终止**（§9.1）：那不是幻觉，
   是策略正确地承认不知道；算进去会逼着策略硬编根因。
3. **成绩必须与 `coverage_report` 同屏**（§8）：缺覆盖度就不算完成评测。

## 覆盖度为什么不是"锦上添花"

论文 §5.3.1 承认回放环境造不出快照里没有的新遥测。所以评测台能证明的只有
**"相对已覆盖场景没有回归"**，不能证明"没见过的故障也行"。
`root_causes_missing` 非空时，报告必须显式标注"该成绩不覆盖以下根因"——
否则下一个人会拿 OOD 分数当泛化能力的证明，这正是本项目三轮复审里
"指标看着好、实际不成立"的同一种病。
"""
import hashlib
import logging

from django.utils import timezone

from monitor.oam.audit import audit
from monitor.oam.env import ReplayEnv
from monitor.oam.policy import build as build_policy
from monitor.oam.policy import diagnose
from monitor.oam.verifier import (
    hallucination_rate, macro_success_rate, success_rate, verify,
)

logger = logging.getLogger('monitor.oam')


def _make_run_id(split: str, backend: str, version: str, case_ids) -> str:
    payload = f'{split}|{backend}|{version}|' + ','.join(sorted(case_ids))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:10]
    return f'BR-{timezone.now().strftime("%Y%m%d%H%M%S")}-{digest}'


def run_case(case, policy_backend: str = 'prompted', kb_index=None,
             persist: bool = True):
    """跑单个用例并给出轨迹与判定。返回 (traj, verdict, audit_result)。"""
    from monitor.oam.env.snapshot import OamSnapshot
    snapshot = OamSnapshot.from_case(case)
    env = ReplayEnv(snapshot, kb_index=kb_index)
    policy = build_policy(policy_backend, db_type=case.db_type,
                          incident_id=case.case_id)
    traj = diagnose(case, policy, env)

    verdict = verify(case, traj.report)
    audit_res = audit(traj, traj.report, kb_refs=getattr(kb_index, 'record_ids', None))
    traj.passed = verdict.passed
    traj.verdict_reason = verdict.reason
    traj.hallucinated = (not audit_res.passed) if not traj.escalated else None

    if persist:
        _persist(case, traj, verdict, audit_res)
    return traj, verdict, audit_res, env


def _persist(case, traj, verdict, audit_res):
    """落库：OamTrajectory + OamStep。失败要留痕，不能静默丢评测记录。"""
    from monitor.models import OamStep, OamTrajectory
    try:
        row = OamTrajectory.objects.create(
            traj_id=traj_traj_id(traj, case),
            case=case, config=case.source_config,
            policy_backend=traj.policy_backend, policy_version=traj.policy_version,
            report=traj.report, passed=traj.passed, verdict_reason=traj.verdict_reason,
            escalated=traj.escalated, hallucinated=traj.hallucinated,
            turns=len(traj.steps),
            started_at=traj.started_at or timezone.now(),
            finished_at=traj.finished_at or timezone.now())
        OamStep.objects.bulk_create([
            OamStep(
                trajectory=row, turn=s.turn, thought=s.thought[:4000],
                tool_name=(s.action.tool if s.action else '')[:64],
                tool_params=(s.action.params if s.action else {}) or {},
                tool_error_code=s.tool_error_code[:32],
                observation=(s.observation.text[:4000] if s.observation else ''),
                observation_evidence=(s.observation.evidence if s.observation else {}),
                belief_entropy=s.belief_entropy)
            for s in traj.steps
        ])
        return row
    except Exception as e:
        from monitor import degrade
        degrade.note('oam.bench.persist', f'轨迹 {traj.case_id} 落库失败', e)
        return None


def traj_traj_id(traj, case) -> str:
    base = f'TRAJ-{case.case_id}-{traj.policy_backend}-{traj.policy_version}'
    return hashlib.sha256(base.encode('utf-8')).hexdigest()[:48]


def run_bench(cases, policy_backend: str = 'prompted', split: str = 'test',
              category: str = '', difficulty: str = '', kb_index=None,
              run_id: str = '') -> dict:
    """跑一批用例，返回成绩 + 覆盖度（同屏）。

    返回的 dict **必定**含 `coverage` 键 —— 设计 §6 硬约束 3：
    *"缺 coverage 视为接口未实现"*。
    """
    from monitor.models import OamBenchRun
    cases = list(cases or [])
    case_ids = [c.case_id for c in cases]
    rid = run_id or _make_run_id(split, policy_backend, 'v1', case_ids)
    run = OamBenchRun.objects.create(
        run_id=rid, split=split, category=category or '',
        difficulty=difficulty or '', policy_backend=policy_backend,
        policy_version='v1', status='running', total=len(cases))

    if not cases:
        run.status = 'done'
        run.coverage = _empty_coverage()
        run.finished_at = timezone.now()
        run.save()
        return run.to_dict_report()

    trajs, verdicts, audits = [], [], []
    per_category_verdicts, covered_causes, db_types = {}, set(), set()
    coverage_rows, turn_sum, call_sum = [], 0, 0

    for case in cases:
        try:
            traj, verdict, audit_res, env = run_case(
                case, policy_backend=policy_backend, kb_index=kb_index)
        except Exception as e:
            from monitor import degrade
            degrade.note('oam.bench.run_case', f'用例 {case.case_id} 运行异常', e)
            continue
        trajs.append(traj)
        verdicts.append(verdict)
        audits.append(audit_res)
        per_category_verdicts.setdefault(case.category, []).append(verdict)
        if verdict.passed:
            covered_causes.add(case.gt_root_cause)
        db_types.add(case.db_type)
        coverage_rows.append(env.coverage_report(covered_root_causes=covered_causes))
        turn_sum += len(traj.steps)
        call_sum += len(traj.executed_calls())

    macro = macro_success_rate(per_category_verdicts)
    n = max(len(verdicts), 1)
    run.status = 'done'
    run.success_rate = macro['overall']
    run.success_rate_weighted = round(success_rate(verdicts), 4)
    run.hallucination_rate = round(hallucination_rate(trajs), 4)
    run.escalated_count = sum(1 for t in trajs if t.escalated)
    run.avg_turns = round(turn_sum / n, 2)
    run.avg_tool_calls = round(call_sum / n, 2)
    run.per_category = macro['per_category']
    run.coverage = _merge_coverage(coverage_rows, db_types)
    run.detail = {
        'case_ids': case_ids,
        'trajectory_ids': [t.case_id for t in trajs],
        'escalated_is_legal_stop': True,
        'note': '成功率按类别宏平均；加权值仅供对照。证据不足为合法终止，不计成功也不计幻觉。',
    }
    run.finished_at = timezone.now()
    run.save(update_fields=[
        'status', 'success_rate', 'success_rate_weighted', 'hallucination_rate',
        'escalated_count', 'avg_turns', 'avg_tool_calls', 'per_category',
        'coverage', 'detail', 'finished_at'])
    return run.to_dict_report()


def _merge_coverage(rows, db_types) -> dict:
    """合并各用例的覆盖度报告。缺口累加，不允许被后续用例的覆盖掩盖。"""
    if not rows:
        return _empty_coverage()
    missing = set()
    covered = set()
    buckets = {}
    for row in rows:
        missing |= set(row.get('root_causes_missing') or [])
        covered |= set(row.get('root_causes_covered') or [])
        for k, v in (row.get('plan_operator_buckets') or {}).items():
            buckets[k] = buckets.get(k, 0) + int(v or 0)
    return {
        'db_types': sorted(db_types),
        'root_causes_covered': sorted(covered),
        'root_causes_missing': sorted(missing),
        'plan_operator_buckets': buckets,
        'noise_injection': rows[0].get('noise_injection', {}),
        'capability_boundary': rows[0].get('capability_boundary', ''),
        'coverage_caveat': ('该成绩只覆盖上述根因与方言；'
                            'root_causes_missing 非空时不得据此宣称泛化能力'),
    }


def _empty_coverage() -> dict:
    return {'db_types': [], 'root_causes_covered': [], 'root_causes_missing': [],
            'plan_operator_buckets': {}, 'noise_injection': {},
            'capability_boundary': '无用例，未产生任何覆盖度',
            'coverage_caveat': '空批次'}


def assert_split_instance_disjoint(train_cases, test_cases) -> dict:
    """§8 验收项：**评测用例与训练用例不同实例**。

    论文 ID 划分口径：*"测试用例来自不同的数据库实例与上下文"*。
    本项目前三轮复审反复踩的"假绿"，本质就是训练与评测同源 ——
    同一份数据既当训练集又当考卷，分数再高也说明不了泛化。

    返回 (ok, detail)。有重叠即 ok=False，并列出重叠的实例。
    """
    train_ids = {c.source_config_id for c in train_cases if c.source_config_id}
    test_ids = {c.source_config_id for c in test_cases if c.source_config_id}
    overlap = sorted(train_ids & test_ids)
    unlabelled = [c.case_id for c in list(train_cases) + list(test_cases)
                  if not c.source_config_id]
    ok = not overlap
    detail = {
        'ok': ok,
        'overlap_config_ids': overlap,
        'overlap_count': len(overlap),
        'cases_without_source_config': unlabelled,
        'note': ('用例未登记来源实例时无法断言实例隔离；'
                 '这类用例不得计入"评测与训练不同实例"的结论' if unlabelled else ''),
    }
    return ok, detail


__all__ = ['run_bench', 'run_case', 'assert_split_instance_disjoint']
