#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TimescaleDB 时序库初始化脚本（修复 AAS/性能中心 502 TSDB_DOWN）
================================================================

背景：
    docker-compose.dev.yml 仅通过 POSTGRES_DB=db_monitor 初始化配置库，
    从未创建性能中心所需的时序库 timeseriesdb，导致 AAS/ASH 等性能接口
    返回 502 (TSDB_DOWN)。本脚本幂等地补齐该数据库与扩展。

功能（可重复执行）：
    1. 连接 postgres 维护库，不存在则创建 TIMESCALEDB_NAME 数据库；
    2. 在时序库中启用 timescaledb 扩展；
    3. 提示运行 `python manage.py init_timeseries` 创建超表。

用法：
    venv/bin/python scripts/setup_timeseries_db.py
"""
import os
import sys

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT


def _env(key, default):
    return os.environ.get(key, default)


def main():
    host = _env('TIMESCALEDB_HOST', 'localhost')
    port = int(_env('TIMESCALEDB_PORT', '5432'))
    user = _env('TIMESCALEDB_USER', 'postgres')
    password = _env('TIMESCALEDB_PASSWORD', 'postgres123')
    dbname = _env('TIMESCALEDB_NAME', 'timeseriesdb')

    print(f"[setup] 目标: {user}@{host}:{port}/{dbname}")

    # 1. 连接维护库 postgres
    try:
        maint = psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname='postgres', connect_timeout=10,
        )
    except Exception as e:
        print(f"[setup][ERROR] 无法连接 TimescaleDB 容器: {e}")
        return 1
    maint.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

    # 2. 幂等创建数据库
    cur = maint.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
    if cur.fetchone():
        print(f"[setup] 数据库 {dbname} 已存在，跳过创建")
    else:
        cur.execute(f'CREATE DATABASE "{dbname}"')
        print(f"[setup] 已创建数据库 {dbname}")
    cur.close()
    maint.close()

    # 3. 启用 timescaledb 扩展
    try:
        db_conn = psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname=dbname, connect_timeout=10,
        )
        db_conn.autocommit = True
        db_cur = db_conn.cursor()
        db_cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        db_cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';")
        row = db_cur.fetchone()
        print(f"[setup] timescaledb 扩展就绪 (version={row[0] if row else '?'})")
        db_cur.close()
        db_conn.close()
    except Exception as e:
        print(f"[setup][ERROR] 启用 timescaledb 扩展失败: {e}")
        return 1

    print("[setup] 完成。请再执行超表初始化:")
    print("        venv/bin/python manage.py init_timeseries")
    return 0


if __name__ == '__main__':
    sys.exit(main())
