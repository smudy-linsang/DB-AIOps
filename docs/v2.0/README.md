# DB-AIOps v2.0 智能数据库运维平台 - 设计文档集总目录与导读

> **版本号**：v2.0  
> **编制日期**：2026-08-16  
> **核心战役目标**：**1 分钟发现问题（1-Min Detect）、5 分钟定位根因（5-Min Root Cause）、15 分钟闭环解决（15-Min Remediation）**

---

## 📚 v2.0 完整设计文档集索引

本次 v2.0 升级改造已完成全套规范化设计文档编制，分为 5 大独立分册，覆盖从业务目标、系统架构、代码级实现、数据库表结构到前端交互与接口契约的全部维度：

| 分册编号 | 文档名称 | 文档路径 | 核心内容概述 |
| :--- | :--- | :--- | :--- |
| **01** | **概要设计说明书 (HLD)** | [`docs/v2.0/01_HIGH_LEVEL_DESIGN.md`](file:///Users/mac/DB_Monitor/db-aiops/docs/v2.0/01_HIGH_LEVEL_DESIGN.md) | 1-5-15 目标技术分解、系统总体分层架构、四大子系统划分及非功能性指标 |
| **02** | **详细设计说明书 (LLD)** | [`docs/v2.0/02_LOW_LEVEL_DESIGN.md`](file:///Users/mac/DB_Monitor/db-aiops/docs/v2.0/02_LOW_LEVEL_DESIGN.md) | **照图施工标准**：异构 DB 内核级 SQL 采集清单、多变量动态基线算法、RCA 3.0 因果图谱推导、Dry-Run 预演沙箱 |
| **03** | **数据库设计说明书 (DDL)** | [`docs/v2.0/03_DATABASE_DESIGN.md`](file:///Users/mac/DB_Monitor/db-aiops/docs/v2.0/03_DATABASE_DESIGN.md) | 严格符合 4NF 规范的表结构（画像基线表、因果链明细表、Playbook 模板/执行表）与 TimescaleDB ASH 高频 Hypertable 优化 |
| **04** | **前端美工与交互设计 (UI/UX)** | [`docs/v2.0/04_UI_UX_DESIGN.md`](file:///Users/mac/DB_Monitor/db-aiops/docs/v2.0/04_UI_UX_DESIGN.md) | 科技深蓝设计系统 (Tokens)、一站式排障作战室 (Incident WarRoom)、360° 性能中枢、交互式 Blocking Tree 及 Copilot Drawer |
| **05** | **接口设计说明书 (API Spec)** | [`docs/v2.0/05_API_SPECIFICATION.md`](file:///Users/mac/DB_Monitor/db-aiops/docs/v2.0/05_API_SPECIFICATION.md) | RESTful API v2.0 完整契约、错误码体系、请求/响应 JSON Schema 及鉴权机制 |

---

## 🎯 核心技术实现路径速览

1. **采集广度与深度**：从 ~40 项指标全面升级至 **120+ 内核级指标**（RAC Cache Fusion, ADG Lag, InnoDB Buffer Pool 脏页/行锁, PG 死元组/Vacuum, 达梦 MAL 等）。
2. **智能化分析**：**168h 时变基线 + 拓扑降噪**（1分钟感知） ➔ **RCA 3.0 因果图谱 + Multi-Agent**（5分钟定位） ➔ **Playbook + Dry-Run 沙箱**（15分钟解决）。
3. **用户体验重塑**：**Incident WarRoom 一体化单页**，结合时光机回放与全局 AI Copilot 助手，实现 DBA 生产力质的飞跃。

*(注：当前未对任何业务代码做改动，请审阅上述文档集。确认后即可开展工程施工！)*
