# -*- coding: utf-8 -*-
"""数据库租约与 fencing token，用于后台角色单活和故障切换。"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from datetime import timedelta, timezone as dt_timezone
from typing import Callable, Optional

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from monitor import appconf
from monitor.models import ProcessLease

logger = logging.getLogger('monitor.process_lease')


class LeaseUnavailable(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


def _database_now():
    """使用元数据数据库时钟，避免应用节点时钟漂移导致双主。"""
    with connection.cursor() as cursor:
        cursor.execute('SELECT CURRENT_TIMESTAMP')
        value = cursor.fetchone()[0]
    if isinstance(value, str):
        value = parse_datetime(value.replace(' ', 'T'))
    if value is None:
        raise LeaseUnavailable('无法读取元数据数据库时钟')
    if timezone.is_naive(value):
        # SQLite CURRENT_TIMESTAMP 与 PostgreSQL timestamptz 都以 UTC 为基准；
        # SQLite 驱动仅缺少 tzinfo，不能误套本地时区。
        value = timezone.make_aware(value, dt_timezone.utc)
    return value


class ProcessLeaseGuard:
    """可续租的进程领导权；失租后以事件和回调通知业务循环停机。"""

    def __init__(self, role: str, shard_key: str = 'global', *,
                 owner_id: Optional[str] = None,
                 on_lost: Optional[Callable[[], None]] = None):
        self.role = role
        self.shard_key = shard_key
        self.owner_id = owner_id or (
            f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}')
        self.ttl_sec = appconf.get('PROCESS_LEASE_TTL_SEC')
        self.renew_sec = appconf.get('PROCESS_LEASE_RENEW_SEC')
        if self.renew_sec >= self.ttl_sec:
            raise ValueError('PROCESS_LEASE_RENEW_SEC 必须小于 PROCESS_LEASE_TTL_SEC')
        self.on_lost = on_lost
        self.fencing_token = None
        self._lease_pk = None
        self._local_deadline = 0.0
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = None

    def acquire(self) -> bool:
        """原子领取空闲/过期租约；冲突返回 False。"""
        for _ in range(3):
            try:
                with transaction.atomic():
                    now = _database_now()
                    lease = ProcessLease.objects.select_for_update().filter(
                        role=self.role, shard_key=self.shard_key).first()
                    if lease is None:
                        lease = ProcessLease.objects.create(
                            role=self.role, shard_key=self.shard_key,
                            owner_id=self.owner_id, fencing_token=1,
                            heartbeat_at=now,
                            expires_at=now + timedelta(seconds=self.ttl_sec),
                            metadata={'pid': os.getpid(), 'host': socket.gethostname()},
                        )
                    elif lease.owner_id == self.owner_id:
                        lease.heartbeat_at = now
                        lease.expires_at = now + timedelta(seconds=self.ttl_sec)
                        lease.save(update_fields=['heartbeat_at', 'expires_at', 'updated_at'])
                    elif not lease.owner_id or lease.expires_at is None or lease.expires_at <= now:
                        lease.owner_id = self.owner_id
                        lease.fencing_token += 1
                        lease.heartbeat_at = now
                        lease.expires_at = now + timedelta(seconds=self.ttl_sec)
                        lease.metadata = {'pid': os.getpid(), 'host': socket.gethostname()}
                        lease.save(update_fields=[
                            'owner_id', 'fencing_token', 'heartbeat_at', 'expires_at',
                            'metadata', 'updated_at'])
                    else:
                        return False
                self._lease_pk = lease.pk
                self.fencing_token = lease.fencing_token
                self._local_deadline = time.monotonic() + self.ttl_sec
                return True
            except IntegrityError:
                # 两个首次启动者同时创建唯一行时，输家重读已提交的租约。
                continue
        return False

    def start(self):
        if not self.acquire():
            raise LeaseUnavailable(
                f'{self.role}/{self.shard_key} 已由其他实例持有')
        self._thread = threading.Thread(
            target=self._renew_loop, daemon=True,
            name=f'lease-{self.role}-{self.shard_key}')
        self._thread.start()
        logger.info('获得租约 %s/%s owner=%s token=%s',
                    self.role, self.shard_key, self.owner_id, self.fencing_token)
        return self

    def renew(self) -> bool:
        """仅未过期的当前 owner/token 可以续租，阻断暂停进程复活。"""
        with transaction.atomic():
            now = _database_now()
            updated = ProcessLease.objects.filter(
                pk=self._lease_pk,
                owner_id=self.owner_id,
                fencing_token=self.fencing_token,
                expires_at__gt=now,
            ).update(
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=self.ttl_sec),
                metadata={'pid': os.getpid(), 'host': socket.gethostname()},
            )
        if updated:
            self._local_deadline = time.monotonic() + self.ttl_sec
        return bool(updated)

    def _renew_loop(self):
        while not self._stop.wait(self.renew_sec):
            try:
                close_old_connections()
                if not self.renew():
                    self._mark_lost('租约已过期或 fencing token 已变化')
                    return
            except Exception as exc:
                logger.error('租约续租失败 %s/%s: %s',
                             self.role, self.shard_key, type(exc).__name__)
                if time.monotonic() >= self._local_deadline:
                    self._mark_lost('元数据数据库不可用直至本地租约期限')
                    return
            finally:
                connection.close()

    def _mark_lost(self, reason: str):
        if self._lost.is_set():
            return
        self._lost.set()
        logger.critical('失去租约 %s/%s: %s', self.role, self.shard_key, reason)
        if self.on_lost:
            try:
                self.on_lost()
            except Exception:
                logger.exception('租约失效回调失败: %s/%s', self.role, self.shard_key)

    def assert_leader(self) -> int:
        if self._lost.is_set() or time.monotonic() >= self._local_deadline:
            self._mark_lost('业务周期开始前租约已失效')
            raise LeaseLost(f'{self.role}/{self.shard_key} 已失去领导权')
        return int(self.fencing_token)

    def release(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=min(self.renew_sec, 5))
        if self._lease_pk is not None and not self._lost.is_set():
            try:
                with transaction.atomic():
                    now = _database_now()
                    ProcessLease.objects.filter(
                        pk=self._lease_pk, owner_id=self.owner_id,
                        fencing_token=self.fencing_token,
                    ).update(owner_id='', expires_at=now, heartbeat_at=now)
            except Exception as exc:
                logger.warning('释放租约失败 %s/%s: %s',
                               self.role, self.shard_key, type(exc).__name__)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
