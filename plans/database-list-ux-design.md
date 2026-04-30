# 数据库列表页面 UX 改进方案

## 1. 目标
让DBA**一眼看清哪些库有问题**，快速定位问题数据库，实现"好用、易用、方便DBA快速定位问题"。

## 2. 核心改进

### 2.1 数据获取策略
采用前端批量调用API，保证数据实时性：
```
DatabaseList → GET /api/v1/databases/                    # 基础列表
            → GET /api/v1/databases/{id}/status/        # 状态+指标
            → GET /api/v1/databases/{id}/health/       # 健康评分
            → GET /api/v1/databases/{id}/alerts/        # 告警数量
```

使用 `Promise.all` 并行请求，使用 `AbortController` 支持取消请求。

### 2.2 新增列设计

| 列名 | 数据来源 | 显示内容 | 颜色编码 |
|------|----------|----------|----------|
| 状态 | status.status | UP/DOWN | 绿UP/红DOWN/灰UNKNOWN |
| 健康分 | health.scores[0].total_score | 0-100 | 绿≥80/黄≥60/红<60 |
| 告警数 | alerts.filter | 数字徽章 | 红>0/绿=0 |
| CPU | status.metrics.cpu | 百分比 | 红>80%/黄>60%/绿 |
| 连接数 | status.metrics.connections | 数字 | - |
| 表空间 | status.metrics.tablespace_percent | 百分比 | 红>90%/黄>80%/绿 |

### 2.3 智能排序规则

**默认排序**：问题库置顶
```
优先级 = (100 - 健康分) * 10 + 告警数 * 5 + (状态 !== 'UP' ? 1000 : 0)
```

**可切换排序**：
- 按健康分升序/降序
- 按告警数降序
- 按状态
- 按名称
- 按最后更新时间

### 2.4 卡片统计区增强

原统计：
- 总数据库数
- 正常运行
- 离线
- 总数据库类型

**新增统计**：
- 🟢 健康库数（健康分≥80）
- 🟡 亚健康库数（60≤健康分<80）
- 🔴 问题库数（健康分<60）
- 🚨 告警中库数（有未确认告警）

## 3. UI组件设计

### 3.1 状态徽章
```jsx
// 状态颜色
const STATUS_CONFIG = {
  UP: { color: '#52c41a', text: '正常', icon: <CheckCircleOutlined /> },
  DOWN: { color: '#ff4d4f', text: '故障', icon: <CloseCircleOutlined /> },
  UNKNOWN: { color: '#999', text: '未知', icon: <QuestionCircleOutlined /> }
}
```

### 3.2 健康分徽章
```jsx
// 健康分颜色
const getHealthBadge = (score) => {
  if (score === null || score === undefined) return <Tag>无数据</Tag>
  if (score >= 80) return <Tag color="success">{score}分</Tag>
  if (score >= 60) return <Tag color="warning">{score}分</Tag>
  return <Tag color="error">{score}分</Tag>
}
```

### 3.3 告警徽章
```jsx
// 告警数量徽章
const getAlertBadge = (count) => {
  if (count === 0) return <Badge count={0} showZero color="#52c41a" />
  return <Badge count={count} color="#ff4d4f" />
}
```

### 3.4 关键指标显示
```jsx
// 指标格式化显示
const formatMetric = (value, metric) => {
  if (value === null || value === undefined) return '-'
  if (metric.includes('percent') || metric.includes('pct')) {
    return <Progress percent={value.toFixed(1)} size="small" />
  }
  return value.toLocaleString()
}
```

## 4. 性能优化

### 4.1 请求优化
- 并行请求：使用 `Promise.all`
- 请求取消：使用 `AbortController`
- 增量加载：先显示基础列表，再加载详情
- 防抖刷新：避免频繁刷新

### 4.2 缓存策略
```javascript
// 缓存配置
const CACHE_CONFIG = {
  status: { ttl: 30000, key: 'db_status_' },   // 30秒
  health: { ttl: 300000, key: 'db_health_' },  // 5分钟
  alerts: { ttl: 60000, key: 'db_alerts_' }    // 1分钟
}
```

### 4.3 虚拟滚动
如果数据库数量>100，使用虚拟滚动优化渲染性能。

## 5. 用户交互

### 5.1 刷新机制
- 手动刷新：点击刷新按钮
- 自动刷新：可配置间隔（30s/1min/5min）
- 数据时效提示：显示"数据更新时间"

### 5.2 排序交互
- 点击表头排序
- 下拉选择排序方式
- 保存用户排序偏好

### 5.3 告警快捷操作
- 点击告警数 → 跳转告警列表（筛选该库）
- 点击健康分 → 跳转详情页健康分tab

## 6. 实现步骤

### Phase 1: 基础增强
1. 重构数据获取逻辑，批量调用API
2. 添加健康分列（颜色编码）
3. 添加告警数徽章列
4. 优化卡片统计区

### Phase 2: 高级功能
5. 添加关键指标列
6. 实现智能排序（问题库置顶）
7. 添加排序切换功能
8. 添加数据时效提示

### Phase 3: 性能优化
9. 添加缓存机制
10. 优化渲染性能
11. 添加加载状态优化

## 7. 预期效果

**Before:**
```
| 名称 | 类型 | 状态 | 环境 | 最后更新 | 操作 |
|------|------|------|------|----------|------|
| 核心交易库 | Oracle | 正常 | 生产 | 04-29 10:00 | 详情 |
| 用户中心库 | MySQL | 正常 | 生产 | 04-29 10:00 | 详情 |
```

**After:**
```
| 名称 | 类型 | 状态 | 健康分 | 告警 | CPU | 连接 | 空间 | 最后更新 | 操作 |
|------|------|------|--------|------|-----|------|------|----------|------|
| 核心交易库 | Oracle | 🟢UP | 95分 | 0 | 45% | 120 | 72% | 04-29 10:00 | 详情 |
| 用户中心库 | MySQL | 🔴DOWN | 32分 | 5 | 89% | 950 | 91% | 04-29 09:55 | 详情 |
```

问题库自动置顶，告警数量一目了然。