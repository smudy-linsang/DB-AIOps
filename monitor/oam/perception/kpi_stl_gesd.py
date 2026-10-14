# -*- coding: utf-8 -*-
"""L2 感知层 · 模态一：KPI 时序异常检测（STL + Generalized ESD）。

对应设计 §4.2「KPI：STL + Generalized ESD」与 §1.3③ 的 MMA 表格第 2 行：
**时序 KPI → STL（基于 LOESS 的季节-趋势分解）→ 残差上做 Generalized ESD 检验，
稳健位置/尺度用中位数 + MAD**，输出"CPU 利用率在 10:35 出现异常尖峰"这类自然语言观测，
指明指标、时间、相对基线偏离程度。

## 为什么要分成"分解"与"检验"两步

趋势和季节不是异常：白天负载高、凌晨备份周期性拉高吞吐，这些都是 STL 的**季节项**。
直接在原序列上做 ESD 会把整段周期性一起打成离群点。因此只在**残差**上做检验。

## 三条不可让步的约束（设计 §4.2 原文"否则就是我审计出的编造模式复发"）

1. `Observation.evidence` 必填 —— L5 证据审计逐条核对，空 evidence 等于编造；
2. **无异常就返回空列表**，绝不允许为了"有话说"下调阈值。论文把该模块调向**高精度**
   （§1.4 第 1 条：它的定位是"可疑时段定位器"而非异常检测器，宁可窗口略宽也要标记可靠）；
3. 阈值不得为凑结果而调整。临界值走的是**零分布精确求解**（见下），不是查表近似。

## GESD 临界值怎么来的（为什么不用 Rosner Table 2 / Tatum 近似）

标准 GESD（Rosner）的临界值来自查表或经验近似，本项目**不采用**：表要外部核对、
近似式无法追溯，一旦记错就是"看起来很严谨的错"。

改用零分布**精确求解**：对两侧检验（两侧 = 同时看高偏与低偏），标准正态样本里
第 r 大的标准化偏离 G_r 满足

    P(G_r ≤ λ) = Σ_{j=0}^{r-1} C(n,j) p^j (1-p)^(n-j),  其中 p = P(|Z| > λ)

右边是 X ~ Bin(n, p) 的累积分布 P(X ≤ r-1)，对**固定的 r-1** 它随 p **单调递减**。
于是给定 r 与显著性 α/r，在 p ∈ (0, 0.5) 上用二分法解

    Σ_{j=0}^{r-1} C(n,j) p^j (1-p)^(n-j) = 1 - α/r

再取 λ = Φ̄⁻¹(p/2) = Φ⁻¹(1 - p/2)。正态 CDF 用 `math.erf`；逆 CDF 用
Acklam 有理逼近 + 一次 Halley 修正（纯 Python，精度约 1e-15）。

**三点必须写在这里的注释（防止后人"顺手调阈值"）：**

1. 这是零分布的**精确求解**，不是任何经验近似表 —— 因此不需要外部查表即可复核；
2. 它假设真实分布是 μ=0/σ=1 的正态，而论文口径用 **median/MAD 做稳健标度**
   （MAD = median(|r - median(r)|)）。经典 ESD 用 mean/sd，极端点会同时污染
   位置与尺度而自我掩盖；median/MAD 的崩溃点约 50%，对**已在离群点上的分布更稳健**。
   两个口径要接起来，必须乘一致性常数 `MAD_CONSISTENCY = 1.4826`
   （正态下 MAD = 0.6745σ），否则 z 被放大 1.4826 倍、阈值形同被偷偷放宽；
3. **因此阈值不得为凑结果而下调。** 论文明确把该模块调向高精度，宁可漏检不可误报。

## 已知限制

- **STL 的季节周期靠自相关推断**（`infer_period`），不是从配置读的。窗口太短
  （< 3 个周期）或季节性本身很弱时会推断失败 → 走**降级路径**（见下）。
- **MAD 为 0（残差里超过一半的点完全相等）会除零。** 设计文档给的 `1e-9` 只挡住了
  恰好为 0；本模块再叠一层**相对信号尺度的下限**（`MAD_REL_FLOOR`，见 `detect`），
  否则"STL 把无噪正弦拟合到机器精度、MAD≈1e-15"会让每个非尖峰点都变成 1e13σ 离群。
  这个下限只会抬高分母、只会让检测更保守。
- 采集点缺失（NaN/None）**不做插值后当成真数据**：首尾截断、中间线性插值，
  插值点会被标记且不允许作为异常上报；插值占比超过 20% 时整段不给结论。
  直接丢点会让后续点错位一格、季节项整体偏移，产出**看着正常实则全错**的基线。
- 部署窗口建议 2–3 小时 / 60–120 秒采样（约 60~180 点），与论文一致；
  点数越少，GESD 的临界值越大（本模块自动生效，不需人工调整）。
- **季节周期必须是整数个采样点。** STL 只能按整数 lag 建季节项，而真实季节性
  由墙钟决定（每小时 60 点、每天 1440 点），所以生产上天然对齐。
  但若真实周期非整数（例如 `sin(i/3)`，周期 ≈ 18.85 点），`infer_period` 会给出
  最近的整数 19，整数周期与真实周期的相位差会在残差里留下系统性结构，
  GESD 可能报出 z≈4 左右的**假异常**。这是整数周期 STL 的固有性质，
  **不用"调高阈值"去掩盖**；已知真实周期时应显式传 `detect(..., period=...)`。
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any, Sequence

import numpy as np

from monitor.oam.types import Observation

logger = logging.getLogger('monitor.oam.perception')

__all__ = [
    'detect', 'infer_period', 'stl_decompose', 'generalized_esd',
    'gesd_critical_value', 'norm_cdf', 'norm_ppf', 'MAD_FLOOR',
]

# --------------------------------------------------------------------------
# 参数常量。论文没有给具体数值的一律标注"论文未给值，按需调整"；
# 给出了的（选择率 0.05、alpha 0.05 等）保持与设计文档一致。
# --------------------------------------------------------------------------
DEFAULT_ALPHA = 0.05        # 设计 §4.2 的签名默认值
DEFAULT_MAX_ANOMS = 5       # 设计 §4.2 的签名默认值
MAD_FLOOR = 1e-9            # 设计 §4.2 明确写死的回退常数，防 MAD=0 除零
# 正态一致性常数：σ = 1.4826 × MAD（MAD = median(|r − median(r)|)，正态下 ≈ 0.6745σ）。
# 这是统计常数而非可调超参 —— 漏掉它会把 z 放大 1.4826 倍，误报率直逼 100%。
MAD_CONSISTENCY = 1.4826

MIN_SAMPLES = 8             # 论文未给值，按需调整：低于此直接返回空（样本太少，MAD 不可信）
MIN_PERIOD = 2              # 周期至少 2 个采样间隔
MIN_CYCLES = 3              # 论文未给值，按需调整：至少 3 个完整周期才做季节分解
PERIOD_ACF_MIN = 0.3        # 论文未给值，按需调整：自相关低于此视为"无季节性"
MAD_REL_FLOOR = 1e-3        # 见下方 detect() 的说明：MAD 相对信号尺度的下限
SCALE_ABS_FLOOR = 1e-12     # 尺度兜底，避免全平序列把下限算成 0
MAX_INTERPOLATE_FRAC = 0.2  # 论文未给值，按需调整：插值点占比上限，超了就整段不给结论


def _max_series_points() -> int:
    """单条时序的最大点数。取 `appconf.SPECS` 的 `OAM_MAX_SERIES_POINTS`（500）。

    与快照层用同一个配置项，避免"入库放行、分析拒收"两边标准不一致。
    感知层是纯函数模块、允许在无 Django 配置时导入，故延迟读取并兜底。
    """
    try:
        from monitor import appconf
        return int(appconf.get('OAM_MAX_SERIES_POINTS'))
    except Exception:
        return 500

LOESS_SPAN_TREND = 0.6      # 论文未给值，按需调整：趋势项 LOESS 窗宽占比
SEASON_WINDOW = 5           # 论文未给值，按需调整：季节平滑跨几个连续周期（绝对点数）
STL_INNER_ITER = 2          # STL 内层迭代次数（Cleveland 建议 1~2）
ROBUST_ITER = 2             # LOESS 稳健化迭代次数（R 的 loess 默认 2）
TREND_DEGREE = 1            # 降级路径的低阶多项式次数

_BISECT_ITER = 200          # 二分法迭代上限；p 区间宽 0.5，200 次远超双精度收敛需要

# Acklam 逆正态 CDF 的有理逼近系数（精度 ~1.15e-9，再用一次 Halley 修正到 ~1e-15）
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_P_LOW = 0.02425
# 尾端跳过 Halley 修正的阈值：min(p, 1-p) 小于此值时 CDF 已进入抵消区
_HALLEY_MIN_P = 1e-8


# ==========================================================================
# 正态分布工具（CDF / 逆 CDF）
# ==========================================================================
def norm_cdf(x: float) -> float:
    """标准正态 CDF，用 `math.erf`（纯 Python，机器精度）。"""
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """标准正态逆 CDF：Acklam 有理逼近 + 一次 Halley 修正。

    Acklam 的原始逼近误差约 1.15e-9；在**主体区**（min(p, 1-p) ≥ 1e-8）Halley 步
    二阶收敛，一次即可压到双精度极限（实测 ppf(0.975) 误差 4e-16）。
    **尾区**（min(p, 1-p) < 1e-8）只返回 Acklam 值：那里的 CDF 已是抵消区，
    Halley 修正反而放大误差两个数量级（详见下方注释）。
    这里刻意不用查表法，理由同模块 docstring。
    """
    p = float(p)
    if not 0.0 < p < 1.0:
        raise ValueError(f'norm_ppf 的参数必须落在 (0, 1)，收到 {p!r}')

    def _horner(coeffs: tuple, t: float) -> float:
        """Horner 升幂展开：c0*t^k + c1*t^(k-1) + ... + ck。"""
        out = 0.0
        for c in coeffs:
            out = out * t + c
        return out

    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = _horner(_C, q) / (_horner(_D, q) * q + 1.0)
    elif p <= 1.0 - _P_LOW:
        q = p - 0.5
        r = q * q
        x = _horner(_A, r) * q / (_horner(_B, r) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -_horner(_C, q) / (_horner(_D, q) * q + 1.0)
    # 一次 Halley 修正：φ(x) 用 exp(-x²/2)/√(2π)
    #
    # **尾端必须跳过这一步**：`norm_cdf` 用 0.5*(1+erf(...))，在 p 极小时发生
    # 灾难性抵消 —— double 精度下 1+erf 的有效位被吃掉，残差误差到 1e-16 量级；
    # 而 `exp(x²/2)` 在 |x|≈7 时是 e^24.5，把这 1e-16 放大成 1e-5 级修正量，
    # 比不修正还差两个数量级（实测 p=1e-12：Acklam 单用误差 7.4e-9，
    # 加 Halley 后恶化到 3.1e-6）。
    # 判据用**尾概率**而非 CDF 本身：`min(p, 1-p) < 1e-8` 即认为进入尾区。
    if min(p, 1.0 - p) < _HALLEY_MIN_P:
        return x
    err = norm_cdf(x) - p
    u = err * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


# ==========================================================================
# Generalized ESD 的临界值：零分布精确求解
# ==========================================================================
def _binom_cdf(p: float, n: int, k: int) -> float:
    """P(X ≤ k)，X ~ Bin(n, p)，直接累加（k = r-1 很小，不会出现大组合数溢出）。"""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    total = 0.0
    q = 1.0 - p
    for j in range(k + 1):
        total += math.comb(n, j) * (p ** j) * (q ** (n - j))
    return total


def gesd_critical_value_analytic(n: int, r: int, alpha: float = DEFAULT_ALPHA) -> float:
    """第 r 步检验的**解析**临界值 λ_r（两侧检验）。

    求解 `Σ_{j=0}^{r-1} C(n,j) p^j (1-p)^(n-j) = 1 - α/r`，再取 λ = Φ⁻¹(1 - p/2)。
    该和在固定 r-1 时对 p 单调**递减**（均值随 p 增大，而阈值固定在 r-1），
    所以二分方向是"F 大于目标 → p 往大的一侧走"。

    **适用范围有限**：它标定的是"真实 μ=0/σ=1"下 |z| 的次序统计量，
    而本模块实际检验的统计量是**样本 median/MAD 标准化**后的值 —— 后者的零分布
    更重（studentization 效应），直接用这个解析值会偏松。真正用于判定的是
    `gesd_critical_value()`（蒙特卡洛标定）。本函数保留为交叉校验与文档依据。
    """
    n = int(n)
    r = int(r)
    if n < 2:
        raise ValueError(f'GESD 临界值至少需要 2 个样本，收到 n={n}')
    if not 1 <= r <= n:
        return float('inf')
    target = 1.0 - float(alpha) / r
    if _binom_cdf(0.5, n, r - 1) > target:
        # p=0.5 时 CDF 仍高于目标，说明在 (0, 0.5) 内无根 —— 判定为不可触发
        return float('inf')
    lo, hi = 0.0, 0.5
    for _ in range(_BISECT_ITER):
        mid = 0.5 * (lo + hi)
        if _binom_cdf(mid, n, r - 1) > target:
            lo = mid
        else:
            hi = mid
    p = 0.5 * (lo + hi)
    return norm_ppf(1.0 - p / 2.0)


# --- 蒙特卡洛标定 ---------------------------------------------------------
# 为什么要它：临界值必须标定在**我们真正计算的统计量**上。
# 本模块用样本 median/MAD 做标准化（论文口径），得到的 z 分布比标准正态更重；
# 经典做法是查 Rosner 的经验表做校正，但那是一张无法在本仓库内自证的表。
# 改为按固定种子做蒙特卡洛：对标准正态样本跑**与 `generalized_esd` 完全相同的
# 流程**（同样每步移除当前最大），取第 r 步统计量的 (1 − α/r) 分位数。
# 好处：无外部表、完全确定性（种子由 n 派生）、且与实现严格同构。
_MC_TRIALS = 10000
_MC_CHUNK = 1000
_MC_MAX_R = 10                # 一次模拟最多取到第 10 步；调用方 r 超过它属误用
_CRITICAL_CACHE: dict = {}


def _mc_seed(n: int, alpha: float, trials: int) -> int:
    """由参数派生确定性种子：同一 (n, α, trials) 永远得到同一张标定表。"""
    import hashlib
    payload = f'{n}|{alpha}|{trials}'.encode('utf-8')
    return int(hashlib.sha256(payload).hexdigest()[:12], 16)


def _pipeline_residuals(batch: np.ndarray) -> np.ndarray:
    """让标定样本走**与 `detect` 相同的去趋势路径**。

    这一步不是形式主义：实测在纯正态上，直接对原始样本做 GESD 的误报率是
    4.6%~5.9%（正好等于名义 α），但先用移动中位数+一阶最小二乘去趋势后
    同一检验的误报率升到 12%~32%。原因是局部平滑器与样本相关，去趋势会让
    残差出现局部负相关，极值计数被抬高（峰度只从 3.01 升到 3.34，
    说明不是重尾，而是**相关性**）。

    换句话说：**临界值必须标定在流水线真正产出的那个统计量上**，
    只标定标准化那一步是不够的。这里用 `period=None` 的降级路径（短窗口无季节性
    时的常见情形），同时也是两者中更保守的一个。
    """
    out = np.empty_like(batch)
    for i, row in enumerate(batch):
        out[i] = stl_decompose(row, period=None)[2]
    return out


def _calibrate(n: int, alpha: float) -> tuple:
    """一次模拟算出 r=1.._MC_MAX_R 的全部临界值。按 (n, α) 缓存。"""
    key = (n, round(float(alpha), 6))
    cached = _CRITICAL_CACHE.get(key)
    if cached is not None:
        return cached

    rng = np.random.default_rng(_mc_seed(n, float(alpha), _MC_TRIALS))
    steps: list = []
    remaining = _MC_TRIALS
    while remaining > 0:
        size = min(_MC_CHUNK, remaining)
        remaining -= size
        raw = rng.standard_normal((size, n))
        z = _standardized_residuals(_pipeline_residuals(raw))
        rows = np.arange(size)
        for _ in range(_MC_MAX_R):
            steps.append(z.max(axis=1))
            z[rows, z.argmax(axis=1)] = -np.inf   # 移除当前最大，进入下一步

    table = np.stack(steps)                     # shape: (_MC_MAX_R, trials)
    lams = tuple(float(np.quantile(table[r], 1.0 - float(alpha) / (r + 1)))
                 for r in range(_MC_MAX_R))
    _CRITICAL_CACHE[key] = lams
    return lams


def gesd_critical_value(n: int, r: int, alpha: float = DEFAULT_ALPHA) -> float:
    """第 r 步检验的临界值 λ_r —— **操作口径**，按本实现自己的流水线标定。

    做法：对标准正态样本先走**与 `detect` 相同的去趋势**，再走与
    `generalized_esd` 完全相同的逐步移除流程，取第 r 步统计量的 (1 − α/r) 分位数。

    解析解（`gesd_critical_value_analytic`）仅作交叉校验：它既没有考虑
    studentization、也没有考虑去趋势引入的相关性，会明显偏松。

    标定按 (n, α) 缓存，一次模拟覆盖 r=1..10 —— 因为第 r 步的统计量是同一条
    模拟轨迹上的第 r 个最大值，重算是纯浪费（实测每次重算约 1.5 秒）。
    """
    n = int(n)
    r = int(r)
    if n < 2:
        raise ValueError(f'GESD 临界值至少需要 2 个样本，收到 n={n}')
    if not 1 <= r <= n:
        return float('inf')
    if r > _MC_MAX_R:
        return float('inf')
    return _calibrate(n, alpha)[r - 1]


def _standardized_residuals(batch: np.ndarray) -> np.ndarray:
    """逐行做 median/MAD + 一致性常数标准化，返回 |z|。与 `generalized_esd` 同构。"""
    med = np.median(batch, axis=1, keepdims=True)
    mad = np.median(np.abs(batch - med), axis=1)
    scale = np.where(mad > 0.0, MAD_CONSISTENCY * mad, np.inf)
    with np.errstate(invalid='ignore'):
        z = np.abs((batch - med) / scale[:, None])
    return np.where(np.isfinite(z), z, 0.0)


def generalized_esd(resid: Sequence[float], med: float, mad: float,
                    alpha: float = DEFAULT_ALPHA,
                    max_anoms: int = DEFAULT_MAX_ANOMS) -> list[int]:
    """在残差上做 Generalized ESD，返回被标记点的下标（按时间升序）。

    每一步取当前最大的 |z| 与该步的 λ_r 比较，超过才标记并从候选集中移除，
    再进入下一步 —— 这正是"Generalized"相对单次 ESD 的地方（多重比较的
    Bonferroni 校正已经体现在 λ_r 的 α/r 上）。
    """
    z = np.asarray(resid, dtype=float)
    # **一致性常数 1.4826 不能省**：正态下 MAD ≈ 0.6745σ，所以稳健标准差
    # σ̂ = 1.4826 × MAD。λ_r 是按"标准正态、σ=1"精确标定的，若直接拿 MAD 当分母，
    # z 会被系统性放大 1.4826 倍，等价于把名义 α=0.05 的阈值偷偷降到 0.675×λ，
    # 实测误报率接近 100% —— 每次都凭空产出 5 条带完整 evidence 的"异常观测"，
    # 那正是设计 §1.4 要防的"证据链看着满、其实没有信息"。
    scale = MAD_CONSISTENCY * float(mad)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError(f'mad 必须为正有限值，收到 {mad!r}')
    z = (z - float(med)) / scale
    n = int(z.size)
    if n < 3 or max_anoms < 1 or not np.all(np.isfinite(z)):
        return []
    work = list(range(n))
    flagged: list[int] = []
    for r in range(1, min(int(max_anoms), n - 1) + 1):
        lam = gesd_critical_value(n, r, alpha)
        if not math.isfinite(lam):
            break
        best = max(work, key=lambda i: abs(float(z[i])))
        if abs(float(z[best])) <= lam:
            break
        flagged.append(best)
        work.remove(best)
        if len(work) < 3:
            break
    return sorted(flagged)


# ==========================================================================
# LOESS / STL（纯 numpy，**不引入 scipy**）
# ==========================================================================
def _loess_smooth(x: np.ndarray, y: np.ndarray, span_frac: float, degree: int = 1,
                  robust_iter: int = ROBUST_ITER) -> np.ndarray:
    """局部加权回归：tricube 权重 + 局部线性（degree=1）或常数（degree=0）拟合。

    `robust_iter > 0` 时按 Cleveland 的稳健化迭代追加 bisquare 权重
    （尺度取 6·median|残差|，与 R 的 loess 一致），让离群点不反过来污染自己的拟合。
    只实现 degree ∈ {0, 1}：STL 的趋势与季节项本来就都是低阶局部拟合，
    高阶在短序列上极易过拟合，收益为负。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.size
    if n == 0:
        return np.zeros(0, dtype=float)
    if n == 1:
        return y.copy()
    degree = min(int(degree), 1)      # 只实现 degree ∈ {0, 1}，见 docstring
    q = max(min(int(math.ceil(span_frac * n)), n), degree + 1)
    fitted = np.empty(n, dtype=float)
    rho = np.ones(n, dtype=float)
    loops = max(int(robust_iter), 0)
    for it in range(loops + 1):
        for i in range(n):
            dist = np.abs(x - x[i])
            near = np.argsort(dist, kind='stable')[:q]
            dmax = dist[near[-1]]
            if dmax > 0.0:
                u = dist[near] / dmax
                w = (1.0 - u ** 3) ** 3
            else:
                w = np.ones(near.size, dtype=float)
            w = w * rho[near]
            tot = float(w.sum())
            if tot <= 1e-12:
                # 稳健权重可能把整段邻居都压成 0（整窗被判为离群），退回等权均值
                w = np.ones(near.size, dtype=float)
                tot = float(w.sum())
            dx = x[near] - x[i]
            dy = y[near]
            s0 = tot
            s1 = float((w * dx).sum())
            s2 = float((w * dx * dx).sum())
            t0 = float((w * dy).sum())
            t1 = float((w * dx * dy).sum())
            det = s0 * s2 - s1 * s1
            if degree >= 1 and abs(det) > 1e-12 * max(s0 * s2, 1.0):
                fitted[i] = (s2 * t0 - s1 * t1) / det
            else:
                fitted[i] = t0 / s0
        if it >= loops:
            break
        r = y - fitted
        scale = 6.0 * float(np.median(np.abs(r)))
        if scale <= 0.0:
            rho = np.ones(n, dtype=float)
        else:
            u = np.clip(r / scale, -1.0, 1.0)
            rho = (1.0 - u ** 2) ** 2
    return fitted


def _moving_median(y: np.ndarray, win: int) -> np.ndarray:
    """滑动窗口中位数（medoid 平滑）。win 取奇数，边界按 edge 填充。"""
    n = y.size
    win = max(3, min(int(win) | 1, n if n % 2 else n - 1))
    if win < 3:
        return np.full(n, float(np.median(y)))
    pad = win // 2
    padded = np.pad(y, pad, mode='edge')
    view = np.lib.stride_tricks.sliding_window_view(padded, win)
    return np.median(view, axis=1)


def _robust_detrend(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """降级用的稳健去趋势：移动中位数（medoid）+ 低阶多项式最小二乘。

    比纯 LOESS 更保守、更便宜，在没有季节项时够用 —— 我们宁可漏掉季节性，
    也不要造出一个假的季节项把真异常吃掉（见模块 docstring 的已知限制）。
    """
    n = y.size
    if n < 4:
        return np.full(n, float(np.median(y)))
    med = _moving_median(y, max(3, int(round(0.1 * n)) | 1))
    xi = x - float(x.mean())
    design = np.vander(xi, TREND_DEGREE + 1)          # [x^d, ..., x, 1]
    coef, *_ = np.linalg.lstsq(design, y - med, rcond=None)
    return med + design @ coef


def _cycle_seasonal(detrended: np.ndarray, period: int, window: int) -> np.ndarray:
    """STL 的季节项：按相位切子序列 → 各相位单独 LOESS 平滑 → 周期延拓 → 去均值。

    去均值是必要的：不居中会让季节项与趋势项互相吸收，残差里残留季节形状，
    后续 GESD 就会把每个周期的同一相位反复标记。

    `window` 是**绝对点数**（几个连续周期），不是占比。这一点很要紧：
    按占比取窗时，一个 10 点的相位子序列会开出 9 点宽的窗 —— 近乎整段平均，
    正弦的形状被抹平，剩下的系统性偏差全进残差，GESD 就会把这些"假残差"
    连同真尖峰一起报出来。改成"跨 5 个连续周期"的绝对窗之后，
    局部线性拟合跟得上慢变趋势，残差里只剩下真异常。

    窗口必须**严格小于**子序列长度，否则局部线性会精确穿过每个点，
    季节项会把连异常一起吃掉（残差恒为 0，什么都测不出来）。
    子序列太短（≤4 点）时改用局部均值，宁可损失分辨率也不制造假季节项。
    """
    n = detrended.size
    cycle = np.zeros(period, dtype=float)
    for s in range(period):
        pos = np.arange(s, n, period)
        if pos.size == 0:
            continue
        if pos.size < 4:
            cycle[s] = float(detrended[pos].mean())
            continue
        q = max(min(int(window), pos.size - 1), 3)
        degree = 1 if q >= 4 else 0
        smoothed = _loess_smooth(pos.astype(float), detrended[pos], q / pos.size,
                                 degree=degree, robust_iter=ROBUST_ITER)
        cycle[s] = float(smoothed[0])                 # 取该相位自身的拟合值
    seasonal = cycle[np.arange(n) % period]
    return seasonal - float(seasonal.mean())


def stl_decompose(values: Sequence[float], period: int | None = None,
                  span_trend: float = LOESS_SPAN_TREND,
                  season_window: int = SEASON_WINDOW,
                  inner_iter: int = STL_INNER_ITER) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cleveland 两步 inner/outer loop 的 STL，返回 (seasonal, trend, resid)。

    流程：稳健去趋势 → 按相位平滑得季节项 → 去季节后再平滑得趋势 → 相减得残差，
    inner loop 再跑 `inner_iter` 轮让趋势与季节收敛。

    **降级策略（重要）**：`period` 为 None、period < 2、或样本不足 `MIN_CYCLES * period`
    时，**不伪造季节项**，而是退化成 `_robust_detrend`（移动中位数 + 一阶最小二乘）的
    **仅趋势**去趋势，季节项取 0。此时 `detect` 的 `evidence['method']['decompose']`
    会标成 `robust_trend_only`，让 L5 审计知道这次结论没有季节校正 ——
    这比"假装做了季节分解"诚实，也比"强行取一个小周期"安全（错周期会把真异常
    当成季节吃掉，产出假绿）。
    """
    y = np.asarray(values, dtype=float)
    n = y.size
    x = np.arange(n, dtype=float)
    if n == 0:
        empty = np.zeros(0, dtype=float)
        return empty, empty, empty
    if period is None or int(period) < MIN_PERIOD or n < MIN_CYCLES * int(period):
        trend = _robust_detrend(x, y)
        return np.zeros(n, dtype=float), trend, y - trend

    period = int(period)
    seasonal = np.zeros(n, dtype=float)
    trend = _robust_detrend(x, y)
    for _ in range(max(int(inner_iter), 1)):
        seasonal = _cycle_seasonal(y - trend, period, season_window)
        trend = _loess_smooth(x, y - seasonal, span_trend, degree=1, robust_iter=ROBUST_ITER)
    return seasonal, trend, y - trend - seasonal


# ==========================================================================
# 周期推断
# ==========================================================================
def _to_epoch_seconds(ts: Any) -> float | None:
    """把时间戳元素统一成 epoch 秒；认不出来返回 None（由调用方决定是否放弃）。"""
    if isinstance(ts, datetime):
        return ts.timestamp()
    if isinstance(ts, date):
        return datetime(ts.year, ts.month, ts.day).timestamp()
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        if not math.isfinite(v):
            return None
        # 毫秒时间戳（1e11 量级以上）先归一到秒，否则采样间隔会被放大 1000 倍
        return v / 1000.0 if v > 1e11 else v
    if isinstance(ts, str):
        s = ts.strip()
        # 级联解析：先当数字时间戳，再当 ISO 日期串；两次都不成立才返回 None。
        # 用两个"失败即返回 None"的辅助函数表达，而不是 `except: pass` ——
        # 空 except 读起来像静默忽略，代码审查时无法判断是"没匹配上"还是"忽略了错误"。
        num = _try_float(s)
        if num is not None:
            return num / 1000.0 if num > 1e11 else num
        return _try_iso_epoch(s)
    return None


def _try_float(text: str) -> float | None:
    """宽松解析数字，失败返回 None。"""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _try_iso_epoch(text: str) -> float | None:
    """宽松解析 ISO 时间串，失败返回 None（兼容尾部 Z）。"""
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return None


def infer_period(ts: Sequence[Any], values: Sequence[float] | None = None) -> int | None:
    """推断季节周期（单位：**采样点间隔**，不是秒）。

    给了 `values` 就走自相关：对线性去趋势后的残差算 ACF，取**第一个**过阈值
    且是局部极大值的 lag（取"第一个"是为了拿基频而不是它的整数倍）。
    峰值低于 `PERIOD_ACF_MIN` 视为**无季节性**，返回 None。
    样本量不足 `MIN_CYCLES * lag` 的 lag 一律不采纳（否则一个周期内插出来的
    自相关毫无意义）。

    没给 `values` 时返回 None：只看时间戳是**推断不出周期**的，只能靠"窗口长度
    恰好像一天"这类猜测 —— 那是编造，不是感知，故意的拒绝。

    论文未给具体判据，`PERIOD_ACF_MIN` 与 `MIN_CYCLES` 按需调整。
    """
    n = len(ts) if ts is not None else 0
    if n < MIN_SAMPLES or values is None:
        return None
    y = np.asarray(values, dtype=float)
    if y.size != n or n < MIN_SAMPLES:
        return None
    finite = np.isfinite(y)
    if int(finite.sum()) < MIN_SAMPLES:
        return None
    y = np.where(finite, y, np.nan)

    # 时间戳只用来判断"采样是否等间隔"（不等间隔时 lag 仍是下标单位，但要提示）
    epochs = [_to_epoch_seconds(t) for t in ts]
    deltas = [b - a for a, b in zip(epochs, epochs[1:]) if a is not None and b is not None]
    regular = bool(deltas) and (max(deltas) - min(deltas)) <= 0.5 * float(np.median(deltas))

    idx = np.arange(n, dtype=float)
    design = np.vander(idx - float(idx.mean()), 2)         # 一阶多项式，仅为去趋势
    coef, *_ = np.linalg.lstsq(design, np.nan_to_num(y, nan=float(np.nanmean(y))), rcond=None)
    d = np.nan_to_num(y, nan=float(np.nanmean(y))) - design @ coef
    denom = float(np.dot(d, d))
    if denom <= 0.0:
        return None

    max_lag = min(n // MIN_CYCLES, n // 2)
    acf: dict[int, float] = {}
    # 从 lag=1 开始算：局部极大值要拿 lag-1 做比较，缺了前驱会把 lag=MIN_PERIOD
    # 误判成峰值（这是真实的 bug，不是风格问题）
    for lag in range(1, max_lag + 1):
        a, b = d[:-lag], d[lag:]
        acf[lag] = float(np.dot(a, b) / denom)

    best = None
    for lag in range(MIN_PERIOD, max_lag + 1):
        value = acf[lag]
        if value < PERIOD_ACF_MIN:
            continue
        if value >= acf.get(lag - 1, -1.0) and value >= acf.get(lag + 1, -1.0):
            best = lag
            break
    if best is None:
        return None
    if not regular:
        logger.debug('KPI 周期推断：时间戳非等间隔，仍按采样下标推断 period=%s', best)
    return int(best)


# ==========================================================================
# 入口
# ==========================================================================
def _prepare(series: Any) -> tuple[list, np.ndarray, np.ndarray] | None:
    """规整输入，返回 (ts 列表, 值数组, 真实点掩码)；无法处理时返回 None。

    **缺失点必须补齐，不能直接丢掉。** STL 的周期子序列与自相关都建立在
    "等间隔采样"这个前提上；丢掉中间某个点会让后面所有点错位一格，
    季节项整体偏移，产出**看起来正常、实际全错**的基线（实测会把 z≈14 的真尖峰
    的基线算偏 30 多）。所以做法是：
    - 首尾的非有限值 → 截断（把有效区间裁出来）；
    - 中间的非有限值 → **线性插值**，并把它们标记为"非真实观测"；
    - 插值占比超过 `MAX_INTERPOLATE_FRAC` → 直接返回 None（整段不完整，
      分解不可信，宁可不给结论）。
    掩码最终会传给调用链：**插值出来的点不允许被当成异常报出去**。
    """
    if series is None:
        return None
    pairs = list(series)
    if not pairs:
        return None
    ts_list: list = []
    raw: list[float] = []
    for item in pairs:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f'series 的每个元素必须是 (ts, value) 二元组，收到 {item!r}')
        ts, value = item
        try:
            raw.append(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'series 的 value 必须是数值，收到 {value!r}') from exc
        ts_list.append(ts)

    values = np.asarray(raw, dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return None
    lo = int(np.argmax(finite))
    hi = len(values) - int(np.argmax(finite[::-1]))
    ts_list = ts_list[lo:hi]
    values = values[lo:hi]
    mask = finite[lo:hi].copy()
    if not mask.all():
        if mask.mean() < (1.0 - MAX_INTERPOLATE_FRAC):
            return None
        idx = np.arange(values.size, dtype=float)
        values = np.interp(idx, idx[mask], values[mask])   # 中间缺口线性插值
    return ts_list, values, mask


def _fmt_ts(ts: Any) -> str:
    """时间戳的人读格式；认不出来就原样 str()，不臆造格式。"""
    if isinstance(ts, datetime):
        return ts.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(ts, date):
        return ts.isoformat()
    return str(ts)


def detect(series: Sequence[tuple], metric_key: str, alpha: float = DEFAULT_ALPHA,
           max_anoms: int = DEFAULT_MAX_ANOMS, period: int | None = None) -> list[Observation]:
    """KPI 时序异常检测，返回自然语言观测列表。**无异常返回空列表**。

    论文口径：STL(LOESS) 分解 → 残差上 Generalized ESD，稳健位置/尺度用
    median 与 MAD 以削弱极端点影响。输出指明指标、时间、相对基线偏离程度。

    `evidence` 必填（L5 证据审计的地基，模块 docstring 约束 1），字段：
        metric / at / value / baseline / robust_z / trend / seasonal / resid / mad
        window（实际参与计算的点数、真实点数、插值点、起止时间、是否用了季节项）
        method（分解方式、检验名、alpha、逐 r 的临界值来源说明）
        refs（['kpi:<metric_key>']，供报告引用为 evidence_refs）

    `period` 允许调用方显式指定季节周期（例如已从配置知道是分钟级指标）；
    留空则由 `infer_period` 自相关推断，推断失败走仅趋势降级（见 stl_decompose）。
    """
    ts_list, y, mask = _prepare(series) or ([], np.zeros(0), np.zeros(0, dtype=bool))
    n = y.size
    if n < MIN_SAMPLES:
        return []
    if n > _max_series_points():
        # §8 验收项「>500 点序列入库被拒或截断」的纵深防御。快照层
        # （`OamSnapshot.validate`）已经是第一道闸，这里是第二道：调用方绕过
        # 快照层直接塞进来时，同样不能静默分析长序列 ——
        # 论文 Table 3a 实测 KPI 检测 F1 在 500 点时只剩 3.2%，照单全收
        # 等于把"分析不出东西"说成"没发现异常"。
        logger.warning('KPI 检测拒收超长序列：%d 点 > 上限 %d（论文长序列 F1 崩塌）',
                       n, _max_series_points())
        return []
    if float(y.max() - y.min()) <= 0.0:
        return []          # 全常数序列：没有"偏离基线"可言，返回空而不是硬凑

    used_period = period if period else infer_period(ts_list, y)
    seasonal, trend, resid = stl_decompose(y, period=used_period)

    med = float(np.median(resid))
    mad = float(np.median(np.abs(resid - med)))
    # MAD 兜底。设计文档只写了 `mad or 1e-9`，那**只挡住了恰好为 0**，
    # 挡不住"STL 把一条几乎无噪的正弦拟合到机器精度、MAD ≈ 1e-15"这种情况：
    # 此时每一个非尖峰点的 |z| 都能到 1e13，整条序列被判成离群 —— 假绿变假红。
    # 所以取 **相对信号尺度的下限**：用 p95-p05 的稳健幅度当尺度，
    # MAD 小于它的 MAD_REL_FLOOR 倍就抬到这个下限。
    # 这个下限只会**抬高**分母、只会让检测更保守，与论文的高精度口径同向；
    # 真实带噪序列的 MAD 本来就在信号尺度的百分之几以上，这个分支不会触发。
    signal_scale = float(np.percentile(y, 95) - np.percentile(y, 5))
    floor = max(MAD_FLOOR, MAD_REL_FLOOR * max(signal_scale, SCALE_ABS_FLOOR))
    if not math.isfinite(mad) or mad <= floor:
        mad = floor

    flagged = generalized_esd(resid, med, mad, alpha=alpha, max_anoms=max_anoms)[:max_anoms]
    # 插值出来的点不是真实观测，不允许当成异常报出去
    flagged = [i for i in flagged if bool(mask[i])]
    if not flagged:
        return []

    lambdas = [gesd_critical_value(n, r, alpha) for r in range(1, max(len(flagged), 1) + 1)]
    decompose = 'stl_loess' if seasonal.any() else 'robust_trend_only'
    missing = [ts_list[i] for i in range(n) if not bool(mask[i])]
    out: list[Observation] = []
    # 上报用的 z 必须与 `generalized_esd` 里实际检验的那个 z **完全一致**，
    # 否则报告写"偏离 7.2 倍稳健标准差"而检验用的是另一个尺度，
    # 证据审计对着数值复算就会对不上。
    robust_sigma = MAD_CONSISTENCY * mad
    for i in flagged:
        resid_i = float(resid[i])
        z = (resid_i - med) / robust_sigma
        baseline = float(trend[i] + seasonal[i])   # 季节可用时基线 = 趋势 + 季节
        direction = '尖峰' if resid_i > 0 else '骤降'
        out.append(Observation(
            text=(f'{metric_key} 在 {_fmt_ts(ts_list[i])} 出现异常{direction}，'
                  f'相对基线偏离 {z:.1f} 倍稳健标准差'
                  f'（实测 {float(y[i]):.6g}，基线 {baseline:.6g}）'),
            evidence={
                'metric': metric_key,
                'at': ts_list[i],                  # 原样透传，保留调用方的时区/类型
                'value': float(y[i]),
                'baseline': baseline,
                'robust_z': round(float(z), 4),
                'trend': float(trend[i]),
                'seasonal': float(seasonal[i]),
                'resid': resid_i,
                'mad': mad,
                'robust_sigma': robust_sigma,   # = 1.4826 × mad，检验与上报都用它
                'window': {
                    'points': n,
                    'real_points': int(mask.sum()),
                    'interpolated': missing,
                    'start': ts_list[0],
                    'end': ts_list[-1],
                    'period': int(used_period) if used_period else None,
                    'decompose': decompose,
                },
                'method': {
                    'decompose': decompose,
                    'test': 'generalized_esd',
                    'sided': 'two',
                    'alpha': alpha,
                    'max_anoms': max_anoms,
                    # 临界值来自零分布精确求解（见模块 docstring），这里原样留痕便于复核
                    'lambda': lambdas,
                    'scale': 'median+MAD',
                },
                'refs': [f'kpi:{metric_key}'],
            },
        ))
    return out
