# -*- coding: utf-8 -*-
"""
Phase 6A: pipeline_worker (phase6/10 §6)。

三消费组单进程多线程:
  cg_detect  : events → incident_manager.ingest_event  (6A 完整)
  cg_diag    : diagnosis → run_diagnosis               (6B 填充; 6A 骨架 XACK)
  cg_verify  : verify → 验证回路                        (6C 填充; 6A 骨架 XACK)
"""
import logging
import threading

from django.db import close_old_connections, connection as dj_conn

from monitor import redis_bus as rb

logger = logging.getLogger("monitor.pipeline")


def _process_detect(payload):
    from monitor.incident_manager import ingest_event
    inc = ingest_event(payload)
    if inc:
        logger.info("[detect] event %s → incident %s (%s)",
                    payload.get('event_uid'), inc.incident_id, inc.status)


def _process_diag(payload):
    incident_id = payload.get('incident_id')
    try:
        from monitor.diagnosis_pipeline import run_diagnosis  # 6B 提供
    except Exception:
        logger.debug("[diag] 诊断管道未就绪(6B), 跳过 incident=%s", incident_id)
        return
    try:
        run_diagnosis(incident_id)
        logger.info("[diag] incident %s 诊断完成", incident_id)
    except Exception as e:
        logger.error("[diag] incident %s 诊断失败: %s", incident_id, e)


def _process_verify(payload):
    try:
        from monitor.verify_loop import run_verify  # 6C 提供
    except Exception:
        logger.debug("[verify] 验证回路未就绪(6C), 跳过")
        return
    try:
        run_verify(payload)
    except Exception as e:
        logger.error("[verify] 失败: %s", e)


_CONSUMERS = [
    (rb.STREAM_EVENTS, rb.GROUP_DETECT, 'pipeline-detect', _process_detect),
    (rb.STREAM_DIAG, rb.GROUP_DIAG, 'pipeline-diag', _process_diag),
    (rb.STREAM_VERIFY, rb.GROUP_VERIFY, 'pipeline-verify', _process_verify),
]


class ConsumerThread(threading.Thread):
    def __init__(self, stream, group, consumer, handler, stop_event):
        super().__init__(name=f"pipe-{group}", daemon=True)
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.handler = handler
        self.stop_event = stop_event

    def run(self):
        bus = rb.get_bus()
        logger.info("消费线程启动: %s/%s", self.stream, self.group)
        while not self.stop_event.is_set():
            try:
                close_old_connections()
                batch = rb.read_group(self.stream, self.group, self.consumer,
                                      count=50, block_ms=2000, bus=bus)
                for msg_id, payload in batch:
                    if not payload:
                        rb.ack(self.stream, self.group, msg_id, bus=bus)  # 坏消息直接 ack
                        continue
                    try:
                        self.handler(payload)
                        rb.ack(self.stream, self.group, msg_id, bus=bus)
                    except Exception as e:
                        logger.error("处理消息失败(不ack, 待重投): %s | %s", msg_id, e)
            except Exception as e:
                logger.error("消费循环异常: %s", e)
                self.stop_event.wait(2)
            finally:
                try:
                    dj_conn.close()
                except Exception:
                    pass


class Pipeline:
    def __init__(self):
        self.stop_event = threading.Event()
        self.threads = []

    def run(self):
        rb.ensure_groups()
        for stream, group, consumer, handler in _CONSUMERS:
            t = ConsumerThread(stream, group, consumer, handler, self.stop_event)
            t.start()
            self.threads.append(t)
        logger.info("pipeline_worker 启动 (3 消费组)")
        try:
            # W4 自监控：每 ~60s（12 × 5s）上报一次心跳
            _tick = 0
            while not self.stop_event.is_set():
                self.stop_event.wait(5)
                _tick += 1
                if _tick % 12 == 0:
                    from monitor.self_monitor import report
                    report('pipeline')
        except (KeyboardInterrupt, SystemExit):
            self.stop()

    def stop(self):
        self.stop_event.set()


def process_pending_once(max_per_stream=100):
    """调试用: 各流处理一批待处理消息后返回处理计数 (不常驻)。"""
    rb.ensure_groups()
    bus = rb.get_bus()
    counts = {}
    for stream, group, consumer, handler in _CONSUMERS:
        n = 0
        batch = rb.read_group(stream, group, consumer + '-once', count=max_per_stream,
                              block_ms=500, bus=bus)
        for msg_id, payload in batch:
            if payload:
                try:
                    handler(payload)
                except Exception as e:
                    logger.error("once 处理失败: %s", e)
            rb.ack(stream, group, msg_id, bus=bus)
            n += 1
        counts[group] = n
    return counts
