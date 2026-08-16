# DB-AIOps v2.0 智能数据库运维平台 - 接口设计说明书 (API Specification)

> **文档版本**：v2.0  
> **编制日期**：2026-08-16  
> **接口标准**：RESTful API v2.0，基于 Bearer Token 认证与 RBAC 细粒度权限控制，统一返回 JSON 包装。

---

## 1. 统一接口规范与错误码体系

### 1.1 统一请求头规范
- `Authorization: Bearer <token>`
- `Content-Type: application/json`

### 1.2 统一响应格式
```json
{
  "code": "OK",
  "message": "操作成功",
  "data": { ... },
  "timestamp": "2026-08-16T11:30:00Z"
}
```

### 1.3 核心业务错误码表

| 错误码 | HTTP 状态码 | 说明 |
| :--- | :--- | :--- |
| `OK` | 200 | 请求成功 |
| `UNAUTHORIZED` | 401 | 未登录或 Token 已过期 |
| `FORBIDDEN` | 403 | 权限不足或无权访问该数据库实例 |
| `NOT_FOUND` | 404 | 目标资源（数据库/工单/剧本）不存在 |
| `DRYRUN_REJECTED` | 422 | 预演未通过（如尝试终止受保护系统账号） |
| `CONCURRENT_CONFLICT` | 409 | 存在正在执行中的排查或自愈任务 |
| `TIMEOUT` | 504 | 数据库查询或探针执行超时 |

---

## 2. v2.0 核心业务接口契约清单

### 2.1 故障感知与 WarRoom 接口族

#### 接口 1：获取实时活跃故障聚合清单
- **请求**：`GET /api/v2/incidents/realtime-active/?page=1&page_size=10`
- **权限**：`alerts.view`
- **响应**：
  ```json
  {
    "code": "OK",
    "data": {
      "total": 1,
      "items": [
        {
          "incident_id": "INC-20260816-001",
          "title": "Oracle 核心交易库行锁等待堆积与连接耗尽",
          "severity": "critical",
          "config_id": 1,
          "db_name": "核心交易库_主节点",
          "status": "running",
          "duration_seconds": 185,
          "sla_remaining_seconds": 715,
          "active_alert_count": 6
        }
      ]
    }
  }
  ```

#### 接口 2：获取 WarRoom 全景排障上下文
- **请求**：`GET /api/v2/incidents/{incident_id}/warroom-context/`
- **权限**：`alerts.view`
- **响应**：
  ```json
  {
    "code": "OK",
    "data": {
      "incident_id": "INC-20260816-001",
      "summary": "由于 JavaClient 应用发起无主键大批量更新，导致 order_item 表产生全局排他行锁，引发 38 个事务级联阻塞",
      "confidence": 0.94,
      "metrics_timeline": [
        {"time": "11:20:00", "active_sessions": 12, "cpu_pct": 24.5, "lock_waits": 0},
        {"time": "11:23:40", "active_sessions": 142, "cpu_pct": 89.5, "lock_waits": 38}
      ],
      "causal_chain": [
        {
          "seq": 1,
          "node_type": "CHANGE",
          "name": "应用发布更新",
          "desc": "11:20 产生批量更新 SQL",
          "evidence": ["E1"]
        },
        {
          "seq": 2,
          "node_type": "SQL",
          "name": "SQL_ID: 8a7fbc6d 批量更新",
          "desc": "全表扫描更新 order_item",
          "evidence": ["E2"]
        },
        {
          "seq": 3,
          "node_type": "LOCK",
          "name": "根源阻塞源 (SID: 1845)",
          "desc": "持有行级锁超过 120 秒",
          "evidence": ["E3"]
        }
      ]
    }
  }
  ```

---

### 2.2 性能中枢与阻塞图谱接口族

#### 接口 3：获取实时会话阻塞依赖图谱 (Blocking Graph)
- **请求**：`GET /api/v2/databases/{config_id}/blocking-graph/`
- **权限**：`metrics.view`
- **响应**：
  ```json
  {
    "code": "OK",
    "data": {
      "root_blockers": [
        {
          "session_id": "1845",
          "serial_num": "49201",
          "username": "app_user",
          "client_ip": "10.10.20.105",
          "sql_id": "8a7fbc6d",
          "sql_text": "UPDATE order_item SET status = 2 WHERE batch_no = 'B20260816'",
          "wait_time_seconds": 180,
          "blocked_sessions_count": 38,
          "blocked_children": [
            {
              "session_id": "2104",
              "username": "app_user2",
              "wait_event": "enq: TX - row lock contention",
              "wait_time_seconds": 150
            }
          ]
        }
      ]
    }
  }
  ```

---

### 2.3 自愈 Playbook 决策与安全沙箱接口族

#### 接口 4：自愈 Playbook 安全预演 (Dry-Run)
- **请求**：`POST /api/v2/playbooks/execute-dryrun/`
- **权限**：`tickets.execute`
- **请求体**：
  ```json
  {
    "incident_id": "INC-20260816-001",
    "playbook_code": "KILL_ROOT_BLOCKER",
    "config_id": 1,
    "params": {
      "session_id": "1845",
      "serial_num": "49201"
    }
  }
  ```
- **响应**：
  ```json
  {
    "code": "OK",
    "data": {
      "dryrun_status": "PASSED",
      "risk_level": "LOW",
      "impact_summary": "目标会话为普通业务应用连接，无长事务未提交，终止后可立即释放 38 个下游阻塞事务",
      "rollback_plan_available": true,
      "requires_double_check": false
    }
  }
  ```

#### 接口 5：自愈 Playbook 正式执行
- **请求**：`POST /api/v2/playbooks/execute-safely/`
- **权限**：`tickets.execute`
- **请求体**：
  ```json
  {
    "incident_id": "INC-20260816-001",
    "playbook_code": "KILL_ROOT_BLOCKER",
    "config_id": 1,
    "params": {
      "session_id": "1845",
      "serial_num": "49201"
    },
    "reason": "终止根源阻塞会话以恢复 38 个被阻塞业务事务"
  }
  ```
- **响应**：
  ```json
  {
    "code": "OK",
    "data": {
      "run_id": "RUN-20260816-8921",
      "status": "success",
      "message": "会话 1845 已成功终止，38 个锁等待已解除，指标正在恢复中",
      "executed_at": "2026-08-16T11:35:10Z"
    }
  }
  ```

---

### 2.4 DB Copilot 专家对话与一键体检接口族

#### 接口 6：Copilot 专家会话流
- **请求**：`POST /api/v2/copilot/dba-expert-chat/`
- **权限**：`metrics.view`
- **请求体**：
  ```json
  {
    "query": "分析核心交易库过去 10 分钟活跃会话激增的原因并给出处置建议",
    "config_id": 1,
    "history": [
      {"role": "user", "content": "当前有哪些锁等待？"},
      {"role": "assistant", "content": "发现 1 个根阻塞源..."}
    ]
  }
  ```
- **响应**：
  ```json
  {
    "code": "OK",
    "data": {
      "answer": "### 🔍 深度根因分析报告\n\n经过对 **核心交易库_主节点** 的快照与因果图推导：\n1. **直接诱因**：会话 `1845` 正在执行 `SQL_ID: 8a7fbc6d`，因缺少复合索引触发全表扫描并持有行锁；\n2. **处置方案**：建议执行预案 `KILL_ROOT_BLOCKER` 释放阻塞，并在发布窗口补充索引 `CREATE INDEX idx_order_batch ON order_item(batch_no, status);`。",
      "model": "ExpertAgent-v2.0",
      "source": "hybrid_llm_expert",
      "latency_ms": 128
    }
  }
  ```

#### 接口 7：一键智能体检生成
- **请求**：`GET /api/v2/databases/{config_id}/quick-assessment/`
- **权限**：`databases.view`
- **响应**：
  ```json
  {
    "code": "OK",
    "data": {
      "assessment": {
        "overall_score": 92,
        "grade": "A",
        "assessed_at": "2026-08-16T11:35:00Z",
        "dimensions": [
          {"name": "运行可用性", "score": 100, "weight": 0.25},
          {"name": "性能与负载", "score": 88, "weight": 0.25},
          {"name": "告警稳定性", "score": 90, "weight": 0.20},
          {"name": "容量规划", "score": 95, "weight": 0.15},
          {"name": "安全与运维", "score": 90, "weight": 0.15}
        ],
        "risk_items": [
          {"level": "medium", "title": "表空间利用率接近预警线", "desc": "DATA01 表空间已达 78%"}
        ],
        "recommendations": [
          "配置自动扩容策略或在容量中心提前规划扩容工单"
        ]
      }
    }
  }
  ```
