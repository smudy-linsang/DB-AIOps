# -*- coding: utf-8 -*-
"""策略后端（设计 §3.2：后端可替换是相对论文的主要工程改写）。

论文是云厂商自用系统，有 8×Ascend 910B3；我们是可自托管产品。
把 RL 做成架构前提，这份设计对绝大多数部署就是不可用的。

所以：
    prompted —— P0 落地，只需 LLM API
    sft     —— 留接口，单卡可做，但论文实测难任务仅 18%，不作为目标
    rl      —— 留接口，需要多卡；设计 §7 P2 的前置条件是 L1 环境与验证器稳定

`backend_name()` 只认 `prompted`；其余后端显式抛错而不是静默降级 ——
静默降级会让"我以为跑的是 RL"变成假绿。
"""


def backend_name(name: str) -> str:
    name = (name or 'prompted').strip().lower()
    if name == 'prompted':
        return 'prompted'
    raise NotImplementedError(
        f'策略后端 {name!r} 本阶段未落地。设计 §7 的分期是 P0 只做 prompted；'
        f'sft/rl 的前置条件分别是语料规模与多卡算力（且 §9.1 的标注语料尚不存在）。'
        f'**不要静默降级到 prompted**，那会让"跑的是哪套策略"这件事变得不可知。')


def build(backend: str, db_type: str, incident_id: str = ''):
    """构造策略后端实例。"""
    name = backend_name(backend)
    if name == 'prompted':
        from monitor.oam.policy.backends.prompted import PromptedPolicy
        return PromptedPolicy(db_type=db_type, incident_id=incident_id)
    raise NotImplementedError(name)   # 理论不可达


__all__ = ['backend_name', 'build']
