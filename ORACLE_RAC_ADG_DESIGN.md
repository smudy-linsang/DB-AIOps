# Oracle RAC & ADG 监控指标设计

## 1. 文档目标

本文档补充 Oracle RAC（Real Application Clusters）集群监控和 ADG（Active Data Guard）备库监控的完整指标体系。

---

## 2. Oracle RAC 集群监控指标

### 2.1 RAC 集群基础信息

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `cluster_name` | 集群名称 | `GV$CLUSTER` | P0 |
| `cluster_version` | 集群版本 | `GV$CLUSTER` | P0 |
| `cluster_status` | 集群状态 | `GV$CLUSTER` | P0 |
| `instance_count` | 实例数量 | `GV$INSTANCE` | P0 |
| `current_instance` | 当前实例 | `GV$INSTANCE` | P0 |

### 2.2 RAC 节点状态

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `node_name` | 节点名称 | `GV$INSTANCE` | P0 |
| `node_host` | 节点主机名 | `GV$INSTANCE` | P0 |
| `node_status` | 节点状态 (OPEN/STATS/CLOSED) | `GV$INSTANCE` | P0 |
| `node_role` | 节点角色 (PRIMARY/BACKUP) | `GV$INSTANCE` | P1 |
| `instance_number` | 实例编号 | `GV$INSTANCE` | P0 |
| `database_status` | 数据库状态 | `GV$INSTANCE` | P0 |
| `database_role` | 数据库角色 | `V$DATABASE` | P0 |
| `open_mode` | 打开模式 | `V$DATABASE` | P0 |
| `logins` | 允许登录 | `V$INSTANCE` | P1 |
| `shutdown_pending` | 是否有关闭待处理 | `V$INSTANCE` | P1 |

### 2.3 RAC 互联网络 (Interconnect)

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `ic_bytes_sent` | 互联发送字节数 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `ic_bytes_received` | 互联接收字节数 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `ic_packets_sent` | 互联发送数据包 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `ic_packets_received` | 互联接收数据包 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `ic_errors` | 互联错误数 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `ic_network` | 互联网络类型 | `GV$CLUSTER_INTERCONNECTS` | P2 |

### 2.4 RAC 缓存融合 (Cache Fusion)

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `gc_cr_blocks_received` | 接收的CR块数 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `gc_cr_blocks_served` | 发送的CR块数 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `gc_current_blocks_received` | 接收的当前块数 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `gc_current_blocks_served` | 发送的当前块数 | `GV$CLUSTER_INTERCONNECTS` | P1 |
| `gc_buffer_busy` | 全局缓存缓冲区忙 | `GV$SYSTEM_EVENT` | P1 |
| `gc_cr_receive_time` | CR块接收时间 | `GV$CLUSTER_INTERCONNECTS` | P2 |
| `gc_cr_send_time` | CR块发送时间 | `GV$CLUSTER_INTERCONNECTS` | P2 |

### 2.5 RAC 全局队列

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `global_enqueue_queue` | 全局队列深度 | `GV$INSTANCE` | P1 |
| `ges_messages_sent` | GES发送消息数 | `GV$INSTANCE` | P2 |
| `ges_messages_received` | GES接收消息数 | `GV$INSTANCE` | P2 |
| `ges_locks` | GES锁数量 | `GV$INSTANCE` | P2 |

### 2.6 RAC VIP 和 SCAN

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `vip_status` | VIP状态 | `GV$IP` | P2 |
| `scan_name` | SCAN名称 | `GV$SCAN` | P2 |
| `scan_listener_status` | SCAN监听器状态 | `GV$SCAN_LISTENER` | P2 |

---

## 3. Oracle ADG (Data Guard) 监控指标

### 3.1 ADG 基础信息

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `dg_role` | Data Guard 角色 | `V$DATABASE` | P0 |
| `dg_protection_mode` | 保护模式 | `V$DATABASE` | P0 |
| `dg_protection_level` | 保护级别 | `V$DATABASE` | P0 |
| `switchover_status` | 切换状态 | `V$DATABASE` | P0 |
| `database_role` | 数据库角色 | `V$DATABASE` | P0 |
| `open_mode` | 打开模式 | `V$DATABASE` | P0 |

### 3.2 ADG 传输状态

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `archive_dest_1` | 归档目的地1 | `V$ARCHIVE_DEST` | P1 |
| `archive_dest_2` | 归档目的地2 | `V$ARCHIVE_DEST` | P1 |
| `dest_1_status` | 目的地1状态 | `V$ARCHIVE_DEST_STATUS` | P0 |
| `dest_2_status` | 目的地2状态 | `V$ARCHIVE_DEST_STATUS` | P0 |
| `dest_1_error` | 目的地1错误 | `V$ARCHIVE_DEST` | P1 |
| `dest_2_error` | 目的地2错误 | `V$ARCHIVE_DEST` | P1 |

### 3.3 ADG 延迟指标

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `apply_lag` | 应用延迟 (秒) | `V$DATAGUARD_STATUS` | P0 |
| `transport_lag` | 传输延迟 (秒) | `V$DATAGUARD_STATUS` | P0 |
| `apply_lag_display` | 应用延迟 (格式化) | `V$STANDBY_EVENT_HISTOGRAM` | P1 |
| `max_apply_lag` | 最大应用延迟 | `V$STANDBY_EVENT_HISTOGRAM` | P1 |

### 3.4 ADG 归档日志

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `archived_logs` | 归档日志数量 | `V$ARCHIVED_LOG` | P1 |
| `archived_logs_1day` | 一天内归档数 | `V$ARCHIVED_LOG` | P1 |
| `archived_logs_1hour` | 一小时内归档数 | `V$ARCHIVED_LOG` | P1 |
| `archive_gap` | 归档Gap数量 | `V$ARCHIVE_GAP` | P0 |
| `archive_gap_sequence` | Gap序列号 | `V$ARCHIVE_GAP` | P1 |
| `redo_gap` | Redo Gap | `V$ARCHIVE_GAP` | P1 |

### 3.5 ADG 备库应用

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `apply_state` | 应用状态 | `V$MANAGED_STANDBY` | P0 |
| `apply_rac_instances` | 应用实例数 | `V$MANAGED_STANDBY` | P1 |
| `mrp_status` | MRP进程状态 | `V$MANAGED_STANDBY` | P0 |
| `rfs_status` | RFS进程状态 | `V$MANAGED_STANDBY` | P1 |
| `archival_processes` | 归档进程数 | `V$ARCHIVE_DEST_STATUS` | P2 |

### 3.6 ADG 性能指标

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `redo_bytes_per_sec` | Redo字节/秒 | `V$DATAGUARD_STATS` | P1 |
| `redo_kbytes_per_sec` | Redo KB/秒 | `V$DATAGUARD_STATS` | P1 |
| `apply_rate` | 应用速率 | `V$STANDBY_APPLY_stats` | P1 |
| `archive_lag` | 归档延迟 | `V$DATAGUARD_STATS` | P1 |

### 3.7 ADG 健康检查

| 指标名 | 说明 | SQL/视图 | 优先级 |
|--------|------|----------|--------|
| `standby_file_management` | 备库文件管理 | `V$PARAMETER` | P2 |
| `archive_config` | 归档配置 | `V$ARCHIVE_DEST` | P2 |
| `dg_broker_config` | DG Broker配置 | `V$DATAGUARD_BROKER` | P2 |
| `dg_broker_status` | DG Broker状态 | `V$DATAGUARD_BROKER` | P2 |

---

## 4. 告警规则建议

### 4.1 RAC 告警

| 指标 | 警告阈值 | 严重阈值 | 级别 |
|------|----------|----------|------|
| 节点状态 | - | 不是 OPEN | 严重 |
| 实例数量 | < 预期数 | < 预期数-1 | 严重 |
| 互联错误 | > 0 | > 10 | 警告 |
| 全局缓存忙等待 | > 100 | > 500 | 警告 |
| GES 消息队列 | > 1000 | > 5000 | 警告 |

### 4.2 ADG 告警

| 指标 | 警告阈值 | 严重阈值 | 级别 |
|------|----------|----------|------|
| 应用延迟 | > 30秒 | > 300秒 | 严重 |
| 传输延迟 | > 30秒 | > 300秒 | 严重 |
| 归档Gap | > 0 | > 10 | 严重 |
| 保护模式降级 | - | 不是最大可用 | 严重 |
| MRP进程状态 | - | 不是运行 | 严重 |

---

## 5. 实现状态检查清单

### 5.1 当前实现

根据代码检查，当前 Oracle 检查器已有以下 RAC/ADG 指标：

**已有 (部分实现):**
- `rac_instance_count`
- `rac_instances` (包含 inst_id, instance_name, host_name, status)
- `dg_database_role`
- `dg_protection_mode`
- `dg_protection_level`

**遗漏 (需要补充):**
- 互联网络指标 (ic_bytes_sent, ic_bytes_received, etc.)
- 缓存融合指标 (gc_cr_blocks_received, etc.)
- ADG 延迟指标 (apply_lag, transport_lag)
- ADG Gap 指标 (archive_gap)
- MRP/RFS 进程状态
- 告警规则实现

### 5.2 补充建议

需要补充的实现项：
1. 完善 RAC 互联网络监控
2. 增加 ADG 应用延迟实时监控
3. 增加 ADG Gap 检测
4. 实现 RAC/ADG 专用告警规则

---

## 6. SQL 示例

### RAC 节点状态
```sql
SELECT inst_id, instance_name, host_name, status, database_status, open_mode
FROM gv$instance ORDER BY inst_id;
```

### RAC 互联网络
```sql
SELECT inst_id, name, ip_address, ic_bytes_sent, ic_bytes_received, ic_packets_sent, ic_packets_received
FROM gv$cluster_interconnects;
```

### ADG 延迟
```sql
SELECT name, value, unit, time_computed
FROM v$dataguard_stats WHERE name IN ('transport lag', 'apply lag');
```

### ADG Gap
```sql
SELECT * FROM v$archive_gap;
```

### ADG 备库进程
```sql
SELECT process, status, client_process, client_pid
FROM v$managed_standby WHERE process IN ('MRP0', 'MRP1', 'RFS');
```
