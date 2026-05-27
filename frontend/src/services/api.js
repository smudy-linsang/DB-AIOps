import axios from 'axios'

const API_BASE = '/api/v1'
const MAX_RETRIES = 2
const RETRY_DELAY_MS = 1000

// 创建 axios 实例
const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器 - 添加 Token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    // 标记是否可重试（默认允许 GET 请求重试）
    config.__retryCount = config.__retryCount || 0
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器 - 统一错误处理 + 自动重试
api.interceptors.response.use(
  (response) => {
    return response.data
  },
  async (error) => {
    const config = error.config

    // 401 未授权：清除 Token 并跳转登录
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('user')
      // 避免在登录页重复跳转
      if (!window.location.pathname.includes('/login')) {
        window.location.href = '/login'
      }
      return Promise.reject(error)
    }

    // 5xx 服务端错误：对幂等请求自动重试
    const isRetryable = !config || config.__retryCount >= MAX_RETRIES
    const isIdempotent = !config.method || config.method?.toLowerCase() === 'get'
    const isServerError = error.response?.status >= 500

    if (!isRetryable && isIdempotent && (isServerError || !error.response)) {
      config.__retryCount += 1
      // 指数退避延迟
      const delay = RETRY_DELAY_MS * Math.pow(2, config.__retryCount - 1)
      await new Promise(resolve => setTimeout(resolve, delay))
      return api(config)
    }

    // 4xx 客户端错误或其他：直接拒绝
    const message = error.response?.data?.error || error.message || '请求失败'
    return Promise.reject(new Error(message))
  }
)

// ==========================================
// 认证 API
// ==========================================

export const authAPI = {
  login: (username, password) => 
    api.post('/auth/login/', { username, password }),
  logout: () => 
    api.post('/auth/logout/'),
  getCurrentUser: () => 
    api.get('/users/me/'),
}

// ==========================================
// 健康检查 API
// ==========================================

export const healthAPI = {
  check: () => api.get('/health/'),
}

// ==========================================
// 数据库配置 API
// ==========================================

export const databaseAPI = {
  // 获取数据库列表
  list: () => api.get('/databases/'),
  
  // 创建数据库配置
  create: (data) => api.post('/databases/', data),
  
  // 测试数据库连接（不保存）
  testConnection: (data) => api.post('/databases/test-connection/', data),
  
  // 获取数据库详情
  getDetail: (id) => api.get(`/databases/${id}/`),
  
  // 更新数据库配置
  update: (id, data) => api.put(`/databases/${id}/`, data),
  
  // 删除数据库配置
  delete: (id) => api.delete(`/databases/${id}/`),
  
  // 获取数据库状态
  getStatus: (id) => api.get(`/databases/${id}/status/`),
  
  // 获取数据库指标
  getMetrics: (id, params = {}) =>
    api.get(`/databases/${id}/metrics/`, { params }),

  // 获取历史指标（支持自定义时间范围 start_time / end_time）
  getMetricsHistory: (id, params = {}) =>
    api.get(`/databases/${id}/metrics/history/`, { params }),

  // 获取基线
  getBaseline: (id) => api.get(`/databases/${id}/baseline/`),
  
  // 获取预测
  getPrediction: (id) => api.get(`/databases/${id}/prediction/`),
  
  // 获取健康评分
  getHealth: (id) => api.get(`/databases/${id}/health/`),
  
  // 获取统计信息
  getStats: () => api.get('/databases/stats/'),

  // 获取 Performance Hub 聚合数据（EMCC 风格）
  getPerformanceHub: (id, params = {}) =>
    api.get(`/databases/${id}/performance-hub/`, { params }),
}

// ==========================================
// 告警 API
// ==========================================

export const alertAPI = {
  // 获取告警列表
  list: (params = {}) => api.get('/alerts/', { params }),
  
  // 获取告警统计
  getStatistics: () => api.get('/alerts/statistics/'),
  
  // 确认告警（移入已确认列表，继续抑制重复触发）
  acknowledge: (id) => api.post(`/alerts/${id}/acknowledge/`),

  // 彻底删除告警（删除后该指标可重新触发告警）
  delete: (id) => api.delete(`/alerts/${id}/`),

  // 获取数据库关联告警
  getByDatabase: (dbId) => api.get(`/databases/${dbId}/alerts/`),
}

// ==========================================
// 运维工单 API
// ==========================================

export const auditLogAPI = {
  // 获取工单列表
  list: (params = {}) => api.get('/auditlogs/', { params }),
  
  // 审批工单
  approve: (id) => api.post(`/auditlogs/${id}/approve/`),
  
  // 拒绝工单
  reject: (id, reason) => api.post(`/auditlogs/${id}/reject/`, { reason }),
  
  // 执行工单
  execute: (id) => api.post(`/auditlogs/${id}/execute/`),
  
  // 预执行（验证SQL语法）
  dryRun: (id) => api.post(`/auditlogs/${id}/dry-run/`),
}

// ==========================================
// 工单创建 API (Phase 5)
// ==========================================

export const ticketAPI = {
  create: (data) => api.post('/tickets/', data),
}

// ==========================================
// 用户管理 API
// ==========================================

export const userAPI = {
  // 获取用户列表
  list: () => api.get('/users/'),
  
  // 获取用户详情
  getDetail: (id) => api.get(`/users/${id}/`),
  
  // 更新用户
  update: (id, data) => api.put(`/users/${id}/`, data),
  
  // 修改密码
  changePassword: (id, password) => 
    api.put(`/users/${id}/password/`, { password }),
  
  // 获取当前用户
  getCurrentUser: () => api.get('/users/me/'),
}

// ==========================================
// 仪表盘 API
// ==========================================

export const dashboardAPI = {
  // 获取仪表盘统计
  getStats: () => api.get('/dashboard/stats/'),
  
  // 获取仪表盘图表数据
  getCharts: () => api.get('/dashboard/charts/'),
  
  // 获取健康评分趋势
  getHealthTrend: (days = 7) => 
    api.get('/dashboard/health-trend/', { params: { days } }),
  
  // 获取告警趋势
  getAlertTrend: (days = 7) => 
    api.get('/dashboard/alert-trend/', { params: { days } }),
}

// ==========================================
// 告警规则配置 API
// ==========================================

export const alertRuleAPI = {
  // 获取某数据库类型已采集到的所有可用指标键
  listAvailableMetrics: (db_type) =>
    api.get('/alert-rules/available-metrics/', { params: { db_type } }),

  // 获取模板列表（可按 db_type 过滤）
  listTemplates: (db_type) =>
    api.get('/alert-templates/', { params: db_type ? { db_type } : {} }),

  // 创建模板
  createTemplate: (data) => api.post('/alert-templates/', data),

  // 更新模板
  updateTemplate: (id, data) => api.put(`/alert-templates/${id}/`, data),

  // 删除模板
  deleteTemplate: (id) => api.delete(`/alert-templates/${id}/`),

  // 获取某数据库的覆盖配置（含模板基准值）
  listOverrides: (dbId) => api.get(`/databases/${dbId}/alert-overrides/`),

  // 保存（新建或更新）某数据库某指标的覆盖配置
  saveOverride: (dbId, data) => api.post(`/databases/${dbId}/alert-overrides/`, data),

  // 删除覆盖配置（恢复使用模板默认值）
  deleteOverride: (dbId, metricKey) =>
    api.delete(`/databases/${dbId}/alert-overrides/${metricKey}/`),
}

// ==========================================
// 告警模板管理 API（Phase 3: 多模板模式）
// ==========================================

export const alertTemplateAPI = {
  // 列出所有模板（可按 db_type 过滤，或全局）
  list: (params = {}) =>
    api.get('/alert-templates/', { params }),

  // 获取单个模板详情（含内嵌规则）
  getDetail: (id) =>
    api.get(`/alert-templates/${id}/`),

  // 创建模板
  create: (data) =>
    api.post('/alert-templates/', data),

  // 更新模板元信息
  update: (id, data) =>
    api.put(`/alert-templates/${id}/`, data),

  // 删除模板
  delete: (id) =>
    api.delete(`/alert-templates/${id}/`),

  // 克隆模板
  clone: (id, data) =>
    api.post(`/alert-templates/${id}/clone/`, data),

  // 获取模板内的规则列表
  listRules: (templateId) =>
    api.get(`/alert-templates/${templateId}/rules/`),

  // 添加规则到模板
  addRule: (templateId, data) =>
    api.post(`/alert-templates/${templateId}/rules/`, data),

  // 更新规则
  updateRule: (templateId, ruleId, data) =>
    api.put(`/alert-templates/${templateId}/rules/${ruleId}/`, data),

  // 删除规则
  deleteRule: (templateId, ruleId) =>
    api.delete(`/alert-templates/${templateId}/rules/${ruleId}/`),

  // 批量启用/禁用规则
  batchToggleRules: (templateId, ruleIds, enabled) =>
    api.post(`/alert-templates/${templateId}/rules/batch-toggle/`, { rule_ids: ruleIds, enabled }),

  // 为数据库分配模板
  assignTemplate: (dbId, templateId) =>
    api.post(`/databases/${dbId}/assign-template/`, { template_id: templateId }),

  // 获取数据库当前使用的模板
  getAssignedTemplate: (dbId) =>
    api.get(`/databases/${dbId}/assigned-template/`),
}

// ==========================================
// SQL 监控 API（Phase 5: SQL Monitoring）
// ==========================================

export const sqlMonitoringAPI = {
  // 获取慢查询列表 (GET /api/v1/databases/<dbId>/slow-queries/)
  list: (dbId, params = {}) =>
    api.get(`/databases/${dbId}/slow-queries/`, { params }),

  // 获取慢查询分析 (GET /api/v1/databases/<dbId>/slow-queries/analysis/)
  getAnalysis: (dbId, params = {}) =>
    api.get(`/databases/${dbId}/slow-queries/analysis/`, { params }),

  // SQL 文本搜索 (GET /api/v1/databases/<dbId>/slow-queries/search/)
  search: (dbId, params = {}) =>
    api.get(`/databases/${dbId}/slow-queries/search/`, { params }),
}

// ==========================================
// 便捷函数
// ==========================================

export const setAuthToken = (token) => {
  localStorage.setItem('auth_token', token)
}

export const clearAuthToken = () => {
  localStorage.removeItem('auth_token')
  localStorage.removeItem('user')
}

export const getAuthToken = () => {
  return localStorage.getItem('auth_token')
}

export const setUser = (user) => {
  localStorage.setItem('user', JSON.stringify(user))
}

export const getUser = () => {
  const userStr = localStorage.getItem('user')
  return userStr ? JSON.parse(userStr) : null
}

export const isAuthenticated = () => {
  return !!localStorage.getItem('auth_token')
}

// ==========================================
// 告警静默窗口 API (Phase 4)
// ==========================================

export const silenceWindowAPI = {
  list: (params = {}) => api.get('/silence-windows/', { params }),
  create: (data) => api.post('/silence-windows/', data),
  getDetail: (id) => api.get(`/silence-windows/${id}/`),
  update: (id, data) => api.put(`/silence-windows/${id}/`, data),
  delete: (id) => api.delete(`/silence-windows/${id}/`),
}

// ==========================================
// 通知规则 API (Phase 4)
// ==========================================

export const notificationRuleAPI = {
  list: (params = {}) => api.get('/notification-rules/', { params }),
  create: (data) => api.post('/notification-rules/', data),
  getDetail: (id) => api.get(`/notification-rules/${id}/`),
  update: (id, data) => api.put(`/notification-rules/${id}/`, data),
  delete: (id) => api.delete(`/notification-rules/${id}/`),
  test: (data) => api.post('/notification-rules/test/', data),
}

// ==========================================
// 告警通知日志 API (Phase 4)
// ==========================================

export const alertNotificationAPI = {
  list: (params = {}) => api.get('/alert-notifications/', { params }),
}

// ==========================================
// 业务系统 API (Phase 4)
// ==========================================

export const businessSystemAPI = {
  list: (params = {}) => api.get('/business-systems/', { params }),
  create: (data) => api.post('/business-systems/', data),
  getDetail: (id) => api.get(`/business-systems/${id}/`),
  update: (id, data) => api.put(`/business-systems/${id}/`, data),
  delete: (id) => api.delete(`/business-systems/${id}/`),
}

// ==========================================
// 数据库拓扑 API (Phase 4)
// ==========================================

export const topologyAPI = {
  // 获取数据库拓扑信息
  getTopology: (dbId) => api.get(`/databases/${dbId}/topology/`),
  // 创建/更新拓扑关系
  saveTopology: (dbId, data) => api.post(`/databases/${dbId}/topology/`, data),
  // 影响分析
  getImpact: (dbId) => api.get(`/databases/${dbId}/impact/`),
  // 全局拓扑总览
  getOverview: () => api.get('/topology/overview/'),
}

// ==========================================
// 报表 API (Phase 4)
// ==========================================

export const reportAPI = {
  list: (params = {}) => api.get('/reports/', { params }),
  download: (id) => api.get(`/reports/${id}/download/`, { responseType: 'blob' }),
  generate: (data) => api.post('/reports/generate/', data),
}

// ==========================================
// 容量预测增强 API (Phase 5)
// ==========================================

export const capacityAPI = {
  overview: () => api.get('/capacity/overview/'),
  predictNow: (dbId) => api.post(`/databases/${dbId}/predict-now/`),
}

// ==========================================
// 角色管理 API (RBAC v2.0)
// ==========================================

export const roleAPI = {
  list: () => api.get('/roles/'),
  create: (data) => api.post('/roles/', data),
  getDetail: (id) => api.get(`/roles/${id}/`),
  update: (id, data) => api.put(`/roles/${id}/`, data),
  delete: (id) => api.delete(`/roles/${id}/`),
}

export default api
