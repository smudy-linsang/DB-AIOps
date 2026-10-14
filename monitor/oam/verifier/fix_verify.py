# -*- coding: utf-8 -*-
"""修复验证（论文 §3.3 Eq.(1)(2)）。

原文逐字：

```
H(Ŷ; Σ) = 1 ,  当且仅当  ĉ = c*  且  H_rem(m̂; Σ) = 1
        = 0 ,  其他

令 ν ∈ {0,1} 标记该事件是否属于"可执行验证"：

H_rem = 1 , 若 ν = 1 且 Lat(m̂; Σ) ≤ t_ref 且 Out(m̂) = Out(ref)
      = 1 , 若 ν = 0 且 m̂ ≡ m*
      = 0 , 其他
```

## 三条容易抄错的，此处逐条落实

**① 可执行判据是"不更慢且结果一致"，不是"快多少"。**
论文用 `≤` 而非 `>`，排除的是"改完更慢"和"改完结果变了"，**不要求性能提升达标**。
设计 §8 专门有一条验收项防"自作主张加'性能提升需达 X%'门槛把正确修复判成失败"。
所以本模块用 `<=`，并且**不提供任何提升幅度门槛参数**。

**② 诊断过程记录不进入成败判据。** 论文原文明说 does not enter the pass/fail
criterion —— 它供二次分析与证据审计用。本模块的入参只有根因与修复规格。

**③ 咨询类任务的判据是证据集合完全相等。**
论文 §3.3：咨询类任务的"修复规格"是一个必需的证据集合（KB 文档编号），
判定要求引用的文档集合与规格**完全相等** —— 集合相等，不是包含、不是相似。

## 无法判定时怎么办

可执行案例若缺少可执行条件（无执行器、快照也没记实测期望值），
**判失败并写明"缺可执行校验条件"**，绝不退化成"看起来对就算过"。
理由与回放环境的边界同源：判不出真假的场景如果记成通过，评测台就开始生产假绿。
"""
from monitor.oam.verifier.normalize import normalize_fix


def verify_by_execution(case, fix, executor=None) -> tuple:
    """可执行案例：施加修复后比对时延与执行结果。返回 (ok, reason, detail)。

    `executor` 形如 `fn(case, fix) -> {'latency': float, 'output': str}`。
    三条取值路径，按可靠性从高到低：

      1. **有执行器** → 在集群内真正施加修复并测量（生产/回放台的真实路径）；
      2. **无执行器、但 `fix_spec` 记了实测期望值** → 用录制时的测量值判定；
      3. **两者皆无** → 判失败，理由写明缺条件（不猜测）。
    """
    spec = dict(getattr(case, 'fix_spec', None) or {})

    # 先确认"是不是同一类修复"。这一条挡的是张冠李戴：根因对、修复错配。
    want = normalize_fix(spec.get('params', spec), getattr(case, 'db_type', ''))
    got = normalize_fix(fix, getattr(case, 'db_type', ''))
    if want and got and want != got:
        return False, f'修复规格不匹配：给出 {got} ≠ 期望 {want}', {'given': got, 'want': want}

    t_ref = spec.get('t_ref')
    out_ref = spec.get('out_ref')

    measured = None
    if executor is not None:
        try:
            measured = executor(case, fix)
        except Exception as e:
            return False, f'施加修复时执行失败：{type(e).__name__}: {e}', {}
    elif 'measured_latency' in spec or 'measured_output' in spec:
        measured = {'latency': spec.get('measured_latency'),
                    'output': spec.get('measured_output')}

    if measured is None:
        return False, ('缺可执行校验条件：既无执行器，快照也未记录 measured_latency/'
                       'measured_output，故不可判定（不猜测，判失败）'), {}

    lat = measured.get('latency')
    out = measured.get('output')

    if lat is None or t_ref is None:
        return False, '缺可执行校验条件：缺少时延或基线时延', {'measured': measured}

    # ① 判据一：Lat(m̂;Σ) ≤ t_ref —— 注意是 <=，不是"快 X%"
    if float(lat) > float(t_ref):
        return False, f'施加修复后更慢：Lat={lat} > t_ref={t_ref}', {'latency': lat,
                                                                    't_ref': t_ref}
    # ② 判据二：Out(m̂) = Out(ref)
    if out_ref is not None and out != out_ref:
        return False, '施加修复后结果与基线不一致（Out(m̂) ≠ Out(ref)）', {
            'output': out, 'out_ref': out_ref}
    return True, f'可执行验证通过：Lat={lat} ≤ t_ref={t_ref} 且结果一致', {
        'latency': lat, 't_ref': t_ref}


def verify_by_spec(case, fix) -> tuple:
    """不可执行案例：与事件规格在固定归一化规则下比对。返回 (ok, reason, detail)。"""
    spec = dict(getattr(case, 'fix_spec', None) or {})
    db_type = getattr(case, 'db_type', '')
    want = normalize_fix(spec.get('params', spec), db_type)
    got = normalize_fix(fix, db_type)
    if not got:
        return False, '未给出修复规格', {}
    if not want:
        return False, '用例缺少 fix_spec，无法判定修复是否正确（基准集有误）', {}
    if want != got:
        return False, f'修复规格不匹配：归一化后 {got} ≠ {want}', {
            'given_normalized': got, 'want_normalized': want}
    return True, '修复规格比对通过（归一化后相等）', {
        'given_normalized': got, 'want_normalized': want}


def verify_consultation(case, cited) -> tuple:
    """咨询类任务：引用的 KB 文档集合与规格**完全相等**。

    论文 §3.3：判定要求引用的文档集合与规格完全相等 —— 集合相等，
    不是包含、不是相似。所以多引一篇也算错：咨询类任务的价值就在于
    "该引的都引了、没引无关的"。
    """
    want = set(getattr(case, 'required_evidence', None) or [])
    got = set(cited or [])
    if not want:
        return False, '咨询用例缺少 required_evidence（基准集有误）', {}
    if not got:
        return False, '未引用任何知识库文档', {}
    if got != want:
        missing = sorted(want - got)
        extra = sorted(got - want)
        parts = []
        if missing:
            parts.append(f'缺引用 {missing}')
        if extra:
            parts.append(f'多引用 {extra}')
        return False, '引用的文档集合与规格不相等（' + '，'.join(parts) + '）', {
            'missing': missing, 'extra': extra}
    return True, '引用的文档集合与规格完全相等', {'cited': sorted(got)}


__all__ = ['verify_by_execution', 'verify_by_spec', 'verify_consultation']
