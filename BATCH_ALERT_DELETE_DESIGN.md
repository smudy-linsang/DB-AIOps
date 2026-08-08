# 告警中心批量删除功能 详细设计说明书

| 项目 | 内容 |
|------|------|
| 需求 | 告警中心当前仅支持单条删除；管理员应能勾选多条告警一次性批量删除 |
| 权限 | 复用既有细粒度权限 `alerts.delete`（仅 超级管理员/数据库管理员 拥有），不新增权限码 |
| 基线 | 现有单删接口 `DELETE /api/v1/alerts/<id>/`（AlertDeleteView）、前端 `AlertList.jsx` |
| 审计 | 写操作经 `AuditLogMiddleware` 自动落审计日志，无需额外改造 |

---

## 一、后端设计

### 1.1 新增接口
`POST /api/v1/alerts/batch-delete/`

- 装饰器：`@csrf_exempt` + `@require_auth` + `@require_permission('alerts.delete')`（与单删一致）
- 请求体：`{ "ids": [1, 2, 3] }`
- 响应：`{ "deleted": n, "skipped": m, "message": "..." }`

### 1.2 处理逻辑（AlertBatchDeleteView）
1. 解析 JSON；`ids` 必须为非空列表，否则 400。
2. 归一化为 int 列表，去重；数量上限 `MAX_BATCH=500`，超出 400（防滥用/防超长 SQL）。
3. 数据范围控制：`get_user_database_ids(user)` 非 None 时，仅删除 `config_id` 在范围内的告警（越权行计入 skipped）。
4. 用 `AlertLog.objects.filter(id__in=ids[, config_id__in=allowed])` 一次性 `delete()`，返回实际删除数。
5. `skipped = 请求去重数 - deleted`（含不存在/越权行）。
6. 全程单条 DELETE SQL，事务原子。

### 1.3 路由注册
在 `dbmonitor/urls.py` 中 `alerts/statistics/` 之后、`alerts/<int:alert_id>/` 之前注册
`path('api/v1/alerts/batch-delete/', AlertBatchDeleteView.as_view())`。
（`<int:alert_id>` 不匹配 "batch-delete"，无冲突；前置注册更稳妥。）

### 1.4 安全要点
- 权限：无 `alerts.delete` 一律 403（readonly/auditor/config_operator 均无）。
- 未登录 401。
- 数据范围二次校验，防跨库越权删除。
- 数量上限防 DoS。
- 审计中间件自动记录操作人/方法/路径。

## 二、前端设计

### 2.1 api.js
`alertAPI` 增加：`batchDelete: (ids) => api.post('/alerts/batch-delete/', { ids })`

### 2.2 AlertList.jsx
- `AlertTable` 增加受控行选择 `rowSelection`（`selectedRowKeys` 本地 state）。
- 表格上方工具条：当 `selectedRowKeys.length>0` 时显示
  `<PermissionGuard code={Perm.ALERTS_DELETE}><Button danger>批量删除(n)</Button></PermissionGuard>`。
- 点击后 `Modal.confirm` 二次确认（提示"删除后指标可重新触发告警"），确认调用 `onBatchDelete(ids)`。
- 主页面 `handleBatchDelete`：调 `alertAPI.batchDelete`，成功后 `message.success`、清空选择、`fetchAlerts()` 刷新。
- 删除成功/失败均给出反馈；失败不刷新。

### 2.3 交互细节
- 切换 Tab/刷新后清空选择，避免误删不可见行。
- 无 `alerts.delete` 权限的用户不显示批量删除按钮（PermissionGuard）。

## 三、测试计划
- 冒烟：接口可达；admin 登录 200；未登录 401。
- SIT：
  - 正常批量删除返回 deleted 正确、列表减少；
  - 空 ids / 非列表 / 超 500 → 400；
  - 含不存在 id → skipped 计数正确；
  - readonly 用户 → 403（越权）；
  - 数据范围：受限用户删除范围外 id 计入 skipped。
- UAT：浏览器以 admin 登录，勾选多条告警→批量删除→确认→列表刷新、统计数下降；readonly 登录不显示按钮。

## 四、回退方案
- 纯新增接口与前端可选 UI，回退仅需移除路由/按钮，不影响存量单删逻辑。
