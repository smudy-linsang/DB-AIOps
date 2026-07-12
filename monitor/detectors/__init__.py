# -*- coding: utf-8 -*-
"""Phase 6A 三层检测器 (phase6/10 §5)。

检测器统一签名: detect(config, sample_or_metrics) -> list[event_dict]
event_dict 符合 conventions#3.1 (不含 event_uid/schema, 由 redis_bus.emit_event 补)。
"""
from monitor.detectors.l1_hard import detect_l1
from monitor.detectors.l2_baseline import detect_l2
from monitor.detectors.l3_composite import detect_l3

__all__ = ['detect_l1', 'detect_l2', 'detect_l3']
