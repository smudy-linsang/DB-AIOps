"""
PostgreSQL 容量指标辅助（DB-AIOps 设计：used_pct 不得使用库间自比 max 的错误算法）。

在无法读取操作系统磁盘的前提下，采用实例内可解释代理：
    used_pct ≈ 100 * pg_database_size(datname) / SUM(pg_tablespace_size(oid))

分母为集群已分配的表空间字节总和（PG 实例数据目录内的主要占用），分子为单库逻辑大小，
比值表示该库在实例已分配存储中的占比，可用于容量集中度告警；与 Oracle 表空间物理占比语义接近。
"""


def postgresql_db_used_pct(size_bytes: int, total_tablespace_bytes: int) -> float | None:
    if size_bytes < 0 or total_tablespace_bytes <= 0:
        return None
    pct = 100.0 * float(size_bytes) / float(total_tablespace_bytes)
    return round(min(100.0, pct), 2)
