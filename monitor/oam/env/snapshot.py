# -*- coding: utf-8 -*-
"""冻结系统状态快照（论文 §5.3.1 的"冻结的系统快照"）。

一个 `OamSnapshot` 是一个运维事件的**只读**系统状态。诊断期间不可变，
保证可回放与可复现 —— 这是验证器能给出确定性判定的物理基础。

## 窗口必须显式限制（论文数据直接推出的硬约束）

论文的 KPI 异常检测 F1 随序列变长急剧崩塌：

| 序列长度 | 10 | 20 | 100 | 500 |
|---------|----|----|-----|-----|
| F1（指标维度 1-10） | 57.1 | 64.5 | 18.2 | **3.2** |

论文能用，是因为部署诊断窗口只有 2-3 小时、采样间隔 60-120 秒。
**我们的时序库默认保留的窗口远长于此**，直接把全量序列喂进去会原样继承这个失效。
所以 `window` 必填、每条指标截断至 ≤`OAM_MAX_SERIES_POINTS`（默认 500）点。

超出窗口的分析请求要返回**确定性错误**，不是静默截断 ——
静默截断会让评测看起来正常而结论失真，正是本项目"假绿"的同一种病。
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime

from monitor import appconf

logger = logging.getLogger('monitor.oam')


def _as_datetime(value):
    """把 ISO 字符串 / 时间戳数字 / datetime 统一成 `datetime`。

    入库路径（HTTP JSON）给的是 ISO 字符串，回放路径给的是 datetime，
    两条路径必须收敛到同一种类型，否则 `validate()` 会在比较时炸掉。
    """
    from datetime import datetime, timezone as _tz
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=_tz.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _as_iso(value):
    dt = _as_datetime(value)
    return dt.isoformat() if isinstance(dt, datetime) else value


def _jsonable(obj):
    """递归把 datetime 换成 ISO 字符串，其余原样。"""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

# 快照结构性问题用这个异常，与"数据缺失"（NO_DATA）区分开：
# 前者是调用方/入库方的错，后者是合法的"证据不足"。
class SnapshotRangeError(ValueError):
    """请求超出快照范围（如时间窗、序列长度）。"""


@dataclass
class OamSnapshot:
    """一个运维事件的冻结系统状态。诊断期间只读。"""

    case_id: str
    db_type: str
    kpi_series: dict = field(default_factory=dict)   # {metric_key: [(ts, value), ...]}
    window: tuple = ()                                # (since, until)，部署口径 2-3 小时
    session_samples: list = field(default_factory=list)
    plans: list = field(default_factory=list)         # [{sql_id, plan_text, captured_at}]
    logs: list = field(default_factory=list)
    config_meta: dict = field(default_factory=dict)
    schema_meta: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- 校验
    def validate(self, strict: bool = True) -> list:
        """返回问题列表；空列表表示通过。`strict=True` 时超限即抛异常。

        校验项：
          · 窗口必须显式给出，且 end > start；
          · 每条指标序列 ≤ `OAM_MAX_SERIES_POINTS` 点；
          · 时序点必须按时间递增（回放要可复现，乱序会让检测结果依赖字典序）。
        """
        problems = []
        if not self.window or len(self.window) != 2:
            problems.append('window 必填，格式 (since, until)')
        else:
            since, until = self.window
            if until <= since:
                problems.append(f'window 终点({until})必须晚于起点({since})')
            hours = (until - since).total_seconds() / 3600
            if hours > 24:
                problems.append(
                    f'窗口 {hours:.1f} 小时超出论文部署口径(2-10 小时)；'
                    f'长序列会让 KPI 检测 F1 崩塌（500 点时仅 3.2%）')

        max_pts = appconf.get('OAM_MAX_SERIES_POINTS')
        for metric, series in (self.kpi_series or {}).items():
            if len(series) > max_pts:
                problems.append(
                    f'指标 {metric} 有 {len(series)} 点，超过上限 {max_pts}')
            stamps = [p[0] for p in series]
            if stamps != sorted(stamps):
                problems.append(f'指标 {metric} 的时序点未按时间递增')
            if self.window and len(self.window) == 2:
                since, until = self.window
                out = [t for t in stamps if t < since or t > until]
                if out:
                    problems.append(
                        f'指标 {metric} 有 {len(out)} 个采样点落在窗口 '
                        f'{since}~{until} 之外')

        if problems and strict:
            raise SnapshotRangeError('；'.join(problems))
        if problems:
            logger.warning('[oam] 快照 %s 结构性问题: %s', self.case_id, problems)
        return problems

    # -------------------------------------------------------------- 读数
    def series(self, metric_key: str) -> list:
        return list(self.kpi_series.get(metric_key) or [])

    def metric_value(self, metric_key: str):
        """取窗口内最后一个采样值；没有该指标返回 None（属"证据不足"，不是 0）。"""
        pts = self.series(metric_key)
        return pts[-1][1] if pts else None

    def plans_for(self, sql_id: str = '') -> list:
        if not sql_id:
            return list(self.plans)
        return [p for p in self.plans if p.get('sql_id') == sql_id]

    def to_dict(self) -> dict:
        return {
            'case_id': self.case_id,
            'db_type': self.db_type,
            'kpi_series': {k: [list(p) for p in v]
                           for k, v in (self.kpi_series or {}).items()},
            'window': list(self.window) if self.window else [],
            'session_samples': self.session_samples,
            'plans': self.plans,
            'logs': self.logs,
            'config_meta': self.config_meta,
            'schema_meta': self.schema_meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'OamSnapshot':
        """从 dict 构造。时间戳做**显式解析**：JSON 传进来的一定是 ISO 字符串，
        不解析就会在 `validate()` 里炸成 `'str' - 'str'`。"""
        d = dict(data or {})
        window = tuple(_as_datetime(x) for x in (d.get('window') or ()))
        kpi = {}
        for metric, pts in (d.get('kpi_series') or {}).items():
            kpi[metric] = [(_as_datetime(p[0]), p[1]) for p in pts]
        return cls(
            case_id=d.get('case_id', ''),
            db_type=d.get('db_type', ''),
            kpi_series=kpi,
            window=window,
            session_samples=d.get('session_samples') or [],
            plans=d.get('plans') or [],
            logs=d.get('logs') or [],
            config_meta=d.get('config_meta') or {},
            schema_meta=d.get('schema_meta') or {},
        )

    def to_model_dict(self) -> dict:
        """转成能进 `JSONField` 的形态：时间戳一律转 ISO 字符串。

        `JSONField` 不能序列化 datetime，所以入库前必须显式转换 ——
        否则 `OamSnapshotBlob.objects.create()` 会抛
        `TypeError: Object of type datetime is not JSON serializable`。
        """
        return {
            'kpi_series': {k: [[_as_iso(p[0]), p[1]] for p in v]
                           for k, v in (self.kpi_series or {}).items()},
            'session_samples': _jsonable(self.session_samples),
            'plans': _jsonable(self.plans),
            'logs': _jsonable(self.logs),
            'config_meta': _jsonable(self.config_meta),
            'schema_meta': _jsonable(self.schema_meta),
        }

    # ------------------------------------------------------ 与 ORM 的互转
    @classmethod
    def from_case(cls, case, strict: bool = False) -> 'OamSnapshot':
        """从 `OamCase` + 其 `OamSnapshotBlob` 构造。未传快照时返回空快照。

        空快照是合法的 —— 它对应"这个实例根本没有可回放的遥测"，
        策略此时**必须**输出证据不足（设计 §8 有专门验收项防 R25-02 复发）。
        """
        blob = getattr(case, 'snapshot', None)
        if blob is None:
            snap = cls(case_id=case.case_id, db_type=case.db_type)
            if strict:
                raise SnapshotRangeError(f'用例 {case.case_id} 尚未上传冻结快照')
            return snap
        snap = cls(
            case_id=case.case_id,
            db_type=case.db_type,
            # 库里的时间戳是 ISO 字符串（JSONField 存不下 datetime），
            # 读回来必须转回 datetime，否则观测层做时间运算会炸
            kpi_series={k: [(_as_datetime(p[0]), p[1]) for p in v]
                        for k, v in (blob.kpi_series or {}).items()},
            window=(blob.window_start, blob.window_end),
            session_samples=blob.session_samples or [],
            plans=blob.plans or [],
            logs=blob.logs or [],
            config_meta=blob.config_meta or {},
            schema_meta=blob.schema_meta or {},
        )
        snap.validate(strict=strict)
        return snap

    def series_lengths(self) -> dict:
        return {k: len(v) for k, v in (self.kpi_series or {}).items()}


__all__ = ['OamSnapshot', 'SnapshotRangeError']
